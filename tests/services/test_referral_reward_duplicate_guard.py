"""P2: the concurrent-duplicate guard of process_referral_reward never fired.

The cashback is credited to the referrer BEFORE the referral_rewards row is
inserted with ON CONFLICT (buyer_id, purchase_id) DO NOTHING. The code then
compared the command tag with "INSERT 0", but asyncpg returns "INSERT 0 0"
for zero inserted rows (oid + count), so a concurrent duplicate kept its
balance credit instead of raising and rolling the transaction back.
"""
from datetime import datetime, timezone

import pytest

from database.users import process_referral_reward

BUYER = 111
REFERRER = 222


class _Conn:
    def __init__(self, insert_tag):
        self.insert_tag = insert_tag
        self.executed = []

    async def fetchrow(self, sql, *args):
        if "SELECT referrer_id FROM users" in sql:
            return {"referrer_id": REFERRER}
        if "FROM referral_rewards" in sql:
            return None                                  # pre-check: no reward yet
        if "FROM referrals" in sql:
            return {"first_paid_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        if "SELECT balance FROM users" in sql:
            return {"balance": 0}
        return None                                      # cashback multipliers

    async def fetchval(self, sql, *args):
        if "COUNT" in sql.upper():
            return 1
        return None                                      # floor / fixed percent

    async def execute(self, sql, *args):
        self.executed.append(sql)
        if "INSERT INTO referral_rewards" in sql:
            return self.insert_tag
        return "OK"


async def test_concurrent_duplicate_insert_raises_to_roll_back_the_credit():
    conn = _Conn("INSERT 0 0")           # what asyncpg returns when ON CONFLICT skipped the row
    with pytest.raises(ValueError, match="Duplicate referral reward"):
        await process_referral_reward(BUYER, "purchase-1", 1000.0, conn)


async def test_inserted_reward_succeeds():
    conn = _Conn("INSERT 0 1")
    out = await process_referral_reward(BUYER, "purchase-1", 1000.0, conn)
    assert out["success"] is True and out["referrer_id"] == REFERRER
