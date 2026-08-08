"""DB helpers для Happ custom-theme tokens (admin-only feature).

Отдельный модуль, чтобы rollback фичи сводился к DROP TABLE +
удалению этого файла без правки основного subscriptions.py.
"""
from __future__ import annotations

import logging
import secrets
from typing import Optional

from database.core import get_pool

logger = logging.getLogger(__name__)


async def get_or_create_token(telegram_id: int, remnawave_uuid: str) -> Optional[str]:
    """Найти или создать token для (telegram_id + remnawave_uuid).

    Идемпотентно: один telegram_id → один token. Если UUID сменился
    (renewal / re-provision) — обновляем без смены токена.
    """
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT token, remnawave_uuid FROM happ_theme_tokens "
            "WHERE telegram_id = $1",
            telegram_id,
        )
        if row is not None:
            token = row["token"]
            if row["remnawave_uuid"] != remnawave_uuid:
                await conn.execute(
                    "UPDATE happ_theme_tokens SET remnawave_uuid = $2 WHERE token = $1",
                    token, remnawave_uuid,
                )
            return token

        token = secrets.token_hex(16)  # 128 бит энтропии
        await conn.execute(
            "INSERT INTO happ_theme_tokens (token, telegram_id, remnawave_uuid) "
            "VALUES ($1, $2, $3)",
            token, telegram_id, remnawave_uuid,
        )
        return token


async def get_by_token(token: str) -> Optional[dict]:
    """Найти запись по token. None если не существует."""
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT token, telegram_id, remnawave_uuid, "
            "       created_at, last_accessed, access_count "
            "FROM happ_theme_tokens WHERE token = $1",
            token,
        )
    return dict(row) if row else None


async def touch_access(token: str) -> None:
    """UPDATE last_accessed + access_count. Fail-safe."""
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE happ_theme_tokens "
                "SET last_accessed = NOW(), access_count = access_count + 1 "
                "WHERE token = $1",
                token,
            )
    except Exception as e:
        logger.warning("happ_theme touch_access failed token=%s: %s", token[:8], e)
