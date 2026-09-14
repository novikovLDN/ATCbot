"""Marketing links — stats + promo. CRUD + summary for admin dashboard.

Two families:
  /links/stats/*  — attribution / funnel links
  /links/promo/*  — reward links (subscription / discount / GB)

Routes require admin JWT (Depends(require_admin) mounted at router level).
Deeplinks generated on the client from `slug` + configured bot username;
we return slug + a ready-made `t_me_url` for convenience.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

import config
import database
from app.api.dashboard.deps import require_admin
from app.events import bus


router = APIRouter(dependencies=[Depends(require_admin)])


# Максимальное количество активных ссылок каждого типа.
# Юзер попросил лимит в 10 для stats-ссылок; на promo — тот же лимит
# для гигиены.
MAX_ACTIVE_STATS_LINKS = 10
MAX_ACTIVE_PROMO_LINKS = 10


def _serialize(value: Any) -> Any:
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return None
    return value


def _bot_username() -> str:
    """Bot username для генерации t.me/<bot>?start=... .
    Читаем из config.BOT_USERNAME с fallback на пустой ключ (клиент
    покажет slug без полного URL — редкая деградация)."""
    return getattr(config, "BOT_USERNAME", None) or getattr(config, "TELEGRAM_BOT_USERNAME", "") or ""


def _stat_url(slug: str) -> str:
    bot = _bot_username()
    if not bot:
        return f"?start=s-{slug}"
    return f"https://t.me/{bot}?start=s-{slug}"


def _promo_url(slug: str) -> str:
    bot = _bot_username()
    if not bot:
        return f"?start=p-{slug}"
    return f"https://t.me/{bot}?start=p-{slug}"


# ═════════════════════════════════════════════════════════════════════
# STATS LINKS
# ═════════════════════════════════════════════════════════════════════

class StatsLinkCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


@router.get("/stats")
async def stats_links_list():
    """List all stats links (active + inactive). Каждая обогащена
    краткой сводкой (clicks + trials + paid)."""
    try:
        links = await database.list_stats_links(include_inactive=True)
    except Exception as e:
        raise HTTPException(500, f"list_failed: {e}")
    out: List[Dict[str, Any]] = []
    for link in links:
        try:
            summary = await database.get_stats_link_summary(link["id"])
        except Exception:
            summary = None
        merged = _serialize(summary or link)
        merged["t_me_url"] = _stat_url(link["slug"])
        out.append(merged)
    return out


@router.post("/stats")
async def stats_link_create(
    body: StatsLinkCreate,
    admin: dict = Depends(require_admin),
):
    """Создать новую stat-ссылку. Enforce'им лимит 10 активных."""
    try:
        existing = await database.list_stats_links(include_inactive=False)
    except Exception as e:
        raise HTTPException(500, f"list_failed: {e}")
    if len(existing) >= MAX_ACTIVE_STATS_LINKS:
        raise HTTPException(
            409,
            f"limit_reached (max {MAX_ACTIVE_STATS_LINKS} active). "
            "Деактивируй одну из существующих и попробуй ещё раз.",
        )
    try:
        link = await database.create_stats_link(
            name=body.name.strip(),
            created_by=int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"create_failed: {e}")
    bus.publish({
        "type": "stats_link:created",
        "link_id": link["id"],
        "slug": link["slug"],
        "by": admin.get("sub"),
    })
    payload = _serialize(link)
    payload["t_me_url"] = _stat_url(link["slug"])
    return payload


@router.get("/stats/{link_id}")
async def stats_link_detail(link_id: int = Path(..., gt=0)):
    try:
        summary = await database.get_stats_link_summary(link_id)
    except Exception as e:
        raise HTTPException(500, f"detail_failed: {e}")
    if not summary:
        raise HTTPException(404, "Not found")
    out = _serialize(summary)
    out["t_me_url"] = _stat_url(summary["slug"])
    return out


