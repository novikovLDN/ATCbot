"""Экран «События» (/events) — журнал того, что произошло и кто это сделал.

    GET /audit/events   лента audit_log с фильтрами, счётчиками и листанием;
    GET /audit/recent   старый плоский срез, оставлен для внешних вызовов.

ПОРЯДОК МАРШРУТОВ
    Оба пути литеральные, ни одного с параметром. Это не случайность:
    маршрут вида /{id} перехватил бы любой односегментный путь,
    объявленный ниже, и экран отвечал бы 422 — ровно так в этом проекте
    умер список отложенных рассылок (GET /scheduled стоял под
    GET /{broadcast_id}). Появится здесь путь с параметром — объявляйте
    его ПОСЛЕ литеральных. Проверяется tests/services/test_audit_routes.py.

ОШИБКА НЕ ПРЕВРАЩАЕТСЯ В ПУСТОТУ
    Пустой журнал читается как «ничего не происходило». На экране, где
    следят за деньгами и доступами, это самая вредная неправда: она
    успокаивает. Поэтому отказ базы здесь — 500 с внятным кодом, а не
    200 с пустым списком, и фронт пишет «журнал не ответил».

    Счётчики по категориям едут рядом со списком и считаются по тем же
    фильтрам, кроме самой категории. Без них ноль строк в выбранной
    категории было бы неотличимо от «журнал пуст целиком».

СЕКРЕТЫ
    В details попадает текст исключения, а в него — URL метода Telegram
    с токеном бота. Наружу details уходит только пройдя
    app.utils.security.scrub_secrets: за это отвечает слой базы
    (database/dashboard_events.py), здесь же — /recent, который раньше
    отдавал строку из базы как есть.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import database
from app.api.dashboard.deps import require_admin
from app.utils.security import scrub_secrets
from database.dashboard_events import CATEGORIES

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/events")
async def audit_events(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    # 0 — «за всё время». Отдельного значения-строки не заводим: любое
    # число часов, включая ноль, читается одинаково и клиентом, и в логе.
    hours: int = Query(0, ge=0, le=8760),
    who: Optional[int] = Query(None, description="telegram_id автора ИЛИ адресата"),
    q: Optional[str] = Query(None, max_length=100),
    category: Optional[List[str]] = Query(None),
):
    """Страница журнала плюс счётчики по категориям.

    Фильтр по человеку намеренно один на две роли: вопрос «что было с
    этим telegram_id» не делится на «он сделал» и «с ним сделали» —
    отвечать надо обоими списками сразу.
    """
    cats = [c for c in (category or []) if c in CATEGORIES] or None
    text = (q or "").strip() or None
    try:
        items = await database.get_audit_events(
            limit=limit,
            offset=offset,
            hours=hours or None,
            who=who,
            query=text,
            categories=cats,
        )
        counts = await database.get_audit_category_counts(
            hours=hours or None, who=who, query=text,
        )
    except Exception as e:
        logger.exception(
            "AUDIT_EVENTS_FAILED limit=%s offset=%s hours=%s who=%s cats=%s",
            limit, offset, hours, who, cats,
        )
        # Текст исключения долетает до браузера в detail — значит,
        # обязан пройти через scrub_secrets.
        raise HTTPException(500, f"audit_events_failed: {scrub_secrets(e)}")

    # Сколько записей проходит под текущие фильтры целиком — по нему фронт
    # решает, показывать ли «Показать ещё», и печатает «N из M».
    total = sum(counts[c] for c in (cats or CATEGORIES))
    logger.info(
        "AUDIT_EVENTS_OK returned=%s total=%s offset=%s hours=%s who=%s cats=%s q=%s",
        len(items), total, offset, hours, who, cats, bool(text),
    )
    return {
        "items": items,
        "total": total,
        "counts": counts,
        "has_more": offset + len(items) < total,
    }


@router.get("/recent")
async def audit_recent(limit: int = Query(50, gt=0, le=500)):
    """Плоский срез журнала, новое сверху. Экран им больше не пользуется.

    Оставлен ради внешних вызовов, но details теперь проходит через
    scrub_secrets: раньше строка из базы уезжала наружу как есть, вместе
    с токеном бота из текста исключения.
    """
    try:
        rows = await database.get_last_audit_logs(limit)
    except Exception as e:
        logger.exception("AUDIT_RECENT_FAILED limit=%s", limit)
        raise HTTPException(500, f"audit_failed: {scrub_secrets(e)}")
    out: list = []
    for row in rows:
        item: dict = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                item[k] = v.isoformat()
            elif isinstance(v, (bytes, bytearray)):
                continue
            elif k == "details":
                item[k] = scrub_secrets(v)
            else:
                item[k] = v
        out.append(item)
    logger.info("AUDIT_RECENT_OK returned=%s limit=%s", len(out), limit)
    return out
