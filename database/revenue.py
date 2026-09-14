"""Revenue — the single definition of money in this product.

Every money figure on the dashboard is computed here. Routes never write
their own SQL for money; the legacy endpoints that still exist for older
screens delegate to these functions (see app/api/dashboard/routes/stats.py
and payments.py). The human-readable contract lives in
docs/dashboard/metrics.md — keep the two in step.

WHERE MONEY IS RECORDED (verified against the payment code, 2026-09):

  * `pending_purchases` (status='paid') — every purchase that goes through
    an invoice: external providers (platega / wata / cryptobot / lava) via
    finalize_purchase, Telegram-native card and Stars payments for
    subscriptions / gifts / traffic packs, the mini-shop via
    mark_pending_purchase_paid (payment_provider stays NULL there), and
    balance top-ups paid through an external provider.
  * `balance_transactions` (type='topup', source IN ('telegram',
    'telegram_stars')) — balance top-ups paid inside Telegram. These go
    through finalize_balance_topup, which never touches pending_purchases.
    Before this module the dashboard ignored them entirely.
  * Purchases paid FROM the balance (finalize_balance_purchase,
    auto-renewal) write `payments` and a negative `balance_transactions`
    row (type='subscription_payment'), never pending_purchases.

REVENUE = MONEY THAT ENTERED THE BUSINESS FROM OUTSIDE. A ruble arrives
once: as a direct purchase or as a top-up. Spending the balance later is
the wallet moving money inside itself and is reported separately
("balance spend"), never added to revenue.

TIMEZONE: every day boundary is Europe/Moscow (fixed UTC+3, no DST since
2014). Timestamps are passed to asyncpg as naive UTC (_to_db_utc) — the
repo-wide convention; the money columns are TIMESTAMPTZ since migrations
024/025, and SQL buckets them with `AT TIME ZONE 'Europe/Moscow'`.

UNITS: kopecks, integers, end to end. Rubles exist only in the formatter.

Design: SQL here only groups rows; every business rule (what is revenue,
which class a purchase belongs to, what counts as a product) is a pure
function below, so it is unit-tested on fixture rows without Postgres.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from database.core import _to_db_utc, get_pool
from database.readonly import read_conn

logger = logging.getLogger(__name__)

MSK_TZ = timezone(timedelta(hours=3))

# ── Classification ────────────────────────────────────────────────────

BALANCE_PROVIDER = "balance"

# Recurring product revenue — the number that says whether the
# subscription business is growing.
SUBSCRIPTION_TYPES = ("subscription", "gift")

# Money parked in the wallet for later. Real cash-in, so it counts, but
# kept as its own class: folding it into "subscriptions" would overstate
# subscription growth.
TOPUP_TYPES = ("balance_topup",)

# Consumables bought on top of a subscription.
TRAFFIC_TYPES = ("traffic_pack",)

# Owner decision (2026-09-13): VPN revenue stands alone; the shop, the
# MTProto proxy and the in-bot game are each their own line, and all of
# them together are the total turnover (gross).

# Mini-shop goods (SCOPE.md: shop logic is untouched). We buy these and
# resell at a markup, so the charged amount is GMV, not margin.
SHOP_TYPES = ("telegram_premium", "telegram_stars", "steam", "apple_id", "spotify")
# Kept for callers of the v2 name: resale is the shop.
RESALE_TYPES = SHOP_TYPES

# The standalone MTProto proxy product.
PROXY_TYPES = ("proxy",)

# In-bot game purchases.
GAME_TYPES = ("farm_effect",)

# Lines reported next to VPN revenue, never inside it.
SEPARATE_CLASSES = ("shop", "proxy", "game")

# The headline ("net") revenue = these classes.
CASH_IN_CLASSES = ("subscription", "traffic", "topup")
CASH_IN_TYPES = SUBSCRIPTION_TYPES + TRAFFIC_TYPES + TOPUP_TYPES

# Balance top-ups paid inside Telegram live only in balance_transactions.
TELEGRAM_TOPUP_SOURCES = ("telegram", "telegram_stars")

# Provider spellings differ between the two top-up paths. One name per
# provider on the dashboard.
_PROVIDER_ALIASES = {"telegram": "telegram_payment", None: "unknown", "": "unknown"}

# Refunds and chargebacks. Not recorded as money anywhere in main today
# (Platega CHARGEBACKED / WATA Refund are ignored, see audit recon §3a);
# when the payment core starts logging them they land in payment_errors
# under one of these stages / codes and show up here without a code change.
REFUND_MARKERS = ("refund", "chargeback")

_CLASS_OF = {
    **{t: "topup" for t in TOPUP_TYPES},
    **{t: "subscription" for t in SUBSCRIPTION_TYPES},
    **{t: "traffic" for t in TRAFFIC_TYPES},
    **{t: "shop" for t in SHOP_TYPES},
    **{t: "proxy" for t in PROXY_TYPES},
    **{t: "game" for t in GAME_TYPES},
}

_PACK_RE = re.compile(r"^(?:traffic|bypass)_(\d+)gb$")


def revenue_class(purchase_type: str) -> str:
    return _CLASS_OF.get(purchase_type, "other")


def normalize_provider(provider: Optional[str]) -> str:
    return _PROVIDER_ALIASES.get(provider, provider)  # type: ignore[arg-type]


def pack_gb(tariff: Optional[str]) -> Optional[int]:
    """GB in a traffic pack, from its tariff key (`traffic_15gb`, `bypass_50gb`)."""
    if not tariff:
        return None
    m = _PACK_RE.match(tariff)
    return int(m.group(1)) if m else None


def product_key(purchase_type: Optional[str], tariff: Optional[str], is_combo: bool) -> str:
    """One product name per purchase.

    Combo is a separate product (SCOPE.md), even though the database still
    stores it as `tariff=basic|plus` + `is_combo=true`. Legacy business
    rows (biz_*) count as Plus (tariffs.normalize_tier).
    """
    ptype = purchase_type or "subscription"
    if ptype == "subscription":
        from app.services.tariffs import normalize_tier

        t = normalize_tier((tariff or "basic").lower())
        if t in ("basic", "plus"):
            return f"combo_{t}" if is_combo else t
        return "subscription_other"
    if ptype == "gift":
        return "gift"
    if ptype in TRAFFIC_TYPES:
        return "traffic_pack"
    if ptype in TOPUP_TYPES:
        return "topup"
    if ptype in SHOP_TYPES:
        return f"shop_{ptype}"
    if ptype == "proxy":
        return "proxy"
    if ptype in GAME_TYPES:
        return "game"
    return "other"


# ── Moscow calendar ───────────────────────────────────────────────────


def msk_day_start(now_utc: Optional[datetime] = None, days_ago: int = 0) -> datetime:
    """UTC instant of Moscow midnight, `days_ago` days back."""
    now = (now_utc or datetime.now(timezone.utc)).astimezone(MSK_TZ)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight -= timedelta(days=days_ago)
    return midnight.astimezone(timezone.utc)


def msk_date(dt_utc: datetime) -> date:
    return dt_utc.astimezone(MSK_TZ).date()


def window(days: int, now_utc: Optional[datetime] = None) -> dict[str, datetime]:
    """Current window: `days` Moscow days, today included, up to now.
    Previous window: the same span shifted back exactly `days` days,
    [since − days, now − days).

    Like-for-like (dashboard v4). v3 compared the current window — N−1
    full days plus the part of today already gone — with N FULL previous
    days, so every delta was biased downwards during the day (at 10:00 a
    7-day window was ~1/7 short). Now both windows cover the same hours
    and the same time of day; for days=1 that is "today until now" vs
    "yesterday until the same time"."""
    now = now_utc or datetime.now(timezone.utc)
    since = msk_day_start(now, days_ago=days - 1)
    shift = timedelta(days=days)
    return {
        "since": since,
        "until": now,
        "prev_since": since - shift,
        "prev_until": now - shift,
        "today": msk_day_start(now),
    }


def bucket_start(d: date, unit: str) -> date:
    """First day of the Moscow day / ISO week (Monday) / month containing d."""
    if unit == "day":
        return d
    if unit == "week":
        return d - timedelta(days=d.weekday())
    if unit == "month":
        return d.replace(day=1)
    raise ValueError(f"unknown unit {unit!r}")


def bucket_starts(last: date, unit: str, periods: int) -> list[date]:
    """`periods` consecutive bucket starts ending with the bucket of `last`."""
    out: list[date] = []
    cur = bucket_start(last, unit)
    for _ in range(periods):
        out.append(cur)
        if unit == "day":
            cur = cur - timedelta(days=1)
        elif unit == "week":
            cur = cur - timedelta(days=7)
        else:
            prev_month_end = cur - timedelta(days=1)
            cur = prev_month_end.replace(day=1)
    return list(reversed(out))


def fill_series(rows: Iterable[dict], starts: list[date]) -> list[dict[str, Any]]:
    """Emit every bucket, including empty ones — a chart with gaps silently
    rescales its x-axis and misreads as a trend. Rows: {bucket, kopecks, count}."""
    acc: dict[date, dict[str, int]] = {}
    for r in rows:
        b = r["bucket"]
        cur = acc.setdefault(b, {"kopecks": 0, "count": 0})
        cur["kopecks"] += int(r.get("kopecks") or 0)
        cur["count"] += int(r.get("count") or 0)
    return [
        {"date": s.isoformat(), "kopecks": acc.get(s, {}).get("kopecks", 0),
         "count": acc.get(s, {}).get("count", 0)}
        for s in starts
    ]


# ── Pure aggregation ──────────────────────────────────────────────────


def _bucket() -> dict[str, int]:
    return {"count": 0, "kopecks": 0}


def summarize_facts(facts: Iterable[dict]) -> dict[str, Any]:
    """Turn grouped paid-purchase rows into every money figure of a window.

    Each fact: {purchase_type, provider, tariff, is_combo, count, kopecks}.
    Rows from the balance provider are balance-funded spend, not revenue.
    """
    by_type: dict[str, dict[str, int]] = {}
    by_class: dict[str, dict[str, int]] = {}
    by_provider: dict[str, dict[str, int]] = {}
    by_product: dict[str, dict[str, int]] = {}
    stars = _bucket()
    internal = _bucket()
    traffic_gb = 0
    traffic_packs = 0

    for f in facts:
        ptype = f.get("purchase_type") or "subscription"
        provider = normalize_provider(f.get("provider"))
        n = int(f.get("count") or 0)
        k = int(f.get("kopecks") or 0)
        if provider == BALANCE_PROVIDER:
            internal["count"] += n
            internal["kopecks"] += k
            continue

        for bucket_map, key in (
            (by_type, ptype),
            (by_class, revenue_class(ptype)),
            (by_provider, provider),
            (by_product, product_key(ptype, f.get("tariff"), bool(f.get("is_combo")))),
        ):
            b = bucket_map.setdefault(key, _bucket())
            b["count"] += n
            b["kopecks"] += k

        if provider == "telegram_stars":
            stars["count"] += n
            stars["kopecks"] += k
        if ptype in TRAFFIC_TYPES:
            gb = pack_gb(f.get("tariff"))
            if gb:
                traffic_gb += gb * n
                traffic_packs += n

    net_k = sum(by_class.get(c, {}).get("kopecks", 0) for c in CASH_IN_CLASSES)
    net_n = sum(by_class.get(c, {}).get("count", 0) for c in CASH_IN_CLASSES)
    gross_k = sum(b["kopecks"] for b in by_class.values())
    gross_n = sum(b["count"] for b in by_class.values())

    return {
        # Headline: VPN cash-in = subscriptions + traffic + top-ups.
        "net_kopecks": net_k,
        "net_count": net_n,
        "avg_check_kopecks": (net_k // net_n) if net_n else 0,
        # VPN revenue, stated explicitly (same number as net).
        "vpn_kopecks": net_k,
        # Separate lines: never part of VPN revenue.
        "shop_kopecks": by_class.get("shop", {}).get("kopecks", 0),
        "proxy_kopecks": by_class.get("proxy", {}).get("kopecks", 0),
        "game_kopecks": by_class.get("game", {}).get("kopecks", 0),
        "other_kopecks": by_class.get("other", {}).get("kopecks", 0),
        # Total turnover: every external ruble = VPN + shop + proxy + game + other.
        "gross_kopecks": gross_k,
        "gross_count": gross_n,
        # Deprecated v2 alias of shop_kopecks.
        "gmv_resale_kopecks": by_class.get("shop", {}).get("kopecks", 0),
        "by_class": by_class,
        "by_type": by_type,
        "by_provider": by_provider,
        "by_product": by_product,
        "stars": stars,
        "traffic_gb_sold": traffic_gb,
        "traffic_packs_sold": traffic_packs,
        "balance_funded": internal,
    }


def empty_totals() -> dict[str, Any]:
    return summarize_facts([])


def delta_pct(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    """Percent change; None when there is nothing to compare against."""
    if not prev or cur is None:
        return None
    return round((cur - prev) / prev * 100, 1)


def per_user(kopecks: int, users: int) -> int:
    return (kopecks // users) if users else 0


def success_rate(paid: int, expired: int) -> Optional[float]:
    """paid ÷ (paid + expired). Pending attempts are left out of the
    denominator: they have not failed yet."""
    settled = paid + expired
    return round(paid / settled * 100, 1) if settled else None


def cohort_matrix(rows: Iterable[dict], sizes: dict[date, int], months: int,
                  today: Optional[date] = None) -> list[dict[str, Any]]:
    """Cumulative cash-in per payer, by cohort month and month offset.

    rows: {cohort: date, offset: int, kopecks: int}. sizes: payers per cohort.
    Cells after the current month are None (not yet observable), so the
    triangle reads correctly instead of flattening into zeros.
    """
    per: dict[date, dict[int, int]] = {}
    for r in rows:
        per.setdefault(r["cohort"], {})
        per[r["cohort"]][int(r["offset"])] = per[r["cohort"]].get(int(r["offset"]), 0) + int(r["kopecks"] or 0)

    out = []
    for cohort in sorted(sizes):
        size = sizes[cohort]
        cells = per.get(cohort, {})
        observable = _months_since(cohort, today)
        cum = 0
        ltv: list[Optional[int]] = []
        for m in range(months):
            if m > observable:
                ltv.append(None)
                continue
            cum += cells.get(m, 0)
            ltv.append(per_user(cum, size))
        out.append({
            "cohort": cohort.isoformat(),
            "payers": size,
            "revenue_kopecks": sum(cells.values()),
            "ltv_kopecks": ltv,
        })
    return out


def _months_since(cohort: date, today: Optional[date] = None) -> int:
    t = today or msk_date(datetime.now(timezone.utc))
    return (t.year - cohort.year) * 12 + (t.month - cohort.month)


# ── SQL (grouping only) ───────────────────────────────────────────────

_FACTS_SQL = """
    SELECT COALESCE(purchase_type, 'subscription') AS purchase_type,
           payment_provider AS provider,
           tariff,
           COALESCE(is_combo, FALSE) AS is_combo,
           COUNT(*)::BIGINT AS count,
           COALESCE(SUM(price_kopecks), 0)::BIGINT AS kopecks
      FROM pending_purchases
     WHERE status = 'paid'
       AND created_at >= $1 AND created_at < $2
     GROUP BY 1, 2, 3, 4
