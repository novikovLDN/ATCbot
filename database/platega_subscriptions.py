"""
Platega recurring SBP subscriptions — read-only DB helpers.

Рекуррентные подписки ОТКЛЮЧЕНЫ (решение владельца): код создания подписок и
обработки списаний удалён. Таблицы остаются (данные беты не трогаем, DROP —
отдельным релизом):
  - platega_subscriptions          (migration 074; is_combo — migration 082)
  - platega_subscription_charges   (migration 074)

Здесь только чтение — для алерта по «залётному» callback'у
(platega_service._handle_subscription_callback) и админ-диагностики
(/platega_sub_status). Ошибки чтения — fail-safe (warning log + None/[]).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)

# Подписки, которые Platega может ещё списывать/присылать callback'и.
LIVE_STATUSES = ("Active", "PendingAgreement", "PastDue")


async def get_subscription(subscription_id: str) -> Optional[Dict[str, Any]]:
    """Вернуть подписку по её platega subscription_id или None."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM platega_subscriptions WHERE subscription_id = $1",
                subscription_id,
            )
            return dict(row) if row else None
    except Exception as e:
        logger.warning(
            "platega_subscriptions.get_subscription failed: id=%s err=%s",
            subscription_id, e,
        )
        return None


async def list_live_subscriptions(limit: int = 50) -> List[Dict[str, Any]]:
    """Подписки в статусах Active/PendingAgreement/PastDue — их надо отменить
    в кабинете Platega (POST /subscription/{id}/cancel). [] при ошибке."""
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT subscription_id, telegram_id, status, next_charge_at
                  FROM platega_subscriptions
                 WHERE status IN ('Active', 'PendingAgreement', 'PastDue')
                 ORDER BY created_at DESC
                 LIMIT $1
                """,
                int(limit),
            )
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("platega_subscriptions.list_live_subscriptions failed: %s", e)
        return []
