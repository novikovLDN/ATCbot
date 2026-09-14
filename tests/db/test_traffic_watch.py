"""Background traffic check selection + notice state on real Postgres
(owner 2026-09-14): a SUBSET of the old selection (active row with a panel
pointer); users told «трафик закончился» are skipped until GB arrive (a grant
resets the floor, a bought pack after the message re-includes them); users
without a subscription / bypass are never polled; the state is compare-and-set."""
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

USERS = {
    990_401: "active",          # active paid row with a pointer
    990_402: "bypass_only",     # bypass-only row
    990_403: "exhausted",       # told «0»
    990_404: "no_pointer",      # active row, no bypass entity
    990_405: "expired",         # expired row
    990_406: "nothing",         # no subscription at all
}
FAR = datetime.now(timezone.utc) + timedelta(days=3650)
SOON = datetime.now(timezone.utc) + timedelta(days=10)


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
    ids = list(USERS)
    async with p.acquire() as c:
        # traffic_notified_* are added at startup by database/core.py (not a migration)
        for col in ("traffic_notified_8gb", "traffic_notified_5gb", "traffic_notified_3gb",
                    "traffic_notified_1gb", "traffic_notified_500mb", "traffic_notified_0"):
            await c.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} BOOLEAN DEFAULT FALSE")
        await c.execute("DELETE FROM subscriptions WHERE telegram_id = ANY($1::bigint[])", ids)
        await c.execute("DELETE FROM traffic_purchases WHERE telegram_id = ANY($1::bigint[])", ids)
        for tg, kind in USERS.items():
            await c.execute("INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING", tg)
            await c.execute("UPDATE users SET traffic_notice_floor_bytes = NULL, traffic_notice_last_at = NULL "
                            "WHERE telegram_id = $1", tg)
            if kind == "nothing":
                continue
            status = "expired" if kind == "expired" else "active"
            pointer = None if kind == "no_pointer" else f"rw-{tg}"
            exp = FAR if kind == "bypass_only" else SOON
            await c.execute(
                "INSERT INTO subscriptions (telegram_id, expires_at, status, source, remnawave_uuid, is_bypass_only) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                tg, exp.replace(tzinfo=None), status, "bypass_only" if kind == "bypass_only" else "payment",
                pointer, kind == "bypass_only")
        await c.execute("UPDATE users SET traffic_notice_floor_bytes = 0, "
                        "traffic_notice_last_at = (NOW() AT TIME ZONE 'UTC') - interval '1 day' "
                        "WHERE telegram_id = 990403")
    yield p
    async with p.acquire() as c:
        await c.execute("DELETE FROM subscriptions WHERE telegram_id = ANY($1::bigint[])", ids)
        await c.execute("DELETE FROM traffic_purchases WHERE telegram_id = ANY($1::bigint[])", ids)
        await c.execute("DELETE FROM users WHERE telegram_id = ANY($1::bigint[])", ids)
    await p.close()


async def _watched(ids=USERS):
    return {r["telegram_id"] for r in await traffic.get_traffic_watch_users() if r["telegram_id"] in ids}


async def _old_selection(pool):
    async with pool.acquire() as c:
        return {r["telegram_id"] for r in await c.fetch(
            "SELECT telegram_id FROM subscriptions WHERE status = 'active' "
            "AND remnawave_uuid IS NOT NULL AND remnawave_uuid != '' AND telegram_id = ANY($1::bigint[])",
            list(USERS))}


async def test_selection_is_a_subset_of_the_old_one(pool):
    watched = await _watched()
    assert watched == {990_401, 990_402}
    assert watched <= await _old_selection(pool)          # never more panel GETs than before
    assert 990_406 not in watched and 990_404 not in watched


async def test_exhausted_user_comes_back_after_a_grant(pool):
    assert 990_403 not in await _watched()
    await traffic.reset_traffic_notification_flags(990_403)      # every GB grant path calls it
    assert 990_403 in await _watched()


async def test_exhausted_user_comes_back_after_buying_a_pack(pool):
    async with pool.acquire() as c:
        await c.execute("INSERT INTO traffic_purchases (telegram_id, gb_amount, price_rub) VALUES (990403, 10, 99)")
    assert 990_403 in await _watched()


async def test_notice_state_is_compare_and_set(pool):
    st = await traffic.get_traffic_notice_state(990_401)
    assert st["floor"] is None and st["last_at"] is None
    now = datetime.now(timezone.utc).replace(microsecond=0)
    assert await traffic.claim_traffic_notice_state(990_401, None, None, 5 * 1024 ** 3, now) is True
    assert await traffic.claim_traffic_notice_state(990_401, None, None, 3 * 1024 ** 3, now) is False
    st = await traffic.get_traffic_notice_state(990_401)
    assert st["floor"] == 5 * 1024 ** 3 and st["last_at"] == now
    assert await traffic.claim_traffic_notice_state(990_401, 5 * 1024 ** 3, now, 3 * 1024 ** 3, now) is True