"""

_NATIVE_TOPUPS_SQL = """
    SELECT source AS provider,
           COUNT(*)::BIGINT AS count,
           COALESCE(SUM(amount), 0)::BIGINT AS kopecks
      FROM balance_transactions
     WHERE type = 'topup'
       AND source = ANY($3::text[])
       AND created_at >= $1 AND created_at < $2
     GROUP BY 1
"""


async def _facts(conn, since: datetime, until: datetime) -> list[dict]:
    rows = [dict(r) for r in await conn.fetch(_FACTS_SQL, _to_db_utc(since), _to_db_utc(until))]
    try:
        # Savepoint: on a fresh box without the table the error must not
        # abort the surrounding read-only transaction.
        async with conn.transaction():
            topups = await conn.fetch(
                _NATIVE_TOPUPS_SQL, _to_db_utc(since), _to_db_utc(until), list(TELEGRAM_TOPUP_SOURCES),
            )
        for r in topups:
            rows.append({
                "purchase_type": "balance_topup", "provider": r["provider"],
                "tariff": None, "is_combo": False,
                "count": int(r["count"]), "kopecks": int(r["kopecks"]),
            })
    except Exception as e:  # table missing on a fresh box
        logger.warning("revenue: native top-ups unavailable: %s", e)
    return rows


async def totals(since: datetime, until: Optional[datetime] = None) -> dict[str, Any]:
    """All money figures for [since, until). Kopecks throughout."""
    until = until or datetime.now(timezone.utc)
    pool = await get_pool()
    if pool is None:
        out = empty_totals()
    else:
        async with read_conn(pool) as conn:
            out = summarize_facts(await _facts(conn, since, until))
    out["since"] = since.isoformat()
    out["until"] = until.isoformat()
    return out


_SERIES_SQL = """
    SELECT DATE_TRUNC('{unit}', created_at AT TIME ZONE 'Europe/Moscow')::date AS bucket,
           COUNT(*)::BIGINT AS count,
           COALESCE(SUM(price_kopecks), 0)::BIGINT AS kopecks
      FROM pending_purchases
     WHERE status = 'paid'
       AND COALESCE(payment_provider, '') <> $3
       AND COALESCE(purchase_type, 'subscription') = ANY($4::text[])
       AND created_at >= $1 AND created_at < $2
     GROUP BY 1
    UNION ALL
    SELECT DATE_TRUNC('{unit}', created_at AT TIME ZONE 'Europe/Moscow')::date AS bucket,
           COUNT(*)::BIGINT, COALESCE(SUM(amount), 0)::BIGINT
      FROM balance_transactions
     WHERE type = 'topup' AND source = ANY($5::text[])
       AND created_at >= $1 AND created_at < $2
     GROUP BY 1
