"""
Site account linking and navigation handlers.

Scenarios:
A) Site → Bot: User clicks "Link Telegram" on site → /start {telegramLinkToken}
B) Bot → Site: User clicks "Open website" in bot → auto-login via nonce
C) Subscription conflict: Both bot and site have active subscriptions → user picks
D) No site account: Auto-register on site when needed
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
# A) Site → Bot: Deep link handling
# =========================================================================

async def handle_site_deep_link(telegram_id: int, token: str, message) -> bool:
    """
    Handle /start {telegramLinkToken} deep link from website.

    Called from cmd_start when payload is detected as 16-char hex token.
    Returns True if handled (even on error), False only if site sync disabled.
    """
    if not config.SITE_SYNC_ENABLED:
        return False

    language = await resolve_user_language(telegram_id)

    # Call POST /api/bot/link to bind telegram_id to site account
    link_result = await site_api.link_account(token, telegram_id)

    if not link_result:
        # 404 or error — token invalid or already used
        await message.answer(
            i18n_get_text(language, "site_link.token_invalid")
        )
        return True

    # Check for success field in response
    if isinstance(link_result, dict) and link_result.get("success") is False:
        await message.answer(
            i18n_get_text(language, "site_link.token_invalid")
        )
        return True

    # Extract user data from response
    data = link_result.get("data", link_result)
    site_user_id = data.get("userId") or data.get("id")
    email = data.get("email", "")

    if site_user_id:
        await database.set_site_user_id(telegram_id, str(site_user_id))

    # Check for subscription conflict
    bot_sub = await database.get_subscription(telegram_id)
    site_status = await site_api.get_status(telegram_id, force=True)

    bot_has_sub = (
        bot_sub
        and bot_sub.get("status") == "active"
        and bot_sub.get("expires_at")
    )
    site_has_sub = site_status and site_status.get("daysLeft", 0) > 0

    if bot_has_sub and site_has_sub:
        # Both have active subscriptions — ask user which to keep
        bot_days = _days_left(bot_sub["expires_at"])
        site_days = site_status.get("daysLeft", 0)

        text = i18n_get_text(
            language,
            "site_link.choose_subscription",
            bot_days=bot_days,
            site_days=site_days,
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "site_link.keep_telegram"),
                callback_data="site_sync:keep_bot",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "site_link.keep_site"),
                callback_data="site_sync:keep_site",
            )],
        ])
        await message.answer(text, reply_markup=keyboard)
        return True

    # No conflict — linked successfully
    await message.answer(
        i18n_get_text(language, "site_link.linked_success", email=email)
    )

    # If bot has sub but site doesn't — push bot data to site
    if bot_has_sub and not site_has_sub:
        await _sync_bot_to_site(telegram_id, bot_sub)

    return True


# =========================================================================
# B) Bot → Site: "Open website" with auto-login
# =========================================================================

@site_link_router.callback_query(F.data == "open_website")
async def callback_open_website(callback: CallbackQuery, state: FSMContext):
    """
    Generate one-time auth link and send user to site.
    If user not linked — auto-register first.
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
        # Try to find existing account or register new one
        await auto_register_on_site(telegram_id)
        site_user_id = await database.get_site_user_id(telegram_id)

        if not site_user_id:
            await callback.message.edit_text(
                i18n_get_text(language, "site_link.register_failed")
            )
            return

        # Sync bot subscription to site
        bot_sub = await database.get_subscription(telegram_id)
        if bot_sub and bot_sub.get("status") == "active":
            await _sync_bot_to_site(telegram_id, bot_sub)

    # Generate nonce and call auth-login
    nonce = str(uuid_lib.uuid4())
    auth_result = await site_api.auth_login(telegram_id, nonce)

    if not auth_result:
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
# C) Subscription conflict callbacks
# =========================================================================

@site_link_router.callback_query(F.data == "site_sync:keep_bot")
async def callback_keep_bot_subscription(callback: CallbackQuery, state: FSMContext):
    """User chose to keep Telegram bot subscription — overwrite site."""
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

    result = await _sync_bot_to_site(telegram_id, bot_sub)
    if result:
        await callback.message.edit_text(
            i18n_get_text(language, "site_link.synced_to_site")
        )
    else:
        await callback.message.edit_text(
            i18n_get_text(language, "site_link.sync_failed")
        )


