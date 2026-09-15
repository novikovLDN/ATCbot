"""Sales funnel on real Postgres (tests/db): the claim is the dedup + stop +
daily-cap point, and keep_max no longer extends an EQUAL discount.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")
asyncpg = pytest.importorskip("asyncpg")

import database.admin as admin_db  # noqa: E402
import database.funnel as funnel_db  # noqa: E402
from database.core import _to_db_utc  # noqa: E402

TG = 990_101
UTC = timezone.utc


@pytest.fixture
async def pool(monkeypatch):
    from urllib.parse import urlparse
    if not re.search(r"test|dash|ci", urlparse(URL).path.lstrip("/")):
        pytest.skip("not a test DB")
    p = await asyncpg.create_pool(URL, min_size=1, max_size=3, server_settings={"timezone": "UTC"})

    async def get_pool():
        return p
    monkeypatch.setattr(admin_db, "get_pool", get_pool)
    monkeypatch.setattr(funnel_db, "get_pool", get_pool)

    async def clean():
        async with p.acquire() as c:
            for t in ("funnel_messages", "user_discounts", "subscriptions", "payments", "users"):
                await c.execute(f"DELETE FROM {t} WHERE telegram_id = $1", TG)  # noqa: S608 — fixed table names
    await clean()
    yield p
    await clean()
    await p.close()


async def test_keep_max_does_not_extend_an_equal_discount(pool):
    now = datetime.now(UTC)
    async with pool.acquire() as c:
        await c.execute("INSERT INTO users (telegram_id) VALUES ($1)", TG)
    assert await admin_db.create_user_discount(TG, 20, now + timedelta(hours=10), 0, keep_max=True)
    async with pool.acquire() as c:
        first = await c.fetchval("SELECT expires_at FROM user_discounts WHERE telegram_id=$1", TG)
    # pressing the same offer again: the same percent, a later end → nothing changes
    assert await admin_db.create_user_discount(TG, 20, now + timedelta(hours=48), 0, keep_max=True)
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT discount_percent, expires_at FROM user_discounts WHERE telegram_id=$1", TG)
    assert (row["discount_percent"], row["expires_at"]) == (20, first)
    # a bigger one still replaces it
    assert await admin_db.create_user_discount(TG, 25, now + timedelta(hours=48), 0, keep_max=True)
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT discount_percent FROM user_discounts WHERE telegram_id=$1", TG) == 25


async def _start_user(pool, created):
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO users (telegram_id, created_at, captcha_passed_at) VALUES ($1, $2, $2)",
            TG, _to_db_utc(created))


async def test_claim_dedups_stops_and_caps(pool):
    now = datetime.now(UTC)
    created = now - timedelta(hours=2)
    await _start_user(pool, created)
    anchor = (await funnel_db.fetch_due(
        "start", now=now, lower=now - timedelta(days=1), steps=[("1h", 3600.0)],
        day_start=now - timedelta(hours=1), limit=10))
    anchor = [r for r in anchor if r["telegram_id"] == TG]
    assert len(anchor) == 1 and anchor[0]["step"] == "1h"
    a = anchor[0]["anchor_at"]
    kw = dict(now=now, lower=now - timedelta(days=1), day_start=now - timedelta(hours=1),
              other_since=now - timedelta(hours=6))

    cid, reason = await funnel_db.claim(TG, "start", a, "1h", (), **kw)
    assert cid and reason == "claimed"
    assert await funnel_db.claim(TG, "start", a, "1h", (), **kw) == (None, "daily_cap")
    kw_next_day = dict(kw, day_start=now + timedelta(minutes=1))
    assert await funnel_db.claim(TG, "start", a, "1h", (), **kw_next_day) == (None, "duplicate")

    # a trial activation stops the chain at the claim
    async with pool.acquire() as c:
        await c.execute("UPDATE users SET trial_used_at = $2 WHERE telegram_id = $1", TG, _to_db_utc(now))
    assert await funnel_db.claim(TG, "start", a, "1d", ("1h",), **kw_next_day) == (None, "stopped")


BIG_TG = 6_059_619_426  # > 2^31, from the prod log


async def test_claim_accepts_a_telegram_id_above_int32(pool):
    """Prod 2026-09-15: SALES_FUNNEL_PASS_FAILED «6059619426 (value out of int32
    range)» — the advisory-lock key made PG infer int4 and killed every pass."""
    now = datetime.now(UTC)
    kw = dict(now=now, lower=now - timedelta(days=1), day_start=now - timedelta(hours=1),
              other_since=now - timedelta(hours=6))
    try:
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO users (telegram_id, created_at, captcha_passed_at) VALUES ($1, $2, $2)",
                BIG_TG, _to_db_utc(now - timedelta(hours=2)))
        (a,) = [r["anchor_at"] for r in await funnel_db.fetch_due(
            "start", now=now, lower=kw["lower"], steps=[("1h", 3600.0)],
            day_start=kw["day_start"], limit=10) if r["telegram_id"] == BIG_TG]
        cid, reason = await funnel_db.claim(BIG_TG, "start", a, "1h", (), **kw)
        assert cid and reason == "claimed"
    finally:
        async with pool.acquire() as c:
            for t in ("funnel_messages", "users"):
                await c.execute(f"DELETE FROM {t} WHERE telegram_id = $1", BIG_TG)  # noqa: S608 — fixed table names


async def test_claim_waits_6h_after_an_expiry_or_a_traffic_notice(pool):
    """«Nothing within 6 h of another notification» saw only the reminders: the
    «subscription ended» notice and the traffic notices recorded nothing there."""
    now = datetime.now(UTC)
    await _start_user(pool, now - timedelta(hours=2))
    (a,) = [r["anchor_at"] for r in await funnel_db.fetch_due(
        "start", now=now, lower=now - timedelta(days=1), steps=[("1h", 3600.0)],
        day_start=now - timedelta(hours=1), limit=10) if r["telegram_id"] == TG]
    kw = dict(now=now, lower=now - timedelta(days=1), day_start=now - timedelta(hours=1),
              other_since=now - timedelta(hours=6))
    try:
        async with pool.acquire() as c:          # a traffic notice 1 h ago
            await c.execute("UPDATE users SET traffic_notice_last_at = $2 WHERE telegram_id = $1",
                            TG, _to_db_utc(now - timedelta(hours=1)))
        assert await funnel_db.claim(TG, "start", a, "1h", (), **kw) == (None, "other_notification")

        async with pool.acquire() as c:          # 7 h ago; «subscription ended» 1 h ago
            await c.execute("UPDATE users SET traffic_notice_last_at = $2 WHERE telegram_id = $1",
                            TG, _to_db_utc(now - timedelta(hours=7)))
            await c.execute("INSERT INTO automated_notification_sends (key, telegram_id, status, sent_at) "
                            "VALUES ('subscription.expired', $1, 'sent', $2)", TG, now - timedelta(hours=1))
        assert await funnel_db.claim(TG, "start", a, "1h", (), **kw) == (None, "other_notification")

        async with pool.acquire() as c:          # both older than 6 h
            await c.execute("UPDATE automated_notification_sends SET sent_at = $2 WHERE telegram_id = $1",
                            TG, now - timedelta(hours=7))
        cid, reason = await funnel_db.claim(TG, "start", a, "1h", (), **kw)
        assert cid and reason == "claimed"
    finally:
        async with pool.acquire() as c:
            await c.execute("DELETE FROM automated_notification_sends WHERE telegram_id = $1", TG)
