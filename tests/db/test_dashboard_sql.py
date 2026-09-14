"""Dashboard metric SQL against a real Postgres, on one fixture dataset
with every number asserted exactly.

Runs only when DASHBOARD_TEST_DATABASE_URL points at a MIGRATED, DISPOSABLE
database (every test TRUNCATEs the tables it seeds). CI: the Migration
Integrity job runs this file against its freshly migrated postgres:16.
Locally:

    DASHBOARD_TEST_DATABASE_URL=postgresql://… pytest tests/db -q

The dataset is built around NOW = 2026-09-13 12:00 UTC (15:00 MSK):
  current 7-day window  [2026-09-06 21:00Z, NOW)       (7 Moscow days)
  previous 7-day window [2026-08-30 21:00Z, 2026-09-06 12:00Z)  (like-for-like)
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")

asyncpg = pytest.importorskip("asyncpg")

from database import metrics as mx  # noqa: E402
from database import revenue as rev  # noqa: E402
from database.readonly import read_conn  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
TABLES = (
    "users", "subscriptions", "pending_purchases", "payments", "balance_transactions",
    "payment_errors", "provisioning_jobs", "referral_rewards", "referrals", "broadcast_log",
    "broadcasts", "automated_notification_sends", "automated_notifications",
)


def T(s: str) -> datetime:
    """'09-10 10:00' → aware UTC 2026-09-10 10:00."""
    return datetime.strptime(f"2026-{s}", "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


def N(s: str) -> datetime:
    """Naive UTC for TIMESTAMP WITHOUT TIME ZONE columns."""
    return T(s).replace(tzinfo=None)


# telegram_id, created_at, balance, is_reachable, trial_used_at
USERS = (
    [(tg, "08-01 10:00", 0, tg not in (1011, 1012), None) for tg in range(1001, 1023)]
    + [
        (2001, "09-07 10:00", 0, True, "09-07 10:00"),
        (2002, "09-12 08:00", 0, True, "09-12 08:00"),
        (2003, "08-20 10:00", 0, True, "08-20 10:00"),
        (2004, "08-25 10:00", 0, True, "08-25 10:00"),
        (2005, "08-30 10:00", 0, True, "08-30 10:00"),
        (3001, "07-01 10:00", 30_000, True, None),
        (3002, "07-01 10:00", 10_000, True, None),
        (3003, "07-01 10:00", 0, True, None),
    ]
    + [(tg, "07-01 10:00", 0, True, None) for tg in range(3004, 3012)]
    + [
        (4001, "09-01 10:00", 0, True, None),
        (4002, "09-03 10:00", 0, True, None),
        (4003, "09-13 11:00", 0, True, None),
    ]
)

# Inserted in this (chronological) order: ids follow creation, as in prod.
# purchase_id, tg, tariff, price, status, created, expires_in_min, type, provider, is_combo
PURCHASES = [
    ("P9", 1002, "plus", 34_900, "paid", "08-15 10:00", 15, "subscription", "platega", False),
    ("P15", 1015, "basic", 19_900, "paid", "09-02 10:00", 15, "subscription", "platega", False),
    ("P7", 1007, None, 50_000, "paid", "09-08 10:00", 15, "balance_topup", "cryptobot", False),
    ("P8", 1008, None, 300_000, "paid", "09-08 11:00", 15, "steam", None, False),
    ("P5", 1005, "basic", 19_900, "paid", "09-09 12:00", 15, "gift", "platega", False),
    ("P10", 1010, "basic", 19_900, "paid", "09-09 13:00", 15, "subscription", "balance", False),
    ("P1", 1001, "basic", 19_900, "paid", "09-10 10:00", 15, "subscription", "platega", False),
    ("P2", 1002, "plus", 34_900, "paid", "09-11 10:00", 15, "subscription", "wata", False),
    ("P11", 1011, "basic", 19_900, "expired", "09-11 12:00", 15, "subscription", "platega", False),
    ("P3", 1003, "basic", 29_900, "paid", "09-12 09:00", 15, "subscription", "telegram", True),
    ("P4", 1004, "plus", 34_900, "paid", "09-12 11:00", 15, "subscription", "telegram_payment", False),
    ("P6", 1006, "traffic_15gb", 9_900, "paid", "09-12 12:00", 15, "traffic_pack", "platega", False),
    ("P12", 1012, "basic", 19_900, "pending", "09-13 08:00", 15, "subscription", "platega", False),
    ("P13", 1013, "basic", 19_900, "pending", "09-13 11:50", 30, "subscription", "wata", False),
]

# tg, tariff, amount, created, purchase_id, paid_at
PAYMENTS = [
    (3002, "plus_90", 89_900, "06-15 10:00", None, None),
    (1002, "plus_30", 34_900, "08-15 10:05", "P9", "08-15 10:05"),
    (3001, "basic_30", 19_900, "08-15 12:00", None, None),
    (3003, "basic_30", 19_900, "08-18 12:00", None, None),
    (1004, "plus_30", 34_900, "08-20 12:00", None, None),        # earlier balance purchase
    (2003, "basic_30", 19_900, "08-25 10:00", None, None),       # trial 2003 → paid after 5 d
    (3003, "traffic_15gb", 9_900, "09-01 12:00", None, None),    # latest approved = a pack
    (2004, "plus_30", 34_900, "09-05 10:00", None, None),        # trial 2004 → paid after 11 d
    (2001, "basic_30", 19_900, "09-08 10:00", None, None),       # trial 2001 → paid after 1 d
    (1001, "basic_30", 19_900, "09-10 10:03", "P1", "09-10 10:03"),
    (1002, "plus_30", 34_900, "09-11 10:10", "P2", "09-11 10:10"),
    (1003, "basic_30", 29_900, "09-12 09:01", "P3", "09-12 09:01"),
    (1004, "plus_30", 34_900, "09-12 11:02", "P4", "09-12 11:02"),
]

# user_id, amount, type, source, created
BALANCE_TX = [
    (1020, 70_000, "topup", "telegram", "09-09 10:00"),
    (1010, -19_900, "subscription_payment", "balance", "09-09 13:00"),
    (1021, -34_900, "subscription_payment", "auto_renew", "09-10 10:00"),
    (1022, 10_000, "topup", "telegram_stars", "09-11 00:00"),
]

# tg, source, status, expires, auto_renew, type, combo, bypass_only, uuid, activation, last_reminder
SUBS = [
    (3001, "payment", "active", "09-15 12:00", True, "basic", False, False, "u1", "pending", "09-12 10:00"),
    (3002, "payment", "active", "09-17 12:00", True, "plus", False, False, "u2", "failed", "09-01 10:00"),
    (3003, "payment", "active", "09-18 12:00", True, "basic", True, False, "u3", "active", None),
    (3004, "payment", "active", "09-16 12:00", False, "basic", False, False, "u4", "active", "09-10 10:00"),
    (3005, "trial", "active", "09-14 12:00", False, "basic", False, False, "u5", "active", None),
    (3006, "admin", "active", "09-19 12:00", True, "plus", False, False, None, "active", None),
    (3007, "payment", "active", "10-30 12:00", False, "plus", False, False, "u7", "active", None),
    (3008, "payment", "active", "10-01 12:00", False, "basic", False, True, "u8", "active", None),
    (3009, "referral", "active", "10-10 12:00", False, "basic", False, False, "u9", "active", None),
    (3010, "promo_link", "active", "10-10 12:00", False, "basic", False, False, "u10", "active", None),
    (3011, "payment", "expired", "09-01 12:00", False, "basic", False, False, "u11", "active", None),
]

# stage, provider, error_code, message, created
ERRORS = [
    ("amount_mismatch", "wata", None, "amount differs", "09-10 10:00"),
    ("provisioning", None, "dead", "DELIVERY_MISMATCH premium expireAt", "09-13 08:00"),
    ("telegram_gift_failed", "telegram_stars", None, "gift failed", "09-13 09:00"),
    ("amount_mismatch", "wata", None, "amount differs", "09-13 10:00"),
    ("amount_mismatch", "wata", None, "amount differs", "09-13 11:00"),
]

# key, tg, status, attempts, created, updated, done_at
JOBS = [
    ("purchase:J6", 1001, "done", 1, "09-11 09:00", "09-11 09:01", "09-11 09:01"),
    ("purchase:J7", 1002, "dead", 5, "09-10 09:00", "09-10 10:00", None),
    ("purchase:J5", 1003, "done", 1, "09-13 05:59", "09-13 06:00", "09-13 06:00"),
    ("purchase:J4", 1004, "dead", 5, "09-13 09:00", "09-13 10:00", None),
    ("purchase:J2", 1005, "pending", 2, "09-13 11:00", "09-13 11:30", None),
    ("purchase:J1", 1006, "pending", 0, "09-13 11:50", "09-13 11:50", None),
    ("purchase:J3", 1007, "running", 1, "09-13 11:55", "09-13 11:56", None),
]


async def _seed(conn) -> None:
    await conn.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
    for tg, created, bal, reach, trial in USERS:
        await conn.execute(
            "INSERT INTO users (telegram_id, created_at, balance, is_reachable, trial_used_at) "
            "VALUES ($1, $2, $3, $4, $5)",
            tg, T(created), bal, reach, N(trial) if trial else None,
        )
    for pid, tg, tariff, price, status, created, ttl, ptype, prov, combo in PURCHASES:
        await conn.execute(
            "INSERT INTO pending_purchases (purchase_id, telegram_id, tariff, period_days, price_kopecks, "
            "status, created_at, expires_at, purchase_type, payment_provider, is_combo) "
            "VALUES ($1, $2, $3, 30, $4, $5, $6, $7, $8, $9, $10)",
            pid, tg, tariff, price, status, T(created), T(created) + timedelta(minutes=ttl), ptype, prov, combo,
        )
    for tg, tariff, amount, created, pid, paid in PAYMENTS:
        await conn.execute(
            "INSERT INTO payments (telegram_id, tariff, amount, status, created_at, purchase_id, paid_at) "
            "VALUES ($1, $2, $3, 'approved', $4, $5, $6)",
            tg, tariff, amount, T(created), pid, T(paid) if paid else None,
        )
    for uid, amount, ttype, src, created in BALANCE_TX:
        await conn.execute(
            "INSERT INTO balance_transactions (user_id, amount, type, source, created_at) VALUES ($1, $2, $3, $4, $5)",
            uid, amount, ttype, src, T(created),
        )
    for tg, src, status, exp, auto, stype, combo, bypass, uuid, act, rem in SUBS:
        await conn.execute(
            "INSERT INTO subscriptions (telegram_id, source, status, expires_at, auto_renew, subscription_type, "
            "is_combo, is_bypass_only, uuid, activation_status, last_reminder_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
            tg, src, status, T(exp), auto, stype, combo, bypass, uuid, act, T(rem) if rem else None,
        )
    for stage, prov, code, msg, created in ERRORS:
        await conn.execute(
            "INSERT INTO payment_errors (stage, payment_provider, error_code, error_message, created_at) "
            "VALUES ($1, $2, $3, $4, $5)",
            stage, prov, code, msg, T(created),
        )
    for key, tg, status, attempts, created, updated, done in JOBS:
        await conn.execute(
            "INSERT INTO provisioning_jobs (idempotency_key, telegram_id, source, tariff_key, status, attempts, "
            "created_at, updated_at, done_at, last_error) VALUES ($1, $2, 'purchase', 'basic', $3, $4, $5, $6, $7, $8)",
            key, tg, status, attempts, N(created), N(updated), N(done) if done else None,
            "panel 502" if status == "dead" else None,
        )
    for key, title, cat in (("k.one", "One", "trial"), ("k.two", "Two", "subscription")):
        await conn.execute(
            "INSERT INTO automated_notifications (key, title, category, default_text_ru) VALUES ($1, $2, $3, 'x')",
            key, title, cat,
        )
    for key, status, at in (
        ("k.one", "sent", "08-30 10:00"), ("k.one", "sent", "09-08 10:00"), ("k.one", "sent", "09-09 10:00"),
        ("k.one", "sent", "09-10 10:00"), ("k.one", "failed", "09-10 11:00"), ("k.one", "blocked", "09-11 10:00"),
        ("k.two", "skipped_disabled", "09-12 10:00"), ("k.two", "skipped_disabled", "09-12 11:00"),
    ):
        await conn.execute(
            "INSERT INTO automated_notification_sends (key, telegram_id, status, sent_at) VALUES ($1, 1001, $2, $3)",
            key, status, T(at),
        )
    b_old = await conn.fetchval(
        "INSERT INTO broadcasts (title, type, segment, sent_by, created_at) "
        "VALUES ('old', 'text', 'all', 1, $1) RETURNING id", T("08-20 10:00"))
    b_new = await conn.fetchval(
        "INSERT INTO broadcasts (title, type, segment, sent_by, created_at) "
        "VALUES ('new', 'text', 'all', 1, $1) RETURNING id", T("09-10 10:00"))
    for bid, status, n in ((b_old, "sent", 5), (b_new, "sent", 3), (b_new, "deleted", 1), (b_new, "failed", 1)):
        for i in range(n):
            await conn.execute(
                "INSERT INTO broadcast_log (broadcast_id, telegram_id, status) VALUES ($1, $2, $3)",
                bid, 1000 + i, status,
            )
    for ref, created, paid in ((2001, "09-08 10:00", "09-08 12:00"), (2002, "09-12 10:00", None),
                               (2003, "08-20 10:00", "09-07 10:00")):
        await conn.execute(
            "INSERT INTO referrals (referrer_user_id, referred_user_id, created_at, first_paid_at) "
            "VALUES (1001, $1, $2, $3)",
            ref, N(created), N(paid) if paid else None,
        )
    await conn.execute(
        "INSERT INTO referral_rewards (referrer_id, buyer_id, purchase_id, purchase_amount, percent, "
        "reward_amount, created_at) VALUES (1001, 2001, 'PR1', 19900, 10, 1990, $1)",
        T("09-08 12:00"),
    )


@pytest.fixture
async def db(monkeypatch):
    import time
    from urllib.parse import urlparse

    dbname = urlparse(URL).path.lstrip("/")
    if not re.search(r"test|dash|ci", dbname):
        pytest.skip(f"refusing to TRUNCATE a database not named like a test DB: {dbname!r}")
    # The naive-UTC parameter convention (_to_db_utc) has two halves:
    # asyncpg converts a naive datetime bound to a TIMESTAMPTZ parameter
    # with the CLIENT's local zone, and TIMESTAMP columns are compared in
    # the SESSION zone. Production runs both in UTC (Railway container and
    # Postgres); a developer laptop may not — pin both.
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    # The repo passes naive-UTC parameters (_to_db_utc) and relies on a UTC
    # session (Railway, CI postgres:16). A local server may default to the
    # machine's zone, so pin it — exactly the production assumption.
    pool = await asyncpg.create_pool(URL, min_size=1, max_size=4, server_settings={"timezone": "UTC"})

    async def get_pool():
        return pool

    monkeypatch.setattr(rev, "get_pool", get_pool)
    monkeypatch.setattr(mx, "get_pool", get_pool)
    async with pool.acquire() as conn:
        await _seed(conn)
    yield pool
    await pool.close()
    if old_tz is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old_tz
    time.tzset()


W7 = rev.window(7, NOW)


def test_like_for_like_window():
    assert W7["since"] == T("09-06 21:00")
    assert W7["prev_since"] == T("08-30 21:00")
    assert W7["prev_until"] == T("09-06 12:00")


async def test_totals_current_window(db):
    t = await rev.totals(W7["since"], W7["until"])
    # subscriptions P1+P2+P3+P4 + gift P5 = 139 500; pack P6 = 9 900;
    # top-ups: cryptobot P7 50 000 + Telegram card 70 000 + Stars 10 000.
    assert t["by_class"]["subscription"] == {"count": 5, "kopecks": 139_500}
    assert t["by_class"]["traffic"] == {"count": 1, "kopecks": 9_900}
    assert t["by_class"]["topup"] == {"count": 3, "kopecks": 130_000}
    assert t["net_kopecks"] == 279_400 and t["net_count"] == 9
    # Shop is its own line; gross = everything that came from outside.
    assert t["shop_kopecks"] == 300_000
    assert t["gross_kopecks"] == 579_400
    # Purchase paid from the balance (P10) and the balance debits are NOT revenue.
    assert t["balance_funded"] == {"count": 1, "kopecks": 19_900}
    # Combo is its own product; gift is its own product.
    assert t["by_product"]["combo_basic"] == {"count": 1, "kopecks": 29_900}
    assert t["by_product"]["basic"] == {"count": 1, "kopecks": 19_900}
    assert t["by_product"]["plus"] == {"count": 2, "kopecks": 69_800}
    assert t["by_product"]["gift"] == {"count": 1, "kopecks": 19_900}
    assert t["traffic_gb_sold"] == 15
    # 'telegram' + 'telegram_payment' + Telegram card top-up = one provider.
    assert t["by_provider"]["telegram_payment"] == {"count": 3, "kopecks": 134_800}
    assert t["stars"] == {"count": 1, "kopecks": 10_000}


async def test_totals_previous_window_and_delta(db):
    prev = await rev.totals(W7["prev_since"], W7["prev_until"])
    assert prev["net_kopecks"] == 19_900  # P15 only
    assert rev.delta_pct(279_400, prev["net_kopecks"]) == 1304.0


async def test_balance_spend_not_double_counted(db):
    s = await rev.balance_spend(W7["since"], W7["until"])
    assert s == {"count": 2, "kopecks": 54_800, "auto_renew": {"count": 1, "kopecks": 34_900}}


async def test_payers(db):
    p = await rev.payers(W7["since"], W7["until"])
    # 1001-1007 via invoices, 1020/1022 via Telegram top-ups; 1002 paid before (P9).
    assert p["payers"] == 9 and p["new"] == 8 and p["returning"] == 1


async def test_provider_performance_counts_abandoned_invoices(db):
    rows = {r["provider"]: r for r in await rev.provider_performance(W7["since"], W7["until"], NOW)}
    pl = rows["platega"]
    assert (pl["created"], pl["paid"], pl["expired_marked"], pl["abandoned"], pl["pending"]) == (5, 3, 1, 1, 0)
    assert pl["expired"] == 2
    assert pl["success_rate"] == 60.0  # v3 said 75.0: the stale 'pending' P12 was ignored
    assert pl["conversion"] == 60.0
    assert pl["kopecks"] == 49_700
    wa = rows["wata"]
    assert (wa["created"], wa["paid"], wa["pending"], wa["expired"]) == (2, 1, 1, 0)
    assert wa["success_rate"] == 100.0 and wa["conversion"] == 50.0
    assert rows["telegram_payment"]["paid"] == 2  # 'telegram' merged, one row
    assert "telegram" not in rows and "balance" not in rows


async def test_subscription_motion_new_vs_renewal(db):
    m = await mx.subscription_motion(W7["since"], W7["until"])
    # P1 (1001) and P3 (1003) are first-ever; P2 renews P9, P4 follows a balance purchase.
    assert m["new"] == {"count": 2, "kopecks": 49_800, "users": 2}
    assert m["renewal"] == {"count": 2, "kopecks": 69_800}
    assert m["auto_renew_balance"] == {"count": 1, "kopecks": 34_900}
    assert m["new_share"] == 50.0


async def test_trial_conversion_is_cohort_correct(db):
    w30 = rev.window(30, NOW)
    t = await mx.trial_conversion(w30["since"], w30["until"], NOW)
    assert t["trials"] == 5
    assert t["matured_7d"] == 3          # 2003, 2004, 2005 started ≥ 7 days ago
    assert t["paid_7d"] == 2              # 2003 (5 d) and 2001 (1 d, not matured)
    assert t["paid_7d_matured"] == 1
    assert t["rate_7d"] == 33.3           # v3: paid_7d / trials = 40.0
    assert t["matured_30d"] == 0 and t["rate_30d"] is None
    assert t["paid_any"] == 3 and t["rate_any"] == 60.0


async def test_funnel_set_based(db):
    w30 = rev.window(30, NOW)
    f = await mx.funnel(w30["since"], w30["until"])
    steps = {s["key"]: s["users"] for s in f["steps"]}
    # 8 users started in 30 days; 5 took a trial; none opened an invoice;
    # 3 paid a subscription from the balance (2001, 2003, 2004).
    assert steps == {"started": 8, "trial": 5, "tariff_view": None, "invoiced": 0, "paid": 3}


async def test_active_subscriptions_kinds_and_grants(db):
    a = await mx.active_subscriptions(NOW)
    assert a["total"] == 10 and a["with_access"] == 9 and a["paid"] == 5
    assert a["by_kind"] == {"paid": 5, "gift": 0, "granted": 3, "trial": 1, "bypass_only": 1}
    assert a["granted_sources"] == {"admin": 1, "referral": 1, "promo_link": 1}
    assert a["auto_renew_share"] == 60.0
    assert a["expiring_7d"]["total"] == 6 and a["expiring_7d"]["auto_renew_on"] == 4


async def test_renewal_pipeline(db, monkeypatch):
    from app.services import provisioning_flags

    monkeypatch.setattr(provisioning_flags, "is_on", lambda ep: False)
    p = await mx.renewal_pipeline(NOW)
    assert p["expiring"] == 6 and p["auto_renew"] == 3 and p["manual"] == 3
    assert p["by_kind"] == {"paid": 4, "gift": 0, "granted": 1, "trial": 1}
    # 3001 basic_30 199 ₽ (covered by 300 ₽), 3002 plus_90 899 ₽ (100 ₽ on balance),
    # 3003 combo but last approved payment is a pack → basic 30 d, 199 ₽ with the outbox flag off.
    assert p["expected_list_kopecks"] == 129_700
    assert (p["covered"], p["covered_kopecks"]) == (1, 19_900)
    assert (p["not_covered"], p["shortfall_kopecks"]) == (2, 99_800)
    days = {d["date"]: d for d in p["by_day"]}
    assert days["2026-09-15"] == {"date": "2026-09-15", "total": 1, "auto_renew": 1}
    assert days["2026-09-19"] == {"date": "2026-09-19", "total": 1, "auto_renew": 0}  # admin, no uuid

    monkeypatch.setattr(provisioning_flags, "is_on", lambda ep: True)
    p = await mx.renewal_pipeline(NOW)
    assert p["expected_list_kopecks"] == 19_900 + 89_900 + 32_900  # combo_basic 30 d = 329 ₽


async def test_payments_health(db):
    h = await mx.payments_health(NOW)
    assert h["stuck"] == [{"provider": "platega", "count": 1, "still_valid": 0,
                           "oldest_at": T("09-13 08:00").isoformat(), "created_24h": 2}]
    ttp = {t["provider"]: t for t in h["time_to_pay"]}
    assert ttp[None] == {"provider": None, "count": 4, "p50_s": 150.0, "p90_s": 474.0}
    assert ttp["telegram_payment"]["p50_s"] == 90.0
    assert ttp["wata"]["p50_s"] == 600.0
    e = h["errors_24h"]
    assert e["total"] == 4 and e["telegram_money"] == 1
    assert e["by_stage"][0] == {"stage": "amount_mismatch", "count": 2}
    assert h["last_paid"]["platega"] == T("09-12 12:00").isoformat()
    assert h["last_paid"]["wata"] == T("09-11 10:10").isoformat()
    assert h["last_paid"]["telegram_payment"] == T("09-12 11:02").isoformat()
    assert h["last_paid"]["telegram_stars"] == T("09-11 00:00").isoformat()
    assert h["paid_7d"]["platega"] == 3


async def test_delivery_health(db):
    d = await mx.delivery_health(NOW)
    q = d["queue"]
    assert (q["pending_new"], q["retrying"], q["running"], q["dead"]) == (1, 1, 1, 2)
    assert (q["done_24h"], q["dead_24h"]) == (1, 1)
    assert q["oldest_open_age_s"] == 3600
    assert [j["id"] for j in d["dead_jobs"]] == [4, 2]  # J4 (today) before J7
    assert d["activations"] == {"pending": 1, "failed": 1, "max_attempts": 0}
    assert d["errors_24h"]["provisioning_dead"] == 1 and d["errors_24h"]["mismatch"] == 1
    v = mx.delivery_verdict(d)
    assert v["status"] == "critical"
    assert [r["key"] for r in v["reasons"]][:1] == ["dead_jobs"]


async def test_engagement(db):
    e = await mx.engagement(W7["since"], W7["until"])
    assert e["reminders"]["users"] == 2
    a = e["automations"]
    assert (a["sent"], a["failed"], a["blocked"], a["skipped"]) == (3, 1, 1, 2)
    b = e["broadcasts"]
    assert b["count"] == 1 and b["delivered"] == 4 and b["failed"] == 1 and b["delivery_rate"] == 80.0
    assert e["reach"] == {"users": len(USERS), "unreachable": 2,
                          "unreachable_share": round(2 / len(USERS) * 100, 1)}
    assert e["referrals"]["invited"] == 2 and e["referrals"]["converted"] == 2
    assert e["referrals"]["cashback"] == {"count": 1, "kopecks": 1990, "referrers": 1}


async def test_new_users_like_for_like(db):
    assert await mx.new_users(W7["since"], W7["until"]) == 3        # 2001, 2002, 4003
    assert await mx.new_users(W7["prev_since"], W7["prev_until"]) == 2  # 4001, 4002


async def test_reads_are_read_only_with_timeout(db):
    async with read_conn(db) as conn:
        assert await conn.fetchval("SHOW statement_timeout") == "5s"
        with pytest.raises(asyncpg.exceptions.ReadOnlySQLTransactionError):
            await conn.execute("UPDATE users SET balance = 1")
    async with db.acquire() as conn:  # the pooled connection is back to normal
        assert await conn.fetchval("SHOW statement_timeout") != "5s"
