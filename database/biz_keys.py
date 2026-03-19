"""
Database operations for business client key management.

Бизнес-пользователи могут создавать временные VPN-ключи для своих клиентов.
Каждый ключ = один визит с ограниченным временем жизни (от 10 мин до 24 ч).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

import config
import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)


async def get_biz_max_clients(telegram_id: int) -> int:
    """Получить лимит клиентов в день для бизнес-пользователя."""
    if not _core.DB_READY:
        return config.BIZ_DEFAULT_MAX_CLIENTS_PER_DAY
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT max_clients_per_day FROM biz_settings WHERE telegram_id = $1",
            telegram_id,
        )
        if row:
            return row["max_clients_per_day"]
    return config.BIZ_DEFAULT_MAX_CLIENTS_PER_DAY


async def set_biz_max_clients(telegram_id: int, max_clients: int) -> None:
    """Установить лимит клиентов в день (админ)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO biz_settings (telegram_id, max_clients_per_day)
               VALUES ($1, $2)
               ON CONFLICT (telegram_id) DO UPDATE SET max_clients_per_day = $2""",
            telegram_id, max_clients,
        )


async def count_keys_today(telegram_id: int) -> int:
    """Сколько ключей создано владельцем за сегодня."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return await conn.fetchval(
            "SELECT COUNT(*) FROM biz_client_keys WHERE owner_telegram_id = $1 AND created_at >= $2",
            telegram_id, today_start,
        ) or 0


async def get_active_keys(telegram_id: int) -> List[Dict[str, Any]]:
    """Получить все активные (не истекшие, не отозванные) ключи владельца."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM biz_client_keys
               WHERE owner_telegram_id = $1
                 AND revoked_at IS NULL
                 AND expires_at > NOW()
               ORDER BY created_at DESC""",
            telegram_id,
        )
        return [dict(r) for r in rows]


async def get_key_by_id(key_id: int, owner_telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить ключ по ID (проверяя владельца)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM biz_client_keys WHERE id = $1 AND owner_telegram_id = $2",
            key_id, owner_telegram_id,
        )
        return dict(row) if row else None


async def create_client_key(
    owner_telegram_id: int,
    client_name: str,
    vless_url: str,
    uuid: str,
    duration_minutes: int,
) -> Dict[str, Any]:
    """Создать клиентский ключ."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=duration_minutes)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO biz_client_keys
                   (owner_telegram_id, client_name, vless_url, uuid, created_at, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6)
               RETURNING *""",
            owner_telegram_id, client_name, vless_url, uuid, now, expires_at,
        )
        return dict(row)


async def revoke_key(key_id: int, owner_telegram_id: int) -> bool:
    """Досрочно отозвать ключ."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE biz_client_keys
               SET revoked_at = NOW()
               WHERE id = $1 AND owner_telegram_id = $2 AND revoked_at IS NULL""",
            key_id, owner_telegram_id,
        )
        return result.endswith("1")


async def extend_key(key_id: int, owner_telegram_id: int, extra_minutes: int) -> Optional[Dict[str, Any]]:
    """Продлить ключ на extra_minutes минут."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE biz_client_keys
               SET expires_at = expires_at + ($3 * INTERVAL '1 minute'),
                   extended_count = extended_count + 1
               WHERE id = $1 AND owner_telegram_id = $2 AND revoked_at IS NULL
               RETURNING *""",
            key_id, owner_telegram_id, extra_minutes,
        )
        return dict(row) if row else None


async def get_keys_expiring_soon(minutes_before: int = 30) -> List[Dict[str, Any]]:
    """Получить ключи, которые истекут через minutes_before минут (для уведомлений)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        threshold = now + timedelta(minutes=minutes_before)
        rows = await conn.fetch(
            """SELECT * FROM biz_client_keys
               WHERE revoked_at IS NULL
                 AND notified_30min = FALSE
                 AND expires_at > $1
                 AND expires_at <= $2""",
            now, threshold,
        )
        return [dict(r) for r in rows]


async def mark_key_notified(key_id: int) -> None:
    """Пометить ключ как уведомлённый (30 мин до истечения)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE biz_client_keys SET notified_30min = TRUE WHERE id = $1",
            key_id,
        )


async def get_analytics(telegram_id: int) -> Dict[str, Any]:
    """Аналитика по ключам владельца."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM biz_client_keys WHERE owner_telegram_id = $1",
            telegram_id,
        ) or 0
        active = await conn.fetchval(
            """SELECT COUNT(*) FROM biz_client_keys
               WHERE owner_telegram_id = $1 AND revoked_at IS NULL AND expires_at > NOW()""",
            telegram_id,
        ) or 0
        today = await count_keys_today(telegram_id)
        max_clients = await get_biz_max_clients(telegram_id)
        return {
            "total_created": total,
            "active_now": active,
            "created_today": today,
            "max_per_day": max_clients,
            "remaining_today": max(0, max_clients - today),
        }
