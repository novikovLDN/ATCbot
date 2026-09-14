"""P2: every exception raised inside process_referral_reward surfaced as
AttributeError("module 'asyncpg' has no attribute 'TimeoutError'").

Its `except (asyncpg.UniqueViolationError, ..., asyncpg.TimeoutError)` tuple
is evaluated only when an exception propagates — and asyncpg has no
TimeoutError, so the tuple itself raised, replacing the real error (a DB
error, the duplicate-reward ValueError). The transaction still rolled back,
but callers and logs saw the wrong exception. Now the builtin TimeoutError.
"""
import asyncpg
import pytest

from database.users import process_referral_reward


class _Conn:
    async def fetchrow(self, sql, *args):
        raise asyncpg.PostgresConnectionError("connection lost")


async def test_db_error_propagates_as_itself():
    with pytest.raises(asyncpg.PostgresConnectionError):
        await process_referral_reward(1, "p-1", 100.0, _Conn())
