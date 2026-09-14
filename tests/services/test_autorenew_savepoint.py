"""P1-9: legacy auto-renewal (flag off) — one savepoint per user.

A per-user exception (e.g. grant_access INVARIANT_VIOLATION when the subscription
expired between the batch selection and the grant) must roll back THAT user's
debit only: no money taken without a renewal, a forced admin alert, and the other
users of the same batch are renewed and billed normally.

Fake asyncpg: one connection with nested transactions that restore balances and
payments rows on an exception (Postgres savepoint semantics).
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import auto_renewal
import database
from app.services import admin_alerts

A, B = 111_001, 111_002


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.snapshots.append(copy.deepcopy((self.conn.balances, self.conn.payments)))
        return self

    async def __aexit__(self, exc_type, *_exc):
        snap = self.conn.snapshots.pop()
        if exc_type is not None:
            self.conn.balances, self.conn.payments = snap
        return False


class Conn:
    def __init__(self, rows):
        self.rows = rows
        self.balances = {r["telegram_id"]: r["balance"] for r in rows}
        self.payments: list = []
        self.snapshots: list = []
        self.fetched = False

    def transaction(self):
        return _Tx(self)

    async def fetch(self, sql, *args):
        if self.fetched:
            return []
        self.fetched = True
        return [dict(r) for r in self.rows]

    async def execute(self, sql, *args):
        return "UPDATE 1"

    async def fetchrow(self, sql, *args):
        if "auto_renew, expires_at" in sql:
            return {"auto_renew": True, "expires_at": None, "last_auto_renewal_at": None}
        if "SELECT vpn_key" in sql:
            return {"vpn_key": "https://panel.test/sub/k"}
        return None

    async def fetchval(self, sql, *args):
        if "INSERT INTO payments" in sql:
            self.payments.append(args)
            return len(self.payments)
        return None


class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


def _row(tg):
    exp = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
    return {"telegram_id": tg, "language": "ru", "balance": 100_000, "status": "active",
            "auto_renew": True, "uuid": f"uuid-{tg}", "expires_at": exp,
            "subscription_type": "basic", "is_combo": False, "last_auto_renewal_at": None}


async def test_per_user_failure_rolls_back_only_that_users_debit(monkeypatch):
    conn = Conn([_row(A), _row(B)])

    async def decrease(*, telegram_id, amount, source, description, conn):
        conn.balances[telegram_id] -= round(amount * 100)
        return True

    async def grant(*, telegram_id, **_k):
        if telegram_id == A:     # expired between selection and grant (P1-9)
            raise RuntimeError("INVARIANT_VIOLATION: add_vless_user must never run inside DB transaction")
        return {"subscription_end": datetime.now(timezone.utc) + timedelta(days=30),
                "action": "renewal", "vless_url": None, "renewal_xray_sync_after_commit": None}

    alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "get_pool", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(database, "get_last_subscription_payment",
                        AsyncMock(return_value={"tariff": "basic_30"}), raising=False)
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(database, "decrease_balance", AsyncMock(side_effect=decrease), raising=False)
    monkeypatch.setattr(database, "increase_balance", AsyncMock(return_value=True), raising=False)
    monkeypatch.setattr(database, "grant_access", AsyncMock(side_effect=grant), raising=False)
    monkeypatch.setattr(auto_renewal, "acquire_connection", lambda _pool, _name: _Acq(conn))
    monkeypatch.setattr(auto_renewal, "safe_send_message", AsyncMock(return_value=None))
    monkeypatch.setattr(auto_renewal, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(auto_renewal.notification_service, "check_notification_idempotency",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(admin_alerts, "send_alert", alerts)
    from app.services import remnawave_service
    from app.services.payments import verify_delivery
    monkeypatch.setattr(remnawave_service, "renew_remnawave_user_bg", MagicMock())
    monkeypatch.setattr(verify_delivery, "schedule_legacy_check", MagicMock())

    await auto_renewal.process_auto_renewals(MagicMock())

    price = 199 * 100
    assert conn.balances[A] == 100_000, "A was debited without a renewal"
    assert conn.balances[B] == 100_000 - price, "B (same batch) must still be renewed and billed"
    assert [p[0] for p in conn.payments] == [B]
    forced = [c for c in alerts.await_args_list if c.kwargs.get("force")]
    assert any("Auto-renewal processing error" in c.args[2] and str(A) in c.args[2] for c in forced)
