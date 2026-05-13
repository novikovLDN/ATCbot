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
  2. Live panel fallback via `remnawave_api.get_user(uuid)` when the
     cache column was never populated for some reason (status drift,
     legacy migration before column 046 existed, …) — back-fills the
     cache on success.
  3. LAZY PROVISION: when a user has an active subscription with a
     samopis uuid but NO premium entity at all (trial users + any
     edge case the migration script missed), provision one on the
     fly so the link is finally a real Remnawave URL.  Per-process
     dedup lock prevents duplicate creation under concurrent clicks.
  4. Fall back to `build_sub_url(telegram_id)` only as a last resort
     (legacy URL → handled by `subscription_proxy` if enabled).

These helpers never raise — they always return *some* URL the bot can
render.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)


# Per-process lock per telegram_id: prevents two concurrent button clicks
# from racing to create two premium entities for the same user.  Locks are
# cheap and short-lived; we never bother to GC them.
_lazy_provision_locks: dict[int, asyncio.Lock] = {}


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
    should consider lazy-provisioning or fall back to the legacy URL.
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


async def _try_lazy_provision_premium(telegram_id: int) -> bool:
    """If the user has an active subscription but no premium entity yet,
    create one on the fly so subsequent URL requests resolve to Remnawave.

    Returns True if a new entity was provisioned (or recovered) and the
    DB cache is now populated, False otherwise.

    Conditions to actually attempt:
      - REMNAWAVE_ENABLED and a MainServer squad uuid is configured.
      - The user has SOME subscription row (any status).
      - The row has a legacy samopis uuid we can use as `vlessUuid` for
        backward-compatibility, OR we generate a fresh UUID for users
        without one.
      - `remnawave_premium_uuid` is not already set for this user.

    Uses a per-process lock keyed on telegram_id to prevent two parallel
    callbacks from racing to create two entities for the same user.
    """
    if not getattr(config, "REMNAWAVE_ENABLED", False):
        return False
    if not getattr(config, "REMNAWAVE_MAIN_SQUAD_UUID", ""):
        return False

    lock = _lazy_provision_locks.setdefault(telegram_id, asyncio.Lock())
    async with lock:
        try:
            import database
            sub = await database.get_subscription_any(telegram_id)
            if not sub:
                return False
            existing_premium = (sub.get("remnawave_premium_uuid") or "").strip()
            if existing_premium:
                # Already has one — caller's earlier cache lookup just missed.
                return False

            samopis_uuid_raw = sub.get("uuid")
            samopis_uuid = samopis_uuid_raw.strip() if samopis_uuid_raw else ""

            # Determine an expireAt for the new entity.  Prefer the
            # subscription's own expires_at if it's still in the future;
            # otherwise fall back to a short window (3 days) so the entity
            # exists but doesn't outlive the actual subscription.  The
            # caller's renewal flow PATCHes expireAt on top-ups.
            from datetime import datetime, timezone, timedelta
            expires = sub.get("expires_at")
            if not expires:
                expire_at = datetime.now(timezone.utc) + timedelta(days=3)
            else:
                if getattr(expires, "tzinfo", None) is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                expire_at = expires
                # If subscription is already past expiry don't lazy-create
                # for them — they have no live access anyway.
                if expire_at <= datetime.now(timezone.utc):
                    return False

            from app.services import remnawave_premium
            result = await remnawave_premium.create_premium_user_entity(
                telegram_id,
                requested_uuid=samopis_uuid or None,
                expire_at=expire_at,
                description="Lazy-provisioned via URL request",
            )
            if not result.ok:
                logger.warning(
                    "LAZY_PROVISION_FAILED: tg=%s status=%s error=%s",
                    telegram_id, result.status, result.error,
                )
                return False
            try:
                await database.set_remnawave_premium_uuid_and_url(
                    telegram_id,
                    result.panel_uuid or "",
                    result.subscription_url,
                    short_uuid=result.short_uuid,
                )
            except Exception as e:
                logger.warning(
                    "LAZY_PROVISION_PERSIST_FAIL: tg=%s err=%s", telegram_id, e,
                )
                return False
            logger.info(
                "LAZY_PROVISION_DONE: tg=%s uuid=%s recovered=%s",
                telegram_id, (result.panel_uuid or "")[:8], result.recovered,
            )
            return True
        except Exception as e:
            logger.warning("LAZY_PROVISION_EXCEPTION: tg=%s err=%s", telegram_id, e)
            return False


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
      1. Cached / live Remnawave premium URL.
      2. Lazy-provision a premium entity for an active user that
         somehow doesn't have one yet (trial / pre-Task-2 edge cases)
         — then re-query.
      3. Legacy samopis URL via `vpn_utils.build_sub_url`.

    Always returns a non-empty string so handlers can render it without
    a None-check.
    """
    premium = await get_user_premium_url(telegram_id)
    if premium:
        return premium

    if await _try_lazy_provision_premium(telegram_id):
        premium = await get_user_premium_url(telegram_id)
        if premium:
            return premium

    return _legacy_sub_url(telegram_id)


__all__ = [
    "get_user_premium_url",
    "get_user_bypass_url",
    "get_user_primary_subscription_url",
]
