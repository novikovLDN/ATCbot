"""Промокоды — список, создание, отключение, включение обратно.

GET    /promo/list            — все коды со счётчиками применений
POST   /promo                 — создать
DELETE /promo/{promo_id}      — отключить
POST   /promo/{promo_id}/activate — включить обратно

ПОРЯДОК МАРШРУТОВ
    Литеральный /list объявлен ПЕРЕД /{promo_id}. Поменяете местами —
    запрос списка уедет в обработчик по идентификатору и вернёт 422.

ЧТО ОТДАЁТ /list
    Строка содержит used_count (сколько раз код уже применили),
    max_uses (сколько разрешено), expires_at и is_effective_active —
    последнее считается в SQL и учитывает сразу четыре условия: флаг,
    мягкое удаление, срок и исчерпание лимита. Экран обязан читать
    именно его, а не один is_active: код с исчерпанным лимитом
    формально «активен», но не работает.

СЕКРЕТЫ
    Текст исключения наружу — только через scrub_secrets.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

import database
from app.api.dashboard.deps import require_admin
from app.events import bus
from app.utils.security import scrub_secrets

logger = logging.getLogger(__name__)

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
    """Все промокоды со счётчиками применений."""
    try:
        rows = await database.get_promo_stats()
    except Exception as e:
        logger.error("promo.list failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"promo_list_failed: {scrub_secrets(e)}")
    logger.info("promo.list ok: %d rows", len(rows or []))
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
    promo_id: Optional[int]
    try:
        promo_id = await database.create_promocode_atomic(
            code=body.code,
            discount_percent=body.discount_percent,
            duration_seconds=body.duration_seconds,
            max_uses=body.max_uses,
            created_by=int(admin["sub"]),
        )
    except Exception as e:
        logger.error("promo.create failed for %s: %s", body.code, scrub_secrets(e))
        raise HTTPException(500, f"promo_create_failed: {scrub_secrets(e)}")
    if not promo_id:
        logger.info("promo.create rejected: код %s занят", body.code)
        raise HTTPException(409, "code_taken_or_invalid")
    bus.publish({
        "type": "promo:created",
        "promo_id": promo_id,
        "code": body.code,
        "by": admin.get("sub"),
    })
    logger.info(
        "promo.create %s: −%s%%, лимит %s, by admin=%s",
        body.code, body.discount_percent, body.max_uses, admin.get("sub"),
    )
    return {"ok": True, "promo_id": promo_id, "code": body.code}


@router.delete("/{promo_id}")
async def promo_deactivate(
    promo_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.deactivate_promocode(promo_id=promo_id)
    except Exception as e:
        logger.error("promo.deactivate failed id=%s: %s", promo_id, scrub_secrets(e))
        raise HTTPException(500, f"promo_deactivate_failed: {scrub_secrets(e)}")
    if not ok:
        raise HTTPException(404, "Promo not found")
    bus.publish({
        "type": "promo:deactivated",
        "promo_id": promo_id,
        "by": admin.get("sub"),
    })
    logger.info("promo.deactivate id=%s by admin=%s", promo_id, admin.get("sub"))
    return {"ok": True}


@router.post("/{promo_id}/activate")
async def promo_reactivate(
    promo_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    """Включить обратно ранее отключённый код: снимает пометку удаления
    и возвращает is_active. Срок и лимит применений при этом не
    сбрасываются — истёкший код так не оживить."""
    try:
        ok = await database.reactivate_promocode(promo_id=promo_id)
    except Exception as e:
        logger.error("promo.reactivate failed id=%s: %s", promo_id, scrub_secrets(e))
        raise HTTPException(500, f"promo_reactivate_failed: {scrub_secrets(e)}")
    if not ok:
        raise HTTPException(404, "Promo not found")
    bus.publish({
        "type": "promo:reactivated",
        "promo_id": promo_id,
        "by": admin.get("sub"),
    })
    logger.info("promo.reactivate id=%s by admin=%s", promo_id, admin.get("sub"))
    return {"ok": True}
