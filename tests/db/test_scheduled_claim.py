"""HOW_IT_WORKS P2: a scheduled broadcast must not be sent twice.
claim_scheduled_run is one guarded UPDATE: it succeeds once per slot
(scheduled_at must still be the value the worker read), moves the row to the
next slot (or deactivates a 'once' row) and counts the run. Real Postgres."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")
asyncpg = pytest.importorskip("asyncpg")

import database.scheduled_broadcasts as sb  # noqa: E402


@pytest.fixture
async def pool(monkeypatch):
    from urllib.parse import urlparse
    if not re.search(r"test|dash|ci", urlparse(URL).path.lstrip("/")):
        pytest.skip("not a test DB")
    p = await asyncpg.create_pool(URL, min_size=1, max_size=2, server_settings={"timezone": "UTC"})

    async def get_pool():
        return p
    monkeypatch.setattr(sb, "get_pool", get_pool)
    yield p
    async with p.acquire() as c:
        await c.execute("DELETE FROM scheduled_broadcasts WHERE title LIKE 'claim-test%'")
    await p.close()


async def _row(pool, recurrence):
    at = datetime.now(timezone.utc) - timedelta(minutes=2)
    async with pool.acquire() as c:
        sid = await c.fetchval(
            """INSERT INTO scheduled_broadcasts (title, message, segment, scheduled_at, recurrence, created_by)
               VALUES ($1, 'm', 'all_users', $2, $3, 1) RETURNING id""",
            f"claim-test-{recurrence}", at, recurrence,
        )
        row = await c.fetchrow("SELECT scheduled_at FROM scheduled_broadcasts WHERE id=$1", sid)
    return sid, row["scheduled_at"]


@pytest.mark.parametrize("recurrence", ["once", "daily"])
async def test_claim_succeeds_once_per_slot(pool, recurrence):
    sid, at = await _row(pool, recurrence)
    assert await sb.claim_scheduled_run(sid, at) is True
    assert await sb.claim_scheduled_run(sid, at) is False       # second worker / retry
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM scheduled_broadcasts WHERE id=$1", sid)
    assert row["run_count"] == 1
    if recurrence == "once":
        assert row["is_active"] is False
    else:
        assert row["is_active"] is True and row["scheduled_at"] > datetime.now(timezone.utc)
    due = [r["id"] for r in await sb.fetch_due_scheduled(limit=50)]
    assert sid not in due


async def test_record_result_keeps_the_schedule(pool):
    sid, at = await _row(pool, "daily")
    assert await sb.claim_scheduled_run(sid, at)
    await sb.record_scheduled_result(sid, last_broadcast_id=None, error="empty_audience")
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM scheduled_broadcasts WHERE id=$1", sid)
    assert row["last_error"] == "empty_audience" and row["run_count"] == 1 and row["is_active"] is True