"""

_UNITS = ("day", "week", "month")


async def series(unit: str = "day", periods: int = 30,
                 now_utc: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Net (VPN cash-in) revenue per Moscow day / week / month."""
    if unit not in _UNITS:
        raise ValueError(f"unit must be one of {_UNITS}")
    now = now_utc or datetime.now(timezone.utc)
    starts = bucket_starts(msk_date(now), unit, periods)
    since = datetime.combine(starts[0], datetime.min.time(), MSK_TZ).astimezone(timezone.utc)
    pool = await get_pool()
    if pool is None:
        return fill_series([], starts)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _SERIES_SQL.format(unit=unit),
            _to_db_utc(since), _to_db_utc(now), BALANCE_PROVIDER,
            list(CASH_IN_TYPES), list(TELEGRAM_TOPUP_SOURCES),
        )
    return fill_series([dict(r) for r in rows], starts)


async def daily_series(days: int = 30) -> list[dict[str, Any]]:
    """Per-Moscow-day net revenue (kept for /payments/kpi)."""
    return await series("day", days)


_PAYERS_SQL = """
    WITH cash AS (
        SELECT telegram_id, created_at
          FROM pending_purchases
         WHERE status = 'paid'
           AND COALESCE(payment_provider, '') <> $3
           AND COALESCE(purchase_type, 'subscription') = ANY($4::text[])
           AND created_at < $2
        UNION ALL
        SELECT user_id, created_at
          FROM balance_transactions
         WHERE type = 'topup' AND source = ANY($5::text[]) AND created_at < $2
    ), per_user AS (
        SELECT telegram_id,
               MIN(created_at) AS first_at,
               BOOL_OR(created_at >= $1) AS in_window
          FROM cash GROUP BY telegram_id
    )
    SELECT COUNT(*) FILTER (WHERE in_window)::BIGINT AS payers,
           COUNT(*) FILTER (WHERE in_window AND first_at >= $1)::BIGINT AS new_payers,
           COUNT(*)::BIGINT AS payers_to_date
      FROM per_user
"""


