"""N1 (docs/audit/06_bug_hunt.md §3): legacy auto-renewal — the DB outcome and the
user message / panel sync must always agree.

The fake connection models the Postgres rules that made the bug:
  * an SQL error aborts the transaction; every later statement fails with
    InFailedSQLTransactionError until ROLLBACK / ROLLBACK TO SAVEPOINT;
  * RELEASE SAVEPOINT of an aborted savepoint fails;
  * COMMIT of an aborted transaction is a SILENT rollback (no error).

Before the fix, decrease_balance(conn=…) swallowed a deadlock and returned False,
the legacy code did `continue` inside its savepoint, RELEASE failed, the rest of the
batch failed, COMMIT silently rolled everything back — and Phase B still sent
"продлено" and synced the panel for users whose debit + renewal no longer existed.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

import auto_renewal
import database
import database.core as db_core
from app.services import admin_alerts, purchase_flow, remnawave_service
from app.services.payments import verify_delivery

A, B, C = 222_001, 222_002, 222_003
START_BALANCE = 100_000
PRICE = 199 * 100


class _PgTx:
    def __init__(self, conn):
        self.conn = conn
        self.nested = False

    async def __aenter__(self):
        self.conn._guard()
        self.nested = bool(self.conn.stack)
        self.conn.stack.append(copy.deepcopy(self.conn.state))
        return self

    async def __aexit__(self, exc_type, *_exc):
        snap = self.conn.stack.pop()
        if exc_type is not None:            # ROLLBACK / ROLLBACK TO SAVEPOINT
            self.conn.state = snap
            self.conn.aborted = False
            return False
        if self.nested:                     # RELEASE SAVEPOINT
            self.conn._guard()
            return False
        if self.conn.aborted:               # COMMIT of an aborted tx = silent ROLLBACK
            self.conn.state = snap
            self.conn.aborted = False
            self.conn.silent_rollbacks += 1
        return False


class PgConn:
    def __init__(self, users):
        self.state = {
            "balances": {tg: START_BALANCE for tg in users},
            "markers": {tg: None for tg in users},
            "payments": [],
            "renewed": [],
        }
        self.users = list(users)
        self.stack: list = []
        self.aborted = False
        self.silent_rollbacks = 0
        self.fail: dict = {}

    def transaction(self):
        return _PgTx(self)

    def _guard(self):
        if self.aborted:
            raise asyncpg.exceptions.InFailedSQLTransactionError(
                "current transaction is aborted, commands ignored until end of transaction block")

    def _maybe_fail(self, key):
        exc = self.fail.pop(key, None)
        if exc is not None:
            self.aborted = True
            raise exc

    def _row(self, tg):
        exp = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
        return {"telegram_id": tg, "language": "ru", "balance": self.state["balances"][tg],
                "status": "active", "auto_renew": True, "uuid": f"uuid-{tg}", "expires_at": exp,
                "subscription_type": "basic", "is_combo": False,
                "last_auto_renewal_at": self.state["markers"][tg]}

    async def fetch(self, sql, *args):
        self._guard()
        return [self._row(tg) for tg in self.users if self.state["markers"][tg] is None]

    async def execute(self, sql, *args):
        self._guard()
        if "SET last_auto_renewal_at" in sql:
            tg = args[1]
            self._maybe_fail(("claim", tg))
            if self.state["markers"][tg] is not None:
                return "UPDATE 0"
            self.state["markers"][tg] = args[0]
            return "UPDATE 1"
        if "UPDATE users SET balance" in sql:
            self.state["balances"][args[1]] = args[0]
            return "UPDATE 1"
        return "SELECT 1"

    async def fetchrow(self, sql, *args):
        self._guard()
        if "auto_renew, expires_at" in sql:
            return {"auto_renew": True, "expires_at": None, "last_auto_renewal_at": None}
        if "SELECT balance FROM users" in sql:
            tg = args[0]
            self._maybe_fail(("debit", tg))
            return {"balance": self.state["balances"][tg]}
        if "SELECT vpn_key" in sql:
            return {"vpn_key": "https://panel.test/sub/k"}
        return None

    async def fetchval(self, sql, *args):
        self._guard()
        if "INSERT INTO payments" in sql:
            self.state["payments"].append(args[0])
            return len(self.state["payments"])
        return None


class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


def _deadlock():
    return asyncpg.exceptions.DeadlockDetectedError("deadlock detected")


@pytest.fixture
def world(monkeypatch):
    conn = PgConn([A, B, C])

    async def grant(*, telegram_id, conn, **_k):
        conn.state["renewed"].append(telegram_id)
        return {"subscription_end": datetime.now(timezone.utc) + timedelta(days=30),
                "action": "renewal", "vless_url": None,
                "renewal_xray_sync_after_commit": {"telegram_id": telegram_id}}

    w = MagicMock()
    w.conn = conn
    w.sent = AsyncMock(return_value=MagicMock())
    w.sync = AsyncMock()
    w.renew_bg = MagicMock()
    w.alerts = AsyncMock(return_value=True)

    monkeypatch.setattr(db_core, "DB_READY", True)
    monkeypatch.setattr(database, "get_pool", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(database, "get_last_subscription_payment",
                        AsyncMock(return_value={"tariff": "basic_30"}), raising=False)
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(database, "increase_balance", AsyncMock(return_value=True), raising=False)
    monkeypatch.setattr(database, "grant_access", AsyncMock(side_effect=grant), raising=False)
    # the REAL decrease_balance runs on the fake connection (it swallows SQL errors)
    monkeypatch.setattr(auto_renewal.provisioning_flags, "is_on", lambda _ep: False)
    monkeypatch.setattr(auto_renewal, "acquire_connection", lambda _pool, _name: _Acq(conn))
    monkeypatch.setattr(auto_renewal, "safe_send_message", w.sent)
    monkeypatch.setattr(auto_renewal, "_failure_notice_sent_at", {})   # per-test 24 h notice cooldown
    monkeypatch.setattr(auto_renewal, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(auto_renewal.notification_service, "check_notification_idempotency",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(auto_renewal.notification_service, "mark_notification_sent",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(admin_alerts, "send_alert", w.alerts)
    monkeypatch.setattr(purchase_flow, "sync_renewal_to_remnawave", w.sync)
    monkeypatch.setattr(remnawave_service, "renew_remnawave_user_bg", w.renew_bg)
    monkeypatch.setattr(verify_delivery, "schedule_legacy_check", MagicMock())
    return w


def _failure_prefix():
    from app.i18n import get_text
    return get_text("ru", "autorenew.failed_debit", amount=0.0).split("\n", 1)[0]


def _messaged(w):
    """Users who got a «продлено» message (the failure notice is not one)."""
    prefix = _failure_prefix()
    return sorted(c.args[1] for c in w.sent.await_args_list if not str(c.args[2]).startswith(prefix))


def _failure_noticed(w):
    """Users told «auto-renewal did not go through» (08_payments_ux #14)."""
    prefix = _failure_prefix()
    return sorted(c.args[1] for c in w.sent.await_args_list if str(c.args[2]).startswith(prefix))


def _synced(w):
    return sorted(c.args[0]["telegram_id"] for c in w.sync.await_args_list)


def _panel_renewed(w):
    return sorted(c.args[0] for c in w.renew_bg.call_args_list)


def _forced(w):
    return [c.args[2] for c in w.alerts.await_args_list if c.kwargs.get("force")]


async def test_deadlock_swallowed_by_debit_skips_only_that_user(world):
    """decrease_balance(conn) turns B's deadlock into False: B's savepoint must roll
    back cleanly, A and C stay renewed, billed and notified; B gets nothing."""
    w = world
    w.conn.fail[("debit", B)] = _deadlock()

    await auto_renewal.process_auto_renewals(MagicMock())

    st = w.conn.state
    assert w.conn.silent_rollbacks == 0, "COMMIT of an aborted batch silently rolled everything back"
    assert sorted(st["renewed"]) == [A, C]
    assert st["balances"] == {A: START_BALANCE - PRICE, B: START_BALANCE, C: START_BALANCE - PRICE}
    assert sorted(st["payments"]) == [A, C]
    assert _messaged(w) == [A, C], "a 'продлено' message must match a committed renewal"
    assert _failure_noticed(w) == [B], "B's refused debit is told to B (was silent)"
    assert _synced(w) == [A, C] and _panel_renewed(w) == [A, C]


async def test_batch_level_failure_sends_nothing_alerts_and_next_run_renews_once(world):
    """A deadlock outside any savepoint (B's claim UPDATE) aborts the whole batch
    transaction: nothing is committed, so nobody may get "продлено" or a panel
    PATCH; the admin gets a forced alert; the next run renews everyone once."""
    w = world
    w.conn.fail[("claim", B)] = _deadlock()

    with pytest.raises(asyncpg.PostgresError):
        await auto_renewal.process_auto_renewals(MagicMock())

    st = w.conn.state
    assert st["renewed"] == [] and st["payments"] == []
    assert st["balances"] == {A: START_BALANCE, B: START_BALANCE, C: START_BALANCE}
    assert st["markers"] == {A: None, B: None, C: None}, "claims must roll back so the next run retries"
    assert _messaged(w) == [] and _synced(w) == [] and _panel_renewed(w) == []
    assert any("batch rolled back" in t for t in _forced(w)), _forced(w)

    await auto_renewal.process_auto_renewals(MagicMock())
    await auto_renewal.process_auto_renewals(MagicMock())

    st = w.conn.state
    assert sorted(st["renewed"]) == [A, B, C], "each user renewed exactly once"
    assert st["balances"] == {tg: START_BALANCE - PRICE for tg in (A, B, C)}
    assert _messaged(w) == [A, B, C] and _synced(w) == [A, B, C] and _panel_renewed(w) == [A, B, C]
