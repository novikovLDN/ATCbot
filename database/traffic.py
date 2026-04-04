"""
Database operations for Remnawave traffic integration.

- remnawave_uuid CRUD on subscriptions table
- traffic notification flags on users table
- traffic_purchases table
"""
import logging
from typing import Optional, List, Dict, Any

import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)


# ── Remnawave UUID ─────────────────────────────────────────────────────

async def get_remnawave_uuid(telegram_id: int) -> Optional[str]:
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT remnawave_uuid FROM subscriptions WHERE telegram_id = $1 AND status = 'active'",
            telegram_id,
        )


async def set_remnawave_uuid(telegram_id: int, uuid: str) -> None:
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_uuid = $1 WHERE telegram_id = $2 AND status = 'active'",
            uuid, telegram_id,
        )


async def set_remnawave_short_uuid(telegram_id: int, short_uuid: str) -> None:
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_short_uuid = $1 WHERE telegram_id = $2 AND status = 'active'",
            short_uuid, telegram_id,
        )


async def get_remnawave_short_uuid(telegram_id: int) -> Optional[str]:
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT remnawave_short_uuid FROM subscriptions WHERE telegram_id = $1 AND status = 'active'",
            telegram_id,
        )


async def clear_remnawave_uuid(telegram_id: int) -> None:
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_uuid = NULL, remnawave_short_uuid = NULL WHERE telegram_id = $1",
            telegram_id,
        )


# ── Traffic notification flags ─────────────────────────────────────────

async def get_traffic_notification_flags(telegram_id: int) -> Dict[str, bool]:
    if not _core.DB_READY:
        return {}
    pool = await get_pool()
    if pool is None:
        return {}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT traffic_notified_3gb, traffic_notified_1gb,
                      traffic_notified_500mb, traffic_notified_0
               FROM users WHERE telegram_id = $1""",
            telegram_id,
        )
        if not row:
            return {}
        return dict(row)


async def set_traffic_notification_flag(telegram_id: int, flag_key: str) -> None:
    if not _core.DB_READY:
        return
    # Whitelist valid flag columns to prevent injection
    valid = {"traffic_notified_3gb", "traffic_notified_1gb", "traffic_notified_500mb", "traffic_notified_0"}
    if flag_key not in valid:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET {flag_key} = TRUE WHERE telegram_id = $1",
            telegram_id,
        )


async def reset_traffic_notification_flags(telegram_id: int) -> None:
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE users SET
                traffic_notified_3gb = FALSE,
                traffic_notified_1gb = FALSE,
                traffic_notified_500mb = FALSE,
                traffic_notified_0 = FALSE
               WHERE telegram_id = $1""",
            telegram_id,
        )


# ── Traffic purchases ──────────────────────────────────────────────────

async def record_traffic_purchase(
    telegram_id: int,
    gb_amount: int,
    price_rub: int,
    payment_method: str = "balance",
) -> Optional[int]:
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO traffic_purchases (telegram_id, gb_amount, price_rub, payment_method)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            telegram_id, gb_amount, price_rub, payment_method,
        )


# ── Queries for traffic monitor worker ─────────────────────────────────

async def get_active_remnawave_users() -> List[Dict[str, Any]]:
    """Users with active subscription AND remnawave_uuid set."""
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.telegram_id, s.remnawave_uuid, s.subscription_type
               FROM subscriptions s
               WHERE s.status = 'active'
                 AND s.remnawave_uuid IS NOT NULL""",
        )
        return [dict(r) for r in rows]
