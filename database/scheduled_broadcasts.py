"""
Scheduled broadcasts — отложенные + повторяющиеся рассылки.

Все datetime внутри БД — TIMESTAMPTZ, храним в UTC. Админ-UI вводит
время в Europe/Moscow (UTC+3) — конвертация происходит на слое API.

recurrence:
    'once'      — разовая
    'daily'     — каждый день в то же время
    'weekdays'  — понедельник-пятница
    'weekly'    — раз в неделю в тот же день недели
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)


VALID_RECURRENCES = {"once", "daily", "weekdays", "weekly"}


async def create_scheduled_broadcast(
    *,
    source_broadcast_id: Optional[int],
    title: str,
    message: str,
    segment: str,
    scheduled_at: datetime,
    recurrence: str,
    created_by: int,
    photo_file_id: Optional[str] = None,
    buttons: Optional[List[str]] = None,
    discount_percent: Optional[int] = None,
    discount_hours: Optional[int] = None,
    discount_label: Optional[str] = None,
    gift_reveal_percent: Optional[int] = None,
    recurrence_end_at: Optional[datetime] = None,
) -> int:
    """Создать задание. Возвращает scheduled_broadcast.id."""
    if recurrence not in VALID_RECURRENCES:
        raise ValueError(f"invalid recurrence: {recurrence}")
    if scheduled_at.tzinfo is None:
        raise ValueError("scheduled_at must be timezone-aware")
    if recurrence_end_at is not None and recurrence_end_at.tzinfo is None:
        raise ValueError("recurrence_end_at must be timezone-aware")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO scheduled_broadcasts (
                    source_broadcast_id, title, message, photo_file_id, buttons,
                    segment, discount_percent, discount_hours, discount_label,
                    gift_reveal_percent, scheduled_at, recurrence,
                    recurrence_end_at, created_by
               ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
               RETURNING id""",
            source_broadcast_id, title, message, photo_file_id,
            list(buttons) if buttons else None,
            segment, discount_percent, discount_hours, discount_label,
            gift_reveal_percent, scheduled_at, recurrence,
            recurrence_end_at, created_by,
        )
        return int(row["id"])


async def list_scheduled_broadcasts(
    *, active_only: bool = True, limit: int = 200,
) -> List[Dict[str, Any]]:
    """Список запланированных рассылок. active_only=True — только те что
    будут выполнены."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if active_only:
            rows = await conn.fetch(
                """SELECT * FROM scheduled_broadcasts
                   WHERE is_active
                   ORDER BY scheduled_at ASC
                   LIMIT $1""",
                limit,
            )
        else:
            rows = await conn.fetch(
                """SELECT * FROM scheduled_broadcasts
                   ORDER BY id DESC
                   LIMIT $1""",
                limit,
            )
        return [dict(r) for r in rows]


async def get_scheduled_broadcast(sched_id: int) -> Optional[Dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM scheduled_broadcasts WHERE id = $1", sched_id,
        )
        return dict(row) if row else None


async def cancel_scheduled_broadcast(sched_id: int, cancelled_by: int) -> bool:
    """Отменить — is_active=FALSE + timestamp. Обратимо только через SQL."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            """UPDATE scheduled_broadcasts
               SET is_active = FALSE,
                   cancelled_at = NOW(),
                   cancelled_by = $2
               WHERE id = $1 AND is_active = TRUE""",
            sched_id, cancelled_by,
        )
        return res.startswith("UPDATE ") and res != "UPDATE 0"


def _next_run_after(
    current_run_at: datetime, recurrence: str,
) -> Optional[datetime]:
    """Посчитать следующий запуск для повторяющейся задачи.
    Возвращает None для 'once'."""
    if recurrence == "once":
        return None
    if recurrence == "daily":
        return current_run_at + timedelta(days=1)
    if recurrence == "weekly":
        return current_run_at + timedelta(days=7)
    if recurrence == "weekdays":
        # Пропускаем сб/вс. weekday: 0=Mon..6=Sun
        candidate = current_run_at + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate
    return None


async def mark_ran_and_reschedule(
    sched_id: int,
    *,
    last_broadcast_id: Optional[int],
    error: Optional[str] = None,
) -> None:
    """Отметить факт запуска + вычислить следующий scheduled_at.
    Если рекуррентность вышла за recurrence_end_at или once — деактивировать."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM scheduled_broadcasts WHERE id = $1", sched_id,
        )
        if not row:
            return
        recurrence = row["recurrence"]
        current_run = row["scheduled_at"]
        end_at = row["recurrence_end_at"]
        next_run = _next_run_after(current_run, recurrence)
        if next_run is not None and end_at is not None and next_run > end_at:
            next_run = None

        if next_run is None:
            await conn.execute(
                """UPDATE scheduled_broadcasts
                   SET last_run_at = NOW(),
                       last_run_broadcast_id = COALESCE($2, last_run_broadcast_id),
                       run_count = run_count + 1,
                       last_error = $3,
                       is_active = FALSE
                   WHERE id = $1""",
                sched_id, last_broadcast_id, error,
            )
        else:
            await conn.execute(
                """UPDATE scheduled_broadcasts
                   SET last_run_at = NOW(),
                       last_run_broadcast_id = COALESCE($2, last_run_broadcast_id),
                       run_count = run_count + 1,
                       last_error = $3,
                       scheduled_at = $4
                   WHERE id = $1""",
                sched_id, last_broadcast_id, error, next_run,
            )


async def fetch_due_scheduled(limit: int = 10) -> List[Dict[str, Any]]:
    """Кандидаты на запуск — scheduled_at <= NOW, is_active=TRUE.
    Использует SKIP LOCKED, чтобы несколько worker'ов не хватали одну и ту же
    задачу (хоть у нас один worker — на будущее)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM scheduled_broadcasts
               WHERE is_active AND scheduled_at <= NOW()
               ORDER BY scheduled_at ASC
               LIMIT $1
               FOR UPDATE SKIP LOCKED""",
            limit,
        )
        return [dict(r) for r in rows]
