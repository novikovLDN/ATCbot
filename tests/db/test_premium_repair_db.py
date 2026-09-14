"""Premium over-issuance repair — the SQL half on a migrated Postgres.

load_repair_inputs (subscription row + approved payments) and
record_premium_repair (log row + shortening a leaked DB date: only shortens,
never a bypass-only row, never a sane row), plus one repair end to end with
the 3.4.3 HTTP panel fake.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")
asyncpg = pytest.importorskip("asyncpg")

from database import reconciliation as recon  # noqa: E402
from database.core import _from_db_utc, _to_db_utc  # noqa: E402

TGS = [990_301, 990_302, 990_303, 990_304, 990_305]
NOW = datetime.now(timezone.utc)
FAR = (NOW + timedelta(days=3650)).replace(microsecond=0)


@pytest.fixture
async def pool(monkeypatch):
    from urllib.parse import urlparse
    if not re.search(r"test|dash|ci", urlparse(URL).path.lstrip("/")):
        pytest.skip("not a test DB")
    # The bot runs in UTC (Railway): asyncpg reads a naive value bound to a
    # TIMESTAMPTZ column as process-local time, so pin the process TZ like prod.
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    p = await asyncpg.create_pool(URL, min_size=1, max_size=4, server_settings={"timezone": "UTC"})

    async def get_pool():
        return p
    monkeypatch.setattr(recon, "get_pool", get_pool)

    async def clean():
        async with p.acquire() as c:
            for t in ("subscriptions", "payments", "subscription_reconciliation_log"):
                await c.execute(f"DELETE FROM {t} WHERE telegram_id = ANY($1::bigint[])", TGS)  # noqa: S608
    async with p.acquire() as c:
        # Added by database.core.init_db, not by a migration.
        await c.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_bypass_only BOOLEAN DEFAULT FALSE")
    await clean()
    yield p
    await clean()
    await p.close()
    if old_tz is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old_tz
    time.tzset()


async def add_sub(p, tg, exp, *, status="active", source="payment", bypass=False, admin=None):
    async with p.acquire() as c:
        await c.execute(
            """INSERT INTO subscriptions (telegram_id, expires_at, status, source, is_bypass_only, admin_grant_days)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            tg, _to_db_utc(exp), status, source, bypass, admin,
        )


async def add_pay(p, tg, tariff, paid_at, *, status="approved"):
    async with p.acquire() as c:
        return await c.fetchval(
            """INSERT INTO payments (telegram_id, tariff, amount, status, paid_at, created_at)
               VALUES ($1, $2, 10000, $3, $4, $4) RETURNING id""",
            tg, tariff, status, _to_db_utc(paid_at),
        )


async def db_exp(p, tg):
    async with p.acquire() as c:
        return _from_db_utc(await c.fetchval("SELECT expires_at FROM subscriptions WHERE telegram_id=$1", tg))


async def test_load_repair_inputs_reads_the_row_and_approved_payments(pool):
    await add_sub(pool, 990_301, FAR, admin=5)
    year = await add_pay(pool, 990_301, "plus_365", NOW - timedelta(days=30))
    await add_pay(pool, 990_301, "basic_30", NOW - timedelta(days=5), status="pending")
    await add_pay(pool, 990_301, "traffic_10", NOW - timedelta(days=3))
    month = await add_pay(pool, 990_302, "basic_30", NOW - timedelta(days=2))     # no subscriptions row
    got = await recon.load_repair_inputs([990_301, 990_302])
    a, b = got[990_301], got[990_302]
    assert (a["db_row"], a["db_status"], a["db_is_bypass_only"], a["admin_grant_days"]) == (True, "active", False, 5)
    assert a["db_expires_at"] == FAR
    assert (a["approved_payments"], a["counted_payments"], a["proof_payment_ids"]) == (2, 1, [year])
    assert (b["db_row"], b["db_expires_at"], b["proof_payment_ids"]) == (False, None, [month])
    d = recon.compute_repair_target(a, now=NOW, panel_expires_at=FAR)
    assert d["db_leaked"] is True and d["by_db"] is None and d["target_source"] == "purchases"


async def _record(tg, new, *, shorten=True):
    return await recon.record_premium_repair(
        tg, old_expires_at=FAR, new_expires_at=new, shorten_db=shorten, reason="t",
        proof_payment_ids=[1, 2], total_paid_days=395, admin_grant_days=0, admin_telegram_id=None, now=NOW,
    )


async def test_record_shortens_only_a_leaked_non_bypass_row_and_logs(pool):
    new = (NOW + timedelta(days=30)).replace(microsecond=0)
    sane = (NOW + timedelta(days=100)).replace(microsecond=0)
    await add_sub(pool, 990_301, FAR)                                   # leaked
    await add_sub(pool, 990_302, FAR, source="bypass_only")             # bypass-only by source
    await add_sub(pool, 990_303, FAR, bypass=True)                      # bypass-only flag
    await add_sub(pool, 990_304, sane)                                  # sane
    await add_sub(pool, 990_305, FAR)                                   # asked not to shorten
    r1 = await _record(990_301, new)
    assert r1["db_shortened"] is True and await db_exp(pool, 990_301) == new
    for tg in (990_302, 990_303):
        assert (await _record(tg, new))["db_shortened"] is False and await db_exp(pool, tg) == FAR
    assert (await _record(990_304, NOW + timedelta(days=1)))["db_shortened"] is False
    assert await db_exp(pool, 990_304) == sane
    assert (await _record(990_305, new, shorten=False))["db_shortened"] is False
    assert await db_exp(pool, 990_305) == FAR
    async with pool.acquire() as c:
        log = await c.fetchrow("SELECT * FROM subscription_reconciliation_log WHERE id=$1", r1["log_id"])
        n = await c.fetchval("SELECT count(*) FROM subscription_reconciliation_log WHERE telegram_id = ANY($1::bigint[])", TGS)
    assert n == 5
    assert log["proof_payment_ids"] == [1, 2] and log["total_paid_days"] == 395
    assert log["new_days_from_now"] in (29, 30) and "leaked date shortened" in log["reason"]


async def test_record_never_lengthens_a_row(pool):
    await add_sub(pool, 990_301, FAR)
    assert (await _record(990_301, FAR + timedelta(days=1)))["db_shortened"] is False
    assert await db_exp(pool, 990_301) == FAR


async def test_repair_end_to_end_on_the_panel_fake(pool, monkeypatch):
    from tests.fakes.remnawave_http import GIB, FakeRemnawaveHTTP
    http = FakeRemnawaveHTTP().install(monkeypatch)
    ent = http.seed_premium(990_301, FAR)
    http.seed_bypass(990_301, 3 * GIB)
    paid = NOW - timedelta(days=30)
    await add_sub(pool, 990_301, FAR)
    await add_pay(pool, 990_301, "plus_365", paid)
    res = await recon.repair_premium_entity(
        990_301, panel_id=ent["id"], panel_username="tg_990301_premium",
        panel_expires_at=FAR, reason="bulk script test",
    )
    assert res["action"] == "fixed" and res["db_shortened"] is True
    target = res["target"]
    assert abs((http.premium_expire(990_301) - target).total_seconds()) < 1
    assert await db_exp(pool, 990_301) == target.replace(tzinfo=timezone.utc)
    assert http.bypass(990_301)["expireAt"].year == 2099 and http.bypass_limit(990_301) == 3 * GIB
