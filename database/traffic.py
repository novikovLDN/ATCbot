"""
Database operations for Remnawave traffic integration.

- remnawave_uuid CRUD on subscriptions table
- traffic notification flags on users table
- traffic_purchases table
- user_traffic_discounts table (promo discounts on traffic packs)
"""
import logging
from datetime import datetime, timezone
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


async def clear_remnawave_uuid(telegram_id: int) -> None:
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_uuid = NULL WHERE telegram_id = $1",
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
            """SELECT traffic_notified_8gb, traffic_notified_5gb,
                      traffic_notified_3gb, traffic_notified_1gb,
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
    valid = {"traffic_notified_8gb", "traffic_notified_5gb", "traffic_notified_3gb", "traffic_notified_1gb", "traffic_notified_500mb", "traffic_notified_0"}
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
                traffic_notified_8gb = FALSE,
                traffic_notified_5gb = FALSE,
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
        _has_pm_col = await conn.fetchval(
            """SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'traffic_purchases' AND column_name = 'payment_method'
            )"""
        )
        if _has_pm_col:
            return await conn.fetchval(
                """INSERT INTO traffic_purchases (telegram_id, gb_amount, price_rub, payment_method)
                   VALUES ($1, $2, $3, $4) RETURNING id""",
                telegram_id, gb_amount, price_rub, payment_method,
            )
        else:
            return await conn.fetchval(
                """INSERT INTO traffic_purchases (telegram_id, gb_amount, price_rub)
                   VALUES ($1, $2, $3) RETURNING id""",
                telegram_id, gb_amount, price_rub,
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
                 AND s.remnawave_uuid IS NOT NULL
                 AND s.remnawave_uuid != ''""",
        )
        return [dict(r) for r in rows]


async def get_active_users_without_remnawave() -> List[Dict[str, Any]]:
    """Users with active non-trial subscription but NO remnawave_uuid."""
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.telegram_id, s.subscription_type, s.expires_at
               FROM subscriptions s
               WHERE s.status = 'active'
                 AND s.subscription_type NOT IN ('trial')
                 AND (s.remnawave_uuid IS NULL OR s.remnawave_uuid = '')
                 AND s.expires_at > NOW()
               ORDER BY s.telegram_id""",
        )
        return [dict(r) for r in rows]


# ── Traffic discounts (promo from broadcasts) ─────────────────────────

async def get_user_traffic_discount(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Return active (non-expired) traffic discount for user, or None."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM user_traffic_discounts
               WHERE telegram_id = $1
                 AND (expires_at IS NULL OR expires_at > NOW())
               ORDER BY created_at DESC LIMIT 1""",
            telegram_id,
        )
        return dict(row) if row else None


async def create_user_traffic_discount(
    telegram_id: int,
    discount_percent: int,
    expires_at: Optional[datetime],
    created_by: int,
) -> bool:
    """Create or replace traffic discount for user. Returns True on success."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        # Column is TIMESTAMP (naive) — strip tzinfo if present
        naive_expires = expires_at.replace(tzinfo=None) if expires_at and expires_at.tzinfo else expires_at
        await conn.execute(
            """INSERT INTO user_traffic_discounts
                   (telegram_id, discount_percent, expires_at, created_by)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (telegram_id) DO UPDATE
                   SET discount_percent = $2, expires_at = $3, created_by = $4, created_at = NOW()""",
            telegram_id, discount_percent, naive_expires, created_by,
        )
        return True


async def delete_user_traffic_discount(telegram_id: int) -> bool:
    """Remove traffic discount for user."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_traffic_discounts WHERE telegram_id = $1",
            telegram_id,
        )
        return result == "DELETE 1"
