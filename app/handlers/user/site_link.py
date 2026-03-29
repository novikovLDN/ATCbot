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
    link_result = await site_api.link_account(token, telegram_id)

    if not link_result:
        await message.answer(
            i18n_get_text(language, "site_link.token_invalid")
        )
        return True

    if isinstance(link_result, dict) and link_result.get("success") is False:
        await message.answer(
            i18n_get_text(language, "site_link.token_invalid")
        )
        return True

    # Extract site data
    data = link_result.get("data", link_result)
    site_user_id = data.get("userId") or data.get("id")
    email = data.get("email", "")
    site_has_sub = bool(data.get("hasActiveSubscription", False))

    # Save site_user_id mapping
    if site_user_id:
        await database.set_site_user_id(telegram_id, str(site_user_id))

    # Site is master → overwrite bot data with site data
    if site_has_sub:
        await _sync_site_to_bot(telegram_id, data)

    # Check: bot had sub but site doesn't → offer to transfer
    bot_sub = await database.get_subscription(telegram_id)
    bot_has_sub = (
        bot_sub
        and bot_sub.get("status") == "active"
        and bot_sub.get("expires_at")
    )

    if bot_has_sub and not site_has_sub:
        # Offer to transfer bot subscription to site
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
        await auto_register_on_site(telegram_id)
        site_user_id = await database.get_site_user_id(telegram_id)

        if not site_user_id:
            await callback.message.edit_text(
                i18n_get_text(language, "site_link.register_failed")
            )
            return

        # Bot is master → push bot subscription to site after registration
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
        return

    # Register new account
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
    Called after successful payment in bot.
    POST /api/bot/extend → site updated, bot updates vpnKey from response.
    """
    if not config.SITE_SYNC_ENABLED:
        return

    # Ensure user is registered on site
    await auto_register_on_site(telegram_id)

    # Call extend — site returns updated subscription data
    result = await site_api.extend_subscription(telegram_id, days, plan)
    if not result:
        logger.warning("Failed to extend subscription on site for user %s", telegram_id)
        return

    logger.info(
        "Site subscription extended for user %s: +%d days (%s)",
        telegram_id, days, plan,
    )

    # Update bot vpnKey from extend response if changed
    resp_data = result.get("data", result)
    site_vpn_key = resp_data.get("vpnKey")
    if site_vpn_key:
        pool = await database.get_pool()
        if pool:
            try:
                async with pool.acquire() as conn:
                    current_key = await conn.fetchval(
                        "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                        telegram_id,
                    )
                    if current_key and current_key != site_vpn_key:
                        await conn.execute(
                            "UPDATE subscriptions SET vpn_key = $1 WHERE telegram_id = $2",
                            site_vpn_key, telegram_id,
                        )
                        logger.info("Updated bot vpn_key from extend response for user %s", telegram_id)
            except Exception as e:
                logger.warning("Failed to update vpn_key from extend response: %s", e)


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


async def _sync_site_to_bot(telegram_id: int, site_data: dict):
    """
    Overwrite bot DB from site data.
    Handles both UPDATE (existing subscription) and INSERT (no row).
    """
    vpn_key = site_data.get("vpnKey")
    subscription_end = site_data.get("subscriptionEnd")
    plan = (site_data.get("subscriptionPlan") or "basic").lower()
    xray_uuid = site_data.get("xrayUuid")

    if not vpn_key or not subscription_end:
        return

    if isinstance(subscription_end, str):
        try:
            sub_end = datetime.fromisoformat(
                subscription_end.replace("Z", "+00:00")
            )
        except ValueError:
            logger.error("Invalid subscriptionEnd from site: %s", subscription_end)
            return
    else:
        sub_end = subscription_end

    # Ensure naive UTC for DB
    if sub_end.tzinfo is not None:
        sub_end = sub_end.replace(tzinfo=None)

    pool = await database.get_pool()
    if not pool:
        return

    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM subscriptions WHERE telegram_id = $1",
            telegram_id,
        )

        if existing:
            await conn.execute(
                """UPDATE subscriptions
                   SET vpn_key = $1, expires_at = $2,
                       subscription_type = $3, status = 'active'
                   WHERE telegram_id = $4""",
                vpn_key, sub_end, plan, telegram_id,
            )
        else:
            await conn.execute(
                """INSERT INTO subscriptions (
                       telegram_id, uuid, vpn_key, expires_at, status, source,
                       subscription_type, activated_at, activation_status
                   ) VALUES ($1, $2, $3, $4, 'active', 'site', $5, NOW(), 'active')""",
                telegram_id,
                xray_uuid or "",
                vpn_key,
                sub_end,
                plan,
            )
            logger.info("Created bot subscription from site data for user %s", telegram_id)


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
