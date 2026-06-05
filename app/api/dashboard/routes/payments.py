"""Payments endpoints — KPIs, breakdowns, recent feed, single lookup."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

import database
from app.api.dashboard.deps import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


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


@router.get("/pending")
async def payments_pending():
    """All payments stuck in pending — useful for catching webhook
    drops and manual reconciliation."""
    try:
        rows = await database.get_pending_payments()
    except Exception as e:
        raise HTTPException(500, f"pending_failed: {e}")
    return _serialize(rows or [])


@router.get("/revenue")
async def payments_revenue(hours: int = Query(24, gt=0, le=8760)):
    """Total + per-type revenue for the trailing N hours.
    Source of truth: pending_purchases (status='paid')."""
    try:
        return _serialize(await database.get_revenue_for_period(hours))
    except Exception as e:
        raise HTTPException(500, f"revenue_failed: {e}")


@router.get("/by-provider")
async def payments_by_provider(hours: int = Query(24, gt=0, le=8760)):
    """Breakdown of paid purchases by payment provider (platega /
    cryptobot / telegram_stars / lava / balance / unknown)."""
    try:
        return _serialize(await database.get_payments_by_provider(hours))
    except Exception as e:
        raise HTTPException(500, f"by_provider_failed: {e}")


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
        raise HTTPException(500, f"recent_failed: {e}")


@router.get("/traffic")
async def payments_traffic(hours: int = Query(24, gt=0, le=8760)):
    """Stats for GB-traffic purchases (separate flow from subscription)."""
    try:
        return _serialize(await database.get_traffic_stats(hours))
    except Exception as e:
        raise HTTPException(500, f"traffic_failed: {e}")


@router.get("/{payment_id}")
async def payment_detail(payment_id: int = Path(..., gt=0)):
    try:
        row = await database.get_payment(payment_id)
    except Exception as e:
        raise HTTPException(500, f"payment_detail_failed: {e}")
    if not row:
        raise HTTPException(404, "Payment not found")
    return _serialize(row)
