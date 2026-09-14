"""Referral cashback notification for EVERY accrual (owner, 2026-09-14;
docs/audit/08_payments_ux.md P1 #4).

Before, only Telegram card/Stars and balance purchases told the referrer; the
main revenue (Platega / WATA / CryptoBot), GB packs, gifts, the shop and
auto-renewal accrued cashback silently. Now process_referral_reward — the one
place cashback is accrued — schedules the notice, which is sent only after the
caller's transaction committed, once per (buyer_id, purchase_id).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.notifications import referral_cashback as rc

KW = dict(buyer_id=11, referrer_id=22, purchase_id="purchase_abc", purchase_amount=199.0,
          reward_amount=19.9, percent=10, paid_referrals_count=3, referrals_needed=22)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(rc, "_POLL_DELAYS", (0, 0, 0))
    monkeypatch.setattr(rc, "_claimed", set())


async def test_sent_once_after_the_accrual_is_committed(monkeypatch):
    monkeypatch.setattr(rc, "_row_committed", AsyncMock(side_effect=[False, True]))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(rc, "_send", send)
    assert await rc._notify_after_commit(object(), **KW) is True
    send.assert_awaited_once()
    assert send.await_args.kwargs["referrer_id"] == 22


async def test_rolled_back_accrual_is_never_announced(monkeypatch):
    monkeypatch.setattr(rc, "_row_committed", AsyncMock(return_value=False))
    send = AsyncMock()
    monkeypatch.setattr(rc, "_send", send)
    assert await rc._notify_after_commit(object(), **KW) is False
    send.assert_not_awaited()


async def test_rolled_back_then_retried_accrual_sends_one_message(monkeypatch):
    """Webhook retry: attempt 1 rolled back, attempt 2 committed the same key —
    both notifiers see the committed row, only one sends."""
    monkeypatch.setattr(rc, "_row_committed", AsyncMock(return_value=True))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(rc, "_send", send)
    await asyncio.gather(rc._notify_after_commit(object(), **KW), rc._notify_after_commit(object(), **KW))
    send.assert_awaited_once()


def test_schedule_without_a_bot_does_nothing(monkeypatch):
    monkeypatch.setattr(rc, "_bot", lambda: None)
    assert rc.schedule(**KW) is False


async def test_schedule_with_a_bot_starts_the_notifier(monkeypatch):
    monkeypatch.setattr(rc, "_bot", lambda: object())
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(rc, "_notify_after_commit", notify)
    assert rc.schedule(**KW) is True
    await asyncio.sleep(0)
    notify.assert_awaited_once()


@pytest.mark.parametrize("purchase_id, pending, expected", [
    ("autorenew_5", None, ("renewal", None)),
    ("balance_purchase_7", None, ("purchase", None)),
    ("purchase_x", {"purchase_type": "subscription", "period_days": 90}, ("purchase", 90)),
    ("purchase_x", {"purchase_type": "gift", "period_days": 30}, ("purchase", 30)),
    ("purchase_x", {"purchase_type": "traffic_pack", "period_days": None}, ("purchase", None)),
])
async def test_purchase_context(monkeypatch, purchase_id, pending, expected):
    import database
    monkeypatch.setattr(database, "get_pending_purchase_by_id", AsyncMock(return_value=pending))
    assert await rc._purchase_context(purchase_id) == expected


async def test_english_referrer_gets_an_english_period(monkeypatch):
    """Before: «⏰ Subscription: 1 месяц» for an EN referrer."""
    import database
    from app.handlers import notifications as notif_mod
    import app.services.language_service as lang_mod
    monkeypatch.setattr(database, "get_pending_purchase_by_id",
                        AsyncMock(return_value={"purchase_type": "subscription", "period_days": 90}))
    monkeypatch.setattr(lang_mod, "resolve_user_language", AsyncMock(return_value="en"))
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(notif_mod, "send_referral_cashback_notification", sender)
    await rc._send(object(), **KW)
    assert sender.await_args.kwargs["subscription_period"] == "3 months"
    assert sender.await_args.kwargs["action_type"] == "purchase"


# ── the hook in process_referral_reward ────────────────────────────────


class _Conn:
    """Just enough of asyncpg for process_referral_reward (dispatch on SQL text)."""

    def __init__(self, insert_tag):
        self.insert_tag = insert_tag

    async def fetchrow(self, sql, *args):
        if "FROM users WHERE telegram_id" in sql and "referrer_id" in sql:
            return {"referrer_id": 22, "referred_by": None}
        if "FROM referral_rewards" in sql:
            return None
        if "FROM referrals" in sql:
            return {"first_paid_at": datetime.now(timezone.utc)}
        if "SELECT balance FROM users" in sql:
            return {"balance": 0}
        return None                                  # no cashback multipliers

    async def fetchval(self, sql, *args):
        if "COUNT(DISTINCT referred_user_id)" in sql:
            return 3
        return None                                  # no floor / fixed percent

    async def execute(self, sql, *args):
        if "INSERT INTO referral_rewards" in sql:
            return self.insert_tag
        return "OK"


@pytest.fixture
def scheduled(monkeypatch):
    import database.subscriptions as subs
    monkeypatch.setattr(subs, "_log_audit_event_atomic", AsyncMock())
    mock = MagicMock(return_value=True)
    monkeypatch.setattr(rc, "schedule", mock)
    return mock


async def test_accrual_schedules_the_notice(scheduled):
    from database.users import process_referral_reward
    out = await process_referral_reward(buyer_id=11, purchase_id="purchase_abc", amount_rubles=199.0,
                                        conn=_Conn("INSERT 0 1"))
    assert out["success"] is True
    kw = scheduled.call_args.kwargs
    assert (kw["buyer_id"], kw["referrer_id"], kw["purchase_id"]) == (11, 22, "purchase_abc")
    assert kw["reward_amount"] == pytest.approx(19.9) and kw["percent"] == 10


async def test_duplicate_accrual_schedules_nothing(scheduled):
    from database.users import process_referral_reward
    with pytest.raises(ValueError):
        await process_referral_reward(buyer_id=11, purchase_id="purchase_abc", amount_rubles=199.0,
                                      conn=_Conn("INSERT 0 0"))
    scheduled.assert_not_called()