@site_link_router.callback_query(F.data == "site_sync:keep_site")
async def callback_keep_site_subscription(callback: CallbackQuery, state: FSMContext):
    """User chose to keep site subscription — update bot data from site."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        await callback.answer()
    except Exception:
        pass

    site_status = await site_api.get_status(telegram_id, force=True)
    if not site_status:
        await callback.message.edit_text(
            i18n_get_text(language, "site_link.sync_failed")
        )
        return

    await _sync_site_to_bot(telegram_id, site_status)
    await callback.message.edit_text(
        i18n_get_text(language, "site_link.synced_from_site")
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

    # Check if already linked
    existing = await database.get_site_user_id(telegram_id)
    if existing:
        return

    # Check if account exists on site by telegram_id
    site_user = await site_api.get_user_by_telegram(telegram_id)
    if site_user:
        site_id = site_user.get("userId") or site_user.get("id")
        if site_id:
            await database.set_site_user_id(telegram_id, str(site_id))
        return

    # Register new account on site
    user = await database.get_user(telegram_id)
    referral_code = user.get("referral_code") if user else None

    result = await site_api.register_account(telegram_id, referral_code)
    if result:
        data = result.get("data", result)
        site_id = data.get("userId") or data.get("id")
        if site_id:
            await database.set_site_user_id(telegram_id, str(site_id))


# =========================================================================
# Post-payment and key sync (called from other modules)
# =========================================================================

async def notify_site_after_payment(telegram_id: int, days: int, plan: str):
    """
    Called after successful payment in bot to sync with site.
    Extends subscription on site side.
    """
    if not config.SITE_SYNC_ENABLED:
        return

    # Ensure user is registered on site
    await auto_register_on_site(telegram_id)

    # Sync current subscription to site
    bot_sub = await database.get_subscription(telegram_id)
    if bot_sub and bot_sub.get("status") == "active":
        await _sync_bot_to_site(telegram_id, bot_sub)

    # Also call extend for proper site-side handling
    result = await site_api.extend_subscription(telegram_id, days, plan)
    if not result:
        logger.warning("Failed to extend subscription on site for user %s", telegram_id)
    else:
        logger.info(
            "Site subscription extended for user %s: +%d days (%s)",
            telegram_id, days, plan,
        )


async def sync_key_to_site(telegram_id: int, vpn_key: str, xray_uuid: str):
    """Sync updated VPN key to site after key reissue in bot."""
    if not config.SITE_SYNC_ENABLED:
        return

    result = await site_api.sync_update_key(telegram_id, vpn_key, xray_uuid)
    if not result:
        logger.warning("Failed to sync key to site for user %s", telegram_id)


# =========================================================================
# Internal sync helpers
# =========================================================================

async def _sync_bot_to_site(telegram_id: int, bot_sub: dict) -> bool:
    """Push bot subscription data to site (overwrite_site)."""
    expires_at = bot_sub.get("expires_at")
    if not expires_at:
        return False

    if isinstance(expires_at, datetime):
        sub_end_iso = expires_at.isoformat()
    else:
        sub_end_iso = str(expires_at)

    plan = (bot_sub.get("subscription_type") or "basic").lower()
    vpn_key = bot_sub.get("vpn_key")
    xray_uuid = bot_sub.get("uuid")

    result = await site_api.sync_overwrite_site(
        telegram_id=telegram_id,
        subscription_end=sub_end_iso,
        plan=plan,
        vpn_key=vpn_key,
        xray_uuid=xray_uuid,
    )
    return result is not None


async def _sync_site_to_bot(telegram_id: int, site_status: dict):
    """Update bot DB from site status data."""
    vpn_key = site_status.get("vpnKey")
    subscription_end = site_status.get("subscriptionEnd")
    plan = site_status.get("subscriptionPlan", "basic")

    if vpn_key and subscription_end:
        pool = await database.get_pool()
        if pool:
            async with pool.acquire() as conn:
                if isinstance(subscription_end, str):
                    try:
                        sub_end = datetime.fromisoformat(
                            subscription_end.replace("Z", "+00:00")
                        )
                    except ValueError:
                        logger.error(
                            "Invalid subscriptionEnd from site: %s",
                            subscription_end,
                        )
                        return
                else:
                    sub_end = subscription_end

                # Ensure naive UTC for DB (TIMESTAMP WITHOUT TIME ZONE)
                if sub_end.tzinfo is not None:
                    sub_end = sub_end.replace(tzinfo=None)

                await conn.execute(
                    """UPDATE subscriptions
                       SET vpn_key = $1, expires_at = $2,
                           subscription_type = $3, status = 'active'
                       WHERE telegram_id = $4""",
                    vpn_key, sub_end, plan, telegram_id,
                )


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