@router.post("/stats/{link_id}/deactivate")
async def stats_link_deactivate(
    link_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.set_stats_link_active(link_id, active=False)
    except Exception as e:
        raise HTTPException(500, f"deactivate_failed: {e}")
    if not ok:
        raise HTTPException(404, "Not found")
    bus.publish({"type": "stats_link:deactivated", "link_id": link_id, "by": admin.get("sub")})
    return {"ok": True}


@router.post("/stats/{link_id}/reactivate")
async def stats_link_reactivate(
    link_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.set_stats_link_active(link_id, active=True)
    except Exception as e:
        raise HTTPException(500, f"reactivate_failed: {e}")
    if not ok:
        raise HTTPException(404, "Not found")
    bus.publish({"type": "stats_link:reactivated", "link_id": link_id, "by": admin.get("sub")})
    return {"ok": True}


@router.delete("/stats/{link_id}")
async def stats_link_delete(
    link_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.delete_stats_link(link_id)
    except Exception as e:
        raise HTTPException(500, f"delete_failed: {e}")
    if not ok:
        raise HTTPException(404, "Not found")
    bus.publish({"type": "stats_link:deleted", "link_id": link_id, "by": admin.get("sub")})
    return {"ok": True}


# ═════════════════════════════════════════════════════════════════════
# PROMO LINKS
# ═════════════════════════════════════════════════════════════════════

class PromoLinkCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    reward_type: str = Field(...)
    reward_value: int = Field(..., gt=0)
    max_uses_total: Optional[int] = Field(None, gt=0, le=1_000_000)
    max_uses_per_user: int = Field(1, ge=1, le=10)
    reward_meta: Optional[Dict[str, Any]] = None
    expires_in_hours: Optional[int] = Field(None, gt=0, le=24 * 365)

    @field_validator("reward_type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        v = v.strip()
        if v not in database.VALID_PROMO_REWARD_TYPES:
            raise ValueError(f"reward_type must be one of {sorted(database.VALID_PROMO_REWARD_TYPES)}")
        return v


def _validate_promo_reward(reward_type: str, reward_value: int) -> None:
    """Server-side проверка: значения соответствуют whitelist'ам,
    которые пользователь выбирает в UI (дни / проценты / ГБ)."""
    if reward_type == "subscription_days":
        if reward_value not in database.VALID_SUB_DAYS:
            raise HTTPException(
                400,
                f"reward_value для subscription_days должен быть одним из "
                f"{sorted(database.VALID_SUB_DAYS)}",
            )
        return
    if reward_type in ("tariff_discount", "bypass_discount"):
        if reward_value not in database.VALID_DISCOUNT_PCTS:
            raise HTTPException(
                400,
                f"reward_value для {reward_type} должен быть одним из "
                f"{sorted(database.VALID_DISCOUNT_PCTS)}",
            )
        return
    if reward_type == "bypass_gb":
        if reward_value not in (5, 10, 15, 20, 25, 30, 50, 100):
            raise HTTPException(
                400,
                "reward_value для bypass_gb должен быть одним из "
                "5/10/15/20/25/30/50/100 ГБ",
            )
        return
    raise HTTPException(400, "unknown reward_type")


@router.get("/promo")
async def promo_links_list():
    try:
        links = await database.list_promo_links(include_inactive=True)
    except Exception as e:
        raise HTTPException(500, f"list_failed: {e}")
    out: List[Dict[str, Any]] = []
    for link in links:
        row = _serialize(link)
        row["t_me_url"] = _promo_url(link["slug"])
        out.append(row)
    return out


@router.post("/promo")
async def promo_link_create(
    body: PromoLinkCreate,
    admin: dict = Depends(require_admin),
):
    _validate_promo_reward(body.reward_type, body.reward_value)
    try:
        existing = await database.list_promo_links(include_inactive=False)
    except Exception as e:
        raise HTTPException(500, f"list_failed: {e}")
    if len(existing) >= MAX_ACTIVE_PROMO_LINKS:
        raise HTTPException(
            409,
            f"limit_reached (max {MAX_ACTIVE_PROMO_LINKS} active). "
            "Деактивируй существующую и попробуй ещё раз.",
        )
    expires_at = None
    if body.expires_in_hours is not None:
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)
    try:
        link = await database.create_promo_link(
            name=body.name.strip(),
            reward_type=body.reward_type,
            reward_value=body.reward_value,
            max_uses_total=body.max_uses_total,
            max_uses_per_user=body.max_uses_per_user,
            reward_meta=body.reward_meta or {},
            expires_at=expires_at,
            created_by=int(admin["sub"]),
        )
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"create_failed: {e}")
    bus.publish({
        "type": "promo_link:created",
        "link_id": link["id"],
        "slug": link["slug"],
        "reward_type": body.reward_type,
        "reward_value": body.reward_value,
        "by": admin.get("sub"),
    })
    payload = _serialize(link)
    payload["t_me_url"] = _promo_url(link["slug"])
    return payload


@router.get("/promo/{link_id}")
async def promo_link_detail(link_id: int = Path(..., gt=0)):
    try:
        summary = await database.get_promo_link_summary(link_id)
    except Exception as e:
        raise HTTPException(500, f"detail_failed: {e}")
    if not summary:
        raise HTTPException(404, "Not found")
    out = _serialize(summary)
    out["t_me_url"] = _promo_url(summary["slug"])
    return out


@router.post("/promo/{link_id}/deactivate")
async def promo_link_deactivate(
    link_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.set_promo_link_active(link_id, active=False)
    except Exception as e:
        raise HTTPException(500, f"deactivate_failed: {e}")
    if not ok:
        raise HTTPException(404, "Not found")
    bus.publish({"type": "promo_link:deactivated", "link_id": link_id, "by": admin.get("sub")})
    return {"ok": True}


@router.post("/promo/{link_id}/reactivate")
async def promo_link_reactivate(
    link_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.set_promo_link_active(link_id, active=True)
    except Exception as e:
        raise HTTPException(500, f"reactivate_failed: {e}")
    if not ok:
        raise HTTPException(404, "Not found")
    bus.publish({"type": "promo_link:reactivated", "link_id": link_id, "by": admin.get("sub")})
    return {"ok": True}


@router.delete("/promo/{link_id}")
async def promo_link_delete(
    link_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.delete_promo_link(link_id)
    except Exception as e:
        raise HTTPException(500, f"delete_failed: {e}")
    if not ok:
        raise HTTPException(404, "Not found")
    bus.publish({"type": "promo_link:deleted", "link_id": link_id, "by": admin.get("sub")})
    return {"ok": True}
