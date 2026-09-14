"""Owner decision 2026-09-14: SBP top-up with a markup credits ONLY the base
amount. Real finalize_purchase on a migrated Postgres.

- row with credit_kopecks (SBP + markup): balance +base, payments.amount and
  pending price_kopecks = what was charged (revenue = money paid);
- legacy row (credit_kopecks NULL): min(paid, price) exactly as before — a
  provider commission overpayment is still not credited.
"""
from __future__ import annotations

import os
import re

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")
asyncpg = pytest.importorskip("asyncpg")

import database.core as core_db  # noqa: E402
import database.subscriptions as subs_db  # noqa: E402
import database.users as users_db  # noqa: E402

TG = 990_101


@pytest.fixture
async def pool(monkeypatch):
    from urllib.parse import urlparse
    if not re.search(r"test|dash|ci", urlparse(URL).path.lstrip("/")):
        pytest.skip("not a test DB")
    p = await asyncpg.create_pool(URL, min_size=1, max_size=4, server_settings={"timezone": "UTC"})

    async def get_pool():
        return p
    for mod in (core_db, subs_db, users_db):
        monkeypatch.setattr(mod, "get_pool", get_pool)
    monkeypatch.setattr(core_db, "DB_READY", True)   # increase_balance refuses to run otherwise
    async with p.acquire() as c:
        await c.execute("INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING", TG)
    yield p
    async with p.acquire() as c:
        await c.execute("DELETE FROM pending_purchases WHERE telegram_id = $1", TG)
        await c.execute("DELETE FROM payments WHERE telegram_id = $1", TG)
    await p.close()


async def _balance(pool) -> float:
    return float(await users_db.get_user_balance(TG))


async def _payment_amount(pool) -> int:
    async with pool.acquire() as c:
        return await c.fetchval(
            "SELECT amount FROM payments WHERE telegram_id=$1 AND tariff='balance_topup' ORDER BY id DESC LIMIT 1", TG,
        )


async def test_sbp_markup_is_not_credited(pool):
    before = await _balance(pool)
    pid = await subs_db.create_pending_balance_topup_purchase(TG, 11100, credit_kopecks=10000)
    out = await subs_db.finalize_purchase(purchase_id=pid, payment_provider="platega", amount_rubles=111.0)
    assert out["success"] is True
    assert await _balance(pool) - before == pytest.approx(100.0)       # only the base amount
    assert await _payment_amount(pool) == 11100                        # what was actually paid
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT price_kopecks FROM pending_purchases WHERE purchase_id=$1", pid) == 11100


async def test_legacy_topup_credit_is_unchanged(pool):
    before = await _balance(pool)
    pid = await subs_db.create_pending_balance_topup_purchase(TG, 10000)
    await subs_db.finalize_purchase(purchase_id=pid, payment_provider="wata", amount_rubles=102.0)
    assert await _balance(pool) - before == pytest.approx(100.0)       # commission not credited
    assert await _payment_amount(pool) == 10000


async def test_underpayment_with_markup_is_still_rejected(pool):
    before = await _balance(pool)
    pid = await subs_db.create_pending_balance_topup_purchase(TG, 11100, credit_kopecks=10000)
    with pytest.raises(ValueError):
        await subs_db.finalize_purchase(purchase_id=pid, payment_provider="platega", amount_rubles=100.0)
    assert await _balance(pool) == pytest.approx(before)
