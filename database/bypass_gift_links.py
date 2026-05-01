"""
Database operations for admin-created bypass GB gift links.

Schema (migration 043):
- bypass_gift_links: link definitions (code, gb_amount, validity_days, max_uses, expires_at)
- bypass_gift_redemptions: per-(link, user) redemption records, UNIQUE(link_id, telegram_id)

Each user can redeem each link only once. The atomic SQL guards in
`redeem_bypass_gift_link` enforce both the per-user uniqueness and the
total max_uses cap without race conditions.
"""
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc

logger = logging.getLogger(__name__)


# Code charset: uppercase letters + digits, excluding ambiguous chars (0/O, 1/I/L)
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 10


def generate_bypass_gift_code(length: int = _CODE_LENGTH) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def _row_to_link(row) -> Dict[str, Any]:
    """Normalize timestamps to aware UTC for callers."""
    if row is None:
        return None
    d = dict(row)
    for key in ("created_at", "expires_at", "deleted_at"):
        if key in d and d[key] is not None:
            d[key] = _from_db_utc(d[key])
    return d


# ── Create / read ──────────────────────────────────────────────────────

async def create_bypass_gift_link(
    created_by: int,
    gb_amount: int,
    validity_days: int,
    max_uses: int,
) -> Optional[Dict[str, Any]]:
    """Create a new gift link. Returns the row dict, or None on failure.

    Generates a unique code by retry. Computes expires_at = now + validity_days.
    """
    if not _core.DB_READY:
        logger.warning("create_bypass_gift_link: DB not ready")
        return None
    if gb_amount <= 0 or validity_days <= 0 or max_uses <= 0:
        return None

    pool = await get_pool()
    if pool is None:
        return None

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=validity_days)

    # Retry on rare code collision.
    for attempt in range(8):
        code = generate_bypass_gift_code()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO bypass_gift_links
                            (code, created_by, gb_amount, validity_days, max_uses, expires_at)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       RETURNING *""",
                    code, created_by, gb_amount, validity_days, max_uses,
                    _to_db_utc(expires_at),
                )
                return _row_to_link(row)
        except Exception as e:
            # Unique violation on `code` — try a fresh code
            if "bypass_gift_links_code_key" in str(e) and attempt < 7:
                continue
            logger.error("create_bypass_gift_link failed: %s", e)
            return None
    return None


async def get_bypass_gift_link_by_code(code: str) -> Optional[Dict[str, Any]]:
    if not _core.DB_READY or not code:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM bypass_gift_links WHERE code = $1",
            code,
        )
        return _row_to_link(row)


async def get_bypass_gift_link_by_id(link_id: int) -> Optional[Dict[str, Any]]:
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM bypass_gift_links WHERE id = $1",
            link_id,
        )
        return _row_to_link(row)


async def list_bypass_gift_links(
    created_by: Optional[int] = None,
    include_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List links with current redemption count for each."""
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    where = ["TRUE"]
    params: List[Any] = []
    if created_by is not None:
        params.append(created_by)
        where.append(f"l.created_by = ${len(params)}")
    if not include_deleted:
        where.append("l.deleted_at IS NULL")
    params.append(limit)
    limit_idx = len(params)
    params.append(offset)
    offset_idx = len(params)
    sql = f"""
        SELECT l.*,
               COALESCE(r.redemption_count, 0) AS redemption_count
        FROM bypass_gift_links l
        LEFT JOIN (
            SELECT link_id, COUNT(*) AS redemption_count
            FROM bypass_gift_redemptions
            GROUP BY link_id
        ) r ON r.link_id = l.id
        WHERE {' AND '.join(where)}
        ORDER BY l.created_at DESC
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
        return [_row_to_link(r) for r in rows]


async def get_bypass_gift_link_redemptions(
    link_id: int,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, link_id, telegram_id, gb_granted, redeemed_at
               FROM bypass_gift_redemptions
               WHERE link_id = $1
               ORDER BY redeemed_at DESC
               LIMIT $2""",
            link_id, limit,
        )
        result = []
        for r in rows:
            d = dict(r)
            if d.get("redeemed_at"):
                d["redeemed_at"] = _from_db_utc(d["redeemed_at"])
            result.append(d)
        return result


