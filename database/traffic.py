"""
Database operations for Remnawave traffic integration.

- remnawave_uuid CRUD on subscriptions table
- traffic notification flags on users table
- traffic_purchases table
- user_traffic_discounts table (promo discounts on traffic packs)
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

import asyncpg

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
            "SELECT remnawave_uuid FROM subscriptions WHERE telegram_id = $1",
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
            "UPDATE subscriptions SET remnawave_uuid = $1 WHERE telegram_id = $2",
            uuid, telegram_id,
        )


async def set_remnawave_id(telegram_id: int, numeric_id: int) -> None:
    """Кеш numeric id панели 3.x (миграция 078) для bypass entity."""
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_id = $1 "
            "WHERE telegram_id = $2",
            int(numeric_id), telegram_id,
        )


async def get_remnawave_id(telegram_id: int) -> Optional[int]:
    """Return cached numeric id from panel 3.x, or None."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT remnawave_id FROM subscriptions "
            "WHERE telegram_id = $1",
            telegram_id,
        )
        return int(val) if val is not None else None


async def set_remnawave_premium_id(telegram_id: int, numeric_id: int) -> None:
    """Кеш numeric id для premium entity (3.x)."""
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET remnawave_premium_id = $1 "
            "WHERE telegram_id = $2",
            int(numeric_id), telegram_id,
        )


async def get_remnawave_premium_id(telegram_id: int) -> Optional[int]:
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT remnawave_premium_id FROM subscriptions "
            "WHERE telegram_id = $1",
            telegram_id,
        )
        return int(val) if val is not None else None


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
            "WHERE telegram_id = $1",
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
                "WHERE telegram_id = $2",
                uuid, telegram_id,
            )
        else:
            await conn.execute(
                "UPDATE subscriptions SET remnawave_premium_uuid = $1 "
                "WHERE telegram_id = $2",
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
                "WHERE telegram_id = $4",
                uuid, sub_url, short_uuid, telegram_id,
            )
        else:
            await conn.execute(
                "UPDATE subscriptions "
                "SET remnawave_premium_uuid = $1, "
                "    remnawave_premium_sub_url = $2, "
                "    remnawave_premium_short_uuid = $3 "
                "WHERE telegram_id = $4",
                uuid, sub_url, short_uuid, telegram_id,
            )


async def set_remnawave_bypass_cache(
    telegram_id: int,
    uuid: Optional[str],
    sub_url: Optional[str],
    short_uuid: Optional[str],
) -> None:
    """Persist (uuid, subscription_url, short_uuid) for the bypass entity.

    Symmetric helper to set_remnawave_premium_uuid_and_url — keeps the
    three bypass columns (remnawave_uuid, remnawave_bypass_sub_url,
    remnawave_bypass_short_uuid) in sync from a single UPDATE so the
    UI never has to round-trip to the panel just to learn the URL.
    """
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions "
            "SET remnawave_uuid = COALESCE($1, remnawave_uuid), "
            "    remnawave_bypass_sub_url = COALESCE($2, remnawave_bypass_sub_url), "
            "    remnawave_bypass_short_uuid = COALESCE($3, remnawave_bypass_short_uuid) "
            "WHERE telegram_id = $4",
            uuid, sub_url, short_uuid, telegram_id,
        )


async def get_remnawave_bypass_cache(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Return (uuid, sub_url, short_uuid) for the bypass entity or None."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT remnawave_uuid, remnawave_bypass_sub_url, remnawave_bypass_short_uuid "
            "FROM subscriptions WHERE telegram_id = $1",
            telegram_id,
        )
        return dict(row) if row else None


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
            "WHERE telegram_id = $2",
            sub_url, telegram_id,
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
        # GB arrived: the thresholds below the new amount apply again (the next
        # check records the new baseline). Separate statement: a missing column
        # (migration 084 not applied yet) must not break the grant that called us.
        try:
            await conn.execute(
                "UPDATE users SET traffic_notice_floor_bytes = NULL WHERE telegram_id = $1", telegram_id,
            )
        except asyncpg.UndefinedColumnError:
            pass


# ── Bypass traffic notices (owner 2026-09-14, migration 084) ───────────

