"""
Site API Client — Синхронизация Telegram-бота с сайтом Atlas Secure.

Все запросы к {SITE_API_URL}/api/bot/* содержат заголовок X-Bot-Api-Key.
Клиент использует httpx с retry и exponential backoff.
"""
import logging
from typing import Optional, Dict, Any

import httpx
import config

logger = logging.getLogger(__name__)

# Timeout for site API requests (seconds)
_TIMEOUT = 10.0
_MAX_RETRIES = 2


def _is_configured() -> bool:
    """Check if site API integration is configured."""
    return bool(config.SITE_API_URL and config.BOT_API_KEY)


def _headers() -> Dict[str, str]:
    return {"X-Bot-Api-Key": config.BOT_API_KEY}


def _base_url() -> str:
    return config.SITE_API_URL.rstrip("/")


async def _request(
    method: str,
    path: str,
    *,
    json: Optional[Dict] = None,
    params: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Make an HTTP request to the site API with retry on transient errors.

    Returns parsed JSON response.
    Raises SiteApiError on permanent failures.
    """
    if not _is_configured():
        raise SiteApiDisabled("SITE_API_URL or BOT_API_KEY not configured")

    url = f"{_base_url()}{path}"
    last_error = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.request(
                    method, url, headers=_headers(), json=json, params=params
                )

            if resp.status_code == 404:
                data = resp.json() if resp.content else {}
                raise SiteApiNotFound(
                    data.get("error", "Not found"),
                    code=data.get("code"),
                )

            if resp.status_code == 401:
                raise SiteApiAuthError("Invalid BOT_API_KEY")

            if resp.status_code >= 500:
                raise SiteApiTransient(f"Server error {resp.status_code}")

            if resp.status_code >= 400:
                data = resp.json() if resp.content else {}
                raise SiteApiError(data.get("error", f"HTTP {resp.status_code}"))

            return resp.json()

        except (SiteApiNotFound, SiteApiAuthError, SiteApiError):
            raise
        except SiteApiTransient as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                import asyncio
                await asyncio.sleep(1 * (attempt + 1))
                continue
            raise
        except httpx.HTTPError as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                import asyncio
                await asyncio.sleep(1 * (attempt + 1))
                continue
            raise SiteApiTransient(f"Network error: {e}") from e
        except Exception as e:
            raise SiteApiError(f"Unexpected error: {e}") from e

    raise SiteApiTransient(f"Max retries exceeded: {last_error}")


# =============================================================================
# Exceptions
# =============================================================================

class SiteApiError(Exception):
    """Base site API error."""
    pass


class SiteApiDisabled(SiteApiError):
    """Site API not configured."""
    pass


class SiteApiNotFound(SiteApiError):
    """404 from site API."""
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class SiteApiAuthError(SiteApiError):
    """401 - invalid API key."""
    pass


class SiteApiTransient(SiteApiError):
    """Transient error (5xx, network)."""
    pass


# =============================================================================
# API Methods
# =============================================================================

async def check_user_exists(telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    GET /api/bot/user-by-telegram?telegram_id={id}

    Returns user data dict if exists, None if 404.
    """
    try:
        resp = await _request("GET", "/api/bot/user-by-telegram", params={"telegram_id": str(telegram_id)})
        return resp.get("data")
    except SiteApiNotFound:
        return None


async def register_user(telegram_id: int, referral_code: Optional[str] = None) -> Dict[str, Any]:
    """
    POST /api/bot/register

    Creates a new user on the site linked to this telegram_id.
    Returns: { userId, email, isNew, daysLeft, vpnKey, referralCode, ... }
    """
    body = {"telegramId": str(telegram_id)}
    if referral_code:
        body["referralCode"] = referral_code

    resp = await _request("POST", "/api/bot/register", json=body)
    return resp.get("data", resp)


async def link_telegram(token: str, telegram_id: int) -> Dict[str, Any]:
    """
    POST /api/bot/link

    Links an existing site account to this telegram_id using a link token.
    """
    resp = await _request("POST", "/api/bot/link", json={
        "token": token,
        "telegramId": str(telegram_id),
    })
    return resp.get("data", resp)


async def auth_login(telegram_id: int, nonce: str) -> Dict[str, Any]:
    """
    POST /api/bot/auth-login

    Stores nonce→userId mapping for Telegram web login.
    Returns: { userId, email }
    """
    resp = await _request("POST", "/api/bot/auth-login", json={
        "telegramId": str(telegram_id),
        "nonce": nonce,
    })
    return resp.get("data", resp)


async def get_status(telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    GET /api/bot/status?telegram_id={id}

    Returns full subscription status from the site.
    Fields: userId, email, telegramLinked, daysLeft, hoursLeft, minutesLeft,
            isExpired, subscriptionEnd, subscriptionPlan, vpnKey,
            referralCode, referrals, paidReferrals
    """
    try:
        resp = await _request("GET", "/api/bot/status", params={"telegram_id": str(telegram_id)})
        return resp.get("data")
    except SiteApiNotFound:
        return None


async def extend_subscription(
    telegram_id: int,
    days: int,
    plan: str,
) -> Dict[str, Any]:
    """
    POST /api/bot/extend

    Extends subscription on the site after payment in bot.
    plan must be "basic" or "plus".
    Returns: { userId, email, daysLeft, subscriptionEnd, vpnKey, subscriptionPlan }
    """
    resp = await _request("POST", "/api/bot/extend", json={
        "telegramId": str(telegram_id),
        "days": days,
        "plan": plan,
    })
    return resp.get("data", resp)


# =============================================================================
# Helper: sync vpn_key from site to local DB
# =============================================================================

async def sync_vpn_key_to_local(telegram_id: int, vpn_key: str | None) -> None:
    """
    Update local bot DB subscription's vpn_key with the one from the site.
    This ensures the bot shows the same subscription URL as the site.
    """
    if not vpn_key:
        return
    try:
        import database
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE subscriptions SET vpn_key = $1 WHERE telegram_id = $2",
                vpn_key, telegram_id
            )
        logger.info("SITE_SYNC_VPN_KEY: user=%s key_length=%s", telegram_id, len(vpn_key))
    except Exception as e:
        logger.warning("SITE_SYNC_VPN_KEY_FAILED: user=%s error=%s", telegram_id, e)


# =============================================================================
# Helper: format time display like the site
# =============================================================================

def format_subscription_time(
    days_left: int,
    hours_left: int,
    minutes_left: int,
    is_expired: bool,
) -> str:
    """
    Format subscription time for display, matching the site format.
    - daysLeft >= 1: "X дн Y ч"
    - daysLeft < 1: "X ч Y мин"
    - isExpired: "Подписка истекла"
    """
    if is_expired:
        return "Подписка истекла"
    if days_left >= 1:
        return f"{days_left} дн {hours_left} ч"
    return f"{hours_left} ч {minutes_left} мин"
