"""Рассылки: читающие эндпоинты дашборда.

ЧТО ЗДЕСЬ
    История рассылок, список сегментов с размерами аудитории, карточка
    рассылки, счётчики доставки и аналитика конверсии.

ПОЧЕМУ ВЫДЕЛЕНО
    Здесь ничего не отправляется и не создаётся — только SELECT'ы через
    слой database. Соседний send.py, наоборот, запускает фоновую отправку
    живым людям; смешивать «показать» и «разослать» в одном файле дорого.

ЧТО ЛЕГКО СЛОМАТЬ
    Порядок объявления. `/recent` и `/segments` обязаны идти ДО
    `/{broadcast_id}`: FastAPI берёт первый подошедший маршрут, и
    `/{broadcast_id}` перехватил бы «recent» как значение параметра —
    экран истории отвечал бы 422.

    Подсчёт сегментов идёт по одному запросу на ключ и намеренно ловит
    исключения: упавший сегмент показывается как count=-1, но не роняет
    весь экран визарда.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, Query

import database
from app.api.dashboard.routes.broadcasts.common import _serialize
from app.api.dashboard.routes.broadcasts.segments_catalog import SEGMENTS

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/recent")
async def broadcasts_recent(limit: int = Query(20, gt=0, le=500)):
    try:
        rows = await database.get_recent_broadcasts(limit)
    except Exception as e:
        raise HTTPException(500, f"broadcasts_failed: {e}")
    return [_serialize(r) for r in rows]


@router.get("/segments")
async def segments_list():
    """Available segments with current member counts + tooltip descriptions.

    Counts are computed eagerly so the wizard can show an audience size
    before the admin commits. `group` группирует сегменты в UI, чтобы
    админу было проще ориентироваться среди 25+ ключей.
    """
    out = []
    for key, label, description, group in SEGMENTS:
        try:
            ids = await database.get_users_by_segment(key)
            count = len(ids)
        except Exception as e:
            logger.warning("SEGMENT_COUNT_FAIL key=%s err=%s", key, e)
            count = -1
        out.append({
            "key": key,
            "label": label,
            "description": description,
            "group": group,
            "count": count,
        })
    return out


@router.get("/{broadcast_id}")
async def broadcast_detail(broadcast_id: int = Path(..., gt=0)):
    """Full broadcast row + discount/gift_reveal — используется UI-ом
    «Отправить снова», чтоб предзаполнить визард всеми полями."""
    try:
        row = await database.get_broadcast(broadcast_id)
    except Exception as e:
        raise HTTPException(500, f"broadcast_detail_failed: {e}")
    if not row:
        raise HTTPException(404, "Broadcast not found")
    out = _serialize(row)
    # Присоединяем скидочные поля — они хранятся в broadcast_discounts,
    # а не в broadcasts. Fail-safe: если строки нет — пустые значения.
    try:
        disc = await database.get_broadcast_discount(broadcast_id)
    except Exception as e:
        logger.warning("BROADCAST_DETAIL_DISC_FAIL id=%s err=%s", broadcast_id, e)
        disc = None
    if disc:
        out["discount_percent"] = disc.get("discount_percent")
        out["discount_hours"] = disc.get("discount_hours")
        out["discount_label"] = disc.get("discount_label")
        out["gift_reveal_percent"] = disc.get("gift_reveal_percent")
    else:
        out["discount_percent"] = None
        out["discount_hours"] = None
        out["discount_label"] = None
        out["gift_reveal_percent"] = None
    return out


@router.get("/{broadcast_id}/stats")
async def broadcast_stats(broadcast_id: int = Path(..., gt=0)):
    try:
        stats = await database.get_broadcast_stats(broadcast_id)
    except Exception as e:
        raise HTTPException(500, f"broadcast_stats_failed: {e}")
    return _serialize(stats or {})


@router.get("/{broadcast_id}/analytics")
async def broadcast_analytics(broadcast_id: int = Path(..., gt=0)):
    """Расширенная аналитика рассылки: conversion / revenue / blocked.

    Возвращает счётчики sent/failed/deleted и окна конверсии
    (1д/3д/7д) — уникальные юзеры, купившие после отправки, и
    их суммарный доход.
    """
    try:
        data = await database.get_broadcast_analytics(broadcast_id)
    except Exception as e:
        raise HTTPException(500, f"broadcast_analytics_failed: {e}")
    return _serialize(data or {})
