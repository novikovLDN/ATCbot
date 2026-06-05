"""Pending activations — subscriptions where the payment cleared but the
VPN-provisioning HTTP call to the panel was unreachable at the moment.

The activation_worker.py background task retries them every ~5 min up to
5 attempts; this endpoint lets the admin see what's queued and force a
retry NOW without waiting for the next cycle (useful for VIPs)."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path

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


@router.get("/pending")
async def activations_pending(limit: int = 100):
    """Subscriptions stuck in activation_status='pending', oldest first."""
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        raise HTTPException(503, "db_unavailable")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, telegram_id, expires_at, activation_attempts,
                      last_activation_error, activated_at, status,
                      subscription_type
               FROM subscriptions
               WHERE activation_status = 'pending'
               ORDER BY COALESCE(activated_at, '1970-01-01'::timestamp) ASC
               LIMIT $1""",
            limit,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
        ) or 0
    return {
        "total": int(total),
        "rows": [_serialize(dict(r)) for r in rows],
    }


@router.post("/{subscription_id}/retry")
async def activations_retry(
    subscription_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    """Force-attempt activation for one subscription. Calls into the same
    service the background worker uses, so we get identical idempotency
    + advisory-lock + retry-on-success semantics."""
    from database.core import get_pool
    from app.services.activation import service as activation_service

    pool = await get_pool()
    if pool is None:
        raise HTTPException(503, "db_unavailable")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT telegram_id, activation_attempts, activation_status
               FROM subscriptions WHERE id = $1""",
            subscription_id,
        )
    if not row:
        raise HTTPException(404, "subscription_not_found")
    if row["activation_status"] != "pending":
        raise HTTPException(409, f"not_pending (current={row['activation_status']})")

    try:
        result: Any = await activation_service.attempt_activation(
            subscription_id=subscription_id,
            telegram_id=row["telegram_id"],
            current_attempts=int(row["activation_attempts"] or 0),
            pool=pool,
        )
    except Exception as e:
        raise HTTPException(500, f"retry_failed: {e}")

    success = getattr(result, "success", False)
    bus.publish({
        "type": "activation:retry",
        "subscription_id": subscription_id,
        "telegram_id": row["telegram_id"],
        "by": admin.get("sub"),
        "success": bool(success),
    })

    # ActivationResult attributes vary by version — serialize what we can.
    payload: dict = {
        "ok": bool(success),
        "subscription_id": subscription_id,
    }
    for field in ("vpn_key", "uuid", "error_message", "attempts"):
        if hasattr(result, field):
            payload[field] = _serialize(getattr(result, field))
    return payload
