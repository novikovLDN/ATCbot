"""Audit log endpoints — read-only timeline of admin actions and lifecycle events."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import database
from app.api.dashboard.deps import require_admin
from app.api.dashboard.errors import server_error

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/recent")
async def audit_recent(
    limit: int = Query(50, gt=0, le=500),
    telegram_id: Optional[int] = Query(None, gt=0),
):
    """Last N audit-log entries, newest first. Wraps
    database.get_last_audit_logs which gracefully returns []
    if the audit_log table doesn't yet exist.

    `telegram_id` narrows the tail to one user (actor OR target) — это
    то, что рисуется в карточке юзера как «кто из админов что с ним
    делал». Без параметра поведение прежнее: глобальный хвост."""
    try:
        rows = await database.get_last_audit_logs(limit, telegram_id=telegram_id)
    except Exception as e:
        raise server_error("audit_failed") from e
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
