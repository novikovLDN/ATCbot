"""−15 % window SQL on real Postgres (owner 2026-09-14: ONE 72 h window per
period, never extended; the expiry does not re-open a window the 3 h reminder
already opened; a new period gets a new window)."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")
asyncpg = pytest.importorskip("asyncpg")

import database.subscriptions as subs  # noqa: E402

TG = 990_115


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
        # added at startup by database/core.py (not a migration)
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS special_offer_created_at TIMESTAMP")
        await c.execute("INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING", TG)
        await c.execute("UPDATE users SET special_offer_created_at = NULL WHERE telegram_id = $1", TG)
    yield p
    async with p.acquire() as c:
        await c.execute("DELETE FROM users WHERE telegram_id = $1", TG)
    await p.close()


async def _created(pool):
    async with pool.acquire() as c:
        return await c.fetchval("SELECT special_offer_created_at FROM users WHERE telegram_id = $1", TG)


async def test_claim_opens_once_and_never_extends(pool):
    end = datetime.now(timezone.utc) + timedelta(hours=3)
    first = await subs.claim_special_offer(TG, end)
    assert first is not None
    assert first["expires_at"] - first["created_at"] == timedelta(hours=72)
    created = await _created(pool)

    again = await subs.claim_special_offer(TG, end)       # the button pressed again
    assert await _created(pool) == created and again["expires_at"] == first["expires_at"]


async def test_expiry_does_not_reopen_the_reminders_window(pool):
    end = datetime.now(timezone.utc) - timedelta(minutes=1)
    await subs.claim_special_offer(TG, end)               # 3 h reminder
    created = await _created(pool)
    async with pool.acquire() as c:
        assert await subs.grant_expiry_special_offer(c, TG, "payment", end) is False
    assert await _created(pool) == created


async def test_a_run_out_window_is_not_reopened_but_a_new_period_gets_one(pool):
    end = datetime.now(timezone.utc)
    stale = (end - timedelta(days=4)).replace(tzinfo=None)       # opened 4 days ago → ran out
    async with pool.acquire() as c:
        await c.execute("UPDATE users SET special_offer_created_at = $2 WHERE telegram_id = $1", TG, stale)
    assert await subs.claim_special_offer(TG, end) is None      # same period: expired, not re-opened
    assert await _created(pool) == stale

    next_end = end + timedelta(days=30)                          # renewed; the next period ends
    assert await subs.claim_special_offer(TG, next_end) is not None
    assert await _created(pool) > stale
