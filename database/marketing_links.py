"""
Marketing links: stats_links (аттрибуция + воронка) и promo_links (награды).

Модуль полностью независим от других database/* модулей, использует
только database.core.get_pool. См. migrations/065_stats_promo_links.sql
для схемы. slug'и генерируем локально — 6 alnum символов, collision <1/10^9.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)

_SLUG_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # без 0/1/l/o/i для чит-абельности
_SLUG_LENGTH = 6

VALID_PROMO_REWARD_TYPES = {
    "subscription_days",
    "tariff_discount",
    "bypass_discount",
    "bypass_gb",
}

VALID_SUB_DAYS = {3, 7, 14, 30, 90, 180, 365}
VALID_DISCOUNT_PCTS = {10, 15, 20, 25, 30, 35, 40, 45, 50}


def _gen_slug() -> str:
    """6-символьный alnum-slug. Не крипто-secret, но не легко угадываемый."""
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LENGTH))


# ─────────────────────────────────────────────────────────────────────
# STATS LINKS
# ─────────────────────────────────────────────────────────────────────

async def create_stats_link(name: str, created_by: Optional[int] = None) -> Dict[str, Any]:
    """Создать новую stat-ссылку. Slug генерируется с повтором при коллизии.
    Возвращает полную запись."""
    if not _core.DB_READY:
        raise RuntimeError("DB not ready")
    pool = await get_pool()
    if pool is None:
        raise RuntimeError("DB pool unavailable")
    async with pool.acquire() as conn:
        for _ in range(5):
            slug = _gen_slug()
            row = await conn.fetchrow(
                """INSERT INTO stats_links (slug, name, created_by)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (slug) DO NOTHING
                   RETURNING *""",
                slug, name, created_by,
            )
            if row:
                return dict(row)
        raise RuntimeError("Failed to allocate unique slug after retries")


async def list_stats_links(include_inactive: bool = True) -> List[Dict[str, Any]]:
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM stats_links
               WHERE $1 OR is_active
               ORDER BY id DESC""",
            include_inactive,
        )
        return [dict(r) for r in rows]


async def get_stats_link_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM stats_links WHERE slug = $1", slug,
        )
        return dict(row) if row else None


async def get_stats_link(link_id: int) -> Optional[Dict[str, Any]]:
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM stats_links WHERE id = $1", link_id,
        )
        return dict(row) if row else None


async def set_stats_link_active(link_id: int, active: bool) -> bool:
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        if active:
            res = await conn.execute(
                """UPDATE stats_links
                   SET is_active = TRUE,
                       reactivated_at = NOW()
                   WHERE id = $1""",
                link_id,
            )
        else:
            res = await conn.execute(
                """UPDATE stats_links
                   SET is_active = FALSE,
                       deactivated_at = NOW()
                   WHERE id = $1""",
                link_id,
            )
        return res.startswith("UPDATE ") and res != "UPDATE 0"


async def delete_stats_link(link_id: int) -> bool:
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM stats_links WHERE id = $1", link_id,
        )
        return res == "DELETE 1"


async def record_stats_link_click(
    link_id: int,
    telegram_id: int,
    is_new_user: bool,
) -> None:
    """Записать клик по stat-ссылке. is_first_click — впервые ли этот
    юзер приходит по этой конкретной ссылке. Если юзер новый, ставим
    also acquired_via_stat_link_id (attribution)."""
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        prev = await conn.fetchval(
            """SELECT 1 FROM stats_link_clicks
               WHERE link_id = $1 AND telegram_id = $2 LIMIT 1""",
            link_id, telegram_id,
        )
        is_first = prev is None
        await conn.execute(
            """INSERT INTO stats_link_clicks
                   (link_id, telegram_id, is_first_click, is_new_user)
               VALUES ($1, $2, $3, $4)""",
            link_id, telegram_id, is_first, is_new_user,
        )
        # Attribution — только для новых юзеров и только если ещё не задана.
        # У существующих юзеров источник уже установлен исторически,
        # переписывать нельзя.
        if is_new_user:
            await conn.execute(
                """UPDATE users
                   SET acquired_via_stat_link_id = $1
                   WHERE telegram_id = $2
                     AND acquired_via_stat_link_id IS NULL""",
                link_id, telegram_id,
            )


