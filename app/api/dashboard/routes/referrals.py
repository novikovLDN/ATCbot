"""Referrals — overall stats, top partners, per-partner detail and history."""
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


@router.get("/overall")
async def referrals_overall():
    """Platform-wide referral KPIs."""
    try:
        data = await database.get_referral_overall_stats()
    except Exception as e:
        raise HTTPException(500, f"overall_failed: {e}")
    return _serialize(data or {})


@router.get("/top")
async def referrals_top(
    sort_by: str = Query("total_revenue", regex="^(total_revenue|invited_count|cashback_paid)$"),
    sort_order: str = Query("DESC", regex="^(ASC|DESC)$"),
    limit: int = Query(50, gt=0, le=500),
    offset: int = Query(0, ge=0),
    q: Optional[str] = Query(None, max_length=64),
):
    """Top referrers — sortable list. `q` matches telegram_id / username."""
    try:
        rows = await database.get_admin_referral_stats(
            search_query=q,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise HTTPException(500, f"top_failed: {e}")
    return _serialize(rows or [])


@router.get("/{referrer_id}")
async def referrer_detail(referrer_id: int = Path(..., gt=0)):
    try:
        data = await database.get_admin_referral_detail(referrer_id)
    except Exception as e:
        raise HTTPException(500, f"detail_failed: {e}")
    if not data:
        raise HTTPException(404, "Referrer not found")
    return _serialize(data)


@router.get("/{partner_id}/history")
async def referrer_history(
    partner_id: int = Path(..., gt=0),
    limit: int = Query(50, gt=0, le=500),
):
    try:
        rows = await database.get_referral_rewards_history(partner_id, limit)
        total = await database.get_referral_rewards_history_count(partner_id)
    except Exception as e:
        raise HTTPException(500, f"history_failed: {e}")
    return {
        "rows": _serialize(rows or []),
        "total": total,
    }
