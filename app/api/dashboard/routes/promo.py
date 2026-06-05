"""Promo codes — list, create, deactivate."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

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


@router.get("/list")
async def promo_list():
    """All promocodes with usage stats."""
    try:
        rows = await database.get_promo_stats()
    except Exception as e:
        raise HTTPException(500, f"promo_list_failed: {e}")
    return _serialize(rows or [])


class PromoCreate(BaseModel):
    code: str = Field(..., min_length=3, max_length=32)
    discount_percent: int = Field(..., ge=1, le=100)
    duration_seconds: int = Field(..., gt=0, le=10 * 365 * 24 * 3600)
    max_uses: int = Field(..., gt=0, le=1_000_000)

    @field_validator("code")
    @classmethod
    def _alnum_upper(cls, v: str) -> str:
        v = v.strip().upper()
        if not v.isalnum():
            raise ValueError("code must be alphanumeric (A-Z 0-9)")
        return v


@router.post("")
async def promo_create(body: PromoCreate, admin: dict = Depends(require_admin)):
    try:
        promo_id = await database.create_promocode_atomic(
            code=body.code,
            discount_percent=body.discount_percent,
            duration_seconds=body.duration_seconds,
            max_uses=body.max_uses,
            created_by=int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"promo_create_failed: {e}")
    if not promo_id:
        raise HTTPException(409, "code_taken_or_invalid")
    bus.publish({
        "type": "promo:created",
        "promo_id": promo_id,
        "code": body.code,
        "by": admin.get("sub"),
    })
    return {"ok": True, "promo_id": promo_id, "code": body.code}


@router.delete("/{promo_id}")
async def promo_deactivate(
    promo_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.deactivate_promocode(promo_id=promo_id)
    except Exception as e:
        raise HTTPException(500, f"promo_deactivate_failed: {e}")
    if not ok:
        raise HTTPException(404, "Promo not found")
    bus.publish({
        "type": "promo:deactivated",
        "promo_id": promo_id,
        "by": admin.get("sub"),
    })
    return {"ok": True}
