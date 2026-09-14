"""Subscriber, funnel and operations metrics — the non-money half of the
dashboard's definitions. Money lives in database/revenue.py; the written
contract for both is docs/dashboard/metrics.md.

Read-only. Same conventions as revenue.py: naive-UTC parameters
(_to_db_utc), Moscow day boundaries, business rules as pure functions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from database.core import _from_db_utc, _to_db_utc, get_pool
from database.readonly import read_conn
from database.revenue import BALANCE_PROVIDER, TRAFFIC_TYPES, SUBSCRIPTION_TYPES

logger = logging.getLogger(__name__)

# subscription_history actions that mark a paid period. Admin grants
# ('admin_grant'), trials ('trial') and gifts received ('gift') are not
# paid periods for the purpose of renewal / churn.
PAID_HISTORY_ACTIONS = ("purchase", "renewal")

# A subscription payment in `payments` — the one table every paid path
# writes (external providers, balance purchases, auto-renewal). Tariff
# format there is "<tariff>_<days>".
SUBSCRIPTION_PAYMENT_TARIFF_RE = r"^(basic|plus|biz_[a-z]+)_[0-9]+$"

DEFAULT_RENEWAL_GRACE_DAYS = 3
EXPIRING_SOON_DAYS = 7

# subscriptions.source → the kind shown on the dashboard.
_KIND_OF_SOURCE = {
    "payment": "paid",
    "gift": "gift",
    "trial": "trial",
    "bypass_only": "bypass_only",
}


def subscription_kind(source: Optional[str], is_bypass_only: bool) -> str:
    """paid | gift | trial | granted | bypass_only.

    `granted` = admin / referral / promo and anything else nobody paid
    for. The old "active paid" count only excluded trials, so admin grants
    were counted as paying subscribers.
    """
    if is_bypass_only:
        return "bypass_only"
    return _KIND_OF_SOURCE.get((source or "").lower(), "granted")


def tariff_family(subscription_type: Optional[str]) -> str:
    from app.services.tariffs import normalize_tier

    t = normalize_tier((subscription_type or "basic").lower())  # legacy biz_* → plus
    return t if t in ("basic", "plus") else "other"


def summarize_active(rows: Iterable[dict]) -> dict[str, Any]:
    """rows: {source, bypass_only, sub_type, auto_renew, count, expiring}."""
    kinds = ("paid", "gift", "granted", "trial", "bypass_only")
    by_kind = {k: 0 for k in kinds}
    expiring_by_kind = {k: 0 for k in kinds}
    by_tariff: dict[str, int] = {}
    # `granted` split by the raw subscriptions.source (admin, referral,
    # promo_link, …) so admin grants and promo grants are not one lump.
    granted_sources: dict[str, int] = {}
    auto_renew_paid = 0
    expiring = 0
    expiring_auto = 0
    total = 0
    for r in rows:
        n = int(r.get("count") or 0)
        e = int(r.get("expiring") or 0)
        kind = subscription_kind(r.get("source"), bool(r.get("bypass_only")))
        total += n
        by_kind[kind] += n
        expiring_by_kind[kind] += e
        if kind == "granted":
            src = (r.get("source") or "unknown").lower()
            granted_sources[src] = granted_sources.get(src, 0) + n
        if kind != "bypass_only":
            fam = tariff_family(r.get("sub_type"))
            by_tariff[fam] = by_tariff.get(fam, 0) + n
            expiring += e
            if r.get("auto_renew"):
                expiring_auto += e
        if kind == "paid" and r.get("auto_renew"):
            auto_renew_paid += n
    with_access = total - by_kind["bypass_only"]
    return {
        "total": total,
        # Anyone who has premium access right now (bypass-only excluded).
        "with_access": with_access,
        "paid": by_kind["paid"],
        "by_kind": by_kind,
        "granted_sources": dict(sorted(granted_sources.items(), key=lambda kv: -kv[1])),
        "by_tariff": by_tariff,
        "auto_renew_paid": auto_renew_paid,
        "auto_renew_share": round(auto_renew_paid / by_kind["paid"] * 100, 1) if by_kind["paid"] else None,
        "expiring_7d": {
            "total": expiring,
            "auto_renew_on": expiring_auto,
            "by_kind": expiring_by_kind,
        },
    }


def rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole * 100, 1) if whole else None


def summarize_renewals(row: dict, grace_days: int) -> dict[str, Any]:
    """Renewal rate = renewed ÷ (renewed + churned). Periods still inside
    the grace window are `open` and left out, otherwise a period that ended
    yesterday would count as churn before the user had a chance to pay."""
    renewed = int(row.get("renewed") or 0)
    churned = int(row.get("churned") or 0)
    decided = renewed + churned
    return {
        "grace_days": grace_days,
        "ending": int(row.get("ending") or 0),
        "renewed": renewed,
        "churned": churned,
        "open": int(row.get("open") or 0),
        "renewal_rate": rate(renewed, decided),
        "churn_rate": rate(churned, decided),
    }


# ── SQL ───────────────────────────────────────────────────────────────


async def active_subscriptions(now_utc: Optional[datetime] = None) -> dict[str, Any]:
    """Active = status='active' AND expires_at > now (the same predicate
    fast_expiry_cleanup uses)."""
    now = now_utc or datetime.now(timezone.utc)
    pool = await get_pool()
    if pool is None:
        return summarize_active([])
    async with read_conn(pool) as conn:
        rows = await conn.fetch(
            """SELECT source,
                      COALESCE(is_bypass_only, FALSE) AS bypass_only,
                      subscription_type AS sub_type,
                      COALESCE(auto_renew, FALSE) AS auto_renew,
                      COUNT(*)::BIGINT AS count,
                      COUNT(*) FILTER (WHERE expires_at <= $2)::BIGINT AS expiring
                 FROM subscriptions
                WHERE status = 'active' AND expires_at > $1
                GROUP BY 1, 2, 3, 4""",
            _to_db_utc(now), _to_db_utc(now + timedelta(days=EXPIRING_SOON_DAYS)),
        )
    return summarize_active([dict(r) for r in rows])


_RENEWAL_SQL = """
    WITH paid AS (
        SELECT id, telegram_id, end_date, created_at
          FROM subscription_history
         WHERE action_type = ANY($4::text[])
    ), ending AS (
        SELECT e.*,
               EXISTS (
                   SELECT 1 FROM paid p
                    WHERE p.telegram_id = e.telegram_id
                      AND p.id <> e.id
                      AND p.created_at > e.created_at
                      AND p.created_at <= e.end_date + make_interval(days => $3)
               ) AS renewed
          FROM paid e
         WHERE e.end_date >= $1 AND e.end_date < $2
    )
    SELECT COUNT(*)::BIGINT AS ending,
           COUNT(*) FILTER (WHERE renewed)::BIGINT AS renewed,
           COUNT(*) FILTER (WHERE NOT renewed AND end_date + make_interval(days => $3) < $5)::BIGINT AS churned,
           COUNT(*) FILTER (WHERE NOT renewed AND end_date + make_interval(days => $3) >= $5)::BIGINT AS open
      FROM ending
