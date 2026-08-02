"""Приём существующей bypass-сущности должен начислять купленный трафик.

Дефект: при adopt сущность возвращалась как есть. Пользователь оплачивал
пакет ГБ, сущность в панели находилась — и лимит оставался прежним:
купленный трафик не начислялся вовсе.
"""
import inspect

import pytest

import app.services.remnawave_bypass as bp


def test_adopt_helper_exists():
    assert hasattr(bp, "_adopt_existing_entity")


def test_both_adopt_paths_use_helper():
    """Adopt происходит в двух местах: preflight и гонка на 409."""
    src = inspect.getsource(bp.create_bypass_user_entity)
    assert src.count("_adopt_existing_entity") == 2, (
        "обе точки приёма сущности должны начислять трафик"
    )


def test_helper_adds_traffic_not_overwrites():
    """Перезапись лимита съела бы неизрасходованный остаток пакета."""
    src = inspect.getsource(bp._adopt_existing_entity)
    assert "add_bypass_traffic" in src


def test_topup_failure_does_not_break_adopt():
    """Сбой начисления не отменяет приём: подписка уже действует."""
    src = inspect.getsource(bp._adopt_existing_entity)
    assert "BYPASS_ADOPT_TOPUP_FAILED" in src
    assert "except Exception" in src
    tail = src[src.index("except Exception"):]
    assert "return result" in src


@pytest.mark.asyncio
async def test_adopt_returns_recovered_flag(monkeypatch):
    """Флаг recovered отличает приём от создания — на него смотрит вызывающий."""
    async def fake_add(_tg, _bytes):
        return True

    monkeypatch.setattr(bp, "add_bypass_traffic", fake_add)
    entity = {"uuid": "u-1", "subscriptionUrl": "https://p/sub/x", "shortUuid": "s1"}
    res = await bp._adopt_existing_entity(
        entity, 42, traffic_limit_bytes=1024, http_status=200
    )
    assert res.ok is True
    assert res.recovered is True
    assert res.panel_uuid == "u-1"


@pytest.mark.asyncio
async def test_adopt_survives_topup_exception(monkeypatch):
    async def boom(_tg, _bytes):
        raise RuntimeError("panel down")

    monkeypatch.setattr(bp, "add_bypass_traffic", boom)
    res = await bp._adopt_existing_entity(
        {"uuid": "u-2"}, 42, traffic_limit_bytes=1024, http_status=409
    )
    assert res.ok is True, "сбой начисления не должен ломать выдачу"


@pytest.mark.asyncio
async def test_zero_traffic_skips_topup(monkeypatch):
    called = []

    async def spy(_tg, _bytes):
        called.append(_bytes)
        return True

    monkeypatch.setattr(bp, "add_bypass_traffic", spy)
    await bp._adopt_existing_entity({"uuid": "u"}, 42, traffic_limit_bytes=0, http_status=200)
    assert called == [], "нулевой пакет начислять не нужно"
