"""
Bridge between the bot's (telegram_id, kind) coordinates and Remnawave
3.x numeric `user.id`.

The 2.x → 3.x panel migration replaced the string `uuid` identifier with
a BigInt `id`.  We store the id in the new `subscriptions.remnawave_id`
/ `remnawave_premium_id` columns, but any user provisioned before the
cutover has NULL there.  This module resolves the id on-demand and
caches it back into the DB so subsequent calls are O(1).

Resolution order (per `kind`):
  1. Cache hit — read `remnawave_[premium_]id` from `subscriptions`.
  2. by-short-uuid — Remnawave 3.x preserves `shortUuid` across the jump;
     if we cached one, ask the panel to translate it into an id.
  3. stream by telegramId — GET /api/users/stream?telegramId=X, pick the
     entity whose username matches the (bypass|premium) pattern.
  4. Return None; caller must handle it (usually skip the operation).

All lookups are best-effort — panel timeouts / 404s are logged but never
raised.  On success the id is UPDATE-cached into `subscriptions`.

Kind semantics:
    "bypass"  → column `remnawave_id`, username == str(telegram_id).
    "premium" → column `remnawave_premium_id`,
                username == f"tg_{telegram_id}_premium"
                (see app.services.remnawave_premium.build_premium_username
                 for the customisable pattern).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


_VALID_KINDS = ("bypass", "premium")

# Per-process lock keyed on (telegram_id, kind) so two concurrent callers
# don't both trigger a resolve+cache round-trip for the same user.
_locks: dict[tuple, asyncio.Lock] = {}


def _kind_config(kind: str) -> tuple[str, str, str]:
    """Return (id_col, short_uuid_col, legacy_uuid_col) for the kind."""
    if kind == "premium":
        return ("remnawave_premium_id",
                "remnawave_premium_short_uuid",
                "remnawave_premium_uuid")
    return ("remnawave_id",
            "remnawave_bypass_short_uuid",
            "remnawave_uuid")


def _expected_username(telegram_id: int, kind: str) -> str:
    if kind == "premium":
        try:
            from app.services.remnawave_premium import build_premium_username
            return build_premium_username(telegram_id)
        except Exception:
            return f"tg_{telegram_id}_premium"
    return str(telegram_id)


async def _load_cached_row(telegram_id: int, kind: str) -> Optional[dict]:
    """Read the cache row: (id, short_uuid, legacy_uuid) for kind."""
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return None
        id_col, short_col, legacy_col = _kind_config(kind)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {id_col} AS panel_id, "
                f"       {short_col} AS short_uuid, "
                f"       {legacy_col} AS legacy_uuid "
                f"FROM subscriptions WHERE telegram_id = $1 "
                "ORDER BY (status='active') DESC, expires_at DESC NULLS LAST LIMIT 1",
                telegram_id,
            )
        return dict(row) if row else None
    except Exception as e:
        logger.warning(
            "RMN_ID_RESOLVE_LOAD_CACHE_FAIL: tg=%s kind=%s %s",
            telegram_id, kind, e,
        )
        return None


async def _persist_id(telegram_id: int, kind: str, panel_id: int) -> None:
    """UPDATE the id cache column.  Idempotent, non-throwing."""
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return
        id_col, _, _ = _kind_config(kind)
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE subscriptions SET {id_col} = $1 WHERE telegram_id = $2",
                int(panel_id), telegram_id,
            )
    except Exception as e:
        logger.warning(
            "RMN_ID_RESOLVE_PERSIST_FAIL: tg=%s kind=%s id=%s %s",
            telegram_id, kind, panel_id, e,
        )


def _extract_id(entity: dict) -> Optional[int]:
    """Pull the numeric panel id out of a Remnawave 3.x user entity."""
    if not isinstance(entity, dict):
        return None
    raw = entity.get("id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _resolve_via_short_uuid(short_uuid: str) -> Optional[int]:
    if not short_uuid:
        return None
    try:
        from app.services import remnawave_api
        entity = await remnawave_api.get_user_by_short_uuid(short_uuid)
    except Exception as e:
        logger.warning("RMN_ID_RESOLVE_SHORT_FAIL: short=%s %s", short_uuid[:8], e)
        return None
    return _extract_id(entity or {})


async def _resolve_via_stream(telegram_id: int, kind: str) -> Optional[int]:
    """Fallback: GET /api/users/stream?telegramId=X, pick the entity whose
    username matches our expected (bypass|premium) pattern.  If exactly
    one match exists we take it even without a strict username match."""
    try:
        from app.services import remnawave_api
        users = await remnawave_api.get_users_stream_by_telegram_id(telegram_id, size=10)
    except Exception as e:
        logger.warning("RMN_ID_RESOLVE_STREAM_FAIL: tg=%s kind=%s %s", telegram_id, kind, e)
        return None
    if not users:
        return None
    expected = _expected_username(telegram_id, kind)
    for u in users:
        if not isinstance(u, dict):
            continue
        uname = (u.get("username") or "").strip()
        if uname == expected:
            return _extract_id(u)
    # No exact username match — for bypass kind, pattern is a bare int;
    # for premium the tail is fixed.  If exactly one entity exists we
    # tentatively adopt it; otherwise give up and let the caller create
    # a fresh entity.
    if len(users) == 1:
        return _extract_id(users[0])
    return None


async def get_remnawave_id_for(
    telegram_id: int,
    kind: str = "bypass",
    *,
    force_refresh: bool = False,
) -> Optional[int]:
    """Resolve the numeric Remnawave user id for a (telegram_id, kind)."""
    if kind not in _VALID_KINDS:
        raise ValueError(f"unknown kind={kind!r}, expected one of {_VALID_KINDS}")
    if telegram_id is None:
        return None

    if not force_refresh:
        cached = await _load_cached_row(telegram_id, kind)
        if cached and cached.get("panel_id") is not None:
            try:
                return int(cached["panel_id"])
            except (TypeError, ValueError):
                pass

    # Under a per-user-per-kind lock: prevents duplicate HTTP round-trips
    # from concurrent handlers.  Re-check cache inside the lock.
    key = (int(telegram_id), kind)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = await _load_cached_row(telegram_id, kind)
        if cached and cached.get("panel_id") is not None and not force_refresh:
            try:
                return int(cached["panel_id"])
            except (TypeError, ValueError):
                pass

        short_uuid = (cached or {}).get("short_uuid") or None
        panel_id = await _resolve_via_short_uuid(short_uuid) if short_uuid else None

        if panel_id is None:
            panel_id = await _resolve_via_stream(telegram_id, kind)

        if panel_id is None:
            legacy_uuid = (cached or {}).get("legacy_uuid") or ""
            logger.info(
                "RMN_ID_RESOLVE_MISS: tg=%s kind=%s (no short-uuid or stream match; "
                "legacy uuid=%s)",
                telegram_id, kind, legacy_uuid[:8] if legacy_uuid else "—",
            )
            return None

        await _persist_id(telegram_id, kind, panel_id)
        logger.info(
            "RMN_ID_RESOLVE_HIT: tg=%s kind=%s panel_id=%s via=%s",
            telegram_id, kind, panel_id,
            "short_uuid" if short_uuid else "stream",
        )
        return panel_id


async def resolve_from_stored(
    telegram_id: int,
    stored_ref,
    kind: str = "bypass",
) -> Optional[int]:
    """Best-effort: turn whatever we have in DB into a 3.x numeric id.

    If `stored_ref` is already a numeric id (str or int), just int() it.
    Otherwise (legacy UUID or None) delegate to `get_remnawave_id_for`.
    """
    if stored_ref is not None:
        try:
            val = int(str(stored_ref).strip())
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    return await get_remnawave_id_for(telegram_id, kind)


async def clear_cached_id(telegram_id: int, kind: str = "bypass") -> None:
    """Wipe the cached numeric id for a user (used when the panel entity
    was deleted and we want the next resolve to hit the network again)."""
    if kind not in _VALID_KINDS:
        return
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return
        id_col, _, _ = _kind_config(kind)
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE subscriptions SET {id_col} = NULL WHERE telegram_id = $1",
                telegram_id,
            )
    except Exception as e:
        logger.warning(
            "RMN_ID_RESOLVE_CLEAR_FAIL: tg=%s kind=%s %s",
            telegram_id, kind, e,
        )


__all__ = [
    "get_remnawave_id_for",
    "resolve_from_stored",
    "clear_cached_id",
]
