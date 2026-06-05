"""Audit log endpoints — read-only timeline of admin actions and lifecycle events."""
from fastapi import APIRouter, Depends, HTTPException, Query

import database
from app.api.dashboard.deps import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/recent")
async def audit_recent(limit: int = Query(50, gt=0, le=500)):
    """Last N audit-log entries, newest first. Wraps
    database.get_last_audit_logs which gracefully returns []
    if the audit_log table doesn't yet exist."""
    try:
        rows = await database.get_last_audit_logs(limit)
    except Exception as e:
        raise HTTPException(500, f"audit_failed: {e}")
    out: list = []
    for row in rows:
        item: dict = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                item[k] = v.isoformat()
            elif isinstance(v, (bytes, bytearray)):
                continue
            else:
                item[k] = v
        out.append(item)
    return out