"""


async def renewals(since: datetime, until: datetime, grace_days: int = DEFAULT_RENEWAL_GRACE_DAYS,
                   now_utc: Optional[datetime] = None) -> dict[str, Any]:
    """Paid periods that ended in [since, until): how many were renewed
    within `grace_days` after the end (early renewals count)."""
    now = now_utc or datetime.now(timezone.utc)
    pool = await get_pool()
    if pool is None:
        return summarize_renewals({}, grace_days)
    async with read_conn(pool) as conn:
        row = await conn.fetchrow(
            _RENEWAL_SQL, _to_db_utc(since), _to_db_utc(until), int(grace_days),
            list(PAID_HISTORY_ACTIONS), _to_db_utc(now),
        )
    return summarize_renewals(dict(row) if row else {}, grace_days)


_TRIAL_SQL = """
    WITH t AS (
        SELECT telegram_id, trial_used_at
          FROM users
         WHERE trial_used_at >= $1 AND trial_used_at < $2
    ), paid AS (
        SELECT telegram_id, created_at FROM pending_purchases
         WHERE status = 'paid' AND COALESCE(purchase_type, 'subscription') = 'subscription'
           AND telegram_id IN (SELECT telegram_id FROM t)
        UNION ALL
        SELECT telegram_id, created_at FROM payments
         WHERE status IN ('approved', 'paid') AND tariff ~ $3
           AND telegram_id IN (SELECT telegram_id FROM t)
    ), conv AS (
        SELECT t.telegram_id, t.trial_used_at,
               MIN(p.created_at) - t.trial_used_at AS lag
          FROM t JOIN paid p
            ON p.telegram_id = t.telegram_id AND p.created_at >= t.trial_used_at
         GROUP BY t.telegram_id, t.trial_used_at
    )
    SELECT (SELECT COUNT(*) FROM t)::BIGINT AS trials,
           (SELECT COUNT(*) FROM t WHERE trial_used_at <= $4::timestamp - INTERVAL '7 days')::BIGINT AS matured_7d,
           (SELECT COUNT(*) FROM t WHERE trial_used_at <= $4::timestamp - INTERVAL '30 days')::BIGINT AS matured_30d,
           COUNT(*) FILTER (WHERE lag <= INTERVAL '7 days')::BIGINT AS paid_7d,
           COUNT(*) FILTER (WHERE lag <= INTERVAL '7 days'
                              AND trial_used_at <= $4::timestamp - INTERVAL '7 days')::BIGINT AS paid_7d_matured,
           COUNT(*) FILTER (WHERE lag <= INTERVAL '30 days')::BIGINT AS paid_30d,
           COUNT(*) FILTER (WHERE lag <= INTERVAL '30 days'
                              AND trial_used_at <= $4::timestamp - INTERVAL '30 days')::BIGINT AS paid_30d_matured,
           COUNT(*)::BIGINT AS paid_any
      FROM conv
"""


def summarize_trials(row: dict) -> dict[str, Any]:
    """Cohort-correct trial → paid rates. Pure (tested).

    rate_7d only looks at trials that started ≥ 7 days ago (they all had
    the full 7 days to convert); rate_30d likewise with 30 days. v3
    divided by every trial in the window, so a trial started yesterday
    counted as "did not convert within 7 days" and the rates were biased
    low — worst for short windows. `rate_any` (paid by today ÷ all trials
    in the window) stays immature by design and is labelled so."""
    g = {k: int(row.get(k) or 0) for k in (
        "trials", "matured_7d", "matured_30d", "paid_7d", "paid_7d_matured",
        "paid_30d", "paid_30d_matured", "paid_any")}
    return {
        **g,
        "rate_7d": rate(g["paid_7d_matured"], g["matured_7d"]),
        "rate_30d": rate(g["paid_30d_matured"], g["matured_30d"]),
        "rate_any": rate(g["paid_any"], g["trials"]),
    }


async def trial_conversion(since: datetime, until: datetime,
                           now_utc: Optional[datetime] = None) -> dict[str, Any]:
    """Trials started in the window → first paid subscription after the
    trial started (any payment path, balance included)."""
    now = now_utc or datetime.now(timezone.utc)
    pool = await get_pool()
    if pool is None:
        return summarize_trials({})
    async with read_conn(pool) as conn:
        row = await conn.fetchrow(
            _TRIAL_SQL, _to_db_utc(since), _to_db_utc(until), SUBSCRIPTION_PAYMENT_TARIFF_RE,
            _to_db_utc(now),
        )
    return summarize_trials(dict(row) if row else {})


# Set-based (v4): the v3 form ran a correlated EXISTS on `payments` per
# user, and payments has no telegram_id index — on production-like volume
# (60k users, 300k payments) a 30-day funnel ran past the 5 s dashboard
# statement_timeout. Same semantics, one scan of each table.
_FUNNEL_SQL = """
    WITH u AS (
        SELECT telegram_id, trial_used_at
          FROM users WHERE created_at >= $1 AND created_at < $2
    ), inv AS (
        SELECT DISTINCT p.telegram_id FROM pending_purchases p
         WHERE p.telegram_id IN (SELECT telegram_id FROM u)
           AND COALESCE(p.purchase_type, 'subscription') = ANY($3::text[])
    ), paid AS (
        SELECT p.telegram_id FROM pending_purchases p
         WHERE p.telegram_id IN (SELECT telegram_id FROM u) AND p.status = 'paid'
           AND COALESCE(p.payment_provider, '') <> $4
           AND COALESCE(p.purchase_type, 'subscription') = ANY($3::text[])
        UNION
        SELECT pay.telegram_id FROM payments pay
         WHERE pay.telegram_id IN (SELECT telegram_id FROM u) AND pay.status = 'approved'
           AND pay.tariff ~ $5
    )
    SELECT COUNT(*)::BIGINT AS started,
           COUNT(*) FILTER (WHERE u.trial_used_at IS NOT NULL)::BIGINT AS trial,
           COUNT(inv.telegram_id)::BIGINT AS invoiced,
           COUNT(paid.telegram_id)::BIGINT AS paid
      FROM u
      LEFT JOIN inv ON inv.telegram_id = u.telegram_id
      LEFT JOIN paid ON paid.telegram_id = u.telegram_id
