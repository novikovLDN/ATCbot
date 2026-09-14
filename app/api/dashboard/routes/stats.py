"""
Analytics / business-metrics endpoints (legacy shapes).

These endpoints predate dashboard v3 and keep their response shapes for
the screens and scripts that still read them, but every money and
subscriber figure is now computed by the canonical definitions in
database/revenue.py and database/metrics.py (docs/dashboard/metrics.md).
Before v3 they summed the `payments` table, which counts wallet spend a
second time on top of the top-up that funded it, and used UTC days.

The database/admin.py functions they used to call are untouched: the
bot's own admin menu still uses them.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

import database
from app.api.dashboard.deps import require_admin
from database import metrics as mx
from database import revenue as rev

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])


def _server_error(code: str) -> HTTPException:
    """500 with a stable error code only. The exception text (SQL, DSNs,
    driver messages) goes to the log, never into the response (P2-16)."""
    logger.exception("DASHBOARD_ROUTE_FAIL %s", code)
    return HTTPException(500, code)


def _parse_since(since: str | None) -> datetime | None:
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "invalid_since")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.get("/overview")
async def stats_overview():
    """Legacy home blob. Counters that are not money come from
    get_extended_bot_stats(); revenue, MRR, active subscriptions, "today"
    and the two rates are overridden with canonical values (kopecks)."""
    try:
        data = await database.get_extended_bot_stats()
        all_time = await rev.all_time()
        mrr = await rev.mrr_snapshot()
        active = await mx.active_subscriptions()
        w = rev.window(30)
        renewals = await mx.renewals(w["since"], w["until"])
        trial = await mx.trial_conversion(w["since"], w["until"])
        today = await mx.daily_activity(1)
    except Exception as e:
        raise _server_error("stats_overview_failed")
    data.update({
        "total_revenue": all_time["net_kopecks"],
        "mrr": mrr["mrr_kopecks"],
        "active_subs": active["with_access"],
        "active_subscriptions": active["with_access"],
        "active_paid_subscriptions": active["paid"],
        "new_today": today[-1]["new_users"] if today else 0,
        "churn_rate": renewals["churn_rate"] or 0,
        "conversion_rate": trial["rate_any"] or 0,
        "definitions": "v3",
    })
    return data


@router.get("/business")
async def stats_business():
    """avg_payment_approval_time_seconds, avg_subscription_lifetime_days,
    avg_renewals_per_user, approval_rate_percent. Legacy: approval rate
    reads the old `payments` pending flow; provider success rate on the
    Money screen supersedes it."""
    try:
        return await database.get_business_metrics()
    except Exception as e:
        raise _server_error("business_metrics_failed")


@router.get("/revenue")
async def stats_revenue():
    """All-time net revenue, payers, ARPU/ARPPU/LTV in rubles (legacy shape).

    `arpu_rubles` used to be all-time payments ÷ paying users, which is
    ARPPU; it now means net ÷ registered users, and ARPPU has its own key.
    """
    try:
        t = await rev.all_time()
        registered = await rev.users_registered_before(datetime.now(timezone.utc))
    except Exception as e:
        raise _server_error("revenue_failed")
    return {
        "total_revenue_rubles": t["net_kopecks"] / 100,
        "gross_revenue_rubles": t["gross_kopecks"] / 100,
        "paying_users": t["payers"],
        "arpu_rubles": rev.per_user(t["net_kopecks"], registered) / 100,
        "arppu_rubles": t["arppu_kopecks"] / 100,
        "avg_ltv_rubles": t["arppu_kopecks"] / 100,
    }


@router.get("/period")
async def stats_period(
    hours: int = Query(24, gt=0, le=8760),
    since: str | None = Query(None),
):
    """Counters over [since, now) or trailing `hours` window."""
    try:
        return await database.get_analytics_by_period(
            hours, since=_parse_since(since),
        )
    except Exception as e:
        raise _server_error("period_failed")


@router.get("/purchase-breakdown")
async def stats_purchase_breakdown():
    """Sales (not revenue) by tariff and trailing window — every paid
    invoice, including the shop's proxy row. Kopecks."""
    try:
        return await database.get_purchase_breakdown()
    except Exception as e:
        raise _server_error("breakdown_failed")


@router.get("/promo")
async def stats_promo():
    """Promo-code usage stats."""
    try:
        return await database.get_promo_stats()
    except Exception as e:
        raise _server_error("promo_failed")


@router.get("/daily")
async def stats_daily(days: int = Query(30, gt=0, le=180)):
    """Per-Moscow-day series: net revenue + new users / subscriptions.
    One row per day in the window, empty days included."""
    try:
        money = await rev.series("day", days)
        activity = await mx.daily_activity(days)
    except Exception as e:
        raise _server_error("daily_failed")
    act = {a["date"]: a for a in activity}
    return {
        "days": days,
        "tz": "Europe/Moscow",
        "series": [
            {
                "date": p["date"],
                "revenue_rubles": p["kopecks"] / 100,
                "payments_count": p["count"],
                "new_users": act.get(p["date"], {}).get("new_users", 0),
                "new_subscriptions": act.get(p["date"], {}).get("new_subscriptions", 0),
                "new_paid_subscriptions": act.get(p["date"], {}).get("new_paid_subscriptions", 0),
            }
            for p in money
        ],
    }


@router.get("/hourly")
async def stats_hourly(days: int = Query(7, gt=0, le=90)):
    """Hour-of-day (Europe/Moscow) totals over the last `days` days.
    Revenue is canonical net; user / subscription counts as before."""
    try:
        base = await database.get_hourly_timeseries(days)
        money = await rev.hour_of_day(days)
    except Exception as e:
        raise _server_error("hourly_failed")
    by_hour = {m["hour"]: m for m in money}
    for row in base.get("series", []):
        m = by_hour.get(row["hour"], {"kopecks": 0, "count": 0})
        row["revenue_rubles"] = m["kopecks"] / 100
        row["payments_count"] = m["count"]
    return base
