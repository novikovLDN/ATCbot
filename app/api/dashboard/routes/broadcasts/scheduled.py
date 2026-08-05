"""Рассылки: отложенные и повторяющиеся задания.

ЧТО ЗДЕСЬ
    Создание задания-снапшота на основе уже существующей рассылки,
    список заданий, карточка и отмена.

ПОЧЕМУ ВЫДЕЛЕНО
    Другая таблица (scheduled_broadcasts), другой исполнитель
    (scheduled_broadcasts_worker) и своя работа со временем. Ничего из
    этого не касается обычной отправки «прямо сейчас».

ЧТО ЛЕГКО СЛОМАТЬ
    Тайм-зона. Админ вводит время в MSK, хранится и сравнивается всё в
    UTC. Потеряв конвертацию, получите рассылку, ушедшую на три часа
    раньше или позже, — и никакой ошибки в логах.

    Задание — это СНАПШОТ исходной рассылки: текст, кнопки и скидка
    копируются в момент создания. Правка исходной рассылки потом на
    запланированный запуск не влияет, и это осознанно.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator

import database
from app.api.dashboard.deps import require_admin
from app.api.dashboard.routes.broadcasts.common import _serialize
from app.events import bus

logger = logging.getLogger(__name__)

router = APIRouter()


# Тайм-зона планировщика — Europe/Moscow (UTC+3). Админ вводит время
# в MSK через UI, бэкенд конвертирует в UTC для хранения и сравнения
# с NOW() в scheduler-worker'е.

_MSK_TZ = timezone(timedelta(hours=3))
_MAX_SCHEDULE_WEEKS_AHEAD = 4  # запрет планировать больше чем на 4 недели вперёд


class ScheduleBroadcastRequest(BaseModel):
    """Запланировать существующую рассылку.

    Клонирует title/message/photo/buttons/discount из source_broadcast_id
    (снапшот) и создаёт задачу в scheduled_broadcasts.

    scheduled_at_msk: `YYYY-MM-DD HH:MM` в Europe/Moscow. Максимум +4 недели.
    recurrence: once | daily | weekdays | weekly
    recurrence_end_at_msk: опциональный «дедлайн» для recurring — тоже MSK.
    segment: опционально переопределить (по умолчанию — из source).
    """
    source_broadcast_id: int = Field(..., gt=0)
    scheduled_at_msk: str = Field(..., min_length=10, max_length=32)
    recurrence: str = Field("once")
    recurrence_end_at_msk: Optional[str] = Field(None, max_length=32)
    segment: Optional[str] = Field(None, min_length=1, max_length=60)

    @field_validator("recurrence")
    @classmethod
    def _valid_rec(cls, v: str) -> str:
        v = (v or "once").strip().lower()
        if v not in database.VALID_RECURRENCES:
            raise ValueError(
                f"recurrence must be one of {sorted(database.VALID_RECURRENCES)}"
            )
        return v


def _parse_msk(dt_str: str) -> datetime:
    """Парсит `YYYY-MM-DD HH:MM` (или ISO) как MSK, возвращает UTC-aware."""
    dt_str = dt_str.strip().replace("T", " ")
    # Пробуем два формата: с секундами и без
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            naive = datetime.strptime(dt_str, fmt)
            break
        except ValueError:
            continue
    else:
        raise HTTPException(400, f"invalid datetime format: {dt_str!r}")
    msk = naive.replace(tzinfo=_MSK_TZ)
    return msk.astimezone(timezone.utc)


@router.post("/schedule")
async def broadcast_schedule_create(
    body: ScheduleBroadcastRequest,
    admin: dict = Depends(require_admin),
):
    """Создать отложенное/повторяющееся задание на основе существующей рассылки."""
    # 1. Валидация datetime (MSK → UTC)
    scheduled_utc = _parse_msk(body.scheduled_at_msk)
    now_utc = datetime.now(timezone.utc)
    if scheduled_utc < now_utc - timedelta(minutes=1):
        raise HTTPException(400, "scheduled_at is in the past")
    if scheduled_utc > now_utc + timedelta(weeks=_MAX_SCHEDULE_WEEKS_AHEAD):
        raise HTTPException(
            400,
            f"scheduled_at too far in the future (max {_MAX_SCHEDULE_WEEKS_AHEAD} weeks ahead)",
        )
    end_utc: Optional[datetime] = None
    if body.recurrence_end_at_msk:
        end_utc = _parse_msk(body.recurrence_end_at_msk)
        if end_utc <= scheduled_utc:
            raise HTTPException(400, "recurrence_end_at must be after scheduled_at")

    # 2. Достаём исходную рассылку — из неё делаем снапшот.
    try:
        source = await database.get_broadcast(body.source_broadcast_id)
    except Exception as e:
        raise HTTPException(500, f"source_lookup_failed: {e}")
    if not source:
        raise HTTPException(404, "source broadcast not found")

    # Discount fields — подтягиваем отдельно (лежат в broadcast_discounts)
    disc = None
    try:
        disc = await database.get_broadcast_discount(body.source_broadcast_id)
    except Exception as e:
        logger.warning("SCHED_DISC_LOOKUP_FAIL: %s", e)
    disc = disc or {}

    # 3. Создаём scheduled_broadcast
    try:
        sched_id = await database.create_scheduled_broadcast(
            source_broadcast_id=body.source_broadcast_id,
            title=str(source.get("title") or ""),
            message=str(source.get("message") or source.get("message_a") or ""),
            segment=body.segment or str(source.get("segment") or ""),
            scheduled_at=scheduled_utc,
            recurrence=body.recurrence,
            recurrence_end_at=end_utc,
            created_by=int(admin["sub"]),
            photo_file_id=(source.get("photo_file_id") or None),
            animation_file_id=(source.get("animation_file_id") or None),
            buttons=list(source.get("buttons") or []) or None,
            discount_percent=disc.get("discount_percent"),
            discount_hours=disc.get("discount_hours"),
            discount_label=disc.get("discount_label"),
            gift_reveal_percent=disc.get("gift_reveal_percent"),
        )
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"schedule_create_failed: {e}")

    bus.publish({
        "type": "broadcast:scheduled",
        "sched_id": sched_id,
        "source_broadcast_id": body.source_broadcast_id,
        "scheduled_at": scheduled_utc.isoformat(),
        "recurrence": body.recurrence,
        "by": admin.get("sub"),
    })
    return {
        "ok": True,
        "sched_id": sched_id,
        "scheduled_at_utc": scheduled_utc.isoformat(),
        "scheduled_at_msk": scheduled_utc.astimezone(_MSK_TZ).isoformat(),
        "recurrence": body.recurrence,
    }


@router.get("/scheduled")
async def broadcast_schedule_list(
    active_only: bool = Query(True),
    limit: int = Query(200, gt=0, le=500),
):
    """Список запланированных задач. active_only=true — только активные,
    active_only=false — вся история (в т.ч. cancelled/completed)."""
    try:
        rows = await database.list_scheduled_broadcasts(
            active_only=active_only, limit=limit,
        )
    except Exception as e:
        raise HTTPException(500, f"scheduled_list_failed: {e}")
    return [_serialize(r) for r in rows]


@router.get("/scheduled/{sched_id}")
async def broadcast_schedule_get(sched_id: int = Path(..., gt=0)):
    try:
        row = await database.get_scheduled_broadcast(sched_id)
    except Exception as e:
        raise HTTPException(500, f"scheduled_get_failed: {e}")
    if not row:
        raise HTTPException(404, "scheduled broadcast not found")
    return _serialize(row)


@router.delete("/scheduled/{sched_id}")
async def broadcast_schedule_cancel(
    sched_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    """Отменить запланированное задание. Уже отработавшие запуски
    остаются в истории broadcasts."""
    try:
        ok = await database.cancel_scheduled_broadcast(
            sched_id, cancelled_by=int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"scheduled_cancel_failed: {e}")
    if not ok:
        raise HTTPException(404, "not found or already inactive")
    bus.publish({
        "type": "broadcast:scheduled_cancelled",
        "sched_id": sched_id,
        "by": admin.get("sub"),
    })
    return {"ok": True}