"""

FUNNEL_NOTES = {
    "tariff_view": (
        "Не записывается: бот не логирует открытие экрана тарифов, "
        "таблицы событий нет. Шаг пропущен, а не выдуман."
    ),
}


def funnel_steps(row: dict) -> list[dict[str, Any]]:
    started = int(row.get("started") or 0)
    steps = [
        ("started", "Запустили бота", started),
        ("trial", "Взяли пробный период", int(row.get("trial") or 0)),
        ("tariff_view", "Открыли тарифы", None),
        ("invoiced", "Создали счёт", int(row.get("invoiced") or 0)),
        ("paid", "Оплатили", int(row.get("paid") or 0)),
    ]
    out = []
    prev: Optional[int] = None
    for key, label, n in steps:
        out.append({
            "key": key,
            "label": label,
            "users": n,
            "of_start": rate(n, started) if n is not None else None,
            "of_prev": rate(n, prev) if (n is not None and prev is not None) else None,
            "recorded": n is not None,
            "note": FUNNEL_NOTES.get(key),
        })
        if n is not None and key != "trial":
            prev = n
    return out


async def funnel(since: datetime, until: datetime) -> dict[str, Any]:
    """Cohort funnel: users who started the bot in the window and what they
    did afterwards (to date). VPN products only — shop is not the funnel."""
    pool = await get_pool()
    if pool is None:
        return {"steps": funnel_steps({})}
    async with read_conn(pool) as conn:
        row = await conn.fetchrow(
            _FUNNEL_SQL, _to_db_utc(since), _to_db_utc(until),
            list(SUBSCRIPTION_TYPES + TRAFFIC_TYPES), BALANCE_PROVIDER,
            SUBSCRIPTION_PAYMENT_TARIFF_RE,
        )
    return {"steps": funnel_steps(dict(row) if row else {})}


async def platega_recurring() -> dict[str, int]:
    """Platega recurring card subscriptions by status (migration 074)."""
    pool = await get_pool()
    if pool is None:
        return {}
    try:
        async with read_conn(pool) as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*)::BIGINT AS n FROM platega_subscriptions GROUP BY 1"
            )
    except Exception:
        return {}
    return {str(r["status"]): int(r["n"]) for r in rows}


async def subscribers_report(days: int = 30, grace_days: int = DEFAULT_RENEWAL_GRACE_DAYS,
                             now_utc: Optional[datetime] = None) -> dict[str, Any]:
    from database import revenue as rev

    w = rev.window(days, now_utc)
    cur = await renewals(w["since"], w["until"], grace_days, now_utc)
    motion = await subscription_motion(w["since"], w["until"])
    return {
        "window_days": days,
        "active": await active_subscriptions(now_utc),
        "renewals": cur,
        "renewals_prev": await renewals(w["prev_since"], w["prev_until"], grace_days, now_utc),
        "trial": await trial_conversion(w["since"], w["until"], now_utc),
        "funnel": await funnel(w["since"], w["until"]),
        "motion": motion,
        "growth": growth(motion, cur),
        "pipeline": await renewal_pipeline(now_utc),
        "auto_renew": {
            "balance": motion["auto_renew_balance"],
            "platega_recurring": await platega_recurring(),
        },
    }


# ── Operations ────────────────────────────────────────────────────────


async def payment_errors_daily(days: int = 14) -> list[dict[str, Any]]:
    from database import revenue as rev

    since = rev.msk_day_start(days_ago=days - 1)
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with read_conn(pool) as conn:
            rows = await conn.fetch(
                """SELECT (created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                          stage, COALESCE(payment_provider, 'unknown') AS provider,
                          COUNT(*)::BIGINT AS n
                     FROM payment_errors WHERE created_at >= $1
                    GROUP BY 1, 2, 3 ORDER BY 1""",
                _to_db_utc(since),
            )
    except Exception:
        return []
    return [{"date": r["day"].isoformat(), "stage": r["stage"], "provider": r["provider"],
             "count": int(r["n"])} for r in rows]


async def provisioning_queue() -> dict[str, Any]:
    """Placeholder for the payment-core outbox (provisioning_jobs,
    migration 082 on the audit branch). Reports `available=False` until
    the table exists, then counts by status without a code change."""
    pool = await get_pool()
    if pool is None:
        return {"available": False}
    async with read_conn(pool) as conn:
        exists = await conn.fetchval("SELECT to_regclass('public.provisioning_jobs') IS NOT NULL")
        if not exists:
            return {"available": False}
        rows = await conn.fetch(
            """SELECT status, COUNT(*)::BIGINT AS n,
                      MIN(created_at) FILTER (WHERE status IN ('pending','running')) AS oldest_open
                 FROM provisioning_jobs GROUP BY status"""
        )
    by_status = {str(r["status"]): int(r["n"]) for r in rows}
    oldest = [r["oldest_open"] for r in rows if r["oldest_open"] is not None]
    return {
        "available": True,
        "by_status": by_status,
        "open": by_status.get("pending", 0) + by_status.get("running", 0),
        "dead": by_status.get("dead", 0),
        "oldest_open_at": min(oldest).isoformat() if oldest else None,
    }


async def operations_snapshot() -> dict[str, Any]:
    """Queues that need a human: pending invoices, stuck activations."""
    pool = await get_pool()
    if pool is None:
        return {"pending_invoices": 0, "stuck_activations": 0}
    async with read_conn(pool) as conn:
        pending = await conn.fetchval(
            """SELECT COUNT(*) FROM pending_purchases
                WHERE status = 'pending' AND (expires_at IS NULL OR expires_at > NOW())"""
        )
        try:
            async with conn.transaction():  # savepoint: column may be missing
                stuck = await conn.fetchval(
                    "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
                )
        except Exception:
            stuck = 0
    return {"pending_invoices": int(pending or 0), "stuck_activations": int(stuck or 0)}


async def panel_entity_counts(now_utc: Optional[datetime] = None) -> dict[str, int]:
    """What the DB expects the panel to hold: premium entities of users
    with access right now, and every bypass entity we know the UUID of."""
    now = now_utc or datetime.now(timezone.utc)
    pool = await get_pool()
    if pool is None:
        return {"premium_active": 0, "bypass_entities": 0}
    async with read_conn(pool) as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*) FILTER (
                          WHERE status = 'active' AND expires_at > $1
                            AND NOT COALESCE(is_bypass_only, FALSE)
                            AND remnawave_premium_uuid IS NOT NULL)::BIGINT AS premium_active,
                      COUNT(*) FILTER (WHERE remnawave_uuid IS NOT NULL)::BIGINT AS bypass_entities
                 FROM subscriptions""",
            _to_db_utc(now),
        )
    return {"premium_active": int(row["premium_active"] or 0),
            "bypass_entities": int(row["bypass_entities"] or 0)}


