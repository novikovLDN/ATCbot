"""REF-DANGLING (docs/audit/09_test_coverage.md): a users.referrer_id left by an old
deletion pointed to a user that no longer exists → process_referral_reward
raised «Referrer … not found for reward» inside the billing transaction → the
paid purchase was rejected (money taken, no access; balance purchases and
auto-renewal too).

Now the referrer row is locked + read before any write; missing → «no
referrer»: no cashback, no referrals row, the dangling link is cleared in the
same transaction, no notification, never an exception. Real stack:
tests/e2e/test_19_dashboard_writes.py::test_invitee_of_an_already_deleted_referrer_can_still_pay.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.notifications import referral_cashback as rc

BUYER, GONE = 11, 9_999_001


class _Conn:
    def __init__(self):
        self.executed = []

    async def fetchrow(self, sql, *args):
        if "SELECT referrer_id FROM users" in sql:
            return {"referrer_id": GONE}
        if "FROM referral_rewards" in sql:
            return None
        if "SELECT balance FROM users" in sql:
            return None                                  # the referrer was deleted
        raise AssertionError(f"unexpected fetchrow after the referrer check: {sql}")

    async def fetchval(self, sql, *args):
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))
        return "UPDATE 1"


@pytest.fixture
def scheduled(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(rc, "schedule", mock)
    return mock


async def test_missing_referrer_is_no_referrer_and_never_fails_the_purchase(scheduled):
    from database.users import process_referral_reward
    conn = _Conn()

    out = await process_referral_reward(buyer_id=BUYER, purchase_id="purchase_x", amount_rubles=199.0, conn=conn)

    assert out["success"] is False and out["reason"] == "referrer_missing"
    sqls = [s for s, _ in conn.executed]
    assert not any("referrals" in s or "balance_transactions" in s or "referral_rewards" in s
                   or "SET balance" in s for s in sqls), sqls        # nothing written for the ghost
    clear = [(s, a) for s, a in conn.executed if s.startswith("UPDATE users SET referrer_id")]
    assert clear and clear[0][1] == (BUYER, GONE)                    # dangling link cleared
    scheduled.assert_not_called()


async def test_award_referral_cashback_returns_quietly(monkeypatch, scheduled):
    """The own-transaction wrapper (shop, gift from balance) — same outcome."""
    import database.users as users

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    conn = _Conn()
    conn.transaction = lambda: _Tx()

    class _Pool:
        def acquire(self):
            class _A:
                async def __aenter__(self_inner):
                    return conn

                async def __aexit__(self_inner, *exc):
                    return False
            return _A()

    monkeypatch.setattr(users, "get_pool", AsyncMock(return_value=_Pool()))
    out = await users.award_referral_cashback(buyer_id=BUYER, purchase_id="purchase_y", amount_rubles=99.0)
    assert out["reason"] == "referrer_missing"
