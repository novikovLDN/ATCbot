"""Payment-level read endpoints — pending list, single payment lookup."""
from fastapi import APIRouter, Depends, HTTPException, Path

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


@router.get("/pending")
async def payments_pending():
    """All payments stuck in pending — useful for catching webhook
    drops and manual reconciliation."""
    try:
        rows = await database.get_pending_payments()
    except Exception as e:
        raise HTTPException(500, f"pending_failed: {e}")
    return _serialize(rows or [])


@router.get("/{payment_id}")
async def payment_detail(payment_id: int = Path(..., gt=0)):
    try:
        row = await database.get_payment(payment_id)
    except Exception as e:
        raise HTTPException(500, f"payment_detail_failed: {e}")
    if not row:
        raise HTTPException(404, "Payment not found")
    return _serialize(row)
