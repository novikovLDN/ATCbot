"""
Site API Client — integration between Telegram Bot and Atlas Secure website.

The website is the single source of truth for subscriptions and VPN keys.
Bot stores only telegram_id → site_user_id mapping.
All subscription/key data is fetched from the site API.

API endpoints:
    GET  /api/bot/status?telegram_id=X     — full subscription status
    GET  /api/bot/user-by-telegram?telegram_id=X — find user by telegram_id
    GET  /api/bot/user?token=X             — find by telegramLinkToken
    POST /api/bot/link                     — link telegram_id to account
    POST /api/bot/register                 — create account from bot
    POST /api/bot/extend                   — extend subscription
    POST /api/bot/sync                     — sync data (overwrite_site / update_key)
    POST /api/bot/auth-login               — create nonce for auto-login from bot to site
"""

import logging
import time
from typing import Optional, Dict, Any

import aiohttp

import config

logger = logging.getLogger(__name__)

# Cache: telegram_id -> (data, timestamp)
_status_cache: Dict[int, tuple] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


def _get_headers() -> Dict[str, str]:
    return {
        "X-Bot-Api-Key": config.BOT_API_KEY,
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return config.SITE_API_URL.rstrip("/")


async def _request(method: str, path: str, **kwargs) -> Optional[Dict[str, Any]]:
    """Make an HTTP request to the site API. Returns parsed JSON or None on error."""
    if not config.SITE_API_URL or not config.BOT_API_KEY:
        logger.warning("SITE_API_URL or BOT_API_KEY not configured, skipping site API call")
        return None

    url = f"{_base_url()}{path}"
    headers = _get_headers()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), **kwargs
            ) as resp:
                if resp.status == 404:
                    logger.info("Site API 404: %s %s", method, path)
                    return None
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(
                        "Site API error: %s %s -> %s: %s",
                        method, path, resp.status, body[:500]
                    )
                    return None
                data = await resp.json()
                logger.debug("Site API OK: %s %s -> %s", method, path, resp.status)
                # Log sync/extend responses at INFO level for debugging
                if "/sync" in path or "/extend" in path:
                    logger.info("Site API response: %s %s -> %s", method, path, data)
                # Unwrap {"success": ..., "data": {...}} envelope
                if isinstance(data, dict) and "data" in data and data["data"] is not None:
                    return data["data"]
                # Check for success=false without data
                if isinstance(data, dict) and data.get("success") is False:
                    logger.warning("Site API success=false: %s %s -> %s", method, path, data)
                    return None
                return data
    except aiohttp.ClientError as e:
        logger.error("Site API connection error: %s %s -> %s", method, path, e)
        return None
    except Exception as e:
        logger.exception("Site API unexpected error: %s %s -> %s", method, path, e)
        return None


# =========================================================================
# Status (cached)
# =========================================================================

async def get_status(telegram_id: int, force: bool = False) -> Optional[Dict[str, Any]]:
    """
    Get full subscription status from website.

    Returns dict with: daysLeft, hoursLeft, minutesLeft, subscriptionEnd,
    subscriptionPlan, vpnKey, xrayUuid, referralCode, referrals, paidReferrals, isExpired.

    Cached for 5 minutes unless force=True.
    """
    now = time.time()
    if not force and telegram_id in _status_cache:
        data, ts = _status_cache[telegram_id]
        if now - ts < _CACHE_TTL_SECONDS:
            return data

    result = await _request("GET", f"/api/bot/status?telegram_id={telegram_id}")
    if result is not None:
        _status_cache[telegram_id] = (result, now)
    return result


def invalidate_status_cache(telegram_id: int):
    """Remove cached status for a user."""
    _status_cache.pop(telegram_id, None)


# =========================================================================
# User lookup
# =========================================================================

async def get_user_by_telegram(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Find user on site by telegram_id."""
    return await _request("GET", f"/api/bot/user-by-telegram?telegram_id={telegram_id}")


async def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Find user on site by telegramLinkToken."""
    return await _request("GET", f"/api/bot/user?token={token}")


# =========================================================================
# Link / Register
# =========================================================================

async def link_account(token: str, telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    Link telegram_id to existing site account via telegramLinkToken.

    POST /api/bot/link
    Body: { "token": "...", "telegramId": "..." }

    Returns: { userId, email, daysLeft, vpnKey } or None on error.
    """
    return await _request("POST", "/api/bot/link", json={
        "token": token,
        "telegramId": str(telegram_id),
    })


async def register_account(telegram_id: int, referral_code: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Create a new site account from bot.

    POST /api/bot/register
    Body: { "telegramId": "...", "referralCode": "..." }

    Returns: { userId, email, vpnKey, subscriptionEnd } or None.
    """
    body: Dict[str, Any] = {"telegramId": str(telegram_id)}
    if referral_code:
        body["referralCode"] = referral_code
    return await _request("POST", "/api/bot/register", json=body)


# =========================================================================
# Sync
# =========================================================================

async def sync_overwrite_site(
    telegram_id: int,
    subscription_end: str,
    plan: str,
) -> Optional[Dict[str, Any]]:
    """
    Overwrite site subscription with bot data (dates + plan only).

    POST /api/bot/sync
    action = "overwrite_site"
    """
    body: Dict[str, Any] = {
        "telegramId": str(telegram_id),
        "action": "overwrite_site",
        "subscriptionEnd": subscription_end,
        "plan": plan,
    }

    logger.info("SYNC_OVERWRITE_SITE: sending body=%s", body)
    result = await _request("POST", "/api/bot/sync", json=body)
    logger.info("SYNC_OVERWRITE_SITE: result=%s", result)
    if result:
        invalidate_status_cache(telegram_id)
    return result


async def sync_referrals(
    telegram_id: int,
    referrals: int,
    paid_referrals: int,
    referral_code: str,
) -> Optional[Dict[str, Any]]:
    """
    Sync referral counters to site.

    POST /api/bot/sync-referrals
    Body: { "telegramId": "...", "referrals": N, "paidReferrals": N, "referralCode": "..." }

    Site applies MAX(bot, site) and returns merged values.
    """
    body = {
        "telegramId": str(telegram_id),
        "referrals": referrals,
        "paidReferrals": paid_referrals,
        "referralCode": referral_code,
    }
    result = await _request("POST", "/api/bot/sync-referrals", json=body)
    if result:
        invalidate_status_cache(telegram_id)
    return result


async def extend_subscription(telegram_id: int, days: int, plan: str) -> Optional[Dict[str, Any]]:
    """
    Extend subscription on site after bot payment.

    POST /api/bot/extend
    Body: { "telegramId": "...", "days": 30, "plan": "basic" }

    Returns: { subscriptionEnd, vpnKey, subscriptionPlan } or None.
    """
    result = await _request("POST", "/api/bot/extend", json={
        "telegramId": str(telegram_id),
        "days": days,
        "plan": plan,
    })
    if result:
        invalidate_status_cache(telegram_id)
    return result


# =========================================================================
# Auth login (bot → site auto-login)
# =========================================================================

async def auth_login(telegram_id: int, nonce: str) -> Optional[Dict[str, Any]]:
    """
    Create a one-time auth nonce for auto-login from bot to site.

    POST /api/bot/auth-login
    Body: { "telegramId": "...", "nonce": "..." }

    Site stores nonce → userId mapping. User opens site with ?tg_auth={nonce}.
    Returns: { success: true } or None on error.
    """
    return await _request("POST", "/api/bot/auth-login", json={
        "telegramId": str(telegram_id),
        "nonce": nonce,
    })
