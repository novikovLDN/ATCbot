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
    """Return the cached Remnawave premium subscription URL or None.

    None means "no migrated/provisioned premium entity in DB" — caller
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
            url = await conn.fetchval(
                "SELECT remnawave_premium_sub_url FROM subscriptions "
                "WHERE telegram_id = $1 AND status = 'active'",
                telegram_id,
            )
        return (url or "").strip() or None
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