async def daily_activity(days: int, now_utc: Optional[datetime] = None) -> list[dict[str, Any]]:
    """New users and newly activated subscriptions per Moscow day."""
    from database import revenue as rev

    now = now_utc or datetime.now(timezone.utc)
    starts = rev.bucket_starts(rev.msk_date(now), "day", days)
    since = rev.msk_day_start(now, days_ago=days - 1)
    pool = await get_pool()
    acc: dict = {s: {"new_users": 0, "new_subscriptions": 0, "new_paid_subscriptions": 0} for s in starts}
    if pool is None:
        return [{"date": s.isoformat(), **v} for s, v in acc.items()]
    async with read_conn(pool) as conn:
        users = await conn.fetch(
            """SELECT (created_at AT TIME ZONE 'Europe/Moscow')::date AS d, COUNT(*)::BIGINT AS n
                 FROM users WHERE created_at >= $1 GROUP BY 1""",
            _to_db_utc(since),
        )
        subs = await conn.fetch(
            """SELECT (activated_at AT TIME ZONE 'Europe/Moscow')::date AS d,
                      COUNT(*)::BIGINT AS n,
                      COUNT(*) FILTER (WHERE source = 'payment')::BIGINT AS paid
                 FROM subscriptions WHERE activated_at >= $1 GROUP BY 1""",
            _to_db_utc(since),
        )
    for r in users:
        if r["d"] in acc:
            acc[r["d"]]["new_users"] = int(r["n"])
    for r in subs:
        if r["d"] in acc:
            acc[r["d"]]["new_subscriptions"] = int(r["n"])
            acc[r["d"]]["new_paid_subscriptions"] = int(r["paid"])
    return [{"date": s.isoformat(), **acc[s]} for s in starts]


# ── Dashboard v4: growth, pipeline, payments / delivery health, engagement ─
#
# Same rules as above: SQL groups rows, business rules are pure functions
# (tested on fixture rows AND against Postgres in tests/db/), every read
# goes through read_conn (read-only, statement_timeout). Definitions:
# docs/dashboard/metrics.md.


async def new_users(since: datetime, until: datetime) -> int:
    """Users whose first /start fell in [since, until) (idx_users_created_at)."""
    pool = await get_pool()
    async with read_conn(pool) as conn:
        if conn is None:
            return 0
        return int(await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= $1 AND created_at < $2",
            _to_db_utc(since), _to_db_utc(until),
        ) or 0)


# Subscription sales by motion. A direct subscription purchase is "new"
# when it is the user's first paid subscription ever (any path: direct or
# from the balance), otherwise a renewal. Auto-renewals are balance debits
# (source='auto_renew') — delivered product, not new cash.
_MOTION_SQL = """
    WITH win AS (
        SELECT telegram_id, created_at, price_kopecks
          FROM pending_purchases
         WHERE status = 'paid'
           AND COALESCE(purchase_type, 'subscription') = 'subscription'
           AND COALESCE(payment_provider, '') <> $3
           AND created_at >= $1 AND created_at < $2
    ), firsts AS (
        SELECT telegram_id, MIN(created_at) AS first_at
          FROM (
                SELECT telegram_id, created_at FROM pending_purchases
                 WHERE status = 'paid'
                   AND COALESCE(purchase_type, 'subscription') = 'subscription'
                   AND telegram_id IN (SELECT telegram_id FROM win)
                UNION ALL
                SELECT telegram_id, created_at FROM payments
                 WHERE status IN ('approved', 'paid') AND tariff ~ $4
                   AND telegram_id IN (SELECT telegram_id FROM win)
               ) x
         GROUP BY telegram_id
    )
    SELECT COUNT(*) FILTER (WHERE w.created_at <= f.first_at)::BIGINT AS new_n,
           COALESCE(SUM(w.price_kopecks) FILTER (WHERE w.created_at <= f.first_at), 0)::BIGINT AS new_k,
           COUNT(DISTINCT w.telegram_id) FILTER (WHERE w.created_at <= f.first_at)::BIGINT AS new_users,
           COUNT(*) FILTER (WHERE w.created_at > f.first_at)::BIGINT AS renew_n,
           COALESCE(SUM(w.price_kopecks) FILTER (WHERE w.created_at > f.first_at), 0)::BIGINT AS renew_k
      FROM win w JOIN firsts f USING (telegram_id)
"""


def summarize_motion(row: dict, auto_renew: dict) -> dict[str, Any]:
    new = {"count": int(row.get("new_n") or 0), "kopecks": int(row.get("new_k") or 0),
           "users": int(row.get("new_users") or 0)}
    renewal = {"count": int(row.get("renew_n") or 0), "kopecks": int(row.get("renew_k") or 0)}
    ar = auto_renew or {}
    auto = {"count": int(ar.get("count") or 0), "kopecks": int(ar.get("kopecks") or 0)}
    return {
        "new": new,
        "renewal": renewal,
        "auto_renew_balance": auto,
        "new_share": rate(new["count"], new["count"] + renewal["count"]),
    }


async def subscription_motion(since: datetime, until: datetime) -> dict[str, Any]:
    from database import revenue as rev

    row: dict = {}
    pool = await get_pool()
    async with read_conn(pool) as conn:
        if conn is not None:
            r = await conn.fetchrow(
                _MOTION_SQL, _to_db_utc(since), _to_db_utc(until), BALANCE_PROVIDER,
                SUBSCRIPTION_PAYMENT_TARIFF_RE,
            )
            row = dict(r) if r else {}
    spend = await rev.balance_spend(since, until)
    return summarize_motion(row, spend.get("auto_renew") or {})


def growth(motion: dict, renewals_row: dict) -> dict[str, Any]:
    """Net paid growth over the window = first-time paying subscribers −
    paid periods that ended in the window and were not renewed within the
    grace days (flows, not a stock difference: the subscriptions table
    keeps only the current state, so "active paid 30 days ago" cannot be
    reconstructed exactly)."""
    new = int((motion.get("new") or {}).get("users") or 0)
    churned = int(renewals_row.get("churned") or 0)
    return {"new_paying": new, "churned": churned, "net": new - churned,
            "undecided": int(renewals_row.get("open") or 0)}


# ── Renewal pipeline ─────────────────────────────────────────────────

PIPELINE_DAYS = 7

_PIPELINE_SUBS_SQL = """
    SELECT s.telegram_id, s.expires_at, s.subscription_type, s.source,
           COALESCE(s.is_combo, FALSE) AS is_combo,
           COALESCE(s.auto_renew, FALSE) AS auto_renew,
           (s.uuid IS NOT NULL) AS has_uuid,
           COALESCE(u.balance, 0)::BIGINT AS balance
      FROM subscriptions s
      LEFT JOIN users u ON u.telegram_id = s.telegram_id
     WHERE s.status = 'active' AND s.expires_at > $1 AND s.expires_at <= $2
       AND NOT COALESCE(s.is_bypass_only, FALSE)
"""

# The exact payment auto_renewal prices from:
# database.get_last_approved_payment = latest approved payment, any kind.
_LAST_APPROVED_SQL = """
    SELECT DISTINCT ON (telegram_id) telegram_id, tariff
      FROM payments
     WHERE telegram_id = ANY($1::bigint[]) AND status = 'approved'
     ORDER BY telegram_id, created_at DESC
"""


