"""N7 (docs/audit/06_bug_hunt.md §3): the −15 % offer for 3 days when a PAID
subscription ends was almost never created.

Design (set_special_offer docstring, check_and_disable_expired_subscription,
main-menu button "Продлить со скидкой 15 % | ⏳", calculate_final_price — the
largest single discount wins, VIP removed 2026-09-14): a paid subscription (source 'payment',
or 'auto_renew' — renewed from balance) ends → users.special_offer_created_at =
now → 3 days of −15 % at checkout.

It was granted only by check_and_disable_expired_subscription, on the full-expiry
branch — but the fast expiry worker (every 60 s) expires the rows first and never
granted it, and users with a bypass entity (most of them) take the bypass-only
branch, which returned before the offer. Now both paths grant it in their expiry
transaction, once per ended period (skipped when an offer was already created at
or after that period's end).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
import database.admin as db_admin
import database.core as db_core
import database.subscriptions as db_subs

TG = 6161
NOW = datetime.now(timezone.utc)
ENDED = (NOW - timedelta(minutes=2)).replace(tzinfo=None)      # naive, as read from the DB


def _norm(sql: str) -> str:
    return " ".join(sql.split()).lower()


class Store:
    def __init__(self, *, source="payment", bypass=True):
        self.row = {"id": 1, "telegram_id": TG, "uuid": "u-1", "vpn_key": "k", "expires_at": ENDED,
                    "status": "active", "source": source, "remnawave_uuid": "rw-1" if bypass else None}
        self.offer_created_at = None
        self.offer_writes = 0


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Conn:
    """The expiry queries of fast_expiry_cleanup / check_and_disable_expired_subscription
    and the special-offer column of users, against one subscription row."""

    def __init__(self, store):
        self.s = store

    def transaction(self):
        return _Tx()

    async def fetch(self, sql, *args):
        r = self.s.row
        if r["status"] == "active" and r["uuid"] is not None:
            return [dict(r)]
        return []

    async def fetchrow(self, sql, *args):
        q = _norm(sql)
        r = self.s.row
        if "special_offer_created_at from users" in q:
            return {"special_offer_created_at": self.s.offer_created_at}
        if q.startswith("select * from subscriptions") or q.startswith("select 1 from subscriptions"):
            return dict(r) if r["status"] == "active" and r["uuid"] is not None else None
        if q.startswith("select uuid, expires_at, status from subscriptions"):
            return {k: r[k] for k in ("uuid", "expires_at", "status")} if r["uuid"] else None
        return None

    async def fetchval(self, sql, *args):
        if "remnawave_uuid" in _norm(sql):
            return self.s.row["remnawave_uuid"]
        return None

    async def execute(self, sql, *args):
        q = _norm(sql)
        r = self.s.row
        if q.startswith("update users set special_offer_created_at"):
            created, _tg, period_end = args[0], args[1], args[2]
            if self.s.offer_created_at is None or self.s.offer_created_at < period_end:
                self.s.offer_created_at = created
                self.s.offer_writes += 1
                return "UPDATE 1"
            return "UPDATE 0"
        if q.startswith("update subscriptions") and r["status"] == "active" and r["uuid"]:
            if "is_bypass_only = true" in q:
                r.update(uuid=None, source="bypass_only", is_bypass_only=True)
            else:
                r.update(status="expired", uuid=None)
            return "UPDATE 1"
        return "UPDATE 0"


class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acq(self.conn)


@pytest.fixture
def world(monkeypatch):
    def _make(**kw):
        store = Store(**kw)
        pool = Pool(Conn(store))
        monkeypatch.setattr(db_core, "DB_READY", True)
        monkeypatch.setattr(database, "DB_READY", True, raising=False)
        monkeypatch.setattr(database, "get_pool", AsyncMock(return_value=pool))
        monkeypatch.setattr(db_subs, "get_pool", AsyncMock(return_value=pool))
        from app.services import remnawave_premium, remnawave_service
        monkeypatch.setattr(remnawave_service, "extend_remnawave_for_bypass_bg", lambda *_a, **_k: None)
        monkeypatch.setattr(remnawave_service, "disable_remnawave_user_bg", lambda *_a, **_k: None)
        monkeypatch.setattr(remnawave_premium, "disable_premium_user", AsyncMock())
        monkeypatch.setattr(db_subs, "_log_vpn_lifecycle_audit_async", AsyncMock())
        return store
    return _make


def _patch_fast_expiry(monkeypatch, store):
    import fast_expiry_cleanup as fec
    from app.core import feature_flags

    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *a, **k):
        if delay == fec.CLEANUP_INTERVAL_SECONDS:
            raise asyncio.CancelledError          # stop after one iteration
        await real_sleep(0)

    monkeypatch.setattr(fec.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(fec.random, "uniform", lambda *_a: 1.0)
    monkeypatch.setattr(feature_flags, "get_feature_flags",
                        lambda: SimpleNamespace(background_workers_enabled=True))
    monkeypatch.setattr(fec, "acquire_connection", lambda _pool, _name: _Acq(Conn(store)))
    monkeypatch.setattr(database, "get_active_paid_subscription", AsyncMock(return_value=None))
    monkeypatch.setattr(database, "_log_vpn_lifecycle_audit_async", AsyncMock(), raising=False)
    monkeypatch.setattr(database, "_log_audit_event_atomic", AsyncMock(), raising=False)
    monkeypatch.setattr(fec, "safe_send_message", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(fec, "resolve_user_language", AsyncMock(return_value="ru"))
    return fec


async def _one_fast_expiry_pass(fec):
    with pytest.raises(asyncio.CancelledError):
        await fec.fast_expiry_cleanup_task(bot=MagicMock())


@pytest.mark.parametrize("bypass", [True, False], ids=["to_bypass_only", "fully_expired"])
@pytest.mark.parametrize("source", ["payment", "auto_renew", "gift"])   # #19: a gift was paid for
async def test_fast_expiry_grants_the_offer_once_for_a_paid_subscription(world, monkeypatch, bypass, source):
    store = world(source=source, bypass=bypass)
    fec = _patch_fast_expiry(monkeypatch, store)

    await _one_fast_expiry_pass(fec)

    assert store.offer_writes == 1, "an ended paid subscription must get the −15 % offer"
    assert store.offer_created_at is not None and store.offer_created_at >= ENDED

    # the same ended period seen again (the other expiry path, a rerun) → no second offer
    store.row.update(status="active", uuid="u-1", source=source)
    await _one_fast_expiry_pass(fec)
    assert store.offer_writes == 1


@pytest.mark.parametrize("source", ["trial", "admin", "game_dice"])
async def test_fast_expiry_grants_no_offer_for_an_unpaid_subscription(world, monkeypatch, source):
    store = world(source=source, bypass=True)
    fec = _patch_fast_expiry(monkeypatch, store)

    await _one_fast_expiry_pass(fec)

    assert store.offer_writes == 0


async def test_check_and_disable_grants_the_offer_on_the_bypass_only_branch(world):
    """Users with a bypass entity (most of them) took the bypass-only branch, which
    returned before the offer was created."""
    store = world(source="payment", bypass=True)

    assert await database.check_and_disable_expired_subscription(TG) is True

    assert store.row["source"] == "bypass_only"
    assert store.offer_writes == 1


async def test_a_new_period_that_ends_gets_a_new_offer(world, monkeypatch):
    store = world(source="payment", bypass=True)
    store.offer_created_at = ENDED - timedelta(days=40)       # offer of the previous period
    fec = _patch_fast_expiry(monkeypatch, store)

    await _one_fast_expiry_pass(fec)

    assert store.offer_writes == 1 and store.offer_created_at >= ENDED


async def test_the_offer_is_applied_at_checkout(world, monkeypatch):
    store = world(source="payment", bypass=True)
    fec = _patch_fast_expiry(monkeypatch, store)
    monkeypatch.setattr(db_admin, "get_user_discount", AsyncMock(return_value=None))
    import config

    await _one_fast_expiry_pass(fec)
    price = await database.calculate_final_price(TG, "basic", 30)

    base = round(config.TARIFFS["basic"][30]["price"] * 100)
    assert price["discount_type"] == "special_offer" and price["discount_percent"] == 15
    assert price["final_price_kopecks"] == base - int(base * 15 / 100)


@pytest.mark.parametrize("is_bypass_only,expected", [(True, 1), (False, 0)],
                         ids=["premium_ended_bypass_only", "premium_active"])
async def test_main_menu_shows_the_offer_after_the_premium_ended(monkeypatch, is_bypass_only, expected):
    """A paid premium that ended leaves the row active + is_bypass_only: the menu took
    the 'active subscription' branch and never showed «Продлить со скидкой 15 %»."""
    import config
    from app.handlers.common import keyboards
    monkeypatch.setattr(database, "DB_READY", True, raising=False)
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
    monkeypatch.setattr(database, "get_subscription", AsyncMock(
        return_value={"status": "active", "is_bypass_only": is_bypass_only}))
    monkeypatch.setattr(database, "get_remnawave_uuid", AsyncMock(return_value="rw-1"))
    monkeypatch.setattr(database, "get_special_offer_info", AsyncMock(
        return_value={"remaining_text": "2д 5ч", "discount_percent": 15}))

    kb = await keyboards.get_main_menu_keyboard("ru", TG)

    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert callbacks.count("special_offer_buy") == expected
