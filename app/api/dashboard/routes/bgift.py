"""Bypass-gift links — create, list, view (with redemptions), soft-delete."""
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

import database
from app.api.dashboard.deps import require_admin
from app.events import bus

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


@router.get("/summary")
async def bgift_summary():
    try:
        data = await database.get_bypass_gift_links_summary()
    except Exception as e:
        raise HTTPException(500, f"summary_failed: {e}")
    return _serialize(data or {})


@router.get("/list")
async def bgift_list(
    page: int = Query(0, ge=0),
    page_size: int = Query(20, gt=0, le=200),
    include_deleted: bool = Query(False),
):
    try:
        rows = await database.list_bypass_gift_links(
            include_deleted=include_deleted,
            limit=page_size,
            offset=page * page_size,
        )
    except Exception as e:
        raise HTTPException(500, f"list_failed: {e}")
    return _serialize(rows or [])


@router.get("/{link_id}")
async def bgift_detail(link_id: int = Path(..., gt=0)):
    try:
        row = await database.get_bypass_gift_link_by_id(link_id)
    except Exception as e:
        raise HTTPException(500, f"detail_failed: {e}")
    if not row:
        raise HTTPException(404, "Link not found")
    return _serialize(row)


@router.get("/{link_id}/redemptions")
async def bgift_redemptions(
    link_id: int = Path(..., gt=0),
    limit: int = Query(100, gt=0, le=1000),
):
    try:
        rows = await database.get_bypass_gift_link_redemptions(link_id, limit)
        total = await database.count_bypass_gift_link_redemptions(link_id)
    except Exception as e:
        raise HTTPException(500, f"redemptions_failed: {e}")
    return {
        "rows": _serialize(rows or []),
        "total": total,
    }


class GiftLinkCreate(BaseModel):
    gb_amount: int = Field(..., gt=0, le=1024)
    validity_days: int = Field(..., gt=0, le=365)
    max_uses: int = Field(..., gt=0, le=10000)


@router.post("")
async def bgift_create(
    body: GiftLinkCreate,
    admin: dict = Depends(require_admin),
):
    try:
        row = await database.create_bypass_gift_link(
            created_by=int(admin["sub"]),
            gb_amount=body.gb_amount,
            validity_days=body.validity_days,
            max_uses=body.max_uses,
        )
    except Exception as e:
        raise HTTPException(500, f"create_failed: {e}")
    if not row:
        raise HTTPException(500, "create_failed")
    bus.publish({
        "type": "bgift:created",
        "link_id": row.get("id"),
        "code": row.get("code"),
        "by": admin.get("sub"),
    })
    return _serialize(row)


@router.delete("/{link_id}")
async def bgift_delete(
    link_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.soft_delete_bypass_gift_link(link_id)
    except Exception as e:
        raise HTTPException(500, f"delete_failed: {e}")
    if not ok:
        raise HTTPException(404, "Link not found")
    bus.publish({
        "type": "bgift:deleted",
        "link_id": link_id,
        "by": admin.get("sub"),
    })
    return {"ok": True}