def renewal_list_price_rub(last_tariff: Optional[str], sub_type: Optional[str],
                           is_combo: bool, combo_pricing: bool = True) -> Optional[int]:
    """Catalog price auto_renewal starts from, before the personal
    discount (auto_renewal.process_auto_renewals + _outbox_renewal_plan):
    tariff and period parsed from the last approved payment exactly as the
    worker does ("plus_90"; legacy "3" = months; anything unparseable or
    unknown → basic 30 d). Combo subscriptions renew at the combo price
    only when the outbox path is on (provisioning_flags "autorenew") —
    otherwise the worker bills the plain tariff price; `combo_pricing`
    carries that flag. None = cannot be priced (unknown combo period)."""
    import config

    from app.services import tariffs as _tariffs

    tier, days = "basic", 30
    t = last_tariff if isinstance(last_tariff, str) and last_tariff else None
    t = _tariffs.normalize_payment_tariff(t)  # legacy biz_* renews as Plus
    if t is not None:
        if "_" in t:
            parts = t.split("_")
            tier = parts[0] if parts else "basic"
            try:
                days = int(parts[1]) if len(parts) > 1 else 30
            except (ValueError, IndexError):
                days = 30
        else:
            tier = "basic"
            try:
                days = int(t) * 30
            except ValueError:
                days = 30
    if tier not in config.TARIFFS or days not in config.TARIFFS[tier]:
        tier, days = "basic", 30
    if is_combo and combo_pricing:
        try:
            from app.services import tariffs

            key = tariffs.tariff_key((sub_type or tier).strip().lower(), True)
            return int(tariffs.renewal_price_rub(key, days))
        except Exception:
            return None
    return int(config.TARIFFS[tier][days]["price"])


def summarize_pipeline(subs: Iterable[dict], last_tariff: dict[int, Optional[str]],
                       now_utc: datetime, days: int = PIPELINE_DAYS,
                       combo_pricing: bool = True) -> dict[str, Any]:
    """Who loses access in the next `days` days and what the auto-renewal
    worker is expected to debit. Pure (tested)."""
    from database import revenue as rev

    first = rev.msk_date(now_utc)
    last = rev.msk_date(now_utc + timedelta(days=days))
    day_list = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    by_day = {d: {"total": 0, "auto_renew": 0} for d in day_list}
    kinds = ("paid", "gift", "granted", "trial")
    by_kind = {k: 0 for k in kinds}
    total = auto_n = 0
    expected_k = covered_n = covered_k = short_n = shortfall_k = unpriced = 0
    for s in subs:
        kind = subscription_kind(s.get("source"), False)
        if kind not in by_kind:
            continue
        total += 1
        by_kind[kind] += 1
        exp = _from_db_utc(s["expires_at"]) if s.get("expires_at") is not None else None
        d = rev.msk_date(exp) if exp else None
        if d in by_day:
            by_day[d]["total"] += 1
        # Mirrors the worker's due query: auto_renew = TRUE AND uuid IS NOT NULL.
        if not (s.get("auto_renew") and s.get("has_uuid")):
            continue
        auto_n += 1
        if d in by_day:
            by_day[d]["auto_renew"] += 1
        price = renewal_list_price_rub(last_tariff.get(int(s["telegram_id"])),
                                       s.get("subscription_type"), bool(s.get("is_combo")),
                                       combo_pricing)
        if price is None:
            unpriced += 1
            continue
        k = price * 100
        expected_k += k
        balance = int(s.get("balance") or 0)
        if balance >= k:
            covered_n += 1
            covered_k += k
        else:
            short_n += 1
            shortfall_k += k - balance
    return {
        "days": days,
        "expiring": total,
        "by_kind": by_kind,
        "auto_renew": auto_n,
        "manual": total - auto_n,
        "expected_list_kopecks": expected_k,
        "covered": covered_n,
        "covered_kopecks": covered_k,
        "not_covered": short_n,
        "shortfall_kopecks": shortfall_k,
        "unpriced": unpriced,
        "by_day": [{"date": d.isoformat(), **v} for d, v in by_day.items()],
    }


async def renewal_pipeline(now_utc: Optional[datetime] = None,
                           days: int = PIPELINE_DAYS) -> dict[str, Any]:
    now = now_utc or datetime.now(timezone.utc)
    pool = await get_pool()
    subs: list[dict] = []
    last: dict[int, Optional[str]] = {}
    async with read_conn(pool) as conn:
        if conn is not None:
            subs = [dict(r) for r in await conn.fetch(
                _PIPELINE_SUBS_SQL, _to_db_utc(now), _to_db_utc(now + timedelta(days=days)),
            )]
            ids = [int(s["telegram_id"]) for s in subs if s["auto_renew"] and s["has_uuid"]]
            if ids:
                last = {int(r["telegram_id"]): r["tariff"]
                        for r in await conn.fetch(_LAST_APPROVED_SQL, ids)}
    try:
        from app.services import provisioning_flags

        combo_pricing = bool(provisioning_flags.is_on("autorenew"))
    except Exception:
        combo_pricing = True
    return summarize_pipeline(subs, last, now, days, combo_pricing)


# ── Payments health ──────────────────────────────────────────────────

# Provider spellings as stored in pending_purchases.payment_provider.
KNOWN_PROVIDERS = ("platega", "wata", "cryptobot", "telegram", "telegram_payment", "telegram_stars")
STUCK_AFTER = timedelta(minutes=30)
SILENCE_MIN_PAID_7D = 7
SILENCE_MIN_HOURS = 3.0
SILENCE_FACTOR = 4.0

_PROVIDER_EXPR = ("CASE WHEN {c} = 'telegram' THEN 'telegram_payment' "
                  "ELSE COALESCE({c}, 'unknown') END")

_STUCK_SQL = f"""
    SELECT {_PROVIDER_EXPR.format(c='payment_provider')} AS provider,
           COUNT(*)::BIGINT AS n,
           COUNT(*) FILTER (WHERE expires_at > $2)::BIGINT AS still_valid,
           MIN(created_at) AS oldest
      FROM pending_purchases
     WHERE status = 'pending' AND created_at < $1 AND created_at >= $3
       AND COALESCE(payment_provider, '') <> 'balance'
     GROUP BY 1
     ORDER BY n DESC
"""

