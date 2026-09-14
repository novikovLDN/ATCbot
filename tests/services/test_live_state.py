"""live_state: what the panel says about a user's access right now, bounded by a
short timeout, reconciled with the DB, cached per user and DB state."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import database
from app.services import remnawave_api
from app.services.subscriptions import live_state as ls

GB = 1024 ** 3
NOW = datetime.now(timezone.utc)
UNTIL = NOW + timedelta(days=20)


def _naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _bypass_ent(limit, used, status="ACTIVE"):
    return {"id": 1, "username": "1", "trafficLimitBytes": limit, "status": status,
            "userTraffic": {"usedTrafficBytes": used}}


def _premium_ent(until, status="ACTIVE"):
    return {"id": 2, "username": "tg_1_premium", "expireAt": until.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "status": status, "trafficLimitBytes": 0}


@pytest.fixture(autouse=True)
def _clean():
    ls.reset_state()
    yield
    ls.reset_state()


def _panel(monkeypatch, *, premium=("present", None), bypass=("present", None), delay=0.0):
    calls = {"premium": 0, "bypass": 0}

    async def get_premium_state(tg):
        calls["premium"] += 1
        await asyncio.sleep(delay)
        return premium

    async def get_bypass_state(tg):
        calls["bypass"] += 1
        await asyncio.sleep(delay)
        return bypass

    monkeypatch.setattr(remnawave_api, "get_premium_state", get_premium_state)
    monkeypatch.setattr(remnawave_api, "get_bypass_state", get_bypass_state)
    return calls


def _sub(**kw):
    row = {"telegram_id": 1, "status": "active", "source": "payment", "is_bypass_only": False,
           "activation_status": "active", "expires_at": UNTIL, "subscription_type": "basic",
           "uuid": "u-1", "remnawave_uuid": "rw-1"}
    row.update(kw)
    return row


# ── bypass ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("state,ent,works,remaining", [
    ("present", _bypass_ent(10 * GB, 3 * GB), True, 7 * GB),
    ("present", _bypass_ent(10 * GB, 11 * GB), False, 0),
    ("present", _bypass_ent(0, 50 * GB), True, 0),                 # unlimited
    ("present", _bypass_ent(10 * GB, 0, status="DISABLED"), False, 0),
    ("absent", None, False, 0),
    ("unavailable", None, None, 0),
])
async def test_read_bypass(monkeypatch, state, ent, works, remaining):
    _panel(monkeypatch, bypass=(state, ent))
    b = await ls.read_bypass(1)
    assert (b.works, b.remaining) == (works, remaining)


async def test_a_slow_panel_is_unavailable_not_a_guess(monkeypatch):
    _panel(monkeypatch, bypass=("present", _bypass_ent(10 * GB, 0)), delay=5)
    b = await ls.read_bypass(1, timeout=0.05)
    assert b.state == "unavailable" and b.works is None


async def test_a_panel_error_is_unavailable(monkeypatch):
    monkeypatch.setattr(remnawave_api, "get_bypass_state", AsyncMock(side_effect=RuntimeError("boom")))
    assert (await ls.read_bypass(1)).state == "unavailable"


@pytest.mark.parametrize("lang,b,text", [
    ("ru", int(7.5 * GB), "7.5 ГБ"), ("en", 12 * GB, "12 GB"), ("ru", 640 * 1024 ** 2, "640 МБ"),
    ("ru", 0, "0 ГБ"), ("en", 3 * GB, "3 GB"), ("ru", int(10.5 * GB), "10.5 ГБ"), ("ru", 150 * GB, "150 ГБ"),
])
def test_format_bytes(lang, b, text):
    assert ls.format_bytes(lang, b) == text


# ── reconcile ─────────────────────────────────────────────────────────


def test_panel_date_wins_and_is_flagged_as_a_mismatch():
    panel_until = UNTIL + timedelta(days=3)
    v = ls.reconcile(_sub(), ls.PremiumInfo("present", panel_until, "ACTIVE"), ls.BypassInfo("absent"))
    assert v.premium_active and v.premium_until == panel_until
    assert v.mismatch and "panel expireAt" in v.mismatch


def test_same_date_within_tolerance_is_no_mismatch():
    v = ls.reconcile(_sub(), ls.PremiumInfo("present", UNTIL + timedelta(minutes=2), "ACTIVE"),
                     ls.BypassInfo("absent"))
    assert v.premium_active and v.mismatch is None


def test_panel_disabled_premium_is_shown_as_inactive():
    v = ls.reconcile(_sub(), ls.PremiumInfo("present", UNTIL, "DISABLED"), ls.BypassInfo("absent"))
    assert not v.premium_active and v.mismatch


def test_panel_unavailable_keeps_the_db_date():
    v = ls.reconcile(_sub(), ls.UNAVAILABLE_PREMIUM, ls.UNAVAILABLE_BYPASS)
    assert v.premium_active and v.premium_until == UNTIL and v.panel_unavailable and v.mismatch is None


def test_pending_activation_is_pending_whatever_the_panel_says():
    v = ls.reconcile(_sub(activation_status="pending", uuid=None), ls.PremiumInfo("absent"),
                     ls.BypassInfo("absent"))
    assert v.pending and v.mismatch is None and not v.panel_unavailable


def test_bypass_only_row_is_not_premium():
    v = ls.reconcile(_sub(is_bypass_only=True, source="bypass_only", expires_at=NOW + timedelta(days=3650)),
                     ls.PremiumInfo("absent"), ls.BypassInfo("present", used=0, limit=5 * GB, status="ACTIVE"))
    assert not v.premium_active and v.is_bypass_only and v.mismatch is None


# ── get_view: cache + alert ───────────────────────────────────────────


async def test_view_is_cached_per_db_state_and_read_fresh_after_a_change(monkeypatch):
    sub = _sub()
    monkeypatch.setattr(database, "get_subscription_any", AsyncMock(side_effect=lambda tg: dict(sub)))
    calls = _panel(monkeypatch, premium=("present", _premium_ent(UNTIL)),
                   bypass=("present", _bypass_ent(10 * GB, GB)))
    v1 = await ls.get_view(1)
    v2 = await ls.get_view(1)
    assert v1 is v2 and calls == {"premium": 1, "bypass": 1}
    sub["expires_at"] = UNTIL + timedelta(days=30)             # a renewal
    await ls.get_view(1)
    assert calls == {"premium": 2, "bypass": 2}


async def test_view_expires_after_the_ttl(monkeypatch):
    monkeypatch.setattr(database, "get_subscription_any", AsyncMock(return_value=_sub()))
    calls = _panel(monkeypatch, premium=("present", _premium_ent(UNTIL)), bypass=("absent", None))
    t = [1000.0]
    monkeypatch.setattr(ls, "_clock", lambda: t[0])
    await ls.get_view(1)
    t[0] += ls.CACHE_TTL_S + 1
    await ls.get_view(1)
    assert calls["premium"] == 2


async def test_unavailable_panel_is_never_cached(monkeypatch):
    monkeypatch.setattr(database, "get_subscription_any", AsyncMock(return_value=_sub()))
    calls = _panel(monkeypatch, premium=("unavailable", None), bypass=("unavailable", None))
    await ls.get_view(1)
    await ls.get_view(1)
    assert calls["premium"] == 2


async def test_mismatch_alerts_the_admin_once(monkeypatch):
    monkeypatch.setattr(database, "get_subscription_any", AsyncMock(return_value=_sub()))
    _panel(monkeypatch, premium=("present", _premium_ent(UNTIL - timedelta(days=5))), bypass=("absent", None))
    from app.services import provisioning
    report = AsyncMock(return_value=True)
    monkeypatch.setattr(provisioning, "report_payment_alert", report)
    v = await ls.get_view(1)
    ls.invalidate(1)
    await ls.get_view(1)
    await asyncio.sleep(0)
    assert v.premium_until.date() == (UNTIL - timedelta(days=5)).date()
    report.assert_awaited_once()
    assert "DELIVERY_MISMATCH" in report.await_args.args[1]
