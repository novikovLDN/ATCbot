"""get_active_paid_subscription on real Postgres: a bypass-only row (10-year
placeholder expires_at) is never an active paid subscription; a real paid one is."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")
asyncpg = pytest.importorskip("asyncpg")

import database.subscriptions as subs  # noqa: E402

TG = 990_231


@pytest.fixture
async def conn():
    from urllib.parse import urlparse
    if not re.search(r"test|dash|ci", urlparse(URL).path.lstrip("/")):
        pytest.skip("not a test DB")
    c = await asyncpg.connect(URL, server_settings={"timezone": "UTC"})
    # added at startup by database/core.py (not a migration)
    await c.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_bypass_only BOOLEAN DEFAULT FALSE")
    await c.execute("INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING", TG)
    await c.execute("DELETE FROM subscriptions WHERE telegram_id = $1", TG)
    yield c
    await c.execute("DELETE FROM subscriptions WHERE telegram_id = $1", TG)
    await c.execute("DELETE FROM users WHERE telegram_id = $1", TG)
    await c.close()


async def _put(c, *, source, bypass_only, expires_in):
    now = datetime.now(timezone.utc)
    await c.execute("DELETE FROM subscriptions WHERE telegram_id = $1", TG)
    await c.execute(
        """INSERT INTO subscriptions (telegram_id, status, source, is_bypass_only, expires_at)
           VALUES ($1, 'active', $2, $3, $4)""",
        TG, source, bypass_only, subs._to_db_utc(now + expires_in),
    )


@pytest.mark.parametrize("source,bypass_only,expires_in,paid", [
    ("bypass_only", True, timedelta(days=3650), False),   # the placeholder row
    ("bypass_only", False, timedelta(days=3650), False),
    ("payment", True, timedelta(days=3650), False),       # flag alone
    ("trial", False, timedelta(days=2), False),
    ("payment", False, timedelta(days=20), True),
    ("auto_renew", False, timedelta(days=20), True),
    ("payment", False, -timedelta(minutes=1), False),
])
async def test_only_a_real_paid_row_counts(conn, source, bypass_only, expires_in, paid):
    await _put(conn, source=source, bypass_only=bypass_only, expires_in=expires_in)
    row = await subs.get_active_paid_subscription(conn, TG, datetime.now(timezone.utc))
    assert (row is not None) is paid