_TTP_SQL = f"""
    SELECT {_PROVIDER_EXPR.format(c='pp.payment_provider')} AS provider,
           GROUPING({_PROVIDER_EXPR.format(c='pp.payment_provider')}) AS is_total,
           COUNT(*)::BIGINT AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (p.paid_at - pp.created_at))) AS p50_s,
           percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (p.paid_at - pp.created_at))) AS p90_s
      FROM pending_purchases pp
      JOIN payments p ON p.purchase_id = pp.purchase_id AND p.status IN ('approved', 'paid')
     WHERE pp.status = 'paid'
       AND pp.created_at >= $1 AND pp.created_at < $2
       AND p.paid_at IS NOT NULL AND p.paid_at >= pp.created_at
       AND COALESCE(pp.payment_provider, '') <> 'balance'
     GROUP BY GROUPING SETS (({_PROVIDER_EXPR.format(c='pp.payment_provider')}), ())
"""

_ERR_MATRIX_SQL = """
    SELECT stage, COALESCE(payment_provider, 'unknown') AS provider,
           COUNT(*)::BIGINT AS n, MAX(created_at) AS last_at
      FROM payment_errors
     WHERE created_at >= $1
     GROUP BY 1, 2
     ORDER BY n DESC
     LIMIT 200
"""

_LAST_PAID_SQL = """
    SELECT pp.created_at, p.paid_at
      FROM pending_purchases pp
      LEFT JOIN payments p ON p.purchase_id = pp.purchase_id AND p.status IN ('approved', 'paid')
     WHERE pp.status = 'paid' AND pp.payment_provider = $1
     ORDER BY pp.id DESC
     LIMIT 1
"""

_LAST_TOPUP_SQL = """
    SELECT created_at FROM balance_transactions
     WHERE type = 'topup' AND source = $1
     ORDER BY id DESC
     LIMIT 1
"""


def summarize_errors(rows: Iterable[dict]) -> dict[str, Any]:
    by_stage: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    matrix = []
    for r in rows:
        n = int(r.get("n") or 0)
        by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + n
        by_provider[r["provider"]] = by_provider.get(r["provider"], 0) + n
        last = r.get("last_at")
        matrix.append({"stage": r["stage"], "provider": r["provider"], "count": n,
                       "last_at": _from_db_utc(last).isoformat() if last else None})
    telegram_money = sum(v for k, v in by_stage.items() if k.startswith("telegram_"))
    return {
        "total": sum(by_stage.values()),
        "by_stage": [{"stage": k, "count": v} for k, v in sorted(by_stage.items(), key=lambda kv: -kv[1])],
        "by_provider": [{"provider": k, "count": v} for k, v in sorted(by_provider.items(), key=lambda kv: -kv[1])],
        "matrix": matrix,
        "telegram_money": telegram_money,
    }


def provider_silence(last_paid_at: Optional[datetime], paid_7d: int, now_utc: datetime,
                     enabled: Optional[bool] = True) -> dict[str, Any]:
    """Is a provider suspiciously quiet? Pure (tested).

    Only judged when the provider sold at least SILENCE_MIN_PAID_7D times
    in 7 days (rarer providers have no "normal" rhythm to break). Expected
    gap = 168 h ÷ paid_7d; silent when the last paid invoice is older than
    max(SILENCE_MIN_HOURS, SILENCE_FACTOR × expected gap). Disabled
    providers are never "silent"."""
    age_h = round((now_utc - last_paid_at).total_seconds() / 3600, 1) if last_paid_at else None
    if enabled is False:
        return {"state": "disabled", "age_h": age_h, "threshold_h": None}
    if paid_7d < SILENCE_MIN_PAID_7D:
        return {"state": "rare", "age_h": age_h, "threshold_h": None}
    threshold = round(max(SILENCE_MIN_HOURS, SILENCE_FACTOR * 168.0 / paid_7d), 1)
    silent = age_h is None or age_h > threshold
    return {"state": "silent" if silent else "ok", "age_h": age_h, "threshold_h": threshold}


async def payments_health(now_utc: Optional[datetime] = None) -> dict[str, Any]:
    """Raw payment-health facts. The route adds provider on/off state and
    the silence verdict (they depend on config, not on the database)."""
    from database import revenue as rev

    now = now_utc or datetime.now(timezone.utc)
    day, week = now - timedelta(hours=24), now - timedelta(days=7)
    prov_24h = await rev.provider_performance(day, now, now)
    prov_7d = await rev.provider_performance(week, now, now)
    stuck: list = []
    ttp: list = []
    errors: list = []
    last_paid: dict[str, Optional[datetime]] = {}
    pool = await get_pool()
    async with read_conn(pool) as conn:
        if conn is not None:
            stuck = [dict(r) for r in await conn.fetch(
                _STUCK_SQL, _to_db_utc(now - STUCK_AFTER), _to_db_utc(now), _to_db_utc(day))]
            ttp = [dict(r) for r in await conn.fetch(_TTP_SQL, _to_db_utc(week), _to_db_utc(now))]
            try:
                async with conn.transaction():
                    errors = [dict(r) for r in await conn.fetch(_ERR_MATRIX_SQL, _to_db_utc(day))]
            except Exception as e:
                logger.warning("payments_health: payment_errors unavailable: %s", e)
            for prov in KNOWN_PROVIDERS:
                r = await conn.fetchrow(_LAST_PAID_SQL, prov)
                if r:
                    at = r["paid_at"] or r["created_at"]
                    key = "telegram_payment" if prov == "telegram" else prov
                    at = _from_db_utc(at)
                    if last_paid.get(key) is None or at > last_paid[key]:
                        last_paid[key] = at
            for src in rev.TELEGRAM_TOPUP_SOURCES:
                try:
                    async with conn.transaction():
                        at = await conn.fetchval(_LAST_TOPUP_SQL, src)
                except Exception:
                    at = None
                if at is not None:
                    key = rev.normalize_provider(src)
                    at = _from_db_utc(at)
                    if last_paid.get(key) is None or at > last_paid[key]:
                        last_paid[key] = at

    created_24h = {p["provider"]: p["created"] for p in prov_24h}
    return {
        "now": now.isoformat(),
        "providers_24h": prov_24h,
        "providers_7d": prov_7d,
        "stuck": [{
            "provider": s["provider"],
            "count": int(s["n"]),
            "still_valid": int(s["still_valid"]),
            "oldest_at": _from_db_utc(s["oldest"]).isoformat() if s["oldest"] else None,
            "created_24h": int(created_24h.get(s["provider"], 0)),
        } for s in stuck],
        "time_to_pay": [{
            "provider": None if t["is_total"] else t["provider"],
            "count": int(t["n"]),
            "p50_s": round(float(t["p50_s"]), 1) if t["p50_s"] is not None else None,
            "p90_s": round(float(t["p90_s"]), 1) if t["p90_s"] is not None else None,
        } for t in ttp],
        "errors_24h": summarize_errors(errors),
        "last_paid": {k: v.isoformat() for k, v in last_paid.items()},
        "paid_7d": {p["provider"]: p["paid"] for p in prov_7d},
    }


# ── Delivery health ──────────────────────────────────────────────────

DEAD_JOBS_LIMIT = 50

