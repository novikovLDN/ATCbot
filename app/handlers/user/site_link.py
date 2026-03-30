"""
Site account linking and navigation handlers.

Rules:
- Link FROM SITE  → site is master → bot takes site data
- Link FROM BOT   → bot is master  → site takes bot data
- After linking   → any change syncs both ways
"""
import logging
import re
import uuid as uuid_lib
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services import site_api
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback

site_link_router = Router()
logger = logging.getLogger(__name__)

# telegramLinkToken = exactly 16 hex characters (a-f, 0-9)
_HEX_TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")


# =========================================================================
# Token detection
# =========================================================================

def is_telegram_link_token(payload: str) -> bool:
    """Check if /start payload is a telegramLinkToken (16 hex chars)."""
    return bool(_HEX_TOKEN_RE.match(payload))


# =========================================================================
# A) Site → Bot: Deep link handling  (SITE IS MASTER)
# =========================================================================

async def handle_site_deep_link(telegram_id: int, token: str, message) -> bool:
    """
    Handle /start {telegramLinkToken} deep link from website.
    Site is the source of truth — bot ALWAYS overwrites its data with site data.

    Returns True if handled (even on error), False only if site sync disabled.
    """
    if not config.SITE_SYNC_ENABLED:
        return False

    language = await resolve_user_language(telegram_id)

    # POST /api/bot/link
    logger.info("SITE_LINK: calling /api/bot/link for user %s, token=%s...", telegram_id, token[:6])
    link_result = await site_api.link_account(token, telegram_id)

    if not link_result:
        logger.warning("SITE_LINK: /api/bot/link returned None for user %s", telegram_id)
        await message.answer(
            i18n_get_text(language, "site_link.token_invalid")
        )
        return True

    # Extract site data (already unwrapped from {"data": ...} by _request)
    data = link_result
    site_user_id = data.get("userId") or data.get("id")
    email = data.get("email", "")
    site_has_sub = bool(data.get("hasActiveSubscription", False))

    logger.info(
        "SITE_LINK: link OK for user %s, site_user_id=%s, email=%s, hasActiveSub=%s, "
        "vpnKey=%s, subscriptionEnd=%s, plan=%s",
        telegram_id, site_user_id, email, site_has_sub,
        bool(data.get("vpnKey")), data.get("subscriptionEnd"), data.get("subscriptionPlan"),
    )

    # Save site_user_id mapping
    if site_user_id:
        await database.set_site_user_id(telegram_id, str(site_user_id))

    # Invalidate status cache so profile shows fresh data
    site_api.invalidate_status_cache(telegram_id)

    # Site is master → ALWAYS overwrite bot data with site data if site has subscription
    if site_has_sub:
        logger.info("SITE_LINK: site has active sub, syncing site→bot for user %s", telegram_id)
        await _sync_site_to_bot(telegram_id, data)
    else:
        logger.info("SITE_LINK: site has NO active sub for user %s", telegram_id)

    # Check: bot had sub but site doesn't → offer to transfer
    bot_sub = await database.get_subscription(telegram_id)
    bot_has_sub = (
        bot_sub
        and bot_sub.get("status") == "active"
        and bot_sub.get("expires_at")
    )

    if bot_has_sub and not site_has_sub:
        logger.info("SITE_LINK: bot has sub but site doesn't, offering transfer for user %s", telegram_id)
        text = i18n_get_text(
            language,
            "site_link.transfer_offer",
            email=email,
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "site_link.transfer_yes"),
                callback_data="site_sync:transfer_to_site",
            )],
        ])
        await message.answer(text, reply_markup=keyboard)
        return True

    # Normal success message
    await message.answer(
        i18n_get_text(language, "site_link.linked_success", email=email)
    )
    return True


# =========================================================================
# B) Bot → Site: "Open website" with auto-login  (BOT IS MASTER)
# =========================================================================

