"""Режим инцидента: баннер, который видят все пользователи бота.

GET  /incident — текущее состояние баннера
POST /incident — включить, выключить или переписать текст

СЕКРЕТЫ
    Текст исключения наружу — только через scrub_secrets. Экран «Сервис»
    показывает detail целиком, то есть строка уходит прямо в браузер.

ЛОГИ
    Оба обработчика пишут в лог и удачу, и отказ: включение баннера видят
    все пользователи разом, и по логу должно быть видно, кто и когда его
    зажёг.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import database
from app.api.dashboard.deps import require_admin
from app.events import bus
from app.utils.security import scrub_secrets

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("")
async def incident_get():
    """Текущее состояние баннера: включён ли и какой текст."""
    try:
        data = await database.get_incident_settings()
    except Exception as e:
        logger.error("incident.get failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"incident_get_failed: {scrub_secrets(e)}")
    logger.info("incident.get ok")
    return data


class IncidentSet(BaseModel):
    is_active: bool
    incident_text: Optional[str] = Field(None, max_length=2000)


@router.post("")
async def incident_set(body: IncidentSet, admin: dict = Depends(require_admin)):
    """Включить или выключить баннер и сохранить его текст."""
    try:
        await database.set_incident_mode(body.is_active, body.incident_text)
    except Exception as e:
        logger.error("incident.set failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"incident_set_failed: {scrub_secrets(e)}")
    bus.publish({
        "type": "incident:updated",
        "is_active": body.is_active,
        "by": admin.get("sub"),
    })
    # Сам текст баннера в лог не пишем: он произвольный и может содержать
    # что угодно, включая случайно вставленную ссылку с токеном.
    logger.info(
        "incident.set ok: is_active=%s by=%s", body.is_active, admin.get("sub")
    )
    return {"ok": True, "is_active": body.is_active}
