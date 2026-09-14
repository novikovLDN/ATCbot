"""Backfill contamination: the BYPASS cache columns point at the PREMIUM entity.

Production 2026-09-14: ~300 premium entities (tg_*_premium) had expireAt ~10
years ahead — free premium. subscriptions.remnawave_id / remnawave_uuid (the
BYPASS columns) held the premium entity's id / vlessUuid, and the legacy bypass
helpers in remnawave_service resolved "the bypass entity" through them:
  - extend_remnawave_for_bypass → PATCH premium expireAt=+10y, ACTIVE;
  - disable_remnawave_user     → premium (limit 0) DISABLED;
  - renew_remnawave_user       → PATCH premium trafficLimitBytes + expireAt=+10y.

Now: every legacy bypass write acts on the entity whose username == str(tg),
the cache is re-written to it, and remnawave_api refuses to send a premium
entity an expireAt more than 5 years ahead (REMNAWAVE_PREMIUM_FAR_EXPIRE_BLOCKED
+ one admin alert). The panel is the real HTTP client against FakeRemnawaveHTTP;
the subscriptions row lives in memory behind a fake pool.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
import database
from app.services import admin_alerts, purchase_flow, remnawave_api, remnawave_service
from tests.fakes.remnawave_http import FakeRemnawaveHTTP, parse_dt

GIB = 1024 ** 3
TG = 5061220608
FAR = timedelta(days=5 * 365)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Conn:
    """Answers exactly the three subscriptions SELECTs remnawave_api runs."""

    def __init__(self, row):
        self.row = row

    async def fetchrow(self, sql, *args):
        r, v = self.row, args[0]
        if "matched_id" in sql:
            if r["remnawave_uuid"] is not None and r["remnawave_uuid"] == v:
                return {"matched_id": r["remnawave_id"]}
            if r["remnawave_premium_uuid"] is not None and r["remnawave_premium_uuid"] == v:
                return {"matched_id": r["remnawave_premium_id"]}
            return None
        if "SELECT telegram_id FROM subscriptions" in sql:
            hit = v in (r["remnawave_uuid"], r["remnawave_premium_uuid"])
            return {"telegram_id": r["telegram_id"]} if hit else None
        if "remnawave_premium_id = $1" in sql:
            return {"?column?": 1} if r["remnawave_premium_id"] == int(v) else None
        raise AssertionError(f"unexpected SQL: {sql}")


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, row):
        self.conn = _Conn(row)

    def acquire(self):
        return _Acquire(self.conn)


@pytest.fixture
def w(monkeypatch):
    panel = FakeRemnawaveHTTP().install(monkeypatch)
    monkeypatch.setattr(config, "REMNAWAVE_SQUAD_UUID", "")
    now = datetime.now(timezone.utc)
    prem = panel.seed_premium(TG, now + timedelta(days=20), tag="BASIC")
    byp = panel.seed_bypass(TG, 10 * GIB, tag="BYPASS")
    # backfill contamination: BOTH bypass columns hold the premium entity
    row = {
        "telegram_id": TG,
        "remnawave_id": prem["id"], "remnawave_uuid": prem["vlessUuid"],
        "remnawave_premium_id": prem["id"], "remnawave_premium_uuid": prem["vlessUuid"],
    }
    pool = _Pool(row)

    async def get_id(tg):
        return row["remnawave_id"] if tg == TG else None

    async def get_uuid(tg):
        return row["remnawave_uuid"] if tg == TG else None

    async def set_id(tg, v):
        row["remnawave_id"] = int(v)

    async def set_uuid(tg, v):
        row["remnawave_uuid"] = v

    async def clear_uuid(tg):
        row["remnawave_uuid"] = None

    async def get_pool():
        return pool

    monkeypatch.setattr(database, "get_pool", get_pool)
    monkeypatch.setattr(database, "get_remnawave_id", get_id)
    monkeypatch.setattr(database, "get_remnawave_uuid", get_uuid)
    monkeypatch.setattr(database, "set_remnawave_id", set_id)
    monkeypatch.setattr(database, "set_remnawave_uuid", set_uuid)
    monkeypatch.setattr(database, "clear_remnawave_uuid", clear_uuid)
    monkeypatch.setattr(database, "reset_traffic_notification_flags", AsyncMock())
    monkeypatch.setattr(database, "get_remnawave_premium_id",
                        AsyncMock(side_effect=lambda tg: row["remnawave_premium_id"]))
    send_alert = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", send_alert)
    monkeypatch.setattr(purchase_flow, "_alert_bot", lambda: object())
    return SimpleNamespace(panel=panel, row=row, prem=prem, byp=byp, send_alert=send_alert)


def _patches_to(w, ent_id):
    return [b for (m, p, b) in w.panel.requests
            if m == "PATCH" and p == "/api/users" and isinstance(b, dict) and b.get("id") == ent_id]


def _premium_untouched(w, expire_before, status_before="ACTIVE"):
    assert _patches_to(w, w.prem["id"]) == [], "a bypass helper PATCHed the premium entity"
    assert w.prem["expireAt"] == expire_before
    assert w.prem["status"] == status_before


def _cache_healed(w):
    assert w.row["remnawave_id"] == w.byp["id"]
    assert w.row["remnawave_uuid"] == w.byp["vlessUuid"]


async def _drain_alerts():
    for _ in range(3):
        tasks = [t for t in list(remnawave_api._bg_tasks) if not t.done()]
        if not tasks:
            break
        await asyncio.gather(*tasks)


# ── legacy bypass helpers on a contaminated row ─────────────────────────

async def test_extend_on_expired_premium_patches_bypass_not_premium(w):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    w.prem["expireAt"], w.prem["status"] = past, "EXPIRED"
    w.byp["status"] = "DISABLED"          # the bypass really needs the extend

    await remnawave_service.extend_remnawave_for_bypass(TG)

    _premium_untouched(w, past, "EXPIRED")
    assert w.byp["status"] == "ACTIVE"
    assert w.byp["expireAt"] > datetime.now(timezone.utc) + FAR
    _cache_healed(w)


async def test_extend_healthy_bypass_no_patch_but_cache_healed(w):
    before = w.prem["expireAt"]
    await remnawave_service.extend_remnawave_for_bypass(TG)
    _premium_untouched(w, before)
    assert _patches_to(w, w.byp["id"]) == []
    _cache_healed(w)


async def test_disable_keeps_bypass_active_and_never_disables_premium(w):
    before = w.prem["expireAt"]
    w.byp["status"] = "DISABLED"
    await remnawave_service.disable_remnawave_user(TG)
    _premium_untouched(w, before)
    assert w.byp["status"] == "ACTIVE"
    _cache_healed(w)


async def test_disable_exhausted_bypass_disables_bypass_only(w):
    before = w.prem["expireAt"]
    w.byp["userTraffic"]["usedTrafficBytes"] = w.byp["trafficLimitBytes"]
    await remnawave_service.disable_remnawave_user(TG)
    _premium_untouched(w, before)
    assert w.byp["status"] == "DISABLED"
    _cache_healed(w)


async def test_renew_tops_up_bypass_not_premium(w, monkeypatch):
    monkeypatch.setattr(remnawave_service, "_traffic_limit_for_tariff", lambda t, p=30: 5 * GIB)
    alert = AsyncMock()
    monkeypatch.setattr(remnawave_service, "_alert_bypass_not_delivered", alert)
    before = w.prem["expireAt"]

    await remnawave_service.renew_remnawave_user(TG, "basic", datetime.now(timezone.utc) + timedelta(days=30))

    _premium_untouched(w, before)
    assert w.byp["trafficLimitBytes"] == 15 * GIB
    alert.assert_not_awaited()
    _cache_healed(w)


async def test_add_traffic_tops_up_bypass_not_premium(w):
    before = w.prem["expireAt"]
    assert await remnawave_service.add_traffic(TG, 5 * GIB) is True
    _premium_untouched(w, before)
    assert w.byp["trafficLimitBytes"] == 15 * GIB
    _cache_healed(w)


async def test_ensure_squad_targets_bypass(w, monkeypatch):
    monkeypatch.setattr(config, "REMNAWAVE_SQUAD_UUID", "sq-clients")
    w.prem["activeInternalSquads"] = []
    await remnawave_service.ensure_squad(TG)
    assert w.prem["activeInternalSquads"] == []
    assert [s["uuid"] for s in w.byp["activeInternalSquads"]] == ["sq-clients"]


@pytest.mark.parametrize("op", ["extend", "disable"])
async def test_no_bypass_entity_panel_untouched(w, op, caplog):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    w.prem["expireAt"], w.prem["status"] = past, "EXPIRED"
    del w.panel.users[w.byp["id"]]
    caplog.set_level(logging.WARNING)

    fn = {"extend": remnawave_service.extend_remnawave_for_bypass,
          "disable": remnawave_service.disable_remnawave_user}[op]
    await fn(TG)

    assert [r for r in w.panel.requests if r[0] == "PATCH"] == []
    assert w.prem["expireAt"] == past and w.prem["status"] == "EXPIRED"
    assert "REMNAWAVE_BYPASS_NOT_RESOLVED" in caplog.text


# ── new-core bypass reader self-heals the cache ─────────────────────────

async def test_bypass_state_cache_mismatch_self_heals(w):
    kind, ent = await remnawave_api.get_bypass_state(TG)
    assert kind == "present" and ent["id"] == w.byp["id"]
    _cache_healed(w)


async def test_bypass_state_correct_cache_writes_nothing(w, monkeypatch):
    w.row["remnawave_id"], w.row["remnawave_uuid"] = w.byp["id"], w.byp["vlessUuid"]
    set_id, set_uuid = AsyncMock(), AsyncMock()
    monkeypatch.setattr(database, "set_remnawave_id", set_id)
    monkeypatch.setattr(database, "set_remnawave_uuid", set_uuid)
    kind, _ = await remnawave_api.get_bypass_state(TG)
    assert kind == "present"
    set_id.assert_not_awaited()
    set_uuid.assert_not_awaited()


async def test_bypass_state_panel_down_writes_nothing(w):
    w.panel.down = True
    kind, _ = await remnawave_api.get_bypass_state(TG)
    assert kind == "unavailable"
    assert w.row["remnawave_id"] == w.prem["id"]


# ── remnawave_api guard: premium never gets expireAt > now + 5y ─────────

async def test_guard_blocks_premium_far_expire_and_alerts_once(w, caplog):
    before = w.prem["expireAt"]
    caplog.set_level(logging.WARNING)
    far = _iso(datetime.now(timezone.utc) + timedelta(days=3650))

    out = await remnawave_api.update_user(w.prem["id"], expireAt=far, status="ACTIVE")
    await _drain_alerts()

    assert out is None
    _premium_untouched(w, before)
    assert "REMNAWAVE_PREMIUM_FAR_EXPIRE_BLOCKED" in caplog.text
    w.send_alert.assert_awaited_once()
    args, kwargs = w.send_alert.await_args
    assert args[1] == "vpn_api" and not kwargs.get("force")


async def test_guard_blocks_uncached_premium_by_username(w):
    w.row["remnawave_premium_id"] = None       # _is_premium_entity cannot know it
    w.row["remnawave_id"] = w.byp["id"]
    before = w.prem["expireAt"]
    far = _iso(datetime.now(timezone.utc) + timedelta(days=3650))
    assert await remnawave_api.update_user(w.prem["id"], expireAt=far) is None
    await _drain_alerts()
    _premium_untouched(w, before)


async def test_guard_blocks_premium_when_panel_read_fails(w):
    before = w.prem["expireAt"]
    w.panel.fail("GET", lambda r, b: r.url.path == f"/api/users/{w.prem['id']}", status=500)
    far = _iso(datetime.now(timezone.utc) + timedelta(days=3650))
    assert await remnawave_api.update_user(w.prem["id"], expireAt=far) is None
    await _drain_alerts()
    _premium_untouched(w, before)


async def test_guard_allows_premium_normal_expire(w):
    target = datetime.now(timezone.utc) + timedelta(days=400)
    assert await remnawave_api.update_user(w.prem["id"], expireAt=_iso(target)) is not None
    assert abs((w.prem["expireAt"] - target).total_seconds()) < 1
    w.send_alert.assert_not_awaited()


async def test_guard_allows_bypass_far_expire_even_if_in_a_premium_column(w):
    # cross-contamination: the bypass id sits in some remnawave_premium_id column
    w.row["remnawave_premium_id"] = w.byp["id"]
    far = datetime.now(timezone.utc) + timedelta(days=3650)
    assert await remnawave_api.update_user(w.byp["id"], expireAt=_iso(far), status="ACTIVE") is not None
    assert w.byp["expireAt"] > datetime.now(timezone.utc) + FAR
    w.send_alert.assert_not_awaited()


async def test_guard_blocks_premium_create_far_expire(w):
    del w.panel.users[w.prem["id"]]
    far = _iso(datetime.now(timezone.utc) + timedelta(days=3650))
    kw = dict(username=f"tg_{TG}_premium", short_uuid="s1", traffic_limit_bytes=0,
              expire_at=far, telegram_id=TG)
    assert await remnawave_api.create_user(**kw) is None
    raw = await remnawave_api.create_user(**kw, raw_response=True)
    await _drain_alerts()
    assert raw["ok"] is False
    assert w.panel.premium(TG) is None
    assert [r for r in w.panel.requests if r[0] == "POST" and r[1] == "/api/users"] == []


async def test_guard_allows_bypass_create_2099(w):
    tg2 = 777000111
    res = await remnawave_api.create_user(
        username=str(tg2), short_uuid="s2", traffic_limit_bytes=GIB,
        expire_at="2099-12-31T23:59:59Z", telegram_id=tg2,
    )
    assert res is not None
    assert w.panel.bypass(tg2)["expireAt"] == parse_dt("2099-12-31T23:59:59Z")
    w.send_alert.assert_not_awaited()
