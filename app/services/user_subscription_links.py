"""
Helpers that return the right subscription URL for a Telegram user
*depending on whether the bot has cut over to the Remnawave-only flow*.

Why this exists: the legacy `vpn_utils.build_sub_url` is synchronous
and always returns the samopis-style URL
`https://atlassecure.ru/api/sub/{token}?id={id}`.  After the Task 2
cut-over (config.PURCHASE_FLOW_REMNAWAVE=true) we want the bot's
"Подключиться" buttons / copy-key blocks to surface the Remnawave-
issued URL instead — but the legacy helper has too many sync call
sites to flip in one go.

So we add async wrappers that:
  1. Read the cached `remnawave_premium_sub_url` /
     `remnawave_bypass_sub_url` from `subscriptions` (populated by
     Task 1 migration + Task 2 purchase flow).
  2. Fall back to `build_sub_url(telegram_id)` (legacy URL → handled
     by `subscription_proxy` if enabled, else just opens the samopis
     endpoint as before).

These helpers never raise — they always return *some* URL the bot can
render.  When PURCHASE_FLOW_REMNAWAVE is OFF or the user has no
Remnawave entity yet, behaviour is identical to the legacy helper.
"""
from __future__ import annotations

import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)


def _legacy_sub_url(telegram_id: int) -> str:
    """Fallback to the existing samopis-style URL. Sync so it always works."""
    from vpn_utils import build_sub_url
    return build_sub_url(telegram_id)


async def get_user_premium_url(telegram_id: int) -> Optional[str]:
    """Return the Remnawave premium subscription URL for the user, or None.

    Resolution order:
      1. cached `remnawave_premium_sub_url` column (status='active' row).
      2. cached `remnawave_premium_sub_url` column ignoring status filter
         (rare: status was != 'active' at the moment the cache was written
         and the column never got populated for that row).
      3. live GET /api/users/{remnawave_premium_uuid} via the panel,
         followed by best-effort back-fill of the cache so the next
         request is fast.
    None means "no migrated/provisioned premium entity at all" — caller
    should fall back to the legacy URL.
    """
    if not getattr(config, "REMNAWAVE_ENABLED", False):
        return None
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            # Step 1: cache hit on the active row.
            row = await conn.fetchrow(
                "SELECT remnawave_premium_uuid, remnawave_premium_sub_url "
                "FROM subscriptions WHERE telegram_id = $1 AND status = 'active'",
                telegram_id,
            )
            # Step 2: any row (status-agnostic) — covers users whose
            # subscription was inactive when the cache was first written.
            if not row:
                row = await conn.fetchrow(
                    "SELECT remnawave_premium_uuid, remnawave_premium_sub_url "
                    "FROM subscriptions WHERE telegram_id = $1 "
                    "ORDER BY (status='active') DESC, expires_at DESC NULLS LAST LIMIT 1",
                    telegram_id,
                )
        if not row:
            return None
        cached_raw = row["remnawave_premium_sub_url"]
        cached = cached_raw.strip() if cached_raw else ""
        if cached:
            return cached

        # Step 3: panel fallback.  We have the entity uuid but the URL
        # column was never populated (e.g. row migrated before column 046
        # existed, or written when status wasn't active).  One round-trip
        # to fix it forever.
        panel_uuid_raw = row["remnawave_premium_uuid"]
        panel_uuid = panel_uuid_raw.strip() if panel_uuid_raw else ""
        if not panel_uuid:
            return None
        try:
            from app.services import remnawave_api
            entity = await remnawave_api.get_user(panel_uuid)
        except Exception as e:
            logger.warning("USER_PREMIUM_PANEL_FALLBACK_FAIL: tg=%s %s", telegram_id, e)
            return None
        url = ((entity or {}).get("subscriptionUrl") or "").strip() or None
        if not url:
            return None
        # Best-effort cache write so the next call is fast.
        try:
            await database.set_remnawave_premium_sub_url(telegram_id, url)
        except Exception as e:
            logger.warning("USER_PREMIUM_BACKFILL_FAIL: tg=%s %s", telegram_id, e)
        return url
    except Exception as e:
        logger.warning("USER_PREMIUM_URL_LOOKUP_FAIL: tg=%s %s", telegram_id, e)
        return None


async def get_user_bypass_url(telegram_id: int) -> Optional[str]:
    """Return the cached Remnawave bypass subscription URL or None."""
    if not getattr(config, "REMNAWAVE_ENABLED", False):
        return None
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            url = await conn.fetchval(
                "SELECT remnawave_bypass_sub_url FROM subscriptions "
                "WHERE telegram_id = $1 AND status = 'active'",
                telegram_id,
            )
        return (url or "").strip() or None
    except Exception as e:
        logger.warning("USER_BYPASS_URL_LOOKUP_FAIL: tg=%s %s", telegram_id, e)
        return None


async def get_user_primary_subscription_url(telegram_id: int) -> str:
    """Return the URL the bot's "Подключиться" / copy-key buttons should
    point at for this user.

    Resolution order:
      1. Remnawave premium URL (post-Task-1-migration or Task-2-cutover).
      2. Legacy samopis URL via `vpn_utils.build_sub_url`.

    Always returns a non-empty string so handlers can render it without
    a None-check.  We don't gate on PURCHASE_FLOW_REMNAWAVE here on
    purpose: even buyers from before the cut-over have a
    `remnawave_premium_uuid` from Task 1 and *should* see the new URL
    if it exists.
    """
    premium = await get_user_premium_url(telegram_id)
    if premium:
        return premium
    return _legacy_sub_url(telegram_id)


__all__ = [
    "get_user_premium_url",
    "get_user_bypass_url",
    "get_user_primary_subscription_url",
]
