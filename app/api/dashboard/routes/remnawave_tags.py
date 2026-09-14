"""Remnawave tags by tariff — preview and the one-off backfill job.

Dashboard → «Ещё» → «Настройки» → «Теги в панели Remnawave». Owner decision
2026-09-14: approved for users with an active subscription, tags only
(app/services/remnawave_tags). Admin-only (require_admin), CSRF Origin check
(router-level in __init__), Idempotency-Key on the POSTs (IdempotentRoute);
start / resume / pause / stop are written to the audit log.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException

import database
from app.api.dashboard.deps import require_admin
from app.api.dashboard.errors import server_error
from app.api.dashboard.idempotency import IdempotentRoute
from app.services import remnawave_tags

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)], route_class=IdempotentRoute)


async def _audit(action: str, admin: dict, status: dict) -> None:
    """Never raises (the audit helper is best-effort itself)."""
    try:
        details = {k: status.get(k) for k in ("state", "total", "done", "patched", "errors")}
        await database._log_audit_event_atomic_standalone(
            action, int(admin["sub"]), None, json.dumps(details),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("REMNAWAVE_TAGS_AUDIT_FAILED: %s %s", action, e)


def _answer(result: dict) -> dict:
    if not result.get("ok"):
        raise HTTPException(409, result.get("error") or "conflict")
    return result


@router.get("/status")
async def tags_status():
    return await remnawave_tags.get_status()


@router.get("/preview")
async def tags_preview():
    """Dry run: entities of active subscribers per target tag that differ now."""
    try:
        return await remnawave_tags.preview()
    except remnawave_tags.TagBackfillUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except Exception as e:
        raise server_error("tags_preview_failed") from e


@router.post("/start")
async def tags_start(admin: dict = Depends(require_admin)):
    result = _answer(await remnawave_tags.start(int(admin["sub"])))
    await _audit("remnawave_tags_backfill_start", admin, result["status"])
    return result


@router.post("/resume")
async def tags_resume(admin: dict = Depends(require_admin)):
    result = _answer(await remnawave_tags.resume(int(admin["sub"])))
    await _audit("remnawave_tags_backfill_resume", admin, result["status"])
    return result


@router.post("/pause")
async def tags_pause(admin: dict = Depends(require_admin)):
    result = _answer(await remnawave_tags.pause())
    await _audit("remnawave_tags_backfill_pause", admin, result["status"])
    return result


@router.post("/stop")
async def tags_stop(admin: dict = Depends(require_admin)):
    result = _answer(await remnawave_tags.stop(int(admin["sub"])))
    await _audit("remnawave_tags_backfill_stop", admin, result["status"])
    return result
