"""Payments endpoints — KPIs, breakdowns, recent feed, single lookup."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

import database
from app.api.dashboard.deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])


def _server_error(code: str) -> HTTPException:
    """500 with a stable error code only. The exception text (SQL, DSNs,
    driver messages) goes to the log, never into the response (P2-16)."""
    logger.exception("DASHBOARD_ROUTE_FAIL %s", code)
    return HTTPException(500, code)


def _parse_since(since: Optional[str]) -> Optional[datetime]:
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "invalid_since")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _serialize(value):
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return None
    return value


@router.get("/kpi")
async def payments_kpi(days: int = Query(30, gt=0, le=365)):
    """Corrected money KPIs — one definition, Moscow days, kopecks.

    Supersedes /payments/revenue, which summed pending_purchases raw:
    balance top-ups were counted as sales (and then again when spent),
    and Steam/Apple resale was folded into the same average order value
    as a 300 ₽ subscription.

    `previous` covers the immediately preceding window of the same
    length, so a delta always has a period to be measured against.
    """
    from database import revenue as rev

    now = datetime.now(timezone.utc)
    today_start = rev.msk_day_start(now)
    window_start = rev.msk_day_start(now, days_ago=days - 1)
    prev_start = rev.msk_day_start(now, days_ago=(days * 2) - 1)

    try:
        today = await rev.totals(today_start)
        current = await rev.totals(window_start)
        previous = await rev.totals(prev_start, until=window_start)
        series = await rev.daily_series(days)
        providers = await rev.provider_performance(window_start)
        mrr = await rev.mrr_snapshot()
    except Exception as e:
        raise _server_error("kpi_failed")

    def _delta(cur: int, prev: int) -> Optional[float]:
        if not prev:
            return None
        return round((cur - prev) / prev * 100, 1)

    return {
        "window_days": days,
        "today": today,
        "current": current,
        "previous": previous,
        "delta_pct": {
            "net": _delta(current["net_kopecks"], previous["net_kopecks"]),
            "count": _delta(current["net_count"], previous["net_count"]),
            "avg_check": _delta(
                current["avg_check_kopecks"], previous["avg_check_kopecks"]
            ),
        },
        "series": series,
        "providers": providers,
        "mrr": mrr,
    }


@router.get("/pending")
async def payments_pending():
    """All payments stuck in pending — useful for catching webhook
    drops and manual reconciliation."""
    try:
        rows = await database.get_pending_payments()
    except Exception as e:
        raise _server_error("pending_failed")
    return _serialize(rows or [])


def _window(hours: int, since: Optional[str] = None) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = _parse_since(since) or (now - timedelta(hours=hours))
    return start.astimezone(timezone.utc), now


def _rub(kopecks: int) -> float:
    return kopecks / 100


@router.get("/revenue")
async def payments_revenue(
    hours: int = Query(24, gt=0, le=8760),
    since: Optional[str] = Query(None),
):
    """Revenue over the window (legacy shape, rubles). Canonical since v3:
    `revenue_rubles` is VPN cash-in (docs/dashboard/metrics.md); shop,
    proxy and game are separate lines, `gross_rubles` is total turnover."""
    from database import revenue as rev

    start, end = _window(hours, since)
    try:
        t = await rev.totals(start, end)
    except Exception as e:
        raise _server_error("revenue_failed")
    return {
        "revenue_rubles": _rub(t["net_kopecks"]),
        "payments_count": t["net_count"],
        "avg_check_rubles": _rub(t["avg_check_kopecks"]),
        "gross_rubles": _rub(t["gross_kopecks"]),
        "shop_rubles": _rub(t["shop_kopecks"]),
        "proxy_rubles": _rub(t["proxy_kopecks"]),
        "game_rubles": _rub(t["game_kopecks"]),
        "by_type": {
            k: {"count": v["count"], "revenue_rubles": _rub(v["kopecks"])}
            for k, v in sorted(t["by_type"].items(), key=lambda kv: -kv[1]["kopecks"])
        },
    }


@router.get("/by-provider")
async def payments_by_provider(hours: int = Query(24, gt=0, le=8760)):
    """External money by provider (canonical; Telegram top-ups included,
    `telegram` and `telegram_payment` merged)."""
    from database import revenue as rev

    start, end = _window(hours)
    try:
        t = await rev.totals(start, end)
    except Exception as e:
        raise _server_error("by_provider_failed")
    return [
        {"provider": k, "count": v["count"], "revenue_rubles": _rub(v["kopecks"])}
        for k, v in sorted(t["by_provider"].items(), key=lambda kv: -kv[1]["kopecks"])
    ]


@router.get("/breakdown")
async def payments_breakdown(hours: int = Query(24, gt=0, le=8760)):
    """Multi-axis sales breakdown for the last N hours. `by_tariff` and
    `by_apple_nominal` are sales detail from get_payments_breakdown();
    `total`, `by_provider` and `by_type` are canonical (v3)."""
    from database import revenue as rev

    start, end = _window(hours)
    try:
        base = await database.get_payments_breakdown(hours)
        t = await rev.totals(start, end)
    except Exception as e:
        raise _server_error("breakdown_failed")
    base = dict(base or {})
    base["total"] = {"count": t["net_count"], "revenue_rubles": _rub(t["net_kopecks"]),
                     "gross_rubles": _rub(t["gross_kopecks"])}
    base["by_provider"] = [
        {"provider": k, "count": v["count"], "revenue_rubles": _rub(v["kopecks"])}
        for k, v in sorted(t["by_provider"].items(), key=lambda kv: -kv[1]["kopecks"])
    ]
    base["by_type"] = [
        {"purchase_type": k, "count": v["count"], "revenue_rubles": _rub(v["kopecks"])}
        for k, v in sorted(t["by_type"].items(), key=lambda kv: -kv[1]["kopecks"])
    ]
    return _serialize(base)


@router.get("/recent")
async def payments_recent(
    limit: int = Query(100, gt=0, le=500),
    hours: Optional[int] = Query(None, gt=0, le=8760),
    status: Optional[str] = Query(None, regex="^(pending|paid|expired)$"),
):
    """Recent purchases for the global feed.

    `status` filters to one specific state; without it returns all
    states in the window so the admin can spot stuck pendings and
    expired carts in one place. `hours=None` means no time filter."""
    try:
        return _serialize(
            await database.get_recent_payments_feed(
                limit=limit, hours=hours, status=status,
            )
        )
    except Exception as e:
        raise _server_error("recent_failed")


@router.get("/traffic")
async def payments_traffic(hours: int = Query(24, gt=0, le=8760)):
    """Traffic-pack sales. count / revenue / GB are canonical (paid packs,
    GB from the pack tariff); `by_method` is the raw traffic_purchases view,
    which also holds combo GB grants at price 0."""
    from database import revenue as rev

    start, end = _window(hours)
    try:
        base = await database.get_traffic_stats(hours)
        t = await rev.totals(start, end)
    except Exception as e:
        raise _server_error("traffic_failed")
    traffic = t["by_class"].get("traffic", {"count": 0, "kopecks": 0})
    base = dict(base or {})
    base.update({
        "count": traffic["count"],
        "revenue_rubles": _rub(traffic["kopecks"]),
        "total_gb": t["traffic_gb_sold"],
    })
    return _serialize(base)


@router.get("/errors/summary")
async def payments_errors_summary(hours: int = Query(24, gt=0, le=8760)):
    try:
        return _serialize(await database.get_payment_errors_summary(hours))
    except Exception as e:
        raise _server_error("errors_summary_failed")


@router.get("/errors")
async def payments_errors(
    limit: int = Query(100, gt=0, le=500),
    hours: Optional[int] = Query(168, gt=0, le=8760),
    provider: Optional[str] = Query(None, max_length=40),
    stage: Optional[str] = Query(None, max_length=60),
):
    try:
        return _serialize(
            await database.get_recent_payment_errors(
                limit=limit, hours=hours, provider=provider, stage=stage,
            )
        )
    except Exception as e:
        raise _server_error("errors_failed")


@router.get("/{payment_id}")
async def payment_detail(payment_id: int = Path(..., gt=0)):
    try:
        row = await database.get_payment(payment_id)
    except Exception as e:
        raise _server_error("payment_detail_failed")
    if not row:
        raise HTTPException(404, "Payment not found")
    return _serialize(row)