async def get_stats_link_summary(link_id: int) -> Optional[Dict[str, Any]]:
    """Полная сводка по одной ссылке:
      total_clicks / unique_visitors / new_users / attributed_users /
      trials_activated / paid_users / total_revenue_kopecks
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        link = await conn.fetchrow(
            "SELECT * FROM stats_links WHERE id = $1", link_id,
        )
        if not link:
            return None

        total_clicks = await conn.fetchval(
            "SELECT COUNT(*) FROM stats_link_clicks WHERE link_id = $1",
            link_id,
        )
        unique_visitors = await conn.fetchval(
            "SELECT COUNT(DISTINCT telegram_id) FROM stats_link_clicks WHERE link_id = $1",
            link_id,
        )
        new_users = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id) FROM stats_link_clicks
               WHERE link_id = $1 AND is_new_user""",
            link_id,
        )

        # Attribution-based метрики.
        attributed = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE acquired_via_stat_link_id = $1",
            link_id,
        )
        trials = await conn.fetchval(
            """SELECT COUNT(*) FROM users
               WHERE acquired_via_stat_link_id = $1
                 AND trial_used_at IS NOT NULL""",
            link_id,
        )
        paid_row = await conn.fetchrow(
            """SELECT COUNT(DISTINCT u.telegram_id) AS n,
                      COALESCE(SUM(p.amount_kopecks), 0)::BIGINT AS revenue
               FROM users u
               JOIN pending_purchases p ON p.telegram_id = u.telegram_id
               WHERE u.acquired_via_stat_link_id = $1
                 AND p.status = 'paid'""",
            link_id,
        )
        paid_users = int(paid_row["n"] or 0) if paid_row else 0
        revenue_kop = int(paid_row["revenue"] or 0) if paid_row else 0

        return {
            **dict(link),
            "total_clicks": int(total_clicks or 0),
            "unique_visitors": int(unique_visitors or 0),
            "new_users": int(new_users or 0),
            "attributed_users": int(attributed or 0),
            "trials_activated": int(trials or 0),
            "paid_users": paid_users,
            "total_revenue_rubles": revenue_kop / 100.0,
        }


# ─────────────────────────────────────────────────────────────────────
# PROMO LINKS
# ─────────────────────────────────────────────────────────────────────

async def create_promo_link(
    name: str,
    reward_type: str,
    reward_value: int,
    max_uses_total: Optional[int] = None,
    max_uses_per_user: int = 1,
    reward_meta: Optional[Dict[str, Any]] = None,
    expires_at: Optional[datetime] = None,
    created_by: Optional[int] = None,
) -> Dict[str, Any]:
    """Создать промо-ссылку. Валидация типа/значения — на слое выше."""
    if reward_type not in VALID_PROMO_REWARD_TYPES:
        raise ValueError(f"Invalid reward_type: {reward_type}")
    if reward_value <= 0:
        raise ValueError("reward_value must be positive")
    if not _core.DB_READY:
        raise RuntimeError("DB not ready")
    pool = await get_pool()
    if pool is None:
        raise RuntimeError("DB pool unavailable")
    import json
    meta_json = json.dumps(reward_meta or {})
    async with pool.acquire() as conn:
        for _ in range(5):
            slug = _gen_slug()
            row = await conn.fetchrow(
                """INSERT INTO promo_links
                       (slug, name, reward_type, reward_value, reward_meta,
                        max_uses_total, max_uses_per_user, expires_at, created_by)
                   VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
                   ON CONFLICT (slug) DO NOTHING
                   RETURNING *""",
                slug, name, reward_type, reward_value, meta_json,
                max_uses_total, max_uses_per_user, expires_at, created_by,
            )
            if row:
                return dict(row)
        raise RuntimeError("Failed to allocate unique slug after retries")


