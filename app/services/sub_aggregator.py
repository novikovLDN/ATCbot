"""
Sub-aggregator integration — thin bot-side helper that upserts sub_pairs
rows and asks the aggregator service to invalidate its cache.

Rollout gate:
  - config.SUB_AGGREGATOR_ENABLED=false  → module is a no-op (all methods
    return None / False without touching DB or network). Safe to import
    everywhere unconditionally.
  - config.SUB_AGGREGATOR_ADMIN_ONLY=true → только ADMIN_TELEGRAM_ID
    получает aggregator ссылку. Все остальные юзеры видят обычные две
    ссылки (main + gb) как раньше.
  - После полного тестирования → SUB_AGGREGATOR_ADMIN_ONLY=false и все
    юзеры автоматически получат агрегатор без другого кода.

The sub-aggregator service lives at sub-aggregator/ in this repo and is
deployed separately (Docker). See sub-aggregator/README.md.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Optional

import httpx

import config
import database

logger = logging.getLogger(__name__)

# httpx client shared across invalidate() calls — keep-alive avoids TCP
# handshake per invalidation (dozens per second under load).
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=3.0)
    return _client


def is_enabled_for(telegram_id: int) -> bool:
    """Gate: должен ли этот юзер видеть aggregator-ссылку?

    Возвращает False если:
    - SUB_AGGREGATOR_ENABLED=false (глобальный kill switch), или
    - SUB_AGGREGATOR_URL пуст (не настроен), или
    - SUB_AGGREGATOR_ADMIN_ONLY=true и юзер не админ.
    """
    if not config.SUB_AGGREGATOR_ENABLED:
        return False
    if not config.SUB_AGGREGATOR_URL:
        return False
    if config.SUB_AGGREGATOR_ADMIN_ONLY:
        return int(telegram_id) == int(config.ADMIN_TELEGRAM_ID)
    return True


def build_public_url(token: str) -> str:
    """Публичная ссылка на агрегатор.

    Формат: https://subscription.palantirdns.uk/a/{token}
    - RF-1 nginx reverse-proxies /a/{token} → https://api.atlassecure.ru/a/{token}
    - Бот embedded-endpoint app/api/sub_aggregator_route.py делает merge и отдаёт.
    """
    return f"{config.SUB_AGGREGATOR_URL}/a/{token}"


def _generate_token() -> str:
    """URL-safe opaque token, 32 chars (192 bits entropy). Matches the
    aggregator's TOKEN_RE ([A-Za-z0-9_-]{4,128})."""
    return secrets.token_urlsafe(24).replace('=', '')[:32]


async def _read_sub_urls(telegram_id: int) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Return (main_sub_url, gb_sub_url, main_uuid, gb_uuid) or Nones."""
    pool = await database.get_pool()
    if pool is None:
        return None, None, None, None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                remnawave_premium_sub_url AS main_url,
                remnawave_bypass_sub_url  AS gb_url,
                remnawave_premium_uuid    AS main_uuid,
                remnawave_uuid            AS gb_uuid
            FROM subscriptions
            WHERE telegram_id = $1
            """,
            telegram_id,
        )
    if not row:
        return None, None, None, None
    return (
        row.get("main_url") or None,
        row.get("gb_url") or None,
        row.get("main_uuid") or None,
        row.get("gb_uuid") or None,
    )


