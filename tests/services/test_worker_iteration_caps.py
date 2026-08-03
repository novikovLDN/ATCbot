"""Проход воркера должен укладываться в свой интервал.

ЧТО ЛОМАЛОСЬ

    traffic_monitor и site_sync_worker шли по всему списку без всякого
    ограничения — в отличие от остальных воркеров, где итерация обёрнута
    в wait_for или ограничена MAX_ITERATION_SECONDS.

    traffic_monitor: на каждого человека запрос к панели плюс пауза
    0.2 секунды. Десять тысяч записей — больше получаса при интервале в
    пять минут.

    site_sync_worker: до 500 человек, по два запроса к сайту и пауза
    0.5 секунды — больше четырёх минут даже при мгновенных ответах, при
    интервале те же пять минут.

ЧЕМ ЭТО ПЛОХО

    Проходы наезжают друг на друга: следующий стартует, пока предыдущий
    ещё идёт. Дальше это множится — к панели и к сайту начинают ходить
    несколько проходов разом, и лимиты на той стороне срабатывают уже по
    настоящему.

    Обрыв не страшен сам по себе: оба прохода идемпотентны, недоделанное
    попадёт в следующий. Страшно молча — поэтому оба пишут, сколько
    успели.
"""
import inspect
import re

import pytest

from app.workers import traffic_monitor, site_sync_worker


WORKERS = [
    ("traffic_monitor", traffic_monitor, traffic_monitor.traffic_monitor_iteration,
     traffic_monitor.INTERVAL_SECONDS),
    ("site_sync", site_sync_worker, site_sync_worker.site_sync_worker_task,
     site_sync_worker.SYNC_INTERVAL),
]


@pytest.mark.parametrize("name,module,func,interval", WORKERS)
def test_iteration_is_capped_by_time(name, module, func, interval):
    src = inspect.getsource(func)
    assert "MAX_ITERATION_SECONDS" in src, (
        f"{name}: проход по списку снова без ограничения по времени"
    )
    assert "time.monotonic()" in src, f"{name}: время прохода не измеряется"


@pytest.mark.parametrize("name,module,func,interval", WORKERS)
def test_cap_is_below_the_interval(name, module, func, interval):
    """Иначе ограничение бессмысленно: проходы всё равно наедут."""
    cap = module.MAX_ITERATION_SECONDS
    assert cap < interval, (
        f"{name}: потолок прохода {cap} с не меньше интервала {interval} с"
    )


@pytest.mark.parametrize("name,module,func,interval", WORKERS)
def test_truncation_is_not_silent(name, module, func, interval):
    """Обрезанный проход, о котором никто не узнал, читается как полный."""
    src = inspect.getsource(func)
    assert "CAPPED" in src, f"{name}: обрыв прохода не попадает в лог"
    assert "logger.warning" in src, f"{name}: обрыв записан не предупреждением"


@pytest.mark.parametrize("name,module,func,interval", WORKERS)
def test_cap_is_configurable(name, module, func, interval):
    """Порог зависит от размера базы — его должно быть видно снаружи."""
    src = inspect.getsource(module)
    assert re.search(r'os\.getenv\("[A-Z_]*MAX_ITERATION_SECONDS"', src), (
        f"{name}: потолок захардкожен, поменять без правки кода нельзя"
    )


def test_dead_concurrency_constant_is_gone():
    """SYNC_CONCURRENCY обещал параллельность, которой в цикле нет."""
    assert not hasattr(site_sync_worker, "SYNC_CONCURRENCY")


@pytest.mark.asyncio
async def test_traffic_monitor_stops_at_the_cap(monkeypatch):
    """Проверка не по исходнику, а по поведению."""
    checked = []

    async def _users():
        return [{"telegram_id": i, "remnawave_uuid": f"u{i}"} for i in range(50)]

    async def _check(_bot, telegram_id, _uuid):
        checked.append(telegram_id)

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(traffic_monitor.database, "get_active_remnawave_users", _users)
    monkeypatch.setattr(traffic_monitor, "_check_user_traffic", _check)
    monkeypatch.setattr(traffic_monitor.asyncio, "sleep", _no_sleep)
    # Нулевой потолок: обрыв обязан произойти на первой же записи.
    monkeypatch.setattr(traffic_monitor, "MAX_ITERATION_SECONDS", -1)

    await traffic_monitor.traffic_monitor_iteration(bot=object())
    assert checked == [], "потолок не остановил проход"


@pytest.mark.asyncio
async def test_traffic_monitor_checks_everyone_when_there_is_time(monkeypatch):
    checked = []

    async def _users():
        return [{"telegram_id": i, "remnawave_uuid": f"u{i}"} for i in range(10)]

    async def _check(_bot, telegram_id, _uuid):
        checked.append(telegram_id)

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(traffic_monitor.database, "get_active_remnawave_users", _users)
    monkeypatch.setattr(traffic_monitor, "_check_user_traffic", _check)
    monkeypatch.setattr(traffic_monitor.asyncio, "sleep", _no_sleep)

    await traffic_monitor.traffic_monitor_iteration(bot=object())
    assert checked == list(range(10)), "проверены не все"
