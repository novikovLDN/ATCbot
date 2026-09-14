"""Premium expireAt > 5 years — check (dry run) and the repair job.

Dashboard → «Ещё» → «Настройки» → «Премиум больше 5 лет». The same service as
scripts/fix_premium_over_issuance.py (app/services/premium_repair, wrapped in a
background job by app/services/premium_repair_job). Admin-only (require_admin),
CSRF Origin check (router-level in __init__), Idempotency-Key on the POSTs
(IdempotentRoute); check / start / resume / pause / stop and the CSV download
are written to the audit log.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

import database
from app.api.dashboard.deps import require_admin
from app.api.dashboard.idempotency import IdempotentRoute
from app.services import premium_repair_job

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)], route_class=IdempotentRoute)

_APPLY_AUDIT_KEYS = ("state", "total", "done", "fixed", "errors", "skipped", "limit")


async def _audit(action: str, admin: dict, details: dict) -> None:
    """Never raises (the audit helper is best-effort itself)."""
    try:
        await database._log_audit_event_atomic_standalone(
            action, int(admin["sub"]), None, json.dumps(details, default=str),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("PREMIUM_REPAIR_AUDIT_FAILED: %s %s", action, type(e).__name__)


def _apply_details(status: dict) -> dict:
    a = status.get("apply") or {}
    return {k: a.get(k) for k in _APPLY_AUDIT_KEYS}


def _answer(result: dict) -> dict:
    if not result.get("ok"):
        raise HTTPException(409, result.get("error") or "conflict")
    return result


class StartBody(BaseModel):
    """`limit`: a trial run — at most N PATCHes, then the job pauses."""
    limit: Optional[int] = Field(default=None, ge=1, le=100_000)


@router.get("/status")
async def repair_status():
    return await premium_repair_job.get_status()


@router.get("/report")
async def repair_report(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=10_000)):
    """Rows of the last check / repair (telegram_id, dates, source, action…)."""
    return await premium_repair_job.get_report(offset, limit)


@router.get("/report.csv")
async def repair_report_csv(admin: dict = Depends(require_admin)):
    """The same rows as CSV (the CLI report's columns)."""
    rep = await premium_repair_job.report_csv()
    if rep is None:
        raise HTTPException(404, "no_report")
    await _audit("premium_repair_report_csv", admin, {"kind": rep["kind"], "rows": rep["rows"]})
    name = f"premium_over_5y_{rep['kind']}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv"
    return Response(
        content=rep["text"],
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/check")
async def repair_check(admin: dict = Depends(require_admin)):
    result = _answer(await premium_repair_job.start_check(int(admin["sub"])))
    await _audit("premium_repair_check_start", admin, {"state": result["status"]["check"]["state"]})
    return result


@router.post("/start")
async def repair_start(body: Optional[StartBody] = None, admin: dict = Depends(require_admin)):
    limit = body.limit if body else None
    result = _answer(await premium_repair_job.start_apply(int(admin["sub"]), limit=limit))
    await _audit("premium_repair_apply_start", admin, _apply_details(result["status"]))
    return result


@router.post("/resume")
async def repair_resume(admin: dict = Depends(require_admin)):
    result = _answer(await premium_repair_job.resume(int(admin["sub"])))
    await _audit("premium_repair_apply_resume", admin, _apply_details(result["status"]))
    return result


@router.post("/pause")
async def repair_pause(admin: dict = Depends(require_admin)):
    result = _answer(await premium_repair_job.pause())
    await _audit("premium_repair_apply_pause", admin, _apply_details(result["status"]))
    return result


@router.post("/stop")
async def repair_stop(admin: dict = Depends(require_admin)):
    result = _answer(await premium_repair_job.stop(int(admin["sub"])))
    await _audit("premium_repair_apply_stop", admin, _apply_details(result["status"]))
    return result