@site_link_router.callback_query(F.data == "open_website")
async def callback_open_website(callback: CallbackQuery, state: FSMContext):
    """
    Generate one-time auth link and send user to site.
    If user not linked — auto-register first, then push bot data to site.
    ALWAYS sync bot data to site before generating login link.
    """
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        await callback.answer()
    except Exception:
        pass

    if not config.SITE_SYNC_ENABLED:
        await callback.message.edit_text(
            i18n_get_text(language, "site_link.site_unavailable")
        )
        return

    # Ensure user has a site account
    site_user_id = await database.get_site_user_id(telegram_id)
    if not site_user_id:
        logger.info("SITE_OPEN: no site_user_id, registering user %s on site", telegram_id)
        await auto_register_on_site(telegram_id)
        site_user_id = await database.get_site_user_id(telegram_id)

        if not site_user_id:
            logger.warning("SITE_OPEN: registration failed for user %s", telegram_id)
            await callback.message.edit_text(
                i18n_get_text(language, "site_link.register_failed")
            )
            return

    # Bot is master → sync bot subscription to site before opening
    # Also pull site data into bot if site has updates
    site_api.invalidate_status_cache(telegram_id)
    bot_sub = await database.get_subscription(telegram_id)
    if bot_sub and bot_sub.get("status") == "active":
        logger.info("SITE_OPEN: syncing bot→site for user %s", telegram_id)
        await _sync_bot_to_site(telegram_id, bot_sub)
    else:
        # No bot subscription — check if site has one and pull it
        site_status = await site_api.get_status(telegram_id, force=True)
        if site_status and site_status.get("hasActiveSubscription"):
            logger.info("SITE_OPEN: site has sub, syncing site→bot for user %s", telegram_id)
            await _sync_site_to_bot(telegram_id, site_status)

    # Generate nonce and call auth-login
    nonce = str(uuid_lib.uuid4())
    auth_result = await site_api.auth_login(telegram_id, nonce)

    if not auth_result:
        logger.warning("SITE_OPEN: auth-login failed for user %s", telegram_id)
        await callback.message.edit_text(
            i18n_get_text(language, "site_link.auth_failed")
        )
        return

    # Build auto-login URL
    site_url = config.SITE_API_URL.rstrip("/")
    login_url = f"{site_url}/?tg_auth={nonce}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "site_link.open_site_button"),
            url=login_url,
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])
    await callback.message.edit_text(
        i18n_get_text(language, "site_link.open_site_text"),
        reply_markup=keyboard,
    )


# =========================================================================
# C) Transfer bot subscription to site (offered when site has no sub)
# =========================================================================

