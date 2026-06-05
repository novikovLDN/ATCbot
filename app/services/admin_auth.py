"""Admin web-dashboard auth.

Storage:
  - admin_credentials (Postgres) — single row, username + bcrypt hash
  - sessions (Redis)              — random opaque token → admin_telegram_id

Flow:
  1. /admin in bot → ALWAYS valid magic-link (used for bootstrap and
     password recovery). The link is the only way to set or change
     the password; if no creds exist, the dashboard shows a setup
     form. Once creds exist, the dashboard shows a login form
     instead — opening the magic-link in someone else's browser
     gives them the login form too, not the dashboard.
  2. Successful login OR setup → 5-day HttpOnly cookie session.
  3. /admin → "Восстановить пароль" — deletes credentials and all
     sessions; next magic-link click brings the setup form back.

Sessions live in Redis: short, cheap, easy to expire on logout. If
Redis isn't configured we fall back to an in-memory dict (the bot
already runs as a single process today, so this is sufficient).
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Optional

import bcrypt

import config

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 5 * 24 * 60 * 60  # 5 days
COOKIE_NAME = "atlas_admin_session"

_REDIS_KEY_PREFIX = "dashboard:session:"

# In-memory fallback (used when REDIS_URL isn't configured). Maps
# session_id → (admin_telegram_id, expires_at_epoch).
_MEM_SESSIONS: dict[str, tuple[int, float]] = {}


# ── Password hashing ─────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    """Return a bcrypt hash. plain → bytes → bcrypt salt + hash."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Credentials (Postgres) ──────────────────────────────────────────


async def get_credentials() -> Optional[dict]:
    """Return the single admin_credentials row or None if not set yet."""
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT username, password_hash, created_at, updated_at "
                "FROM admin_credentials ORDER BY id LIMIT 1"
            )
            return dict(row) if row else None
    except Exception as e:
        logger.warning("get_credentials failed: %s", e)
        return None


async def credentials_exist() -> bool:
    creds = await get_credentials()
    return creds is not None


async def set_credentials(username: str, plain_password: str) -> bool:
    """Atomic upsert — first row wins on insert; subsequent calls
    overwrite that single row."""
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return False
    hashed = hash_password(plain_password)
    try:
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM admin_credentials ORDER BY id LIMIT 1"
            )
            if existing:
                await conn.execute(
                    """UPDATE admin_credentials
                       SET username = $1, password_hash = $2,
                           updated_at = NOW()
                       WHERE id = $3""",
                    username, hashed, existing,
                )
            else:
                await conn.execute(
                    """INSERT INTO admin_credentials
                           (username, password_hash)
                       VALUES ($1, $2)""",
                    username, hashed,
                )
        return True
    except Exception as e:
        logger.exception("set_credentials failed: %s", e)
        return False


async def clear_credentials() -> bool:
    """Delete ALL credential rows (singleton, so it's effectively one)
    plus revoke all active sessions. Called by the bot's reset button."""
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM admin_credentials")
        await purge_all_sessions()
        return True
    except Exception as e:
        logger.exception("clear_credentials failed: %s", e)
        return False


# ── Sessions (Redis / memory fallback) ──────────────────────────────


async def _redis():
    try:
        from app.utils.redis_client import get_client, is_configured
        if not is_configured():
            return None
        return await get_client()
    except Exception:
        return None


async def create_session(admin_telegram_id: int) -> str:
    """Mint a new session token, store it, return the opaque value."""
    token = secrets.token_urlsafe(32)
    r = await _redis()
    if r is not None:
        try:
            await r.set(
                _REDIS_KEY_PREFIX + token,
                str(admin_telegram_id),
                ex=SESSION_TTL_SECONDS,
            )
            return token
        except Exception as e:
            logger.warning("session redis store failed, falling back to memory: %s", e)
    _MEM_SESSIONS[token] = (admin_telegram_id, time.time() + SESSION_TTL_SECONDS)
    return token


async def lookup_session(token: str) -> Optional[int]:
    """Return the admin telegram_id for a valid token, or None."""
    if not token:
        return None
    r = await _redis()
    if r is not None:
        try:
            raw = await r.get(_REDIS_KEY_PREFIX + token)
            if raw is None:
                # Try memory in case the process moved between stores.
                pass
            else:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    return int(raw)
                except Exception:
                    return None
        except Exception as e:
            logger.warning("session redis lookup failed: %s", e)
    # Memory fallback
    entry = _MEM_SESSIONS.get(token)
    if entry is None:
        return None
    admin_id, exp = entry
    if exp < time.time():
        _MEM_SESSIONS.pop(token, None)
        return None
    return admin_id


async def revoke_session(token: str) -> None:
    if not token:
        return
    r = await _redis()
    if r is not None:
        try:
            await r.delete(_REDIS_KEY_PREFIX + token)
        except Exception:
            pass
    _MEM_SESSIONS.pop(token, None)


async def purge_all_sessions() -> None:
    """Best-effort: revoke EVERY active session. Called on password
    reset and on credential change."""
    r = await _redis()
    if r is not None:
        try:
            # SCAN to avoid blocking — typically only a handful of keys.
            cursor = 0
            while True:
                cursor, keys = await r.scan(
                    cursor=cursor, match=_REDIS_KEY_PREFIX + "*", count=100,
                )
                if keys:
                    await r.delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning("session redis purge failed: %s", e)
    _MEM_SESSIONS.clear()


# ── Admin identity guard ────────────────────────────────────────────


def is_admin(telegram_id: int) -> bool:
    return telegram_id == config.ADMIN_TELEGRAM_ID
