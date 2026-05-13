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


# ── Remnawave premium UUID (MainServer squad, migration 045) ──────────

async def get_remnawave_premium_uuid(telegram_id: int) -> Optional[str]:
    """Return the Remnawave UUID of the premium (MainServer) entity, if any."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT remnawave_premium_uuid FROM subscriptions "
            "WHERE telegram_id = $1 AND status = 'active'",
            telegram_id,
        )


async def set_remnawave_premium_uuid(
    telegram_id: int,
    uuid: str,
    *,
    mark_migrated: bool = True,
) -> None:
    """Store the premium Remnawave UUID. Also stamps samopis_migrated_at by default."""
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        if mark_migrated:
            await conn.execute(
                "UPDATE subscriptions "
                "SET remnawave_premium_uuid = $1, samopis_migrated_at = NOW() "
                "WHERE telegram_id = $2 AND status = 'active'",
                uuid, telegram_id,
            )
        else:
            await conn.execute(
                "UPDATE subscriptions SET remnawave_premium_uuid = $1 "
                "WHERE telegram_id = $2 AND status = 'active'",
                uuid, telegram_id,
            )


async def set_remnawave_premium_uuid_and_url(
    telegram_id: int,
    uuid: str,
    sub_url: Optional[str],
    *,
    short_uuid: Optional[str] = None,
    mark_migrated: bool = True,
) -> None:
    """Atomically persist (uuid, subscription_url, short_uuid) for the premium entity.

    Used by the migration script so the fallback router never has to call
    Remnawave just to learn the URL — single UPDATE keeps the columns in
    sync.  Any of sub_url / short_uuid may be None when the panel didn't
    return them; callers can patch sub_url later via
    set_remnawave_premium_sub_url().
    """
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        if mark_migrated:
            await conn.execute(
                "UPDATE subscriptions "
                "SET remnawave_premium_uuid = $1, "
                "    remnawave_premium_sub_url = $2, "
                "    remnawave_premium_short_uuid = $3, "
                "    samopis_migrated_at = NOW() "
                "WHERE telegram_id = $4 AND status = 'active'",
                uuid, sub_url, short_uuid, telegram_id,
            )
        else:
            await conn.execute(
                "UPDATE subscriptions "
                "SET remnawave_premium_uuid = $1, "
                "    remnawave_premium_sub_url = $2, "
                "    remnawave_premium_short_uuid = $3 "
                "WHERE telegram_id = $4 AND status = 'active'",
                uuid, sub_url, short_uuid, telegram_id,
            )


async def set_remnawave_premium_sub_url(telegram_id: int, sub_url: str) -> None:
    """Back-fill the cached subscriptionUrl for the premium entity.

    Used by the fallback router on a cache miss (legacy rows migrated
    before column 046 existed).
    """
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_premium_sub_url = $1 "
            "WHERE telegram_id = $2 AND status = 'active'",
            sub_url, telegram_id,
        )


async def clear_remnawave_premium_uuid(telegram_id: int) -> None:
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_premium_uuid = NULL "
            "WHERE telegram_id = $1",
            telegram_id,
        )


async def get_subscription_by_premium_uuid(uuid: str) -> Optional[Dict[str, Any]]:
    """Look up a subscription by its premium Remnawave UUID.

    Used by the subscription-URL fallback endpoint to translate a legacy
    samopis UUID (which the migration may have reused as the panel UUID)
    into a Telegram-id / Remnawave-UUID pair.
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT telegram_id, remnawave_premium_uuid, remnawave_premium_sub_url, "
            "       remnawave_uuid, status, subscription_type, expires_at, samopis_migrated_at "
            "FROM subscriptions WHERE remnawave_premium_uuid = $1",
            uuid,
        )
        return dict(row) if row else None


async def get_subscription_by_samopis_uuid(uuid: str) -> Optional[Dict[str, Any]]:
    """Look up a subscription by its legacy samopis Xray UUID."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT telegram_id, uuid, remnawave_premium_uuid, remnawave_premium_sub_url, "
            "       remnawave_uuid, status, subscription_type, expires_at, samopis_migrated_at "
            "FROM subscriptions WHERE uuid = $1",
            uuid,
        )
        return dict(row) if row else None


async def count_premium_migration_progress() -> Dict[str, int]:
    """Snapshot of where the samopis→Remnawave premium migration stands.

    Returns dict with three counters:
      migrated              — rows that already have remnawave_premium_uuid
                              set (samopis_migrated_at NOT NULL).
      remaining_candidates  — rows still eligible for migration (matches
                              the SQL of list_subscriptions_for_premium_migration).
      total_active_paid     — migrated + remaining (total denominator the
                              admin progress UI shows).
    All counters return 0 if the DB pool isn't ready.
    """
    if not _core.DB_READY:
        return {"migrated": 0, "remaining_candidates": 0, "total_active_paid": 0}
    pool = await get_pool()
    if pool is None:
        return {"migrated": 0, "remaining_candidates": 0, "total_active_paid": 0}

    async with pool.acquire() as conn:
        migrated = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE remnawave_premium_uuid IS NOT NULL "
            "  AND remnawave_premium_uuid != '' "
            "  AND samopis_migrated_at IS NOT NULL"
        )
        remaining = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE status = 'active' "
            "  AND uuid IS NOT NULL "
            "  AND uuid != '' "
            "  AND expires_at > NOW() "
            "  AND subscription_type IS DISTINCT FROM 'trial' "
            "  AND (remnawave_premium_uuid IS NULL OR remnawave_premium_uuid = '')"
        )
    return {
        "migrated": int(migrated or 0),
        "remaining_candidates": int(remaining or 0),
        "total_active_paid": int((migrated or 0) + (remaining or 0)),
    }


async def list_subscriptions_for_premium_migration(
    *,
    limit: Optional[int] = None,
    telegram_id: Optional[int] = None,
    include_already_migrated: bool = False,
) -> List[Dict[str, Any]]:
    """Return rows that the samopis→Remnawave-premium migration should process.

    A candidate has:
      - status = 'active'
      - uuid (samopis Xray UUID) IS NOT NULL AND != ''
      - expires_at > NOW()  (unexpired)
      - subscription_type NOT IN ('trial')  (paid users only)
    Unless `include_already_migrated` is True, rows where
    `remnawave_premium_uuid` is already set are excluded so the script
    can be safely resumed.
    """
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []

    clauses = [
        "status = 'active'",
        "uuid IS NOT NULL",
        "uuid != ''",
        "expires_at > NOW()",
        "subscription_type IS DISTINCT FROM 'trial'",
    ]
    args: list = []
    if not include_already_migrated:
        clauses.append("(remnawave_premium_uuid IS NULL OR remnawave_premium_uuid = '')")
    if telegram_id is not None:
        args.append(telegram_id)
        clauses.append(f"telegram_id = ${len(args)}")

    query = (
        "SELECT telegram_id, uuid, remnawave_uuid, remnawave_premium_uuid, "
        "       subscription_type, expires_at, status, samopis_migrated_at "
        "FROM subscriptions WHERE " + " AND ".join(clauses) +
        " ORDER BY telegram_id"
    )
    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]


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
