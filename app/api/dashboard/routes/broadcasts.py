"""Broadcasts — read-only history + per-broadcast send stats.

Creation wizard lives in Phase 2B. For now we just expose what's
already in the DB via database.get_recent_broadcasts and
get_broadcast_stats.
"""
from fastapi import APIRouter, Depends, HTTPException, Path, Query

import database
from app.api.dashboard.deps import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/recent")
async def broadcasts_recent(limit: int = Query(20, gt=0, le=200)):
    try:
        rows = await database.get_recent_broadcasts(limit)
    except Exception as e:
        raise HTTPException(500, f"broadcasts_failed: {e}")
    return [_serialize(r) for r in rows]


@router.get("/{broadcast_id}")
async def broadcast_detail(broadcast_id: int = Path(..., gt=0)):
    try:
        row = await database.get_broadcast(broadcast_id)
    except Exception as e:
        raise HTTPException(500, f"broadcast_detail_failed: {e}")
    if not row:
        raise HTTPException(404, "Broadcast not found")
    return _serialize(row)


@router.get("/{broadcast_id}/stats")
async def broadcast_stats(broadcast_id: int = Path(..., gt=0)):
    try:
        stats = await database.get_broadcast_stats(broadcast_id)
    except Exception as e:
        raise HTTPException(500, f"broadcast_stats_failed: {e}")
    return _serialize(stats or {})


def _serialize(row: dict | object) -> dict:
    if not isinstance(row, dict):
        return {}
    out: dict = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            continue
        else:
            out[k] = v
    return out