async def ensure_pair(telegram_id: int) -> Optional[str]:
    """Upsert a sub_pairs row for the user and return the aggregator URL.

    Returns None if the user isn't gated in, or if the user doesn't yet
    have both premium and bypass upstream URLs cached (e.g. trial-only,
    or bypass hasn't been provisioned yet — no combined URL to give).
    """
    if not is_enabled_for(telegram_id):
        return None

    main_url, gb_url, main_uuid, gb_uuid = await _read_sub_urls(telegram_id)
    if not main_url or not gb_url:
        logger.info(
            "SUB_AGGREGATOR_NOT_READY tg=%s has_main=%s has_gb=%s — "
            "нужны обе апстрим ссылки в subscriptions",
            telegram_id, bool(main_url), bool(gb_url),
        )
        return None

    pool = await database.get_pool()
    if pool is None:
        return None

    async with pool.acquire() as conn:
        # UPSERT по telegram_id (unique index). Возвращаем финальный token —
        # либо существующий, либо новый.
        row = await conn.fetchrow(
            """
            INSERT INTO sub_pairs (
                token, telegram_id, main_sub_url, gb_sub_url,
                main_user_uuid, gb_user_uuid, status, updated_at
            )
            VALUES ($1, $2, $3, $4, $5::uuid, $6::uuid, 'active', now())
            ON CONFLICT (telegram_id) DO UPDATE SET
                main_sub_url   = EXCLUDED.main_sub_url,
                gb_sub_url     = EXCLUDED.gb_sub_url,
                main_user_uuid = EXCLUDED.main_user_uuid,
                gb_user_uuid   = EXCLUDED.gb_user_uuid,
                status         = 'active',
                updated_at     = now()
            RETURNING token
            """,
            _generate_token(), telegram_id, main_url, gb_url,
            main_uuid, gb_uuid,
        )
    token = row["token"] if row else None
    if not token:
        return None
    # После апсёрта URL мог измениться — просим агрегатор сбросить кеш.
    await invalidate(token)
    return build_public_url(token)


async def get_url(telegram_id: int) -> Optional[str]:
    """Return the aggregator URL for the user, if the row already exists.
    Fast read path — doesn't touch upstream URLs; use ensure_pair() to
    create/refresh the row after purchases/renewals."""
    if not is_enabled_for(telegram_id):
        return None
    pool = await database.get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT token, status FROM sub_pairs WHERE telegram_id = $1",
            telegram_id,
        )
    if not row or row.get("status") != "active":
        return None
    return build_public_url(row["token"])


async def revoke(telegram_id: int) -> None:
    """Mark the user's pair revoked — aggregator will serve the stub link.
    Idempotent, no-op if the row doesn't exist."""
    if not config.SUB_AGGREGATOR_ENABLED:
        return
    pool = await database.get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE sub_pairs SET status = 'revoked', updated_at = now()
            WHERE telegram_id = $1
            RETURNING token
            """,
            telegram_id,
        )
    if row and row.get("token"):
        await invalidate(row["token"])


async def invalidate(token: str) -> bool:
    """Мгновенный in-process сброс кеша агрегатора для этого токена.

    Раньше слал POST на loopback URL (WEBHOOK_URL → сам себе) — это шло
    через public internet, TLS handshake, и могло фейлить (Railway
    proxy retries, cold start). Теперь — прямой вызов clear_cache()
    в том же процессе: 0ms, без сети.

    Следствие: после /aggregator, покупки, продления кеш чистится
    МГНОВЕННО. Следующий GET клиента дёргает fresh данные из панели.
    Cache TTL 15 сек → даже если invalidate почему-то не позвали,
    stale-окно очень маленькое.
    """
    if not token:
        return True
    try:
        from app.api.sub_aggregator_route import clear_cache
        clear_cache(token)
        return True
    except Exception as e:
        logger.warning("SUB_AGG_INVALIDATE_FAIL token=%s… err=%s", token[:6], str(e)[:120])
        return False


def invalidate_bg(telegram_id: int) -> None:
    """Fire-and-forget invalidate by telegram_id. Never blocks the caller.
    Useful in hot paths (add_bypass_traffic, renew_premium) — we don't want
    a 3-second timeout to add latency to a successful purchase.
    """
    if not config.SUB_AGGREGATOR_ENABLED:
        return
    try:
        asyncio.create_task(_invalidate_by_tg(telegram_id))
    except RuntimeError:
        # No running loop (e.g. called from sync context) — skip silently.
        pass


async def _invalidate_by_tg(telegram_id: int) -> None:
    pool = await database.get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT token FROM sub_pairs WHERE telegram_id = $1 AND status = 'active'",
            telegram_id,
        )
    if row and row.get("token"):
        await invalidate(row["token"])


async def close() -> None:
    """Called on graceful shutdown to close the httpx keep-alive pool."""
    global _client
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        finally:
            _client = None


__all__ = [
    "is_enabled_for",
    "build_public_url",
    "ensure_pair",
    "get_url",
    "revoke",
    "invalidate",
    "invalidate_bg",
    "close",
]