async def list_promo_links(include_inactive: bool = True) -> List[Dict[str, Any]]:
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM promo_links
               WHERE $1 OR is_active
               ORDER BY id DESC""",
            include_inactive,
        )
        return [dict(r) for r in rows]


async def get_promo_link(link_id: int) -> Optional[Dict[str, Any]]:
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM promo_links WHERE id = $1", link_id,
        )
        return dict(row) if row else None


async def get_promo_link_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM promo_links WHERE slug = $1", slug,
        )
        return dict(row) if row else None


async def set_promo_link_active(link_id: int, active: bool) -> bool:
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        if active:
            res = await conn.execute(
                """UPDATE promo_links
                   SET is_active = TRUE,
                       reactivated_at = NOW()
                   WHERE id = $1""",
                link_id,
            )
        else:
            res = await conn.execute(
                """UPDATE promo_links
                   SET is_active = FALSE,
                       deactivated_at = NOW()
                   WHERE id = $1""",
                link_id,
            )
        return res.startswith("UPDATE ") and res != "UPDATE 0"


async def delete_promo_link(link_id: int) -> bool:
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM promo_links WHERE id = $1", link_id,
        )
        return res == "DELETE 1"


async def try_redeem_promo_link(
    link_id: int,
    telegram_id: int,
) -> Dict[str, Any]:
    """Атомарно пробуем зарезервировать редемпцию.

    Возвращает dict со статусом:
      {"ok": True,  "reward_type": ..., "reward_value": ..., "reward_meta": ...}
      {"ok": False, "reason": <str>}

    Возможные reason'ы:
      not_found  — ссылки нет
      inactive   — is_active=FALSE
      expired    — expires_at ≤ NOW
      exhausted  — used_count ≥ max_uses_total
      already_redeemed_by_user — юзер уже использовал (лимит per-user)
    """
    if not _core.DB_READY:
        return {"ok": False, "reason": "db_not_ready"}
    pool = await get_pool()
    if pool is None:
        return {"ok": False, "reason": "db_not_ready"}
    async with pool.acquire() as conn:
        async with conn.transaction():
            link = await conn.fetchrow(
                "SELECT * FROM promo_links WHERE id = $1 FOR UPDATE",
                link_id,
            )
            if not link:
                return {"ok": False, "reason": "not_found"}
            if not link["is_active"]:
                return {"ok": False, "reason": "inactive"}
            expires_at = link["expires_at"]
            if expires_at is not None:
                now = datetime.now(timezone.utc)
                # PostgreSQL уже отдал aware datetime
                if expires_at <= now:
                    return {"ok": False, "reason": "expired"}
            max_total = link["max_uses_total"]
            if max_total is not None and link["used_count"] >= max_total:
                return {"ok": False, "reason": "exhausted"}
            per_user = link["max_uses_per_user"]
            used_by_user = await conn.fetchval(
                """SELECT COUNT(*) FROM promo_link_redemptions
                   WHERE link_id = $1 AND telegram_id = $2""",
                link_id, telegram_id,
            )
            if used_by_user is not None and used_by_user >= per_user:
                return {"ok": False, "reason": "already_redeemed_by_user"}
            # Всё ок — регистрируем редемпцию + инкрементим счётчик.
            await conn.execute(
                """INSERT INTO promo_link_redemptions
                       (link_id, telegram_id, reward_type_snapshot, reward_value_snapshot)
                   VALUES ($1, $2, $3, $4)""",
                link_id, telegram_id, link["reward_type"], link["reward_value"],
            )
            await conn.execute(
                "UPDATE promo_links SET used_count = used_count + 1 WHERE id = $1",
                link_id,
            )
            # Meta — asyncpg отдаёт JSONB как dict или строку в зависимости
            # от версии/конфига. Приводим к dict устойчиво.
            meta = link["reward_meta"]
            if isinstance(meta, str):
                import json
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            elif meta is None:
                meta = {}
            return {
                "ok": True,
                "reward_type": link["reward_type"],
                "reward_value": link["reward_value"],
                "reward_meta": meta,
            }


async def rollback_promo_link_redemption(link_id: int, telegram_id: int) -> bool:
    """Откатить редемпцию: удалить запись + декрементнуть used_count.

    Вызывается когда `try_redeem_promo_link` прошёл (награда
    зарезервирована), но применение награды упало — юзер должен иметь
    возможность попробовать ещё раз, а не терять слот навсегда.

    Идемпотентна: если записи уже нет, просто возвращает False.
    """
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await conn.execute(
                """DELETE FROM promo_link_redemptions
                   WHERE link_id = $1 AND telegram_id = $2""",
                link_id, telegram_id,
            )
            if deleted == "DELETE 0":
                return False
            await conn.execute(
                """UPDATE promo_links
                   SET used_count = GREATEST(0, used_count - 1)
                   WHERE id = $1""",
                link_id,
            )
            return True


async def get_promo_link_summary(link_id: int) -> Optional[Dict[str, Any]]:
    """Сводка по промо-ссылке — количество использований + список
    последних 20 редемпций."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        link = await conn.fetchrow(
            "SELECT * FROM promo_links WHERE id = $1", link_id,
        )
        if not link:
            return None
        recent = await conn.fetch(
            """SELECT telegram_id, created_at
               FROM promo_link_redemptions
               WHERE link_id = $1
               ORDER BY id DESC LIMIT 20""",
            link_id,
        )
        return {
            **dict(link),
            "recent_redemptions": [dict(r) for r in recent],
        }
