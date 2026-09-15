"""Broadcast segments of database/segments.py on real Postgres: every new SQL
branch against one seeded population, with the exact expected members.

Runs only with DASHBOARD_TEST_DATABASE_URL (a migrated, disposable DB). Rows
live in a private telegram_id range and are deleted before and after.
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

UTC = timezone.utc
BASE = 880_000
TABLES = ("referrals", "payments", "subscription_history", "subscriptions", "users")

# people (telegram_id = BASE + n)
A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P = (BASE + n for n in range(1, 17))  # noqa: E741
R, S, T, U, V, W, X, Y = (BASE + n for n in range(18, 26))
OURS = {A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P, R, S, T, U, V, W, X, Y}


def ago(days: float) -> datetime:
    return (datetime.now(UTC) - timedelta(days=days)).replace(tzinfo=None)


def ahead(days: float) -> datetime:
    return ago(-days)


async def _clean(conn):
    lo, hi = BASE, BASE + 999
    await conn.execute("DELETE FROM referrals WHERE referrer_user_id BETWEEN $1 AND $2 "
                       "OR referred_user_id BETWEEN $1 AND $2", lo, hi)
    for t in TABLES[1:]:
        await conn.execute(f"DELETE FROM {t} WHERE telegram_id BETWEEN $1 AND $2", lo, hi)  # noqa: S608 — fixed names


async def _seed(conn):
    async def user(tg, created, *, seen=None, trial=None, reachable=True, lang="ru"):
        trial_used, trial_exp = trial or (None, None)
        await conn.execute(
            "INSERT INTO users (telegram_id, created_at, last_seen_at, trial_used_at, trial_expires_at, "
            "is_reachable, language) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            tg, ago(created), seen and ago(seen), trial_used and ago(trial_used),
            trial_exp is not None and ago(trial_exp) or None, reachable, lang)

    async def sub(tg, expires, *, source="payment", status="active", bypass=False, auto=False):
        await conn.execute(
            "INSERT INTO subscriptions (telegram_id, expires_at, status, source, is_bypass_only, auto_renew, "
            "remnawave_uuid) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            tg, expires, status, source, bypass, auto, f"rw-{tg}" if bypass else None)

    async def hist(tg, action, start, end):
        await conn.execute(
            "INSERT INTO subscription_history (telegram_id, vpn_key, start_date, end_date, action_type) "
            "VALUES ($1, 'k', $2, $3, $4)", tg, start, end, action)

    async def pay(tg, tariff, at, status="approved"):
        await conn.execute(
            "INSERT INTO payments (telegram_id, tariff, amount, status, created_at) VALUES ($1, $2, 19900, $3, $4)",
            tg, tariff, status, ago(at))

    bypass_row = dict(source="bypass_only", bypass=True)

    # A: paid, ended 10 days ago, not renewed
    await user(A, 100, seen=5)
    await sub(A, ago(10), status="expired")
    await hist(A, "purchase", ago(40), ago(10))
    await pay(A, "basic_30", 40)
    # B: paid twice, renewed → active until +20 days, auto-renew on
    await user(B, 100, seen=0.05)
    await sub(B, ahead(20), auto=True)
    await hist(B, "purchase", ago(40), ago(10))
    await hist(B, "renewal", ago(10), ahead(20))
    await pay(B, "basic_30", 40)
    await pay(B, "basic_30", 11)
    # C: paid Plus ended 5 days ago → bypass-only now
    await user(C, 100, seen=2)
    await sub(C, ahead(3650), **bypass_row)
    await hist(C, "purchase", ago(35), ago(5))
    await pay(C, "plus_30", 35)
    # D: paid, ended 200 days ago, not seen for 150 days
    await user(D, 400, seen=150)
    await sub(D, ago(200), status="expired")
    await hist(D, "purchase", ago(230), ago(200))
    await pay(D, "basic_30", 230)
    # E: trial ended 10 days ago, never paid
    await user(E, 14, seen=9, trial=(13, 10))
    await sub(E, ago(10), source="trial", status="expired")
    await hist(E, "trial", ago(13), ago(10))
    # F: bought GB (bypass-purchase gift = trial), trial ended 4 days ago, bypass-only now
    await user(F, 10, seen=1, trial=(7, 4))
    await sub(F, ahead(3650), **bypass_row)
    await hist(F, "trial", ago(7), ago(4))
    await pay(F, "bypass_15gb", 7)
    # G: trial long ago, then paid; paid ended 3 days ago
    await user(G, 60, seen=3, trial=(53, 50))
    await sub(G, ago(3), status="expired")
    await hist(G, "trial", ago(53), ago(50))
    await hist(G, "purchase", ago(33), ago(3))
    await pay(G, "basic_30", 33)
    # H: gift active
    await user(H, 30, seen=1)
    await sub(H, ahead(10), source="gift")
    await hist(H, "gift", ago(20), ahead(10))
    # I: gift ended 8 days ago, never paid
    await user(I, 60, seen=8)
    await sub(I, ago(8), source="gift", status="expired")
    await hist(I, "gift", ago(38), ago(8))
    # J: admin days ended 15 days ago
    await user(J, 60, seen=15)
    await sub(J, ago(15), source="admin", status="expired")
    await hist(J, "admin_grant", ago(45), ago(15))
    # K: admin days active
    await user(K, 60, seen=1)
    await sub(K, ahead(5), source="admin")
    await hist(K, "admin_grant", ago(25), ahead(5))
    # L: /start 2 days ago, nothing since; came by W's link
    await user(L, 2)
    # M: trial running (ends in 2 days)
    await user(M, 3, seen=0.05, trial=(1, -2))
    await sub(M, ahead(2), source="trial")
    await hist(M, "trial", ago(1), ahead(2))
    # N: bought 2 days ago, auto-renew off; came by V's link
    await user(N, 5, seen=0.05)
    await sub(N, ahead(28))
    await hist(N, "purchase", ago(2), ahead(28))
    await pay(N, "basic_30", 2)
    # O / P: paid, ends in 3 days — auto-renew off / on
    await user(O, 100, seen=1)
    await sub(O, ahead(3))
    await hist(O, "purchase", ago(27), ahead(3))
    await pay(O, "basic_30", 27)
    await user(P, 100, seen=1)
    await sub(P, ahead(3), auto=True)
    await hist(P, "purchase", ago(27), ahead(3))
    await pay(P, "plus_30", 27)
    # R: not seen for 40 days; S: never seen, started 400 days ago; T: seen an hour ago
    await user(R, 100, seen=40)
    await user(S, 400)
    await user(T, 100, seen=0.05)
    # U: like A but blocked the bot
    await user(U, 100, seen=5, reachable=False)
    await sub(U, ago(10), status="expired")
    await hist(U, "purchase", ago(40), ago(10))
    await pay(U, "basic_30", 40)
    # V / W: referrers of N (paid) and L (never paid)
    await user(V, 100, seen=0.05)
    await user(W, 100, seen=0.05)
    await conn.execute("INSERT INTO referrals (referrer_user_id, referred_user_id) VALUES ($1, $2), ($3, $4)",
                       V, N, W, L)
    # X: English
    await user(X, 100, seen=0.05, lang="en")
    # Y: auto-renewed (row source 'auto_renew'), paid twice, ends in 40 days
    await user(Y, 100, seen=1)
    await sub(Y, ahead(40), source="auto_renew", auto=True)
    await hist(Y, "purchase", ago(70), ago(10))
    await hist(Y, "auto_renew", ago(10), ahead(40))
    await pay(Y, "basic_30", 70)
    await pay(Y, "basic_30", 10)
    # V has 100 ₽ on the balance (users.balance is kopecks), W 49 ₽
    await conn.execute("UPDATE users SET balance = 10000 WHERE telegram_id = $1", V)
    await conn.execute("UPDATE users SET balance = 4900 WHERE telegram_id = $1", W)
    # Noise that must never count: a pending payment and a top-up
    await pay(L, "basic_30", 1, status="pending")
    await pay(T, "balance_topup", 1)


@pytest.fixture
async def pool(monkeypatch):
    from urllib.parse import urlparse
    if not re.search(r"test|dash|ci", urlparse(URL).path.lstrip("/")):
        pytest.skip("not a test DB")
    p = await asyncpg.create_pool(URL, min_size=1, max_size=3, server_settings={"timezone": "UTC"})

    async def get_pool():
        return p
    monkeypatch.setattr(admin_db, "get_pool", get_pool)
    async with p.acquire() as c:
        await _clean(c)
        await _seed(c)
    yield p
    async with p.acquire() as c:
        await _clean(c)
    await p.close()


EXPECTED = {
    # ── new fixed ──
    "bypass_only_now": {C, F},
    "trial_ended_bypass": {F},
    "gb_only_buyers": {F},
    "gift_active": {H},
    "granted_active": {K},
    "paid_once": {A, C, D, G, N, O, P, U},
    "paid_loyal": {B, Y},
    "autorenew_on": {B, P, Y},
    "autorenew_off": {N, O},
    "referrers_paid": {V},
    "referred_never_paid": {L},
    "lang_en": {X},
    # ── parametric ──
    "paid_ended:30d": {A, C, G, U},
    "paid_ended:7d": {C, G},
    "paid_ended:6m": {A, C, G, U},
    "paid_ended:7m": {A, C, D, G, U},
    "paid_ended:any": {A, C, D, G, U},
    "grant_ended:30d": {I, J},
    "grant_ended:10d": {I},
    "trial_ended:30d": {E, F},
    "trial_ended:7d": {F},
    "trial_ended:any": {E, F},
    "any_ended:30d": {A, C, E, F, G, I, J, U},
    "any_ended:7d": {C, F, G},
    "cold_start:7d": {L},
    "cold_start:1d": set(),
    "cold_start:any": {L, R, S, V, W, X},          # T topped up the balance → not cold
    "bought_sub:7d": {N},
    "bought_sub:30d": {B, N, O, P, Y},
    "bought_sub:1m": {B, N, O, P, Y},
    "premium_ended_bypass:30d": {C, F},
    "premium_ended_bypass:3d": set(),
    "premium_ended_bypass:any": {C, F},
    "inactive:30d": {D, R, S},
    "inactive:6m": {S},
    "paid_expiring:7d": {O, P},
    "paid_expiring_manual:7d": {O},
    "paid_expiring:30d": {B, N, O, P},
    "paid_expiring:2m": {B, N, O, P, Y},
    "paid_expiring_manual:30d": {N, O},
    # legacy key fixed on the way (users.balance, not the missing balance_kopecks)
    "has_balance_50plus": {V},
}


async def test_count_equals_the_send_list_for_every_key(pool):
    """GET /segments counts with COUNT(*) in SQL; the number must be the size of
    the list a broadcast is sent to (unreachable users dropped)."""
    for key in EXPECTED:
        assert await admin_db.count_users_by_segment(key) == len(await admin_db.get_users_by_segment(key)), key


@pytest.mark.parametrize("key", list(EXPECTED))
async def test_members(pool, key):
    got = set(await admin_db._segment_user_ids(key)) & OURS
    assert got == EXPECTED[key], (key, sorted(n - BASE for n in got), sorted(n - BASE for n in EXPECTED[key]))


async def test_unreachable_users_are_dropped_from_the_send_list(pool):
    assert U in set(await admin_db._segment_user_ids("paid_ended:30d"))
    assert set(await admin_db.get_users_by_segment("paid_ended:30d")) & OURS == {A, C, G}


async def test_quick_action_audience_matches_the_legacy_7_day_segment(pool):
    """paid_expiring:7d ⊇ the old fixed paid_expires_in_7d (it also counts rows
    auto-renewed by the worker, source='auto_renew')."""
    legacy = set(await admin_db._segment_user_ids("paid_expires_in_7d")) & OURS
    assert legacy == {O, P} == EXPECTED["paid_expiring:7d"]


async def test_bad_parametric_key_raises(pool):
    from database.segments import SegmentKeyError
    for key in ("paid_ended:0d", "paid_ended:3651d", "inactive:any", "nope:7d"):
        with pytest.raises(SegmentKeyError):
            await admin_db._segment_user_ids(key)
    # an unknown legacy key keeps the old behaviour (empty, logged)
    assert await admin_db._segment_user_ids("no_such_segment") == []
