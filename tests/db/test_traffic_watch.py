"""Traffic notice state on real Postgres (owner 2026-09-14, migration 084):
compare-and-set (two passes never send the same notice twice); a GB grant
(reset_traffic_notification_flags) re-arms the thresholds; the background
check's selection is exactly production's."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")
asyncpg = pytest.importorskip("asyncpg")

import database.core as core  # noqa: E402
import database.traffic as traffic  # noqa: E402

TG = 990_401
GB = 1024 ** 3


@pytest.fixture
async def pool(monkeypatch):
    from urllib.parse import urlparse
    if not re.search(r"test|dash|ci", urlparse(URL).path.lstrip("/")):
        pytest.skip("not a test DB")
    p = await asyncpg.create_pool(URL, min_size=1, max_size=2, server_settings={"timezone": "UTC"})

    async def get_pool():
        return p
    monkeypatch.setattr(traffic, "get_pool", get_pool)
    monkeypatch.setattr(core, "DB_READY", True)
    async with p.acquire() as c:
        # traffic_notified_* are added at startup by database/core.py (not a migration)
        for col in ("traffic_notified_8gb", "traffic_notified_5gb", "traffic_notified_3gb",
                    "traffic_notified_1gb", "traffic_notified_500mb", "traffic_notified_0"):
            await c.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} BOOLEAN DEFAULT FALSE")
        await c.execute("DELETE FROM subscriptions WHERE telegram_id = $1", TG)
        await c.execute("INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING", TG)
        await c.execute("UPDATE users SET traffic_notice_floor_bytes = NULL, traffic_notice_last_at = NULL "
                        "WHERE telegram_id = $1", TG)
        await c.execute(
            "INSERT INTO subscriptions (telegram_id, expires_at, status, source, remnawave_uuid) "
            "VALUES ($1, $2, 'active', 'payment', 'rw-1')",
            TG, (datetime.now(timezone.utc) + timedelta(days=10)).replace(tzinfo=None))
    yield p
    async with p.acquire() as c:
        await c.execute("DELETE FROM subscriptions WHERE telegram_id = $1", TG)
        await c.execute("DELETE FROM users WHERE telegram_id = $1", TG)
    await p.close()


async def test_notice_state_is_compare_and_set(pool):
    st = await traffic.get_traffic_notice_state(TG)
    assert st == {"floor": None, "last_at": None, "legacy_zero_told": False}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    assert await traffic.claim_traffic_notice_state(TG, None, None, 5 * GB, now) is True
    assert await traffic.claim_traffic_notice_state(TG, None, None, 3 * GB, now) is False   # the other pass lost
    st = await traffic.get_traffic_notice_state(TG)
    assert st["floor"] == 5 * GB and st["last_at"] == now
    assert await traffic.claim_traffic_notice_state(TG, 5 * GB, now, 3 * GB, now) is True


async def test_a_gb_grant_re_arms_the_thresholds(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    assert await traffic.claim_traffic_notice_state(TG, None, None, 0, now) is True         # told «0»
    await traffic.reset_traffic_notification_flags(TG)                                      # GB arrived
    st = await traffic.get_traffic_notice_state(TG)
    assert st["floor"] is None and st["last_at"] == now, "a new baseline; the 3 h gap still counts"


async def test_selection_is_production_s(pool):
    rows = [r for r in await traffic.get_active_remnawave_users() if r["telegram_id"] == TG]
    assert len(rows) == 1 and rows[0]["remnawave_uuid"] == "rw-1"
    assert {"is_bypass_only", "source", "expires_at"} <= set(rows[0])
