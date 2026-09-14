"""HOW_IT_WORKS P2: the −15 % buttons overwrote a bigger personal discount
(create_user_discount upserted unconditionally, database/admin.py:3534-3537):
a user with −30 % pressing an old «−15 %» button dropped to −15 %.

create_user_discount(keep_max=True) keeps an ACTIVE bigger discount and
replaces an expired or smaller one; without keep_max the dashboard admin still
sets exactly what they chose. Real Postgres (tests/db).
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
from database.core import _to_db_utc  # noqa: E402

TG = 990_001
NOW = datetime.now(timezone.utc)


@pytest.fixture
async def pool(monkeypatch):
    from urllib.parse import urlparse
    if not re.search(r"test|dash|ci", urlparse(URL).path.lstrip("/")):
        pytest.skip("not a test DB")
    p = await asyncpg.create_pool(URL, min_size=1, max_size=2, server_settings={"timezone": "UTC"})

    async def get_pool():
        return p
    monkeypatch.setattr(admin_db, "get_pool", get_pool)
    async with p.acquire() as c:
        await c.execute("DELETE FROM user_discounts WHERE telegram_id = $1", TG)
        await c.execute("INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING", TG)
    yield p
    async with p.acquire() as c:
        await c.execute("DELETE FROM user_discounts WHERE telegram_id = $1", TG)
    await p.close()


async def _existing(pool, percent, expires_at):
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO user_discounts (telegram_id, discount_percent, expires_at, created_by) VALUES ($1,$2,$3,1)",
            TG, percent, _to_db_utc(expires_at) if expires_at else None,
        )


async def _percent(pool):
    async with pool.acquire() as c:
        return await c.fetchval("SELECT discount_percent FROM user_discounts WHERE telegram_id = $1", TG)


@pytest.mark.parametrize("existing, expires_in, new, keep_max, expected", [
    (30, timedelta(days=5), 15, True, 30),     # bigger active discount is kept
    (30, None, 15, True, 30),                  # bigger permanent discount is kept
    (30, timedelta(days=-1), 15, True, 15),    # bigger but expired → replaced
    (10, timedelta(days=5), 15, True, 15),     # smaller → replaced
    (30, timedelta(days=5), 15, False, 15),    # admin (no keep_max) overwrites as before
])
async def test_create_user_discount_keep_max(pool, existing, expires_in, new, keep_max, expected):
    await _existing(pool, existing, (NOW + expires_in) if expires_in is not None else None)
    ok = await admin_db.create_user_discount(
        telegram_id=TG, discount_percent=new, expires_at=NOW + timedelta(days=7), created_by=0,
        keep_max=keep_max,
    )
    assert ok is True
    assert await _percent(pool) == expected


async def test_keep_max_creates_when_none(pool):
    assert await admin_db.create_user_discount(
        telegram_id=TG, discount_percent=15, expires_at=NOW + timedelta(days=7), created_by=0, keep_max=True,
    )
    assert await _percent(pool) == 15
