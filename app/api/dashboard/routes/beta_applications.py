"""Beta-testing applications — list + counter for dashboard."""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dashboard.deps import require_admin
from database import beta_applications as _beta

router = APIRouter(dependencies=[Depends(require_admin)])


def _serialize(value):
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


@router.get("/summary")
async def summary(program: str = Query("vpn_innovator")):
    """Общий счётчик заявок по программе."""
    try:
        total = await _beta.count_applications(program)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"summary_failed: {e}")
    return {"program": program, "total": int(total or 0)}


@router.get("/list")
async def list_apps(
    program: str = Query("vpn_innovator"),
    page: int = Query(0, ge=0),
    page_size: int = Query(50, gt=0, le=500),
):
    """Пагинированный список заявок, свежие сверху."""
    try:
        rows = await _beta.list_applications(
            program=program,
            limit=page_size,
            offset=page * page_size,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"list_failed: {e}")
    return _serialize(rows or [])
