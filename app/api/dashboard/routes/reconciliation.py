"""Subscription reconciliation endpoints — «Сверка» screen backend.

Reads:
  GET  /candidates                    — list users with expires_at > NOW + 8y (premium only)
  GET  /candidates/{telegram_id}      — detailed reconciliation snapshot for one user
  GET  /audit-log                     — recent /fix executions
  GET  /over-issuance-log             — recent auto-detected over-issuance events

Writes:
  POST /fix/{telegram_id}             — recompute expires_at from payments+admin_grant_days
                                        and shorten the row; logs before/after with proof
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query

import database
from app.api.dashboard.deps import require_admin
from app.api.dashboard.errors import server_error
from app.api.dashboard.idempotency import IdempotentRoute

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)], route_class=IdempotentRoute)


@router.get("/candidates")
async def candidates(limit: int = Query(200, gt=0, le=1000)):
    """Users whose PREMIUM subscription expires more than 8 years from now."""
    try:
        rows = await database.find_over_issuance_candidates(limit)
    except Exception as e:
        raise server_error("candidates_failed") from e
    return {"total": len(rows), "items": rows}


@router.get("/candidates/{telegram_id}")
async def candidate_detail(telegram_id: int = Path(..., gt=0)):
    """Detailed reconciliation view for one user — subscription row, all
    counted/uncounted payments, computed expected expiry, delta, and the
    recent auto-detected over-issuance events for context."""
    try:
        detail = await database.get_reconciliation_detail(telegram_id)
    except Exception as e:
        raise server_error("detail_failed") from e
    if not detail.get("found"):
        raise HTTPException(404, "no_subscription")
    return detail


@router.post("/fix/{telegram_id}")
async def apply_fix(
    telegram_id: int = Path(..., gt=0),
    reason: str = Query("manual reconciliation via dashboard", max_length=500),
    admin: dict = Depends(require_admin),
):
    """Set the premium entity's expireAt by the shared repair rule
    (database.reconciliation.compute_repair_target: max of the purchases date
    and a sane bot-DB date; none / past → NOW + 1 day), log before/after with
    proof payment_ids.

    Never extends: a rule date not earlier than the panel's current expireAt
    → 409 would_extend, nothing written. The bypass entity is never touched."""
    admin_id = int(admin["sub"])
    try:
        result = await database.apply_reconciliation_fix(
            telegram_id, admin_id, reason=reason,
        )
    except Exception as e:
        logger.exception("reconciliation_fix crash user=%s", telegram_id)
        raise server_error("fix_failed") from e
    if not result.get("success"):
        # После рефакторинга single-source-of-truth ошибка возможна ровно
        # одна: Remnawave-панель не приняла PATCH expireAt (сеть, 5xx,
        # entity удалён). db_unavailable — только если пул недоступен.
        err = result.get("error")
        if err == "db_unavailable":
            raise HTTPException(503, result)
        if err == "would_extend":
            raise HTTPException(409, result)
        # panel_error / любое другое → 502 (upstream не отвечает).
        raise HTTPException(502, result)
    return result


@router.get("/audit-log")
async def audit_log(limit: int = Query(100, gt=0, le=500)):
    """Recent reconciliation actions (POST /fix calls)."""
    try:
        rows = await database.list_reconciliation_log(limit)
    except Exception as e:
        raise server_error("audit_failed") from e
    return rows


@router.get("/over-issuance-log")
async def over_issuance_log(limit: int = Query(100, gt=0, le=500)):
    """Recent auto-detected >8y over-issuance events (written by the
    watchdog after every grant_access write to expires_at)."""
    try:
        rows = await database.list_over_issuance_log(limit)
    except Exception as e:
        raise server_error("over_issuance_failed") from e
    return rows
