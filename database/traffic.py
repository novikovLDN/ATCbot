"""
Database functions for Remnawave traffic tracking.

Tables:
    - traffic_purchases: purchased GB packs
    - users: traffic notification flags (traffic_notified_3gb, etc.)
    - subscriptions: remnawave_uuid
"""
import logging
from typing import Optional

from database.core import get_pool

logger = logging.getLogger(__name__)


# =========================================================================
# Remnawave UUID
# =========================================================================

async def get_remnawave_uuid(telegram_id: int) -> Optional[str]:
    """Get Remnawave UUID for a user (stored in subscriptions table)."""
    pool = await get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT remnawave_uuid FROM subscriptions WHERE telegram_id = $1",
            telegram_id,
        )


async def set_remnawave_uuid(telegram_id: int, remnawave_uuid: str) -> None:
    """Set Remnawave UUID for a user."""
    pool = await get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_uuid = $1 WHERE telegram_id = $2",
            remnawave_uuid, telegram_id,
        )


async def clear_remnawave_uuid(telegram_id: int) -> None:
    """Clear Remnawave UUID for a user."""
    pool = await get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_uuid = NULL WHERE telegram_id = $1",
            telegram_id,
        )


# =========================================================================
# Traffic notification flags
# =========================================================================

async def get_traffic_notification_flags(telegram_id: int) -> dict:
    """Get traffic notification flags for a user."""
    pool = await get_pool()
    if not pool:
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
    return {
        "3gb": row["traffic_notified_3gb"] or False,
        "1gb": row["traffic_notified_1gb"] or False,
        "500mb": row["traffic_notified_500mb"] or False,
        "0": row["traffic_notified_0"] or False,
    }


async def set_traffic_notification_flag(telegram_id: int, flag: str) -> None:
    """
    Set a single traffic notification flag.
    flag: one of "3gb", "1gb", "500mb", "0"
    """
    column_map = {
        "3gb": "traffic_notified_3gb",
        "1gb": "traffic_notified_1gb",
        "500mb": "traffic_notified_500mb",
        "0": "traffic_notified_0",
    }
    col = column_map.get(flag)
    if not col:
        return
    pool = await get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET {col} = TRUE WHERE telegram_id = $1",
            telegram_id,
        )


async def reset_traffic_notification_flags(telegram_id: int) -> None:
    """Reset all traffic notification flags (after traffic purchase or renewal)."""
    pool = await get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE users
               SET traffic_notified_3gb = FALSE,
                   traffic_notified_1gb = FALSE,
                   traffic_notified_500mb = FALSE,
                   traffic_notified_0 = FALSE
               WHERE telegram_id = $1""",
            telegram_id,
        )


# =========================================================================
# Traffic purchases
# =========================================================================

async def record_traffic_purchase(
    telegram_id: int,
    gb_amount: int,
    price_rub: int,
    purchase_id: Optional[str] = None,
) -> None:
    """Record a traffic pack purchase."""
    pool = await get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO traffic_purchases (telegram_id, gb_amount, price_rub, purchase_id)
               VALUES ($1, $2, $3, $4)""",
            telegram_id, gb_amount, price_rub, purchase_id,
        )


# =========================================================================
# Active users with Remnawave (for traffic monitor worker)
# =========================================================================

async def get_active_remnawave_users() -> list:
    """
    Get all active subscriptions that have a remnawave_uuid.
    Returns list of dicts with telegram_id and remnawave_uuid.
    """
    pool = await get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.telegram_id, s.remnawave_uuid
               FROM subscriptions s
               WHERE s.status = 'active'
                 AND s.remnawave_uuid IS NOT NULL
                 AND s.remnawave_uuid != ''""",
        )
    return [dict(r) for r in rows]


async def get_active_users_without_remnawave() -> list:
    """
    Get active subscriptions that do NOT have a remnawave_uuid yet.
    Used to auto-provision existing subscribers on Remnawave.
    Returns list of dicts with telegram_id, uuid, expires_at, subscription_type.
    """
    pool = await get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.telegram_id, s.uuid, s.expires_at, s.subscription_type
               FROM subscriptions s
               WHERE s.status = 'active'
                 AND s.uuid IS NOT NULL AND s.uuid != ''
                 AND (s.remnawave_uuid IS NULL OR s.remnawave_uuid = '')
                 AND s.expires_at > NOW()""",
        )
    return [dict(r) for r in rows]
