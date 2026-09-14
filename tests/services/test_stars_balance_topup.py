"""P0: a balance top-up paid with Telegram Stars took the Stars and credited
nothing.

finalize_balance_topup_payment accepted provider="telegram_stars" but its
provider → idempotency-column map had no such key: KeyError before any DB
write, wrapped into PaymentFinalizationError; the successful_payment handler
only logged it and showed "payment processing error" — no credit, no admin
alert, and Telegram never resends successful_payment.

Now the Stars charge id is stored like any Telegram charge id
(payments.telegram_payment_charge_id, same idempotency check) and a failed
top-up finalization after Telegram took the money is a forced admin alert.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
import database.admin as db_admin
import database.users as db_users
from app.handlers.payments import payments_messages as pm
from app.services.payments import service as payment_service
from app.services.payments.service import PaymentFinalizationError

TG = 4242


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self):
        self.inserts = []
        self.balance = 0

    def transaction(self):
        return _Tx()

    async def fetchval(self, sql, *args):
        if "information_schema.columns" in sql:
            return 1
        if "SELECT telegram_id FROM users" in sql:
            return TG
        if "INSERT INTO payments" in sql:
            self.inserts.append((sql, args))
            return 77
        if "SELECT balance FROM users" in sql:
            return self.balance
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetchrow(self, sql, *args):
        assert "FROM payments" in sql
        return None                      # no duplicate

    async def execute(self, sql, *args):
        if "UPDATE users SET balance = balance +" in sql:
            self.balance += args[0]
        return "OK"


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _A:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *exc):
                return False
        return _A()


async def test_stars_topup_credits_the_balance(monkeypatch):
    conn = _Conn()

    async def get_pool():
        return _Pool(conn)

    monkeypatch.setattr(db_admin, "get_pool", get_pool)
    monkeypatch.setattr(db_users, "process_referral_reward", AsyncMock(return_value={"success": False}))

    result = await db_admin.finalize_balance_topup(
        telegram_id=TG, amount_rubles=500.0, provider="telegram_stars",
        provider_charge_id="stars-charge-1", description="Stars",
    )

    assert result["success"] is True
    assert conn.balance == 50_000
    sql, args = conn.inserts[0]
    assert args[3] == "telegram_stars" and args[4] == "stars-charge-1"
    # the Stars charge id lands in telegram_payment_charge_id (idempotency key)
    assert "IN ('telegram', 'telegram_stars') THEN $5" in " ".join(sql.split())


@pytest.mark.parametrize("provider", ["telegram", "telegram_stars"])
async def test_telegram_topup_accrues_no_referral_cashback(monkeypatch, provider):
    """Owner rule 2026-09-14 (N17): referral cashback only for a purchase, never
    for a balance top-up — the purchase paid from that balance earns it instead."""
    conn = _Conn()

    async def get_pool():
        return _Pool(conn)

    referral = AsyncMock(return_value={"success": True, "referrer_id": 1, "reward_amount": 50.0})
    monkeypatch.setattr(db_admin, "get_pool", get_pool)
    monkeypatch.setattr(db_users, "process_referral_reward", referral)

    result = await db_admin.finalize_balance_topup(
        telegram_id=TG, amount_rubles=500.0, provider=provider, provider_charge_id=f"{provider}-charge-9",
    )

    assert result["success"] is True and conn.balance == 50_000
    referral.assert_not_awaited()
    assert result["referral_reward"] is None


async def test_failed_topup_finalization_after_payment_is_a_forced_alert(monkeypatch):
    monkeypatch.setattr(database, "DB_READY", True, raising=False)
    monkeypatch.setattr(pm, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(
        payment_service, "verify_payment_payload",
        AsyncMock(return_value=SimpleNamespace(payload_type="balance_topup", amount=500)),
    )
    monkeypatch.setattr(
        payment_service, "finalize_balance_topup_payment",
        AsyncMock(side_effect=PaymentFinalizationError("boom")),
    )
    alert = AsyncMock()
    monkeypatch.setattr(pm, "_alert_money_taken_not_granted", alert)

    message = MagicMock()
    message.from_user.id = TG
    message.message_id = 1
    message.answer = AsyncMock()
    message.successful_payment = SimpleNamespace(
        currency="XTR", total_amount=300, invoice_payload=f"balance_topup_{TG}_500",
        telegram_payment_charge_id="stars-charge-2",
    )
    await pm.process_successful_payment(message, MagicMock())

    assert alert.await_count == 1
    kwargs = alert.await_args.kwargs
    assert kwargs["telegram_id"] == TG and kwargs["provider"] == "telegram_stars"
    message.answer.assert_awaited()          # the user still gets the error text