# Background check = a SUBSET of the old selection (active row with a panel
# pointer): a user already told «трафик закончился» (floor 0) is skipped until
# GB arrive — a grant resets the floor (reset_traffic_notification_flags), a
# bought pack after that message shows in traffic_purchases.
_WATCH_SQL = """
    SELECT s.telegram_id, s.remnawave_uuid, s.remnawave_id, s.subscription_type,
           COALESCE(s.is_bypass_only, FALSE) AS is_bypass_only, s.source, s.expires_at,
           u.traffic_notice_floor_bytes, u.traffic_notice_last_at,
           COALESCE(u.traffic_notified_0, FALSE) AS legacy_zero_told
    FROM subscriptions s
    JOIN users u ON u.telegram_id = s.telegram_id
    WHERE s.status = 'active'
      AND s.remnawave_uuid IS NOT NULL
      AND s.remnawave_uuid != ''
      AND (u.traffic_notice_floor_bytes IS DISTINCT FROM 0
           OR EXISTS (SELECT 1 FROM traffic_purchases tp
                      WHERE tp.telegram_id = s.telegram_id
                        AND tp.created_at > (u.traffic_notice_last_at AT TIME ZONE 'UTC')))
"""


async def get_traffic_watch_users() -> List[Dict[str, Any]]:
    """Rows the background traffic check polls (see _WATCH_SQL). [] while
    migration 084 is missing (the pass is skipped, nothing is guessed)."""
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(_WATCH_SQL)
        except asyncpg.UndefinedColumnError as e:
            logger.warning("TRAFFIC_WATCH_SCHEMA_OUTDATED: %s — pass skipped", e)
            return []
    out = []
    for r in rows:
        d = dict(r)
        d["traffic_notice_last_at"] = _core._from_db_utc(d["traffic_notice_last_at"]) if d["traffic_notice_last_at"] else None
        d["expires_at"] = _core._from_db_utc(d["expires_at"]) if d["expires_at"] else None
        out.append(d)
    return out


async def get_traffic_notice_state(telegram_id: int) -> Optional[Dict[str, Any]]:
    """{floor, last_at, legacy_zero_told} of one user, or None (no row / schema)."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """SELECT traffic_notice_floor_bytes, traffic_notice_last_at,
                          COALESCE(traffic_notified_0, FALSE) AS legacy_zero_told
                   FROM users WHERE telegram_id = $1""",
                telegram_id,
            )
        except asyncpg.UndefinedColumnError:
            return None
    if row is None:
        return None
    last = row["traffic_notice_last_at"]
    return {"floor": row["traffic_notice_floor_bytes"],
            "last_at": _core._from_db_utc(last) if last else None,
            "legacy_zero_told": bool(row["legacy_zero_told"])}


async def claim_traffic_notice_state(telegram_id: int, old_floor: Optional[int], old_last_at,
                                     new_floor: Optional[int], new_last_at) -> bool:
    """Compare-and-set of the notice state: True only for the caller whose view
    of the state was current — the worker and a screen never send one notice twice."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    to_db = lambda dt: _core._to_db_utc(dt) if dt is not None else None  # noqa: E731
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE users
               SET traffic_notice_floor_bytes = $4::bigint, traffic_notice_last_at = $5::timestamp
               WHERE telegram_id = $1
                 AND traffic_notice_floor_bytes IS NOT DISTINCT FROM $2::bigint
                 AND traffic_notice_last_at IS NOT DISTINCT FROM $3::timestamp""",
            telegram_id, old_floor, to_db(old_last_at), new_floor, to_db(new_last_at),
        )
    return str(result).endswith(" 1")


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
    """Users with active subscription AND remnawave_uuid set.

    Возвращает также remnawave_id (numeric, 3.x) — traffic_monitor
    предпочитает id, чтобы избежать UUID→id resolve на каждый check
    (5min × 10k юзеров = 2k stream-запросов в панель, лишний overhead).
    """
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.telegram_id, s.remnawave_uuid, s.remnawave_id,
                      s.subscription_type
               FROM subscriptions s
               WHERE s.status = 'active'
                 AND s.remnawave_uuid IS NOT NULL
                 AND s.remnawave_uuid != ''""",
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