async def soft_delete_bypass_gift_link(link_id: int) -> bool:
    """Mark link as deleted (soft delete). Past redemptions stay."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        now = _to_db_utc(datetime.now(timezone.utc))
        result = await conn.execute(
            """UPDATE bypass_gift_links
               SET deleted_at = $2
               WHERE id = $1 AND deleted_at IS NULL""",
            link_id, now,
        )
        return result == "UPDATE 1"


async def count_bypass_gift_link_redemptions(link_id: int) -> int:
    """Total redemption count for a single link (cheap COUNT(*))."""
    if not _core.DB_READY:
        return 0
    pool = await get_pool()
    if pool is None:
        return 0
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM bypass_gift_redemptions WHERE link_id = $1",
            link_id,
        )
        return int(n or 0)


async def rollback_bypass_gift_redemption(link_id: int, telegram_id: int) -> bool:
    """Delete a (link_id, telegram_id) redemption record.

    Used when the post-redemption side-effect (Remnawave grant) failed,
    so the user can retry the same link without being blocked by the
    UNIQUE(link_id, telegram_id) idempotency guard.
    """
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM bypass_gift_redemptions WHERE link_id = $1 AND telegram_id = $2",
            link_id, telegram_id,
        )
        return result == "DELETE 1"


# ── Redeem ─────────────────────────────────────────────────────────────

async def redeem_bypass_gift_link(code: str, telegram_id: int) -> Dict[str, Any]:
    """Atomically redeem a gift link for a user.

    Returns a dict:
        {
            "status": "success" | "already_redeemed" | "expired"
                       | "max_uses_reached" | "not_found" | "deleted",
            "link": <link dict or None>,
            "gb_amount": <int or None>,
            "redemption_count": <int after this attempt>,
        }

    Atomicity strategy:
      1. Lock the link row with `FOR UPDATE`.
      2. Check expires_at and deleted_at.
      3. Try `INSERT ... ON CONFLICT DO NOTHING` into redemptions.
         - If 0 rows inserted → user already redeemed → return already_redeemed.
      4. After insert, count total redemptions; if it exceeds max_uses,
         roll back the insert (delete that row) and return max_uses_reached.
         The final SELECT count happens inside the same transaction so a
         concurrent redeemer is serialized via the FOR UPDATE lock.
    """
    if not _core.DB_READY or not code:
        return {"status": "not_found", "link": None, "gb_amount": None, "redemption_count": 0}
    pool = await get_pool()
    if pool is None:
        return {"status": "not_found", "link": None, "gb_amount": None, "redemption_count": 0}

    now_db = _to_db_utc(datetime.now(timezone.utc))

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Lock the link row to serialize concurrent redeemers.
            link_row = await conn.fetchrow(
                "SELECT * FROM bypass_gift_links WHERE code = $1 FOR UPDATE",
                code,
            )
            if link_row is None:
                return {"status": "not_found", "link": None, "gb_amount": None, "redemption_count": 0}

            link = _row_to_link(link_row)

            if link.get("deleted_at") is not None:
                return {"status": "deleted", "link": link, "gb_amount": None, "redemption_count": 0}

            # Expired?
            if link_row["expires_at"] is not None and link_row["expires_at"] <= now_db:
                return {"status": "expired", "link": link, "gb_amount": None, "redemption_count": 0}

            link_id = link_row["id"]
            gb_amount = link_row["gb_amount"]
            max_uses = link_row["max_uses"]

            # Per-user uniqueness: ON CONFLICT DO NOTHING.
            inserted = await conn.fetchrow(
                """INSERT INTO bypass_gift_redemptions
                        (link_id, telegram_id, gb_granted)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (link_id, telegram_id) DO NOTHING
                   RETURNING id""",
                link_id, telegram_id, gb_amount,
            )

            if inserted is None:
                # User already redeemed this link.
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM bypass_gift_redemptions WHERE link_id = $1",
                    link_id,
                )
                return {
                    "status": "already_redeemed",
                    "link": link,
                    "gb_amount": None,
                    "redemption_count": int(count or 0),
                }

            # Check the new total against max_uses.
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM bypass_gift_redemptions WHERE link_id = $1",
                link_id,
            )
            count = int(count or 0)
            if count > max_uses:
                # Roll back this insert — link is at capacity.
                await conn.execute(
                    "DELETE FROM bypass_gift_redemptions WHERE id = $1",
                    inserted["id"],
                )
                return {
                    "status": "max_uses_reached",
                    "link": link,
                    "gb_amount": None,
                    "redemption_count": count - 1,
                }

            return {
                "status": "success",
                "link": link,
                "gb_amount": int(gb_amount),
                "redemption_count": count,
            }


# ── Stats ──────────────────────────────────────────────────────────────

async def get_bypass_gift_links_summary(created_by: Optional[int] = None) -> Dict[str, Any]:
    """Aggregate stats across all links."""
    if not _core.DB_READY:
        return {"total_links": 0, "active_links": 0, "total_redemptions": 0, "total_gb_granted": 0}
    pool = await get_pool()
    if pool is None:
        return {"total_links": 0, "active_links": 0, "total_redemptions": 0, "total_gb_granted": 0}
    where = ["TRUE"]
    params: List[Any] = []
    if created_by is not None:
        params.append(created_by)
        where.append(f"created_by = ${len(params)}")
    where_sql = " AND ".join(where)
    async with pool.acquire() as conn:
        total_links = await conn.fetchval(
            f"SELECT COUNT(*) FROM bypass_gift_links WHERE {where_sql}",
            *params,
        )
        active_links = await conn.fetchval(
            f"""SELECT COUNT(*) FROM bypass_gift_links
                WHERE {where_sql} AND deleted_at IS NULL
                AND expires_at > (NOW() AT TIME ZONE 'UTC')""",
            *params,
        )
        if created_by is not None:
            total_redemptions = await conn.fetchval(
                """SELECT COUNT(*) FROM bypass_gift_redemptions r
                   JOIN bypass_gift_links l ON l.id = r.link_id
                   WHERE l.created_by = $1""",
                created_by,
            )
            total_gb = await conn.fetchval(
                """SELECT COALESCE(SUM(r.gb_granted), 0) FROM bypass_gift_redemptions r
                   JOIN bypass_gift_links l ON l.id = r.link_id
                   WHERE l.created_by = $1""",
                created_by,
            )
        else:
            total_redemptions = await conn.fetchval(
                "SELECT COUNT(*) FROM bypass_gift_redemptions"
            )
            total_gb = await conn.fetchval(
                "SELECT COALESCE(SUM(gb_granted), 0) FROM bypass_gift_redemptions"
            )
        return {
            "total_links": int(total_links or 0),
            "active_links": int(active_links or 0),
            "total_redemptions": int(total_redemptions or 0),
            "total_gb_granted": int(total_gb or 0),
        }
