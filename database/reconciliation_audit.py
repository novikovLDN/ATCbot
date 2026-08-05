"""Сверка: журналы — чтение обоих и запись превышений.

ЧТО ЗДЕСЬ
    • `list_reconciliation_log` — что уже исправляли руками;
    • `list_over_issuance_log` — когда и чем срок раздули;
    • `record_over_issuance` — запись превышения, её зовёт
      app/services/subscription_watchdog после каждой записи expires_at.

ПОЧЕМУ ВЫДЕЛЕНО
    Журналы — отдельная таблица и отдельный повод для правок (колонки,
    лимиты, ретеншен). К расчёту сроков и к панели они не относятся, а
    `record_over_issuance` вообще вызывается из вотчдога, а не из экрана
    сверки.

ЧТО ЛЕГКО СЛОМАТЬ
    Обе читалки ловят UndefinedTableError и пишут ERROR в лог. Убрать
    логирование — и отсутствующая таблица журнала станет неотличима от
    «исправлений не было»: соврёт сам инструмент аудита.

    `record_over_issuance` не должна поднимать исключений: её зовут из
    вотчдога по ходу выдачи подписки, и падение здесь уронило бы выдачу.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from database.core import get_pool, _to_db_utc

logger = logging.getLogger(__name__)


async def list_reconciliation_log(limit: int = 100) -> List[Dict[str, Any]]:
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """SELECT id, telegram_id, old_expires_at, new_expires_at,
                          old_days_from_now, new_days_from_now, days_removed,
                          reason, proof_payment_ids, total_paid_days,
                          admin_grant_days_kept, admin_telegram_id, created_at
                   FROM subscription_reconciliation_log
                   ORDER BY created_at DESC
                   LIMIT $1""",
                limit,
            )
        except asyncpg.UndefinedTableError:
            # Самая опасная форма вранья: врёт сам инструмент аудита.
            # Пустой список без записи в лог экран сверки рисует как
            # «исправлений не было», хотя на деле журнала не существует —
            # то есть «аудита нет вовсе» выглядит как «всё чисто».
            logger.error(
                "RECONCILIATION_LOG_TABLE_MISSING — таблица "
                "subscription_reconciliation_log отсутствует; журнал сверки "
                "пуст не потому, что исправлений не было",
            )
            return []
    return [_serialize(r) for r in rows]


async def list_over_issuance_log(limit: int = 100) -> List[Dict[str, Any]]:
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """SELECT id, telegram_id, old_expires_at, new_expires_at,
                          duration_added_seconds, grant_action, source, tariff,
                          admin_telegram_id, admin_grant_days,
                          caller_context, created_at
                   FROM subscription_over_issuance_log
                   ORDER BY created_at DESC
                   LIMIT $1""",
                limit,
            )
        except asyncpg.UndefinedTableError:
            # См. выше: пустой журнал должен отличаться от отсутствующего.
            logger.error(
                "OVER_ISSUANCE_LOG_TABLE_MISSING — таблица "
                "subscription_over_issuance_log отсутствует; журнал пуст не "
                "потому, что превышений не было",
            )
            return []
    return [_serialize(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────
#  Запись превышений — сюда ходит app/services/subscription_watchdog
# ──────────────────────────────────────────────────────────────────────

async def record_over_issuance(
    telegram_id: int,
    *,
    old_expires_at: Optional[datetime],
    new_expires_at: datetime,
    grant_action: str,
    source: Optional[str],
    tariff: Optional[str],
    admin_telegram_id: Optional[int],
    admin_grant_days: Optional[int],
    caller_context: Optional[str],
) -> Optional[int]:
    """Insert one over-issuance log row. Fire-and-forget — never raises."""
    pool = await get_pool()
    if pool is None:
        return None
    try:
        duration_added = None
        if new_expires_at and old_expires_at:
            duration_added = int((new_expires_at - old_expires_at).total_seconds())
        elif new_expires_at:
            duration_added = int(
                (new_expires_at - datetime.now(timezone.utc)).total_seconds()
            )
        async with pool.acquire() as conn:
            log_id = await conn.fetchval(
                """INSERT INTO subscription_over_issuance_log (
                       telegram_id, old_expires_at, new_expires_at,
                       duration_added_seconds, grant_action, source, tariff,
                       admin_telegram_id, admin_grant_days, caller_context
                   )
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   RETURNING id""",
                telegram_id,
                _to_db_utc(old_expires_at) if old_expires_at else None,
                _to_db_utc(new_expires_at),
                duration_added,
                grant_action,
                source,
                tariff,
                admin_telegram_id,
                admin_grant_days,
                (caller_context or "")[:2000],
            )
        return log_id
    except Exception as e:
        logger.warning(
            "record_over_issuance failed user=%s: %s", telegram_id, e,
        )
        return None


def _serialize(row) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            continue
        else:
            out[k] = v
    return out

