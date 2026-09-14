"""P1-2: double tap on «Оплатить с баланса» with a Redis FSM debited twice.

The FSM guard (read state → write state) is two network round trips with
RedisStorage, so two parallel updates both pass it. finalize_balance_purchase
serialised them on the per-user advisory lock but did not deduplicate: the
second call debited the balance again. Now, while holding that lock, a second
IDENTICAL purchase (same user, tariff+period, amount) committed within
BALANCE_PURCHASE_DEDUP_WINDOW_S is rejected with DuplicateBalancePurchase —
nothing is debited. Legacy path (flag off) and outbox path (flag on).

Fake asyncpg: per-connection transactions with rollback, and
pg_advisory_xact_lock as a real per-user lock held until the transaction ends.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database.admin as db_admin
import database.subscriptions as db_subs
import database.users as db_users
from app.services import provisioning

TG = 222_001
PRICE_KOP = config.TARIFFS["basic"][30]["price"] * 100
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"


class State:
    def __init__(self):
        self.balance = 100_000
        self.payments: list = []
        self.lock = asyncio.Lock()


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, *_exc):
        # Roll back only THIS transaction's writes: every write happens after the
        # lock, so the state at lock time is what this transaction started from.
        if exc_type is not None and self.conn.snap is not None:
            self.conn.s.balance, self.conn.s.payments = self.conn.snap
        self.conn.snap = None
        if self.conn.holds_lock:            # xact lock ends with the transaction
            self.conn.holds_lock = False
            self.conn.s.lock.release()
        return False


class Conn:
    def __init__(self, s: State):
        self.s = s
        self.holds_lock = False
        self.snap = None

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock" in sql:
            await self.s.lock.acquire()
            self.holds_lock = True
            self.snap = copy.deepcopy((self.s.balance, self.s.payments))
            return "SELECT 1"
        if sql.strip().startswith("UPDATE users SET balance"):
            self.s.balance = args[0]
            return "UPDATE 1"
        return "OK"

    async def fetchrow(self, sql, *args):
        await asyncio.sleep(0)              # let the other tap interleave
        if "FROM subscriptions" in sql:
            exp = datetime.now(timezone.utc) + timedelta(days=5)
            return {"telegram_id": TG, "status": "active", "uuid": "u-1",
                    "expires_at": exp.replace(tzinfo=None)}
        if "FROM payments" in sql:          # the duplicate guard: tg, tariff, amount, window s
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=float(args[3]))
            for p in reversed(self.s.payments):
                if (p["telegram_id"], p["tariff"], p["amount"]) == args[:3] and p["created_at"] > cutoff:
                    return {"id": p["id"]}
            return None
        if "FROM users" in sql:
            return {"balance": self.s.balance}
        return None

    async def fetchval(self, sql, *args):
        if "INSERT INTO payments" in sql:
            pid = len(self.s.payments) + 1
            self.s.payments.append({"id": pid, "telegram_id": args[0], "tariff": args[1],
                                    "amount": args[2], "created_at": datetime.now(timezone.utc)})
            return pid
        if "SELECT balance" in sql:
            return self.s.balance
        return None


class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class Pool:
    def __init__(self, s):
        self.s = s

    def acquire(self):
        return _Acq(Conn(self.s))


@pytest.fixture(params=["off", "on"])
def world(request, monkeypatch):
    s = State()
    monkeypatch.setattr(db_admin, "get_pool", AsyncMock(return_value=Pool(s)))

    async def grant(**kw):
        await asyncio.sleep(0.01)
        return {"subscription_end": datetime.now(timezone.utc) + timedelta(days=35),
                "action": "renewal", "vless_url": None, "subscription_type": "basic",
                "renewal_xray_sync_after_commit": None}
    monkeypatch.setattr(db_subs, "grant_access", grant)
    monkeypatch.setattr(db_users, "process_referral_reward",
                        AsyncMock(return_value={"success": False, "reason": "no_referrer"}))
    from app.services.payments import verify_delivery
    monkeypatch.setattr(verify_delivery, "schedule_legacy_check", MagicMock())
    monkeypatch.setenv(MODE_VAR, "off")
    monkeypatch.delenv(EP_VAR, raising=False)
    if request.param == "on":
        monkeypatch.setenv(MODE_VAR, "on")
        monkeypatch.setenv(EP_VAR, "balance")
        monkeypatch.setattr(provisioning, "enqueue", AsyncMock(return_value=1))
        monkeypatch.setattr(provisioning, "run_now", AsyncMock(return_value=True))
    return s


def _buy():
    return db_admin.finalize_balance_purchase(
        telegram_id=TG, tariff_type="basic", period_days=30, amount_rubles=PRICE_KOP / 100,
    )


async def test_double_tap_debits_once(world):
    results = await asyncio.gather(_buy(), _buy(), return_exceptions=True)

    ok = [r for r in results if isinstance(r, dict) and r.get("success")]
    dup_cls = getattr(db_admin, "DuplicateBalancePurchase", ())
    dup = [r for r in results if isinstance(r, dup_cls)]
    assert len(ok) == 1 and len(dup) == 1, results
    assert world.balance == 100_000 - PRICE_KOP, "balance debited twice"
    assert len(world.payments) == 1


async def test_same_purchase_after_the_window_is_a_new_purchase(world):
    await _buy()
    world.payments[-1]["created_at"] -= timedelta(seconds=db_admin.BALANCE_PURCHASE_DEDUP_WINDOW_S + 1)

    second = await _buy()

    assert second["success"] is True
    assert world.balance == 100_000 - 2 * PRICE_KOP
    assert len(world.payments) == 2