async def payers(since: datetime, until: Optional[datetime] = None) -> dict[str, int]:
    """Users who brought VPN cash-in during the window; `new` = their first
    cash-in ever is inside the window, `returning` = paid before too."""
    until = until or datetime.now(timezone.utc)
    pool = await get_pool()
    if pool is None:
        return {"payers": 0, "new": 0, "returning": 0, "payers_to_date": 0}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _PAYERS_SQL, _to_db_utc(since), _to_db_utc(until), BALANCE_PROVIDER,
            list(CASH_IN_TYPES), list(TELEGRAM_TOPUP_SOURCES),
        )
    p, n = int(row["payers"] or 0), int(row["new_payers"] or 0)
    return {"payers": p, "new": n, "returning": p - n, "payers_to_date": int(row["payers_to_date"] or 0)}


async def users_registered_before(until: datetime) -> int:
    pool = await get_pool()
    if pool is None:
        return 0
    async with pool.acquire() as conn:
        return int(await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at < $1", _to_db_utc(until),
        ) or 0)


def provider_row(r: dict) -> dict[str, Any]:
    """One provider line from grouped invoice counts. Pure (tested).

    `expired` = every invoice that ended unpaid: explicitly marked
    `expired` (a newer invoice of the same user closes the old one) plus
    `abandoned` — still `pending` although its expires_at passed. Nothing
    in the bot sweeps stale invoices (expire_old_pending_purchases is
    never called), so abandoned checkouts stay `pending` forever; v3
    counted them as "pending" and left them out of the success rate,
    which overstated it."""
    paid = int(r.get("paid") or 0)
    marked = int(r.get("expired") or 0)
    abandoned = int(r.get("abandoned") or 0)
    created = int(r.get("created") or 0)
    inv, pu = int(r.get("invoiced_users") or 0), int(r.get("paid_users") or 0)
    return {
        "provider": normalize_provider(r.get("provider")),
        "created": created,
        "paid": paid,
        "expired": marked + abandoned,
        "expired_marked": marked,
        "abandoned": abandoned,
        "pending": int(r.get("pending") or 0),
        "kopecks": int(r.get("kopecks") or 0),
        "success_rate": success_rate(paid, marked + abandoned),
        "conversion": round(paid / created * 100, 1) if created else None,
        "invoiced_users": inv,
        "paid_users": pu,
        "user_conversion": round(pu / inv * 100, 1) if inv else None,
    }