_PROV_STATUS_SQL = """
    SELECT COUNT(*) FILTER (WHERE status = 'pending' AND attempts = 0)::BIGINT AS pending_new,
           COUNT(*) FILTER (WHERE status = 'pending' AND attempts > 0)::BIGINT AS retrying,
           COUNT(*) FILTER (WHERE status = 'running')::BIGINT AS running,
           COUNT(*) FILTER (WHERE status = 'dead')::BIGINT AS dead,
           COUNT(*) FILTER (WHERE status = 'shadow')::BIGINT AS shadow,
           COUNT(*) FILTER (WHERE status = 'done' AND done_at >= $1)::BIGINT AS done_24h,
           COUNT(*) FILTER (WHERE status = 'dead' AND updated_at >= $1)::BIGINT AS dead_24h,
           MIN(created_at) FILTER (WHERE status IN ('pending', 'running')) AS oldest_open,
           COALESCE(MAX(attempts) FILTER (WHERE status IN ('pending', 'running')), 0) AS max_attempts_open
      FROM provisioning_jobs
"""

_DEAD_JOBS_SQL = """
    SELECT id, telegram_id, source, tariff_key, attempts, last_error, created_at, updated_at
      FROM provisioning_jobs
     WHERE status = 'dead'
     ORDER BY updated_at DESC
     LIMIT $1
"""

_ACTIVATION_SQL = """
    SELECT COUNT(*) FILTER (WHERE activation_status = 'pending')::BIGINT AS pending,
           COUNT(*) FILTER (WHERE activation_status = 'failed')::BIGINT AS failed,
           COALESCE(MAX(activation_attempts) FILTER (WHERE activation_status = 'pending'), 0) AS max_attempts
      FROM subscriptions
"""

_DELIVERY_ERR_SQL = """
    SELECT COUNT(*) FILTER (WHERE stage = 'provisioning' AND error_code = 'dead')::BIGINT AS provisioning_dead,
           COUNT(*) FILTER (WHERE stage = 'provisioning' AND error_code = 'retry')::BIGINT AS provisioning_retry,
           COUNT(*) FILTER (WHERE COALESCE(error_message, '') LIKE '%DELIVERY_MISMATCH%')::BIGINT AS mismatch,
           COUNT(*) FILTER (WHERE stage = 'renewal_sync')::BIGINT AS renewal_sync,
           COUNT(*) FILTER (WHERE stage = 'bypass_topup')::BIGINT AS bypass_topup
      FROM payment_errors
     WHERE created_at >= $1
"""


def _iso(v: Any) -> Optional[str]:
    return _from_db_utc(v).isoformat() if v is not None else None


async def delivery_health(now_utc: Optional[datetime] = None) -> dict[str, Any]:
    now = now_utc or datetime.now(timezone.utc)
    day = _to_db_utc(now - timedelta(hours=24))
    out: dict[str, Any] = {"now": now.isoformat(), "queue": {"available": False}, "dead_jobs": [],
                           "activations": {"pending": 0, "failed": 0, "max_attempts": 0},
                           "errors_24h": {}}
    pool = await get_pool()
    async with read_conn(pool) as conn:
        if conn is None:
            return out
        if await conn.fetchval("SELECT to_regclass('public.provisioning_jobs') IS NOT NULL"):
            q = dict(await conn.fetchrow(_PROV_STATUS_SQL, day))
            oldest = q.pop("oldest_open")
            out["queue"] = {"available": True, **{k: int(v) for k, v in q.items()},
                            "oldest_open_at": _iso(oldest),
                            "oldest_open_age_s": round((now - _from_db_utc(oldest)).total_seconds())
                            if oldest else None}
            out["dead_jobs"] = [{
                "id": int(r["id"]), "telegram_id": int(r["telegram_id"]), "source": r["source"],
                "tariff_key": r["tariff_key"], "attempts": int(r["attempts"]),
                "last_error": (r["last_error"] or "")[:300] or None,
                "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"]),
            } for r in await conn.fetch(_DEAD_JOBS_SQL, DEAD_JOBS_LIMIT)]
        try:
            async with conn.transaction():
                a = await conn.fetchrow(_ACTIVATION_SQL)
            out["activations"] = {k: int(a[k] or 0) for k in ("pending", "failed", "max_attempts")}
        except Exception as e:
            logger.warning("delivery_health: activation columns unavailable: %s", e)
        try:
            async with conn.transaction():
                e = await conn.fetchrow(_DELIVERY_ERR_SQL, day)
            out["errors_24h"] = {k: int(e[k] or 0) for k in e.keys()}
        except Exception as e:
            logger.warning("delivery_health: payment_errors unavailable: %s", e)
    return out


# ── Engagement: reminders, automations, broadcasts, reach, referrals ─

_AUTO_NOTIF_SQL = """
    SELECT an.key, an.title, an.category, an.is_enabled,
           s.sent, s.failed, s.blocked, s.skipped
      FROM automated_notifications an
      CROSS JOIN LATERAL (
          SELECT COUNT(*) FILTER (WHERE x.status = 'sent')::BIGINT AS sent,
                 COUNT(*) FILTER (WHERE x.status = 'failed')::BIGINT AS failed,
                 COUNT(*) FILTER (WHERE x.status = 'blocked')::BIGINT AS blocked,
                 COUNT(*) FILTER (WHERE x.status = 'skipped_disabled')::BIGINT AS skipped
            FROM automated_notification_sends x
           WHERE x.key = an.key AND x.sent_at >= $1 AND x.sent_at < $2
      ) s
"""

_BROADCASTS_SQL = """
    SELECT id, title, segment, created_at
      FROM broadcasts
     WHERE created_at >= $1 AND created_at < $2
     ORDER BY id DESC
     LIMIT 20
"""

_BROADCAST_LOG_SQL = """
    SELECT broadcast_id,
           COUNT(*) FILTER (WHERE status IN ('sent', 'deleted'))::BIGINT AS delivered,
           COUNT(*) FILTER (WHERE status = 'failed')::BIGINT AS failed
      FROM broadcast_log
     WHERE broadcast_id = ANY($1::int[])
     GROUP BY 1
"""


def summarize_broadcasts(broadcasts: list[dict], log: dict[int, dict]) -> dict[str, Any]:
    rows = []
    delivered = failed = 0
    for b in broadcasts:
        s = log.get(int(b["id"]), {})
        d, f = int(s.get("delivered") or 0), int(s.get("failed") or 0)
        delivered += d
        failed += f
        rows.append({"id": int(b["id"]), "title": b.get("title"), "segment": b.get("segment"),
                     "created_at": _iso(b.get("created_at")), "delivered": d, "failed": f,
                     "delivery_rate": rate(d, d + f)})
    return {"count": len(rows), "delivered": delivered, "failed": failed,
            "delivery_rate": rate(delivered, delivered + failed), "recent": rows}


