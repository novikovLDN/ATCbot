"""Автопродление не должно списывать деньги в обход всех проверок.

Дефект: перед основным циклом auto_renewal_task безусловно вызывал
process_auto_renewals — без feature-флагов, без database.DB_READY, без
asyncio.wait_for и без ITERATION_START/END.

Два последствия, оба про деньги. Первое: при выключенном auto_renewal_enabled
стартовый прогон всё равно списывал средства — флаг читался только на первом
витке цикла, то есть уже после списаний. Второе: зависший прогон навсегда
удерживал _worker_lock, каждая следующая итерация честно рапортовала timeout,
но причина в логах не видна вообще — стартовый прогон не логировался.
"""
import asyncio
import inspect

import pytest

import auto_renewal
import database


def _task_src():
    return inspect.getsource(auto_renewal.auto_renewal_task)


def test_no_run_before_the_loop():
    """До `while True` не должно быть вызова обработки."""
    src = _task_src()
    before_loop = src[: src.index("while True:")]
    assert "await process_auto_renewals(" not in before_loop, (
        "стартовый прогон вернулся — работа с деньгами идёт мимо всех гейтов"
    )


def test_the_only_call_is_wrapped_in_timeout():
    """Единственный вызов живёт внутри тела итерации под wait_for."""
    src = _task_src()
    assert src.count("await process_auto_renewals(") == 1
    body = src[src.index("async def _run_iteration_body"):]
    assert "await process_auto_renewals(" in body
    assert "ITERATION_HARD_TIMEOUT_SECONDS" in src


async def test_db_gate_blocks_work_at_startup(monkeypatch):
    """При неготовой БД воркер не делает НИ одного прогона.

    Со старым кодом стартовый прогон уходил в работу до этой проверки.
    """
    calls = []

    async def _spy(bot):
        calls.append(bot)

    monkeypatch.setattr(auto_renewal, "process_auto_renewals", _spy)
    monkeypatch.setattr(database, "DB_READY", False)
    # Стартовый jitter в тесте не ждём: он до минуты.
    monkeypatch.setattr(auto_renewal.random, "uniform", lambda a, b: 0)

    task = asyncio.create_task(auto_renewal.auto_renewal_task(object()))
    await asyncio.sleep(0.05)
    task.cancel()
    # Воркер ловит CancelledError сам (штатная остановка), поэтому наружу
    # исключение может и не выйти — важно лишь дождаться завершения.
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert calls == [], "работа пошла при неготовой БД"


async def test_feature_flag_blocks_work_at_startup(monkeypatch):
    """При выключенном auto_renewal_enabled денег никто не списывает."""
    calls = []

    async def _spy(bot):
        calls.append(bot)

    monkeypatch.setattr(auto_renewal, "process_auto_renewals", _spy)
    monkeypatch.setattr(database, "DB_READY", True)
    monkeypatch.setattr(auto_renewal.random, "uniform", lambda a, b: 0)

    from app.core import feature_flags

    class _Off:
        background_workers_enabled = True
        auto_renewal_enabled = False

    monkeypatch.setattr(feature_flags, "get_feature_flags", lambda: _Off())

    task = asyncio.create_task(auto_renewal.auto_renewal_task(object()))
    await asyncio.sleep(0.05)
    task.cancel()
    # Воркер ловит CancelledError сам (штатная остановка), поэтому наружу
    # исключение может и не выйти — важно лишь дождаться завершения.
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert calls == [], "рубильник автопродления не удержал стартовый прогон"