# 'telegram' and 'telegram_payment' are one provider (two spellings in
# the two Telegram paths); grouping on the raw column split it in two rows.
_PROVIDER_PERF_SQL = """
    SELECT CASE WHEN payment_provider = 'telegram' THEN 'telegram_payment'
                ELSE COALESCE(payment_provider, 'unknown') END AS provider,
           COUNT(*)::BIGINT AS created,
           COUNT(*) FILTER (WHERE status = 'paid')::BIGINT AS paid,
           COUNT(*) FILTER (WHERE status = 'expired')::BIGINT AS expired,
           COUNT(*) FILTER (WHERE status = 'pending' AND expires_at <= $4)::BIGINT AS abandoned,
           COUNT(*) FILTER (WHERE status = 'pending' AND expires_at > $4)::BIGINT AS pending,
           COUNT(DISTINCT telegram_id)::BIGINT AS invoiced_users,
           COUNT(DISTINCT telegram_id) FILTER (WHERE status = 'paid')::BIGINT AS paid_users,
           COALESCE(SUM(price_kopecks) FILTER (WHERE status = 'paid'), 0)::BIGINT AS kopecks
      FROM pending_purchases
     WHERE created_at >= $1 AND created_at < $2
       AND COALESCE(payment_provider, '') <> $3
     GROUP BY 1
     ORDER BY kopecks DESC
"""


