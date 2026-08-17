"""Beta-testing applications storage.

Юзер жмёт кнопку «Оставить заявку» в рассылке → пишем сюда.
UNIQUE(telegram_id, program) → повторный клик безопасен, вернётся
already_applied=True.

Все операции работают через тот же pool, что и остальные database/*
модули.  Ошибки логгируются, не пробрасываются — юзерский flow не
должен ронять кнопку из-за проблем с БД.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import core as _core

logger = logging.getLogger(__name__)


async def record_application(
    telegram_id: int,
    *,
    program: str = "vpn_innovator",
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    source_broadcast_id: Optional[int] = None,
) -> dict[str, Any]:
    """Записать заявку.  Возвращает:
      {"status": "created", "row": {...}}       — новая
      {"status": "already_applied", "row": {...}} — дубль (UNIQUE)
      {"status": "error"}                          — БД недоступна
    """
    if not _core.DB_READY:
        return {"status": "error"}
    pool = await _core.get_pool()
    if pool is None:
        return {"status": "error"}
    try:
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, telegram_id, program, username, first_name, applied_at, "
                "source_broadcast_id FROM beta_applications "
                "WHERE telegram_id = $1 AND program = $2",
                telegram_id, program,
            )
            if existing:
                return {"status": "already_applied", "row": dict(existing)}
            row = await conn.fetchrow(
                """INSERT INTO beta_applications
                       (telegram_id, program, username, first_name, source_broadcast_id)
                   VALUES ($1, $2, $3, $4, $5)
                   RETURNING id, telegram_id, program, username, first_name,
                             applied_at, source_broadcast_id""",
                telegram_id, program, username, first_name, source_broadcast_id,
            )
            return {"status": "created", "row": dict(row)}
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "beta_applications.record_application failed tg=%s program=%s: %s",
            telegram_id, program, e,
        )
        return {"status": "error"}


async def count_applications(program: str = "vpn_innovator") -> int:
    """Общий счётчик заявок по программе."""
    if not _core.DB_READY:
        return 0
    pool = await _core.get_pool()
    if pool is None:
        return 0
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*)::int AS n FROM beta_applications WHERE program = $1",
                program,
            )
            return int(row["n"] or 0) if row else 0
    except Exception as e:  # noqa: BLE001
        logger.warning("beta_applications.count failed: %s", e)
        return 0


async def list_applications(
    program: str = "vpn_innovator",
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Пагинированный список заявок (свежие сверху)."""
    if not _core.DB_READY:
        return []
    pool = await _core.get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, telegram_id, program, username, first_name,
                          applied_at, source_broadcast_id
                     FROM beta_applications
                    WHERE program = $1
                    ORDER BY applied_at DESC
                    LIMIT $2 OFFSET $3""",
                program, limit, offset,
            )
            return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning("beta_applications.list failed: %s", e)
        return []


async def has_applied(telegram_id: int, program: str = "vpn_innovator") -> bool:
    """Быстрая проверка — заявлялся ли юзер."""
    if not _core.DB_READY:
        return False
    pool = await _core.get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM beta_applications WHERE telegram_id = $1 AND program = $2",
                telegram_id, program,
            )
            return row is not None
    except Exception:
        return False


__all__ = [
    "record_application",
    "count_applications",
    "list_applications",
    "has_applied",
]
