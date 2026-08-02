"""Напоминания об истечении подписки: кому и когда писать.

ЧТО ЗДЕСЬ ЕСТЬ
    Выборки подписок, которым пора отправить напоминание, и отметки о том,
    что напоминание уже ушло. Сама отправка живёт в reminders.py — здесь
    только работа с базой.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Выделено из database/subscriptions.py: этот код читают и правят при
    работе над рассылками, а не над платежами, и держать его рядом с
    денежными транзакциями было незачем.

ВАЖНО ПРО ФЛАГИ
    Отметки о напоминаниях (reminder_sent, notified_*) нужны, чтобы не
    писать пользователю повторно. Любая новая выборка обязана их учитывать,
    иначе при рестарте воркера пользователь получит письмо второй раз.
"""
import asyncpg
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import database.core as _core
from database.core import (
    get_pool,
    _to_db_utc,
    _from_db_utc,
    _normalize_subscription_row,
)

logger = logging.getLogger(__name__)


async def get_subscriptions_needing_reminder() -> list:
    """Получить подписки, которым нужно отправить напоминание
    
    Возвращает список подписок, где:
    - expires_at > now (активная)
    - reminder_sent = FALSE
    - expires_at <= now + 3 days
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscriptions_needing_reminder skipped")
        return []
    now = datetime.now(timezone.utc)
    reminder_date = now + timedelta(days=3)
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscriptions_needing_reminder skipped")
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM subscriptions 
               WHERE expires_at > $1 
               AND expires_at <= $2
               AND reminder_sent = FALSE
               ORDER BY expires_at ASC""",
            _to_db_utc(now), _to_db_utc(reminder_date)
        )
        return [_normalize_subscription_row(row) for row in rows]


async def mark_reminder_sent(telegram_id: int):
    """Отметить, что напоминание отправлено пользователю (старая функция, для совместимости)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET reminder_sent = TRUE WHERE telegram_id = $1",
            telegram_id
        )


# SECURITY: Pre-built SQL queries for each reminder flag.
# Eliminates f-string SQL interpolation — only static SQL strings are used.
_REMINDER_FLAG_UPDATE_QUERIES = {
    "reminder_7d_sent": (
        "UPDATE subscriptions SET reminder_7d_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_3d_sent": (
        "UPDATE subscriptions SET reminder_3d_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_1d_sent": (
        "UPDATE subscriptions SET reminder_1d_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_24h_sent": (
        "UPDATE subscriptions SET reminder_24h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_3h_sent": (
        "UPDATE subscriptions SET reminder_3h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_6h_sent": (
        "UPDATE subscriptions SET reminder_6h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "trial_notif_24h_sent": (
        "UPDATE subscriptions SET trial_notif_24h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "trial_notif_3h_sent": (
        "UPDATE subscriptions SET trial_notif_3h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
}

# Expose frozenset for external validation (used by app/services/notifications/service.py)
_ALLOWED_REMINDER_FLAGS = frozenset(_REMINDER_FLAG_UPDATE_QUERIES.keys())


async def mark_reminder_flag_sent(telegram_id: int, flag_name: str):
    """Отметить, что конкретное напоминание отправлено пользователю

    Args:
        telegram_id: Telegram ID пользователя
        flag_name: Имя флага ('reminder_3d_sent', 'reminder_24h_sent', 'reminder_3h_sent', 'reminder_6h_sent')

    Raises:
        ValueError: если flag_name не в whitelist
    """
    query = _REMINDER_FLAG_UPDATE_QUERIES.get(flag_name)
    if query is None:
        raise ValueError(
            f"Invalid flag_name '{flag_name}'. "
            f"Allowed: {sorted(_ALLOWED_REMINDER_FLAGS)}"
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(query, telegram_id)


async def mark_user_unreachable(telegram_id: int) -> None:
    """Mark user as unreachable (chat not found, blocked). Background workers filter by is_reachable."""
    if not _core.DB_READY:
        return
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_reachable = FALSE WHERE telegram_id = $1",
                telegram_id
            )
    except asyncpg.UndefinedColumnError:
        logger.debug("mark_user_unreachable skipped: is_reachable column not present")
    except Exception as e:
        logger.warning(f"mark_user_unreachable failed for user={telegram_id}: {e}")


async def update_last_reminder_at(subscription_id: int) -> None:
    """Update last_reminder_at for idempotency guard (container restart protection)."""
    if not _core.DB_READY:
        return
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE subscriptions SET last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE id = $1",
                subscription_id
            )
    except Exception as e:
        logger.warning(f"update_last_reminder_at failed for subscription_id={subscription_id}: {e}")


# Active promo definition: is_active=true AND deleted_at IS NULL AND expires_at > now() AND used_count < max_uses

async def get_subscriptions_for_reminders() -> list:
    """Получить все активные подписки, которым нужно отправить напоминания

    Filters out users with is_reachable = FALSE (blocked/chat not found).
    Falls back to legacy query if is_reachable column not yet present (migration 014).
    Returns список подписок с информацией о типе (админ-доступ или оплаченный тариф)
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscriptions_for_reminders skipped")
        return []
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscriptions_for_reminders skipped")
        return []
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        query_with_reachable = """
            SELECT s.*,
                   (SELECT action_type FROM subscription_history
                    WHERE telegram_id = s.telegram_id
                    ORDER BY created_at DESC LIMIT 1) as last_action_type
            FROM subscriptions s
            JOIN users u ON s.telegram_id = u.telegram_id
            WHERE s.expires_at > $1
            AND COALESCE(u.is_reachable, TRUE) = TRUE
            ORDER BY s.expires_at ASC"""
        fallback_query = """
            SELECT s.*,
                   (SELECT action_type FROM subscription_history
                    WHERE telegram_id = s.telegram_id
                    ORDER BY created_at DESC LIMIT 1) as last_action_type
            FROM subscriptions s
            WHERE s.expires_at > $1
            ORDER BY s.expires_at ASC"""
        try:
            rows = await conn.fetch(query_with_reachable, _to_db_utc(now))
        except asyncpg.UndefinedColumnError:
            logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, fallback to legacy query")
            rows = await conn.fetch(fallback_query, _to_db_utc(now))
        return [_normalize_subscription_row(row) for row in rows]