@site_link_router.callback_query(F.data == "site_sync:transfer_to_site")
async def callback_transfer_to_site(callback: CallbackQuery, state: FSMContext):
    """User confirmed to transfer bot subscription to site."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        await callback.answer()
    except Exception:
        pass

    bot_sub = await database.get_subscription(telegram_id)
    if not bot_sub:
        await callback.message.edit_text(
            i18n_get_text(language, "errors.no_active_subscription")
        )
        return

    logger.info("SITE_TRANSFER: transferring bot sub to site for user %s", telegram_id)
    result = await _sync_bot_to_site(telegram_id, bot_sub)
    if result:
        await callback.message.edit_text(
            i18n_get_text(language, "site_link.transfer_done")
        )
    else:
        await callback.message.edit_text(
            i18n_get_text(language, "site_link.sync_failed")
        )


# =========================================================================
# D) Auto-register on site
# =========================================================================

async def auto_register_on_site(telegram_id: int):
    """
    Auto-register user on site if no site account exists.
    Called when user opens site from bot or after first purchase.
    """
    if not config.SITE_SYNC_ENABLED:
        return

    existing = await database.get_site_user_id(telegram_id)
    if existing:
        return

    # Check if account already exists on site by telegram_id
    site_user = await site_api.get_user_by_telegram(telegram_id)
    if site_user:
        site_id = site_user.get("userId") or site_user.get("id")
        if site_id:
            await database.set_site_user_id(telegram_id, str(site_id))
            logger.info("SITE_REGISTER: found existing site account for user %s, site_id=%s", telegram_id, site_id)
        return

    # Register new account
    user = await database.get_user(telegram_id)
    referral_code = user.get("referral_code") if user else None

    result = await site_api.register_account(telegram_id, referral_code)
    if result:
        site_id = result.get("userId") or result.get("id")
        if site_id:
            await database.set_site_user_id(telegram_id, str(site_id))
            logger.info("SITE_REGISTER: created site account for user %s, site_id=%s", telegram_id, site_id)
        else:
            logger.warning("SITE_REGISTER: register response has no userId for user %s: %s", telegram_id, result)
    else:
        logger.warning("SITE_REGISTER: /api/bot/register returned None for user %s", telegram_id)


# =========================================================================
# Post-payment and key sync (called from other modules)
# =========================================================================

async def notify_site_after_payment(telegram_id: int, days: int, plan: str):
    """
    Called after successful payment in bot.
    POST /api/bot/extend → site updates subscription dates+plan.
    Bot keeps its own vpnKey — no key sync.
    """
    if not config.SITE_SYNC_ENABLED:
        return

    # Ensure user is registered on site
    await auto_register_on_site(telegram_id)

    # Call extend — site adds days to subscription
    result = await site_api.extend_subscription(telegram_id, days, plan)
    if not result:
        logger.warning("Failed to extend subscription on site for user %s", telegram_id)
        return

    logger.info(
        "Site subscription extended for user %s: +%d days (%s)",
        telegram_id, days, plan,
    )


async def sync_bot_subscription_to_site(telegram_id: int):
    """
    Full bot→site sync. Reads current bot subscription and pushes to site.
    Called after admin grant, gift activation, or any bot-side change.
    """
    if not config.SITE_SYNC_ENABLED:
        return

    # Ensure user is registered on site
    await auto_register_on_site(telegram_id)

    bot_sub = await database.get_subscription(telegram_id)
    if bot_sub and bot_sub.get("status") == "active":
        await _sync_bot_to_site(telegram_id, bot_sub)
    else:
        # Subscription was revoked/expired → tell site
        await notify_site_subscription_revoked(telegram_id)


async def notify_site_subscription_revoked(telegram_id: int):
    """
    Notify site that bot subscription was revoked/expired.
    Sends overwrite_site with empty vpnKey and past date.
    """
    if not config.SITE_SYNC_ENABLED:
        return

    site_user_id = await database.get_site_user_id(telegram_id)
    if not site_user_id:
        return  # Not linked to site

    logger.info("SYNC_REVOKE→SITE: notifying site of revoke for user %s", telegram_id)
    result = await site_api.sync_overwrite_site(
        telegram_id=telegram_id,
        subscription_end="1970-01-01T00:00:00+00:00",
        plan="none",
    )
    if result is None:
        logger.warning("SYNC_REVOKE→SITE: failed for user %s", telegram_id)
    else:
        logger.info("SYNC_REVOKE→SITE: success for user %s", telegram_id)


# =========================================================================
# Internal sync helpers
# =========================================================================

async def _sync_bot_to_site(telegram_id: int, bot_sub: dict) -> bool:
    """Push bot subscription dates+plan to site (no vpnKey sync)."""
    expires_at = bot_sub.get("expires_at")
    if not expires_at:
        logger.warning("SYNC_BOT→SITE: no expires_at for user %s, skipping", telegram_id)
        return False

    if isinstance(expires_at, datetime):
        sub_end_iso = expires_at.isoformat()
    else:
        sub_end_iso = str(expires_at)

    plan = (bot_sub.get("subscription_type") or "basic").lower()

    logger.info(
        "SYNC_BOT→SITE: user=%s plan=%s expires=%s",
        telegram_id, plan, sub_end_iso,
    )

    result = await site_api.sync_overwrite_site(
        telegram_id=telegram_id,
        subscription_end=sub_end_iso,
        plan=plan,
    )
    if result is None:
        logger.warning("SYNC_BOT→SITE: /api/bot/sync returned None for user %s", telegram_id)
        return False

    logger.info("SYNC_BOT→SITE: success for user %s, response=%s", telegram_id, result)

    # Verify: read back from site to confirm data was saved
    site_api.invalidate_status_cache(telegram_id)
    verify = await site_api.get_status(telegram_id, force=True)
    if verify:
        logger.info(
            "SYNC_BOT→SITE_VERIFY: user=%s hasActiveSub=%s sitePlan=%s siteExpires=%s siteVpnKey=%s",
            telegram_id,
            verify.get("hasActiveSubscription"),
            verify.get("subscriptionPlan"),
            verify.get("subscriptionEnd"),
            bool(verify.get("vpnKey")),
        )
    else:
        logger.warning("SYNC_BOT→SITE_VERIFY: could not read back status for user %s", telegram_id)

    return True


async def _sync_site_to_bot(telegram_id: int, site_data: dict):
    """
    Sync subscription dates+plan from site to bot DB.
    Does NOT touch vpn_key — each side manages its own keys.
    Handles both UPDATE (existing subscription) and INSERT (no row).
    """
    subscription_end = site_data.get("subscriptionEnd")
    plan = (site_data.get("subscriptionPlan") or "basic").lower()

    logger.info(
        "SYNC_SITE→BOT: user=%s subEnd=%s plan=%s",
        telegram_id, subscription_end, plan,
    )

    if not subscription_end:
        logger.warning("SYNC_SITE→BOT: no subscriptionEnd in site data for user %s, skipping", telegram_id)
        return

    if isinstance(subscription_end, str):
        try:
            sub_end = datetime.fromisoformat(
                subscription_end.replace("Z", "+00:00")
            )
        except ValueError:
            logger.error("SYNC_SITE→BOT: invalid subscriptionEnd for user %s: %s", telegram_id, subscription_end)
            return
    else:
        sub_end = subscription_end

    # Ensure naive UTC for DB
    if sub_end.tzinfo is not None:
        sub_end = sub_end.replace(tzinfo=None)

    pool = await database.get_pool()
    if not pool:
        logger.error("SYNC_SITE→BOT: no DB pool for user %s", telegram_id)
        return

    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, expires_at FROM subscriptions WHERE telegram_id = $1",
            telegram_id,
        )

        if existing:
            await conn.execute(
                """UPDATE subscriptions
                   SET expires_at = $1, subscription_type = $2, status = 'active'
                   WHERE telegram_id = $3""",
                sub_end, plan, telegram_id,
            )
            logger.info(
                "SYNC_SITE→BOT: UPDATED subscription for user %s (expires changed: %s)",
                telegram_id,
                existing["expires_at"] != sub_end,
            )
        else:
            # No subscription row — create minimal row; vpn_key will be empty
            # until user gets their own key from bot
            await conn.execute(
                """INSERT INTO subscriptions (
                       telegram_id, uuid, vpn_key, expires_at, status, source,
                       subscription_type, activated_at, activation_status
                   ) VALUES ($1, '', '', $2, 'active', 'site', $3, NOW(), 'active')""",
                telegram_id,
                sub_end,
                plan,
            )
            logger.info("SYNC_SITE→BOT: INSERTED new subscription for user %s (no vpn_key)", telegram_id)


# =========================================================================
# Helpers
# =========================================================================

def _days_left(expires_at) -> int:
    if expires_at is None:
        return 0
    now = datetime.now(timezone.utc)
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        delta = expires_at - now
        return max(0, delta.days)
    return 0