async def engagement(since: datetime, until: datetime) -> dict[str, Any]:
    from database import revenue as rev

    out: dict[str, Any] = {
        "reminders": {"users": 0},
        "automations": {"available": False, "sent": 0, "failed": 0, "blocked": 0, "skipped": 0, "by_key": []},
        "broadcasts": summarize_broadcasts([], {}),
        "reach": {"users": 0, "unreachable": 0, "unreachable_share": None},
        "referrals": {"invited": 0, "converted": 0},
    }
    p = (_to_db_utc(since), _to_db_utc(until))
    pool = await get_pool()
    async with read_conn(pool) as conn:
        if conn is not None:
            out["reminders"]["users"] = int(await conn.fetchval(
                "SELECT COUNT(*) FROM subscriptions WHERE last_reminder_at >= $1 AND last_reminder_at < $2", *p,
            ) or 0)
            try:
                async with conn.transaction():
                    keys = [dict(r) for r in await conn.fetch(_AUTO_NOTIF_SQL, *p)]
                by_key = [{"key": k["key"], "title": k["title"], "category": k["category"],
                           "enabled": bool(k["is_enabled"]),
                           **{f: int(k[f] or 0) for f in ("sent", "failed", "blocked", "skipped")}}
                          for k in keys]
                by_key.sort(key=lambda k: -(k["sent"] + k["failed"] + k["blocked"]))
                out["automations"] = {
                    "available": True,
                    **{f: sum(k[f] for k in by_key) for f in ("sent", "failed", "blocked", "skipped")},
                    "by_key": [k for k in by_key if k["sent"] or k["failed"] or k["blocked"] or k["skipped"]],
                }
            except Exception as e:
                logger.warning("engagement: automated notifications unavailable: %s", e)
            bcasts = [dict(r) for r in await conn.fetch(_BROADCASTS_SQL, *p)]
            log: dict[int, dict] = {}
            if bcasts:
                log = {int(r["broadcast_id"]): dict(r) for r in await conn.fetch(
                    _BROADCAST_LOG_SQL, [int(b["id"]) for b in bcasts])}
            out["broadcasts"] = summarize_broadcasts(bcasts, log)
            r = await conn.fetchrow(
                "SELECT COUNT(*)::BIGINT AS n, COUNT(*) FILTER (WHERE is_reachable = FALSE)::BIGINT AS u FROM users"
            )
            n, u = int(r["n"] or 0), int(r["u"] or 0)
            out["reach"] = {"users": n, "unreachable": u, "unreachable_share": rate(u, n)}
            r = await conn.fetchrow(
                """SELECT (SELECT COUNT(*) FROM referrals WHERE created_at >= $1 AND created_at < $2)::BIGINT AS invited,
                          (SELECT COUNT(*) FROM referrals WHERE first_paid_at >= $1 AND first_paid_at < $2)::BIGINT
                              AS converted""",
                *p,
            )
            out["referrals"] = {"invited": int(r["invited"] or 0), "converted": int(r["converted"] or 0)}
    out["referrals"]["cashback"] = await rev.referral_payouts(since, until)
    return out


# ── Section verdicts (pure, tested) ──────────────────────────────────

PROVIDER_NAMES = {
    "platega": "Platega", "wata": "WATA", "cryptobot": "CryptoBot",
    "telegram_payment": "Telegram", "telegram_stars": "Telegram Stars", "unknown": "Без провайдера",
}
ERRORS_CRITICAL_24H = 10
LOW_SUCCESS_RATE = 50.0
LOW_SUCCESS_MIN_SETTLED = 20
DELIVERY_WAIT_WARN_S = 15 * 60


def _verdict(reasons: list[dict]) -> dict[str, Any]:
    order = {"critical": 0, "warning": 1, "info": 2}
    reasons.sort(key=lambda r: order[r["level"]])
    status = "critical" if any(r["level"] == "critical" for r in reasons) else (
        "warning" if any(r["level"] == "warning" for r in reasons) else "ok")
    return {"status": status, "reasons": reasons}


def payments_verdict(h: dict) -> dict[str, Any]:
    """Payments section status from payments_health() + provider silence."""
    reasons: list[dict] = []
    total = int((h.get("errors_24h") or {}).get("total") or 0)
    if total:
        stages = ", ".join(f"{s['stage']} ×{s['count']}" for s in (h["errors_24h"].get("by_stage") or [])[:3])
        reasons.append({"level": "critical" if total >= ERRORS_CRITICAL_24H else "warning",
                        "key": "payment_errors", "text": f"Ошибки платежей за 24 ч: {total} ({stages})."})
    for p in h.get("providers") or []:
        if p.get("state") == "silent":
            name = PROVIDER_NAMES.get(p["provider"], p["provider"])
            age = "ни одной" if p.get("age_h") is None else f"{p['age_h']:.0f} ч"
            reasons.append({"level": "warning", "key": f"silent_{p['provider']}",
                            "text": f"{name}: последняя оплата {age} назад, обычно не дольше {p['threshold_h']:.0f} ч."})
    for p in h.get("providers_7d") or []:
        settled = int(p.get("paid") or 0) + int(p.get("expired") or 0)
        sr = p.get("success_rate")
        if sr is not None and settled >= LOW_SUCCESS_MIN_SETTLED and sr < LOW_SUCCESS_RATE:
            name = PROVIDER_NAMES.get(p["provider"], p["provider"])
            reasons.append({"level": "warning", "key": f"low_sr_{p['provider']}",
                            "text": f"{name}: оплачено {sr:.0f}% закрытых счетов за 7 дней."})
    return _verdict(reasons)


def delivery_verdict(d: dict) -> dict[str, Any]:
    reasons: list[dict] = []
    q = d.get("queue") or {}
    if q.get("available"):
        if q.get("dead"):
            reasons.append({"level": "critical", "key": "dead_jobs",
                            "text": f"Выдача не удалась: {q['dead']} заданий в статусе dead. Оплачено, доступа нет."})
        age = q.get("oldest_open_age_s")
        if age is not None and age > DELIVERY_WAIT_WARN_S:
            reasons.append({"level": "warning", "key": "queue_wait",
                            "text": f"Самое старое задание выдачи ждёт {age // 60} мин."})
        if q.get("retrying"):
            reasons.append({"level": "info", "key": "retrying",
                            "text": f"Повторяются после ошибки: {q['retrying']}."})
    a = d.get("activations") or {}
    if a.get("pending"):
        reasons.append({"level": "warning", "key": "activations_pending",
                        "text": f"Подписок ждут активации: {a['pending']}."})
    e = d.get("errors_24h") or {}
    if e.get("mismatch"):
        reasons.append({"level": "warning", "key": "mismatch",
                        "text": f"Несовпадений панели с оплатой за 24 ч: {e['mismatch']}."})
    return _verdict(reasons)
