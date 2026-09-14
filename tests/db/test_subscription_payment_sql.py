"""get_last_subscription_payment against a real Postgres (auto-renewal tariff source).

Auto-renewal took the tariff / period from the last approved payment of ANY kind:
a top-up, gift, GB pack or farm shield was parsed as «basic, 30 days». The query
now keeps only subscription purchase / renewal tariffs (`tariff ~ $2`, the regex
is database.subscriptions.SUBSCRIPTION_PAYMENT_TARIFF_RE). This checks the SQL
and the regex in Postgres itself (the unit fakes only reuse the pattern).

Runs only when DASHBOARD_TEST_DATABASE_URL points at a MIGRATED, DISPOSABLE
database. Everything happens in one transaction that is rolled back.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")

asyncpg = pytest.importorskip("asyncpg")

from database import subscriptions as subs  # noqa: E402

TG = 9_100_001
T0 = datetime(2026, 9, 1, 12, 0)          # naive UTC (TIMESTAMP WITHOUT TIME ZONE)


@pytest.fixture
async def conn():
    from urllib.parse import urlparse
    dbname = urlparse(URL).path.lstrip("/")
    if not re.search(r"test|dash|ci", dbname):
        pytest.skip(f"refusing to write to a database not named like a test DB: {dbname!r}")
    c = await asyncpg.connect(URL, server_settings={"timezone": "UTC"})
    tx = c.transaction()
    await tx.start()
    try:
        await c.execute("INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING", TG)
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def _pay(c, tariff, day, status="approved"):
    await c.execute(
        "INSERT INTO payments (telegram_id, tariff, amount, status, created_at) VALUES ($1, $2, $3, $4, $5)",
        TG, tariff, 100, status, T0 + timedelta(days=day),
    )


async def test_newer_non_subscription_payments_are_skipped(conn):
    await _pay(conn, "basic_30", 0)
    await _pay(conn, "plus_90", 1)
    for day, tariff in enumerate(("balance_topup", "gift_basic_30", "traffic_15gb", "bypass_10gb",
                                  "farm_storm_shield", "apple_id_usa_10"), start=2):
        await _pay(conn, tariff, day)
    await _pay(conn, "basic_30", 20, status="pending")

    row = await subs.get_last_subscription_payment(TG, conn=conn)

    assert row["tariff"] == "plus_90"


@pytest.mark.parametrize("tariff", ["basic_30", "plus_365", "biz_team_30", "3"])
async def test_every_subscription_tariff_format_counts(conn, tariff):
    await _pay(conn, "plus_90", 0)
    await _pay(conn, tariff, 1)
    await _pay(conn, "balance_topup", 2)

    row = await subs.get_last_subscription_payment(TG, conn=conn)

    assert row["tariff"] == tariff


async def test_no_subscription_payment_at_all(conn):
    await _pay(conn, "balance_topup", 0)
    await _pay(conn, "gift_plus_90", 1)

    assert await subs.get_last_subscription_payment(TG, conn=conn) is None