async def provider_performance(since: datetime, until: Optional[datetime] = None,
                               now_utc: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Per-provider revenue, attempts, conversion and success rate for
    invoices created in [since, until).

    conversion = paid ÷ created (every invoice opened in the window);
    success_rate = paid ÷ (paid + ended unpaid) — invoices still inside
    their TTL are left out of it. Caveat: a new invoice closes the user's
    previous one, so a user who retried counts one unpaid attempt;
    `user_conversion` (users who paid ÷ users who opened an invoice) is
    immune to that.
    """
    until = until or datetime.now(timezone.utc)
    now = now_utc or datetime.now(timezone.utc)
    pool = await get_pool()
    if pool is None:
        return []
    async with read_conn(pool) as conn:
        rows = await conn.fetch(
            _PROVIDER_PERF_SQL, _to_db_utc(since), _to_db_utc(until), BALANCE_PROVIDER, _to_db_utc(now),
        )
    return [provider_row(dict(r)) for r in rows]


async def mrr_snapshot() -> dict[str, Any]:
    """Monthly recurring revenue, normalised from actual subscription sales.

    Each active subscription contributes its latest direct purchase price
    / period_days * 30. Not "revenue over the last 30 days" — that moves
    with billing cycles rather than with the subscriber base.
    """
    pool = await get_pool()
    if pool is None:
        return {"mrr_kopecks": 0, "active_subscriptions": 0}

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """WITH latest AS (
                   SELECT DISTINCT ON (pp.telegram_id)
                          pp.telegram_id,
                          pp.price_kopecks,
                          NULLIF(pp.period_days, 0) AS period_days
                     FROM pending_purchases pp
                     JOIN subscriptions s ON s.telegram_id = pp.telegram_id
                    WHERE pp.status = 'paid'
                      AND pp.purchase_type = 'subscription'
                      AND s.status = 'active'
                      AND s.expires_at > NOW()
                    ORDER BY pp.telegram_id, pp.created_at DESC
               )
               SELECT COUNT(*)::BIGINT AS n,
                      COALESCE(SUM(price_kopecks::numeric / period_days * 30), 0)::BIGINT AS mrr
                 FROM latest
                WHERE period_days IS NOT NULL"""
        )
    return {
        "mrr_kopecks": int(row["mrr"] or 0) if row else 0,
        "active_subscriptions": int(row["n"] or 0) if row else 0,
    }


async def balance_spend(since: datetime, until: Optional[datetime] = None) -> dict[str, Any]:
    """Purchases paid from the wallet (incl. auto-renewals). Internal money
    movement: delivered product, but no new cash."""
    until = until or datetime.now(timezone.utc)
    pool = await get_pool()
    if pool is None:
        return {"count": 0, "kopecks": 0, "auto_renew": _bucket()}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*)::BIGINT AS n,
                      COALESCE(-SUM(amount), 0)::BIGINT AS k,
                      COUNT(*) FILTER (WHERE source = 'auto_renew')::BIGINT AS ar_n,
                      COALESCE(-SUM(amount) FILTER (WHERE source = 'auto_renew'), 0)::BIGINT AS ar_k
                 FROM balance_transactions
                WHERE type = 'subscription_payment'
                  AND created_at >= $1 AND created_at < $2""",
            _to_db_utc(since), _to_db_utc(until),
        )
    return {
        "count": int(row["n"] or 0),
        "kopecks": int(row["k"] or 0),
        "auto_renew": {"count": int(row["ar_n"] or 0), "kopecks": int(row["ar_k"] or 0)},
    }


