"""В subscriptions.uuid должен попадать существующий идентификатор.

Дефект: provision_subscription всегда возвращал requested_uuid — тот, который
мы ПОПРОСИЛИ у панели. Панель может его не принять (400/422 → повторный POST
уже без uuid), а при усыновлении найденной сущности (recovered=True) у неё
свой vlessUuid, и флаг forced_uuid_accepted всегда False. В обоих случаях в
базу уезжал идентификатор, которого нет ни в одном инбаунде.

Почему это важно: по subscriptions.uuid ищут legacy-ветка
app/api/subscription_proxy.py и database/traffic.py, по нему же сверяют базу с
панелью. Выдуманное значение там означает «ничего не нашли» — молча, без
единой ошибки в логах.
"""
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _cfg():
    cfg = type("Cfg", (), {})()
    cfg.REMNAWAVE_ENABLED = True
    cfg.TRIAL_BYPASS_MB = 500
    cfg.COMBO_TARIFFS = {}
    cfg.TRAFFIC_LIMITS = {"basic": {30: 10 * 1024**3}}
    cfg.DEVICE_LIMITS = {"basic": 5}
    return cfg


def _fake_db(monkeypatch, *, existing_subscription=None):
    db = SimpleNamespace(
        get_pool=AsyncMock(return_value=None),
        get_subscription_any=AsyncMock(return_value=existing_subscription),
        get_remnawave_premium_uuid=AsyncMock(return_value=None),
        get_remnawave_uuid=AsyncMock(return_value=None),
        get_remnawave_bypass_cache=AsyncMock(return_value=None),
        set_remnawave_premium_uuid_and_url=AsyncMock(return_value=None),
        set_remnawave_premium_sub_url=AsyncMock(return_value=None),
        set_remnawave_bypass_cache=AsyncMock(return_value=None),
    )
    monkeypatch.setitem(sys.modules, "database", db)
    return db


async def _provision(monkeypatch, premium_result, panel_entity, *, existing_subscription=None):
    from app.services import purchase_flow, remnawave_bypass
    _fake_db(monkeypatch, existing_subscription=existing_subscription)
    bres = remnawave_bypass.BypassCreateResult(
        True, "byp", "https://rmnw/sub/b", "bs", 201, None, False,
    )
    get_user = AsyncMock(return_value=panel_entity)
    with patch.object(purchase_flow, "config", _cfg()), \
         patch.object(purchase_flow.remnawave_premium, "create_premium_user_entity",
                      AsyncMock(return_value=premium_result)), \
         patch.object(purchase_flow.remnawave_bypass, "create_bypass_user_entity",
                      AsyncMock(return_value=bres)), \
         patch("app.services.remnawave_api.get_user", get_user):
        out = await purchase_flow.provision_subscription(
            42,
            tariff="basic",
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            period_days=30,
            is_trial=False,
        )
    return out, get_user


PANEL_VLESS = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.mark.asyncio
async def test_rejected_forced_uuid_writes_panel_vless_uuid(monkeypatch):
    """Панель не приняла форс → пишем её фактический vlessUuid, не выдуманный."""
    from app.services import remnawave_premium

    pres = remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid="panel-uuid", forced_uuid_accepted=False,
        subscription_url="https://rmnw/sub/p", status=201, error=None,
        recovered=False, short_uuid="ps",
    )
    out, get_user = await _provision(
        monkeypatch, pres, {"vlessUuid": PANEL_VLESS, "subscriptionUrl": "https://rmnw/sub/p"},
    )

    assert out["uuid"] == PANEL_VLESS
    get_user.assert_awaited_once_with("panel-uuid")


@pytest.mark.asyncio
async def test_adopted_entity_writes_panel_vless_uuid(monkeypatch):
    """Усыновление (recovered=True): requested_uuid к сущности отношения не имеет."""
    from app.services import remnawave_premium

    pres = remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid="panel-uuid", forced_uuid_accepted=False,
        subscription_url="https://rmnw/sub/p", status=200, error=None,
        recovered=True, short_uuid="ps",
    )
    out, _ = await _provision(
        monkeypatch, pres, {"vlessUuid": PANEL_VLESS},
    )
    assert out["uuid"] == PANEL_VLESS


@pytest.mark.asyncio
async def test_accepted_forced_uuid_needs_no_extra_panel_call(monkeypatch):
    """Форс принят → requested_uuid И ЕСТЬ панельный vlessUuid, лишний GET не нужен."""
    from app.services import remnawave_premium

    pres = remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid="panel-uuid", forced_uuid_accepted=True,
        subscription_url="https://rmnw/sub/p", status=201, error=None,
        recovered=False, short_uuid="ps",
    )
    out, get_user = await _provision(monkeypatch, pres, {"vlessUuid": PANEL_VLESS})

    get_user.assert_not_awaited()
    assert out["uuid"] != PANEL_VLESS  # вернулся запрошенный, а он и есть панельный
    assert len(out["uuid"]) == 36


@pytest.mark.asyncio
async def test_legacy_samopis_uuid_is_never_replaced(monkeypatch):
    """У человека на руках старые VLESS-ссылки с этим uuid — подменять нельзя.

    Даже если панель отдаёт свой vlessUuid: перезапись сломает резолв
    /sub/<uuid> в legacy-ветке subscription_proxy, а это живые ссылки.
    """
    from app.services import remnawave_premium

    legacy = "11111111-2222-3333-4444-555555555555"
    pres = remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid="panel-uuid", forced_uuid_accepted=False,
        subscription_url="https://rmnw/sub/p", status=201, error=None,
        recovered=True, short_uuid="ps",
    )
    out, get_user = await _provision(
        monkeypatch, pres, {"vlessUuid": PANEL_VLESS},
        existing_subscription={"telegram_id": 42, "uuid": legacy},
    )
    assert out["uuid"] == legacy
    get_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreachable_panel_still_returns_non_empty_uuid(monkeypatch):
    """Панель молчит — покупку ронять нельзя.

    grant_access считает пустой uuid отказом провижининга и уводит оплаченную
    покупку в retry, а на subscriptions.uuid висит частичный UNIQUE-индекс,
    поэтому пустая строка схлопнулась бы на второй же записи.
    """
    from app.services import remnawave_premium

    pres = remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid="panel-uuid", forced_uuid_accepted=False,
        subscription_url="https://rmnw/sub/p", status=201, error=None,
        recovered=False, short_uuid="ps",
    )
    out, _ = await _provision(monkeypatch, pres, None)
    assert out["uuid"]
    assert len(out["uuid"]) == 36
