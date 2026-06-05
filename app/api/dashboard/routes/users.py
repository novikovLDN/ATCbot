"""
User lookup + admin actions.

Read endpoints just proxy database.* functions. Write endpoints route
through the SAME atomic helpers the in-bot admin handlers use
(`admin_grant_access_atomic`, `admin_revoke_access_atomic`, etc.) —
so audit logs, Remnawave sync, and side effects stay identical no
matter whether the action comes from a Telegram chat or the web UI.

Bot-only writes (approve_payment_atomic, grant_access, finalize_purchase,
mark_trial_used) are intentionally NOT exposed here.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator

import config
import database
from app.api.dashboard.deps import require_admin
from app.events import bus

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/search")
async def users_search(q: str = Query(..., min_length=1)):
    """Find by telegram_id (int) or @username (str)."""
    try:
        user = await database.find_user_by_id_or_username(q)
    except Exception as e:
        raise HTTPException(500, f"search_failed: {e}")
    if not user:
        raise HTTPException(404, "User not found")
    return user


@router.get("/{telegram_id}")
async def user_detail(telegram_id: int = Path(..., gt=0)):
    """Full card — user, balance, subscription, discount, vip, trial."""
    try:
        user = await database.get_user(telegram_id)
        if not user:
            raise HTTPException(404, "User not found")
        balance = await database.get_user_balance(telegram_id)
        subscription = await database.get_subscription(telegram_id)
        trial = await database.get_trial_info(telegram_id)
        discount = await database.get_user_discount(telegram_id)
        is_vip = await database.is_vip_user(telegram_id)
        return {
            "user": user,
            "balance_rubles": balance,
            "subscription": subscription,
            "trial": trial,
            "discount": discount,
            "is_vip": is_vip,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"user_detail_failed: {e}")


@router.get("/{telegram_id}/history")
async def user_history(
    telegram_id: int = Path(..., gt=0),
    limit: int = Query(20, gt=0, le=200),
):
    try:
        return await database.get_subscription_history(telegram_id, limit)
    except Exception as e:
        raise HTTPException(500, f"history_failed: {e}")


@router.get("/{telegram_id}/extended-stats")
async def user_extended_stats(telegram_id: int = Path(..., gt=0)):
    try:
        return await database.get_user_extended_stats(telegram_id)
    except Exception as e:
        raise HTTPException(500, f"extended_stats_failed: {e}")


# ──────────────────────────────────────────────────────────────────────
# WRITE endpoints — all go through the same atomic helpers as the
# in-bot admin handlers. Side effects: DB updates, audit log,
# Remnawave sync (where the helper does it), event publication.
# ──────────────────────────────────────────────────────────────────────

class GrantRequest(BaseModel):
    days: int = Field(..., gt=0, le=3650)
    tariff: str = Field("basic")

    @field_validator("tariff")
    @classmethod
    def _valid_tariff(cls, v: str) -> str:
        if v not in config.VALID_SUBSCRIPTION_TYPES:
            raise ValueError(f"invalid tariff: {v}")
        return v


@router.post("/{telegram_id}/grant")
async def user_grant(
    telegram_id: int = Path(..., gt=0),
    body: GrantRequest = ...,
    admin: dict = Depends(require_admin),
):
    try:
        expires_at, vpn_key = await database.admin_grant_access_atomic(
            telegram_id, body.days, int(admin["sub"]), tariff=body.tariff,
        )
    except Exception as e:
        raise HTTPException(500, f"grant_failed: {e}")
    bus.publish({
        "type": "admin:grant",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
        "days": body.days,
        "tariff": body.tariff,
    })
    return {
        "ok": True,
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at,
        "vpn_key": vpn_key,
    }


class GrantMinutesRequest(BaseModel):
    minutes: int = Field(..., gt=0, le=525600)  # ≤ 1 year


@router.post("/{telegram_id}/grant-minutes")
async def user_grant_minutes(
    telegram_id: int = Path(..., gt=0),
    body: GrantMinutesRequest = ...,
    admin: dict = Depends(require_admin),
):
    try:
        expires_at, vpn_key = await database.admin_grant_access_minutes_atomic(
            telegram_id, body.minutes, int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"grant_minutes_failed: {e}")
    bus.publish({
        "type": "admin:grant_minutes",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
        "minutes": body.minutes,
    })
    return {
        "ok": True,
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at,
        "vpn_key": vpn_key,
    }


@router.post("/{telegram_id}/revoke")
async def user_revoke(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.admin_revoke_access_atomic(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"revoke_failed: {e}")
    bus.publish({
        "type": "admin:revoke",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


class SwitchTariffRequest(BaseModel):
    tariff: str = Field(...)

    @field_validator("tariff")
    @classmethod
    def _valid_tariff(cls, v: str) -> str:
        if v not in config.VALID_SUBSCRIPTION_TYPES:
            raise ValueError(f"invalid tariff: {v}")
        return v


@router.post("/{telegram_id}/switch-tariff")
async def user_switch_tariff(
    telegram_id: int = Path(..., gt=0),
    body: SwitchTariffRequest = ...,
    admin: dict = Depends(require_admin),
):
    try:
        updated = await database.admin_switch_tariff(telegram_id, body.tariff)
    except Exception as e:
        raise HTTPException(500, f"switch_tariff_failed: {e}")
    if not updated:
        raise HTTPException(404, "no_active_subscription")
    bus.publish({
        "type": "admin:switch_tariff",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
        "tariff": body.tariff,
    })
    return {"ok": True, "subscription": updated}


class DiscountRequest(BaseModel):
    percent: int = Field(..., ge=1, le=100)
    expires_in_hours: Optional[int] = Field(None, gt=0, le=8760)  # ≤ 1 year


@router.post("/{telegram_id}/discount")
async def user_discount_create(
    telegram_id: int = Path(..., gt=0),
    body: DiscountRequest = ...,
    admin: dict = Depends(require_admin),
):
    expires_at = None
    if body.expires_in_hours is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)
    try:
        ok = await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=body.percent,
            expires_at=expires_at,
            created_by=int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"discount_create_failed: {e}")
    if not ok:
        raise HTTPException(500, "discount_create_failed")
    bus.publish({
        "type": "admin:discount_create",
        "telegram_id": telegram_id,
        "percent": body.percent,
        "by": admin.get("sub"),
    })
    return {
        "ok": True,
        "percent": body.percent,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.delete("/{telegram_id}/discount")
async def user_discount_delete(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.delete_user_discount(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"discount_delete_failed: {e}")
    bus.publish({
        "type": "admin:discount_delete",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


@router.post("/{telegram_id}/vip")
async def user_vip_grant(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.grant_vip_status(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"vip_grant_failed: {e}")
    bus.publish({
        "type": "admin:vip_grant",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


@router.delete("/{telegram_id}/vip")
async def user_vip_revoke(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.revoke_vip_status(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"vip_revoke_failed: {e}")
    bus.publish({
        "type": "admin:vip_revoke",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}
