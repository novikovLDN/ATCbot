"""Paid reminder claims on real Postgres (docs/notifications/matrix.md #16, #24):
a reminder is claimed for the period the pass saw — a renewal inside the pass
(new expires_at, flags reset) leaves nothing to claim; claimed once; a release
after a temporary failure lets the next pass retry."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")
asyncpg = pytest.importorskip("asyncpg")

import database.subscriptions as subs  # noqa: E402

TG = 990_216
END = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=123456)


@pytest.fixture
async def pool(monkeypatch):
    from urllib.parse import urlparse
    if not re.search(r"test|dash|ci", urlparse(URL).path.lstrip("/")):
        pytest.skip("not a test DB")
    p = await asyncpg.create_pool(URL, min_size=1, max_size=2, server_settings={"timezone": "UTC"})

    async def get_pool():
        return p
    monkeypatch.setattr(subs, "get_pool", get_pool)
    monkeypatch.setattr(subs._core, "DB_READY", True)
    async with p.acquire() as c:
        await c.execute("INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING", TG)
        await c.execute("DELETE FROM subscriptions WHERE telegram_id = $1", TG)
        await c.execute(
            "INSERT INTO subscriptions (telegram_id, uuid, expires_at, status, source) "
            "VALUES ($1, 'u-claim', $2, 'active', 'payment')",
            TG, END.replace(tzinfo=None))
    yield p
    async with p.acquire() as c:
        await c.execute("DELETE FROM subscriptions WHERE telegram_id = $1", TG)
        await c.execute("DELETE FROM users WHERE telegram_id = $1", TG)
    await p.close()


async def _flag(pool, flag="reminder_7d_sent"):
    async with pool.acquire() as c:
        return await c.fetchval(f"SELECT {flag} FROM subscriptions WHERE telegram_id = $1", TG)


async def test_claimed_once_for_the_period(pool):
    assert await subs.claim_reminder_flag(TG, "reminder_7d_sent", END) is True
    assert await _flag(pool) is True
    assert await subs.claim_reminder_flag(TG, "reminder_7d_sent", END) is False


async def test_a_renewal_inside_the_pass_leaves_nothing_to_claim(pool):
    async with pool.acquire() as c:        # renewed after the pass read END: new date, flags reset
        await c.execute("UPDATE subscriptions SET expires_at = $2, reminder_7d_sent = FALSE WHERE telegram_id = $1",
                        TG, (END + timedelta(days=30)).replace(tzinfo=None))
    assert await subs.claim_reminder_flag(TG, "reminder_7d_sent", END) is False
    assert await _flag(pool) is False, "the new period keeps its own reminder"


async def test_release_after_a_temporary_failure_lets_the_next_pass_retry(pool):
    assert await subs.claim_reminder_flag(TG, "reminder_3h_sent", END) is True
    await subs.release_reminder_flag(TG, "reminder_3h_sent", END)
    assert await _flag(pool, "reminder_3h_sent") is False
    assert await subs.claim_reminder_flag(TG, "reminder_3h_sent", END) is True


async def test_blocked_user(pool):
    assert await subs.is_user_blocked(TG) is False
    async with pool.acquire() as c:
        await c.execute("UPDATE users SET is_reachable = FALSE WHERE telegram_id = $1", TG)
    assert await subs.is_user_blocked(TG) is True
