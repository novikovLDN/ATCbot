"""
Analytics / business-metrics endpoints.

All routes proxy directly to existing functions in `database.admin`
and `database.subscriptions`. We don't compute anything new here —
the bot already has full coverage, we just expose it over HTTP.

Time-range params accept a trailing-window in hours: `?hours=24`,
`?hours=720` etc. Routes that need a calendar-day window also accept
`?since=<ISO datetime>` which overrides `hours` and uses that as an
absolute lower bound (used for the "Сегодня (МСК)" dashboard tile).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

import database
from app.api.dashboard.deps import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


def _parse_since(since: str | None) -> datetime | None:
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "invalid_since")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/overview")
async def stats_overview():
    """Single-shot blob for the dashboard home page.
    Wraps get_extended_bot_stats() — returns total_users,
    active_subs, pending_payments, business_metrics, referral stats.

    Adds active_paid_subscriptions — same shape as active_subscriptions
    but excludes trials, bypass-only and biz-tariff rows so the number
    reflects only currently-paying-for-VPN users."""
    try:
        data = await database.get_extended_bot_stats()
        try:
            data["active_paid_subscriptions"] = (
                await database.get_active_paid_subscriptions_count()
            )
        except Exception:
            data["active_paid_subscriptions"] = data.get("active_subscriptions")
        return data
    except Exception as e:
        raise HTTPException(500, f"stats_overview_failed: {e}")


@router.get("/business")
async def stats_business():
    """avg_payment_approval_time_seconds, avg_subscription_lifetime_days,
    avg_renewals_per_user, approval_rate_percent."""
    try:
        return await database.get_business_metrics()
    except Exception as e:
        raise HTTPException(500, f"business_metrics_failed: {e}")


@router.get("/revenue")
async def stats_revenue():
    """Aggregate revenue / LTV / ARPU."""
    try:
        total = await database.get_total_revenue()
        paying = await database.get_paying_users_count()
        arpu = await database.get_arpu()
        ltv = await database.get_ltv()
        return {
            "total_revenue_rubles": total,
            "paying_users": paying,
            "arpu_rubles": arpu,
            "avg_ltv_rubles": ltv,
        }
    except Exception as e:
        raise HTTPException(500, f"revenue_failed: {e}")


@router.get("/period")
async def stats_period(
    hours: int = Query(24, gt=0, le=8760),
    since: str | None = Query(None),
):
    """Aggregates over [since, now) or trailing `hours` window.
    `hours` capped at one year. `since` is an ISO datetime — when
    supplied it overrides `hours`."""
    try:
        return await database.get_analytics_by_period(
            hours, since=_parse_since(since),
        )
    except Exception as e:
        raise HTTPException(500, f"period_failed: {e}")


@router.get("/purchase-breakdown")
async def stats_purchase_breakdown():
    """Counts + revenue by tariff and time window."""
    try:
        return await database.get_purchase_breakdown()
    except Exception as e:
        raise HTTPException(500, f"breakdown_failed: {e}")


@router.get("/promo")
async def stats_promo():
    """Promo-code usage stats."""
    try:
        return await database.get_promo_stats()
    except Exception as e:
        raise HTTPException(500, f"promo_failed: {e}")
