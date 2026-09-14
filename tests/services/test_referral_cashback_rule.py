"""N17 — owner rule 2026-09-14: referral cashback is accrued ONLY for a purchase
(any purchase, however it was paid), NEVER for a balance top-up.

The payment-matrix cells (tests/services/test_payment_matrix.py §11) cover the
payment entry points. Here: award_referral_cashback — the helper for purchases
that are not finalized inside a billing transaction (shop orders, a gift paid
from balance) — and the gift-from-balance handler.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import database
import database.core as db_core
import database.users as db_users
from app.handlers.callbacks import gift as gift_mod

TG = 5151


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.tx_entered += 1
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self):
        self.tx_entered = 0

    def transaction(self):
        return _Tx(self)


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


def _install(monkeypatch, prr):
    conn = _Conn()
    monkeypatch.setattr(db_core, "DB_READY", True)
    monkeypatch.setattr(db_users, "get_pool", AsyncMock(return_value=_Pool(conn)))
    monkeypatch.setattr(db_users, "process_referral_reward", prr)
    return conn


async def test_award_helper_accrues_once_in_its_own_transaction(monkeypatch):
    prr = AsyncMock(return_value={"success": True, "referrer_id": 5, "reward_amount": 19.9})
    conn = _install(monkeypatch, prr)

    res = await database.award_referral_cashback(buyer_id=TG, purchase_id="shop-1", amount_rubles=199.0)

    assert res["success"] is True
    prr.assert_awaited_once_with(buyer_id=TG, purchase_id="shop-1", amount_rubles=199.0, conn=conn)
    assert conn.tx_entered == 1


async def test_award_helper_never_raises(monkeypatch):
    """A cashback failure (e.g. the concurrent-duplicate guard) must not fail the
    purchase it belongs to: its own transaction rolls back, the caller goes on."""
    prr = AsyncMock(side_effect=ValueError("Duplicate referral reward prevented"))
    _install(monkeypatch, prr)

    res = await database.award_referral_cashback(buyer_id=TG, purchase_id="shop-1", amount_rubles=199.0)

    assert res["success"] is False and res["reason"] == "error"


async def test_gift_paid_from_balance_gets_cashback_once_on_the_price(monkeypatch):
    state = MagicMock()
    state.get_data = AsyncMock(return_value={
        "gift_tariff": "basic", "gift_period_days": 30, "gift_price_kopecks": 19900})
    state.get_state = AsyncMock(return_value=None)
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    monkeypatch.setattr(gift_mod, "check_rate_limit", lambda *_a, **_k: (True, None))
    monkeypatch.setattr(gift_mod, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(gift_mod, "_send_gift_success", AsyncMock())
    monkeypatch.setattr(database, "get_user_balance", AsyncMock(return_value=500.0))
    monkeypatch.setattr(database, "decrease_balance", AsyncMock(return_value=True))
    create = AsyncMock(return_value={"gift_code": "GIFT1"})
    monkeypatch.setattr(database, "create_gift_subscription", create)
    award = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(database, "award_referral_cashback", award, raising=False)
    cb = MagicMock()
    cb.from_user.id = TG
    cb.answer = AsyncMock()
    cb.message.answer = AsyncMock()

    await gift_mod.callback_gift_pay_balance(cb, state)

    purchase_id = create.await_args.kwargs["purchase_id"]
    award.assert_awaited_once_with(buyer_id=TG, purchase_id=purchase_id, amount_rubles=199.0)
