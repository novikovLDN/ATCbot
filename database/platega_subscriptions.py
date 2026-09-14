"""
Platega recurring SBP subscriptions — DB helpers.

Таблицы:
  - platega_subscriptions          (migration 074)
  - platega_subscription_charges   (migration 074) — списания, PK = charge_id
                                   для идемпотентности webhook'а.

Все функции fail-safe: любое исключение → warning log + None/False.
Никогда не raise (webhook и UI не должны падать из-за DB flake).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)


async def create_subscription(
    subscription_id: str,
    telegram_id: int,
    amount_kopecks: int,
    interval_days: int,
    tariff_type: str,
    description: Optional[str] = None,
) -> None:
    """Записать вновь созданную подписку (статус по умолчанию — PendingAgreement).

    Если запись с таким subscription_id уже существует — ничего не делаем
    (ON CONFLICT DO NOTHING). Callback'и SUBSCRIPTION_ACTIVATED / первое
    списание переведут её в статус Active через update_subscription_status.
    """
    if not _core.DB_READY:
        logger.warning("platega_subscriptions.create_subscription: DB not ready, skip")
        return
    pool = await get_pool()
    if pool is None:
        logger.warning("platega_subscriptions.create_subscription: pool is None")
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO platega_subscriptions
                    (subscription_id, telegram_id, amount_kopecks, interval_days,
                     tariff_type, description, status, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, 'PendingAgreement', NOW(), NOW())
                ON CONFLICT (subscription_id) DO NOTHING
                """,
                subscription_id, int(telegram_id), int(amount_kopecks),
                int(interval_days), tariff_type, (description or "")[:2000],
            )
        logger.info(
            "platega_sub_created: id=%s tg=%s amount_k=%s interval_d=%s tariff=%s",
            subscription_id, telegram_id, amount_kopecks, interval_days, tariff_type,
        )
    except Exception as e:
        logger.warning(
            "platega_subscriptions.create_subscription failed: id=%s tg=%s err=%s",
            subscription_id, telegram_id, e,
        )


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


async def get_user_active_subscriptions(telegram_id: int) -> List[Dict[str, Any]]:
    """Список подписок юзера в состоянии Active (или PendingAgreement).

    PastDue не считаем активной для UI — юзер может создать новую.
    Возвращаем [] при ошибке.
    """
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM platega_subscriptions
                WHERE telegram_id = $1 AND status IN ('Active', 'PendingAgreement')
                ORDER BY created_at DESC
                """,
                int(telegram_id),
            )
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(
            "platega_subscriptions.get_user_active_subscriptions failed: tg=%s err=%s",
            telegram_id, e,
        )
        return []


async def update_subscription_status(
    subscription_id: str,
    status: str,
    next_charge_at: Optional[datetime] = None,
    customer_email: Optional[str] = None,
) -> None:
    """Обновить статус подписки + (опционально) next_charge_at / customer_email.

    COALESCE — если параметр None, оставляем существующее значение.
    """
    if not _core.DB_READY:
        logger.warning("platega_subscriptions.update_subscription_status: DB not ready")
        return
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE platega_subscriptions
                   SET status = $2,
                       next_charge_at = COALESCE($3, next_charge_at),
                       customer_email = COALESCE($4, customer_email),
                       updated_at = NOW()
                 WHERE subscription_id = $1
                """,
                subscription_id, status, next_charge_at, customer_email,
            )
    except Exception as e:
        logger.warning(
            "platega_subscriptions.update_subscription_status failed: id=%s status=%s err=%s",
            subscription_id, status, e,
        )


async def record_charge(
    charge_id: str,
    subscription_id: str,
    telegram_id: int,
    amount_kopecks: int,
    status: str,
) -> bool:
    """Идемпотентная запись списания.

    Возвращает:
      True  — списание записано впервые (можно продлевать подписку/уведомлять).
      False — такой charge_id уже был (дубль webhook'а) ИЛИ DB упала.

    Использует INSERT ... ON CONFLICT DO NOTHING RETURNING — атомарно
    относительно параллельных webhook'ов. Если RETURNING вернул строку
    → это первый вызов; NULL → уже был.

    Также инкрементит charges_success / charges_failed / last_charge_at /
    total_amount_kopecks в platega_subscriptions одной транзакцией — чтобы
    счётчики не разъехались с фактически записанными списаниями.
    """
    if not _core.DB_READY:
        logger.warning("platega_subscriptions.record_charge: DB not ready")
        return False
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    """
                    INSERT INTO platega_subscription_charges
                        (charge_id, subscription_id, telegram_id, amount_kopecks, status, created_at)
                    VALUES ($1, $2, $3, $4, $5, NOW())
                    ON CONFLICT (charge_id) DO NOTHING
                    RETURNING charge_id
                    """,
                    charge_id, subscription_id, int(telegram_id),
                    int(amount_kopecks), status,
                )
                if not inserted:
                    # Дубль — счётчики НЕ трогаем.
                    return False

                status_upper = (status or "").upper()
                if status_upper == "CONFIRMED":
                    await conn.execute(
                        """
                        UPDATE platega_subscriptions
                           SET charges_success = charges_success + 1,
                               total_amount_kopecks = total_amount_kopecks + $2,
                               last_charge_at = NOW(),
                               updated_at = NOW()
                         WHERE subscription_id = $1
                        """,
                        subscription_id, int(amount_kopecks),
                    )
                elif status_upper == "CANCELED":
                    await conn.execute(
                        """
                        UPDATE platega_subscriptions
                           SET charges_failed = charges_failed + 1,
                               updated_at = NOW()
                         WHERE subscription_id = $1
                        """,
                        subscription_id,
                    )
                # Прочие статусы — просто фиксируем факт в charges без счётчиков.
        return True
    except Exception as e:
        logger.warning(
            "platega_subscriptions.record_charge failed: charge=%s sub=%s err=%s",
            charge_id, subscription_id, e,
        )
        return False
