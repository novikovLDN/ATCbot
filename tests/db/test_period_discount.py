"""user_period_discounts on real Postgres (migration 095, owner 2026-09-15):
a broadcast period gift is stored per (user, period); keep_max — a bigger active
one stays, an equal one is not extended, an expired one is replaced; the
general personal discount is never touched.
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
from database import core as db_core  # noqa: E402
from database.core import _to_db_utc  # noqa: E402

TG = 990_201
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
    monkeypatch.setattr(db_core, "DB_READY", True)

    async def clean():
        async with p.acquire() as c:
            await c.execute("DELETE FROM user_period_discounts WHERE telegram_id = $1", TG)
            await c.execute("DELETE FROM user_discounts WHERE telegram_id = $1", TG)
    await clean()
    yield p
    await clean()
    await p.close()


async def test_the_gift_is_only_on_its_period_and_keeps_the_max(pool):
    await admin_db.create_period_discount(TG, 365, 40, NOW + timedelta(hours=24), "gift1y40")
    got = await admin_db.get_period_discount(TG, 365)
    assert got["discount_percent"] == 40 and got["source"] == "gift1y40"
    assert await admin_db.get_period_discount(TG, 30) is None
    first_end = got["expires_at"]

    # a smaller one does not lower it, an equal one does not extend it
    await admin_db.create_period_discount(TG, 365, 30, NOW + timedelta(hours=48), "gift_small")
    await admin_db.create_period_discount(TG, 365, 40, NOW + timedelta(hours=48), "gift1y40")
    got = await admin_db.get_period_discount(TG, 365)
    assert (got["discount_percent"], got["expires_at"]) == (40, first_end)

    # an expired one is replaced
    async with pool.acquire() as c:
        await c.execute("UPDATE user_period_discounts SET expires_at = $2 WHERE telegram_id = $1",
                        TG, _to_db_utc(NOW - timedelta(minutes=1)))
    assert await admin_db.get_period_discount(TG, 365) is None
    await admin_db.create_period_discount(TG, 365, 30, NOW + timedelta(hours=24), "gift_small")
    assert (await admin_db.get_period_discount(TG, 365))["discount_percent"] == 30

    # the general personal discount is untouched
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM user_discounts WHERE telegram_id = $1", TG) == 0