async def refunds(since: datetime, until: Optional[datetime] = None) -> dict[str, Any]:
    """Refunds / chargebacks recorded in payment_errors. `recorded=False`
    means the table is missing; zero with recorded=True means none logged
    — which in main today is also what an unlogged chargeback looks like."""
    until = until or datetime.now(timezone.utc)
    pool = await get_pool()
    empty = {"count": 0, "kopecks": 0, "recorded": False}
    if pool is None:
        return empty
    patterns = [f"%{m}%" for m in REFUND_MARKERS]
    try:
        async with read_conn(pool) as conn:
            row = await conn.fetchrow(
                """SELECT COUNT(*)::BIGINT AS n,
                          COALESCE(SUM(amount_rubles) * 100, 0)::BIGINT AS k
                     FROM payment_errors
                    WHERE (stage ILIKE ANY($3::text[]) OR COALESCE(error_code, '') ILIKE ANY($3::text[]))
                      AND created_at >= $1 AND created_at < $2""",
                _to_db_utc(since), _to_db_utc(until), patterns,
            )
    except Exception as e:
        logger.warning("revenue.refunds unavailable: %s", e)
        return empty
    return {"count": int(row["n"] or 0), "kopecks": int(row["k"] or 0), "recorded": True}


async def liabilities() -> dict[str, Any]:
    """Money we owe users right now: positive wallet balances.

    Withdrawals to the outside were removed (owner 2026-09-14); the
    withdrawal_requests table is kept as history and is not read here."""
    pool = await get_pool()
    if pool is None:
        return {"balance_kopecks": 0, "users_with_balance": 0}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(balance), 0)::BIGINT AS k,
                      COUNT(*)::BIGINT AS n
                 FROM users WHERE balance > 0"""
        )
    return {
        "balance_kopecks": int(row["k"] or 0),
        "users_with_balance": int(row["n"] or 0),
    }


async def referral_payouts(since: datetime, until: Optional[datetime] = None) -> dict[str, Any]:
    """Cashback credited to referrers (referral_rewards.reward_amount, kopecks)."""
    until = until or datetime.now(timezone.utc)
    pool = await get_pool()
    if pool is None:
        return {"count": 0, "kopecks": 0, "referrers": 0}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*)::BIGINT AS n,
                      COALESCE(SUM(reward_amount), 0)::BIGINT AS k,
                      COUNT(DISTINCT referrer_id)::BIGINT AS r
                 FROM referral_rewards
                WHERE created_at >= $1 AND created_at < $2""",
            _to_db_utc(since), _to_db_utc(until),
        )
    return {"count": int(row["n"] or 0), "kopecks": int(row["k"] or 0), "referrers": int(row["r"] or 0)}


_COHORT_SQL = """
    WITH cash AS (
        SELECT telegram_id, created_at, price_kopecks::BIGINT AS kopecks
          FROM pending_purchases
         WHERE status = 'paid'
           AND COALESCE(payment_provider, '') <> $2
           AND COALESCE(purchase_type, 'subscription') = ANY($3::text[])
        UNION ALL
        SELECT user_id, created_at, amount::BIGINT
          FROM balance_transactions
         WHERE type = 'topup' AND source = ANY($4::text[])
    ), first AS (
        SELECT telegram_id,
               DATE_TRUNC('month', MIN(created_at) AT TIME ZONE 'Europe/Moscow')::date AS cohort
          FROM cash GROUP BY telegram_id
    )
    SELECT f.cohort,
           ((EXTRACT(YEAR FROM DATE_TRUNC('month', c.created_at AT TIME ZONE 'Europe/Moscow')) - EXTRACT(YEAR FROM f.cohort)) * 12
            + EXTRACT(MONTH FROM DATE_TRUNC('month', c.created_at AT TIME ZONE 'Europe/Moscow')) - EXTRACT(MONTH FROM f.cohort))::INT AS "offset",
           COALESCE(SUM(c.kopecks), 0)::BIGINT AS kopecks,
           COUNT(DISTINCT c.telegram_id)::BIGINT AS users
      FROM cash c JOIN first f USING (telegram_id)
     WHERE f.cohort >= $1
     GROUP BY 1, 2
"""


