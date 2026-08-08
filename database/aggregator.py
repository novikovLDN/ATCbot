"""DB helpers для aggregated subscriptions (admin-only beta feature).

Отдельный модуль, чтобы rollback фичи сводился к DROP TABLE +
удалению этого файла без правки основного subscriptions.py.
"""
from __future__ import annotations

import logging
import secrets
from typing import Optional

from database.core import get_pool

logger = logging.getLogger(__name__)


async def get_or_create_token(
    telegram_id: int,
    premium_uuid: str,
    whitelist_uuid: str,
) -> Optional[str]:
    """Найти существующую запись по telegram_id или создать новую.

    Идемпотентность: один telegram_id → всегда один и тот же token.
    Если UUID'ы поменялись (новая подписка) — обновляем строку, оставляя
    старый токен, чтобы прежние клиенты продолжали работать.
    """
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT combined_token, premium_uuid, whitelist_uuid "
            "FROM aggregated_subscriptions WHERE telegram_id = $1",
            telegram_id,
        )
        if row is not None:
            token = row["combined_token"]
            # Обновляем UUID, если сменились (renewal → новый premium_uuid).
            if row["premium_uuid"] != premium_uuid or row["whitelist_uuid"] != whitelist_uuid:
                await conn.execute(
                    "UPDATE aggregated_subscriptions "
                    "SET premium_uuid = $2, whitelist_uuid = $3 "
                    "WHERE combined_token = $1",
                    token, premium_uuid, whitelist_uuid,
                )
            return token

        token = secrets.token_hex(16)  # 32 hex = 128 бит энтропии
        await conn.execute(
            "INSERT INTO aggregated_subscriptions "
            "(combined_token, telegram_id, premium_uuid, whitelist_uuid) "
            "VALUES ($1, $2, $3, $4)",
            token, telegram_id, premium_uuid, whitelist_uuid,
        )
        return token


async def get_by_token(combined_token: str) -> Optional[dict]:
    """Найти запись по combined_token. None если не существует."""
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT combined_token, telegram_id, premium_uuid, whitelist_uuid, "
            "       created_at, last_accessed, access_count "
            "FROM aggregated_subscriptions WHERE combined_token = $1",
            combined_token,
        )
    return dict(row) if row else None


async def touch_access(combined_token: str) -> None:
    """UPDATE last_accessed + access_count. Fail-safe: молча логируем при ошибке."""
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE aggregated_subscriptions "
                "SET last_accessed = NOW(), access_count = access_count + 1 "
                "WHERE combined_token = $1",
                combined_token,
            )
    except Exception as e:
        logger.warning("agg touch_access failed token=%s: %s", combined_token[:8], e)