async def cohort_ltv(months: int = 12, now_utc: Optional[datetime] = None) -> dict[str, Any]:
    """Cohort LTV: payers grouped by the Moscow month of their first VPN
    cash-in; cumulative cash-in per payer at month 0, 1, 2…"""
    now = now_utc or datetime.now(timezone.utc)
    starts = bucket_starts(msk_date(now), "month", months)
    pool = await get_pool()
    if pool is None:
        return {"months": months, "cohorts": [], "avg_ltv_kopecks": 0}
    async with pool.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(
            _COHORT_SQL, starts[0], BALANCE_PROVIDER,
            list(CASH_IN_TYPES), list(TELEGRAM_TOPUP_SOURCES),
        )]
    sizes = {r["cohort"]: int(r["users"]) for r in rows if int(r["offset"]) == 0}
    matrix = cohort_matrix(rows, sizes, months)
    total_k = sum(c["revenue_kopecks"] for c in matrix)
    total_p = sum(c["payers"] for c in matrix)
    return {"months": months, "cohorts": matrix, "avg_ltv_kopecks": per_user(total_k, total_p)}


async def money_report(days: int = 30, now_utc: Optional[datetime] = None) -> dict[str, Any]:
    """Everything the Money screen shows for one window, one definition."""
    w = window(days, now_utc)
    current = await totals(w["since"], w["until"])
    previous = await totals(w["prev_since"], w["prev_until"])
    today = await totals(w["today"], w["until"])
    pay = await payers(w["since"], w["until"])
    prev_pay = await payers(w["prev_since"], w["prev_until"])
    registered = await users_registered_before(w["until"])

    return {
        "window_days": days,
        "since": w["since"].isoformat(),
        "today": today,
        "current": current,
        "previous": previous,
        "delta_pct": {
            "net": delta_pct(current["net_kopecks"], previous["net_kopecks"]),
            "gross": delta_pct(current["gross_kopecks"], previous["gross_kopecks"]),
            "count": delta_pct(current["net_count"], previous["net_count"]),
            "avg_check": delta_pct(current["avg_check_kopecks"], previous["avg_check_kopecks"]),
            "payers": delta_pct(pay["payers"], prev_pay["payers"]),
        },
        "payers": pay,
        "arppu_kopecks": per_user(current["net_kopecks"], pay["payers"]),
        "arpu_kopecks": per_user(current["net_kopecks"], registered),
        "registered_users": registered,
        "series": await series("day", days, now_utc),
        "weekly": await series("week", 12, now_utc),
        "monthly": await series("month", 12, now_utc),
        "providers": await provider_performance(w["since"], w["until"]),
        "balance_spend": await balance_spend(w["since"], w["until"]),
        "refunds": await refunds(w["since"], w["until"]),
        "referral_payouts": await referral_payouts(w["since"], w["until"]),
        "liabilities": await liabilities(),
        "mrr": await mrr_snapshot(),
    }


async def all_time() -> dict[str, Any]:
    """All-time net revenue, payers, ARPPU — for legacy /stats/revenue."""
    epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
    t = await totals(epoch)
    p = await payers(epoch)
    return {
        "net_kopecks": t["net_kopecks"],
        "gross_kopecks": t["gross_kopecks"],
        "payers": p["payers"],
        "arppu_kopecks": per_user(t["net_kopecks"], p["payers"]),
    }


async def hour_of_day(days: int, now_utc: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Net revenue by Moscow hour of day over the last `days` days."""
    now = now_utc or datetime.now(timezone.utc)
    since = msk_day_start(now, days_ago=days - 1)
    pool = await get_pool()
    acc = {h: {"kopecks": 0, "count": 0} for h in range(24)}
    if pool is not None:
        async with read_conn(pool) as conn:
            rows = await conn.fetch(
                _SERIES_SQL.replace(
                    "DATE_TRUNC('{unit}', created_at AT TIME ZONE 'Europe/Moscow')::date",
                    "EXTRACT(HOUR FROM created_at AT TIME ZONE 'Europe/Moscow')::int",
                ),
                _to_db_utc(since), _to_db_utc(now), BALANCE_PROVIDER,
                list(CASH_IN_TYPES), list(TELEGRAM_TOPUP_SOURCES),
            )
        for r in rows:
            acc[int(r["bucket"])]["kopecks"] += int(r["kopecks"])
            acc[int(r["bucket"])]["count"] += int(r["count"])
    return [{"hour": h, **v} for h, v in acc.items()]
