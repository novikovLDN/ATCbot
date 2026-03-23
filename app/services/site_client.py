"""
Atlas Secure website API client.

Handles bot ↔ site synchronization:
- GET  /api/bot/user?token=XXX          — get user by telegram_link_token
- GET  /api/bot/user-by-telegram?telegram_id=XXX — get user by telegram_id
- POST /api/bot/link                    — link Telegram account to site user
- POST /api/bot/extend                  — extend subscription on site after bot payment

All calls require X-Bot-Api-Key header.
All calls are best-effort: failures are logged but never break bot flow.
"""

import logging
from typing import Optional, Dict, Any

import httpx

import config

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


def _headers() -> Dict[str, str]:
    return {"X-Bot-Api-Key": config.BOT_API_KEY}


async def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    """
    GET /api/bot/user?token=TOKEN

    Returns user data (email, subscription, vpn_key, days) or None on failure.
    """
    if not config.SITE_INTEGRATION_ENABLED:
        return None
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{config.SITE_API_URL}/api/bot/user",
                params={"token": token},
                headers=_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                logger.info(
                    "SITE_USER_BY_TOKEN: token=%s...%s status=200",
                    token[:4], token[-4:],
                )
                return data
            logger.warning(
                "SITE_USER_BY_TOKEN_FAILED: token=%s...%s status=%s",
                token[:4], token[-4:], resp.status_code,
            )
            return None
    except Exception as e:
        logger.warning("SITE_USER_BY_TOKEN_ERROR: %s: %s", type(e).__name__, e)
        return None


async def get_user_by_telegram(telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    GET /api/bot/user-by-telegram?telegram_id=XXX

    Returns user data from site or None.
    """
    if not config.SITE_INTEGRATION_ENABLED:
        return None
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{config.SITE_API_URL}/api/bot/user-by-telegram",
                params={"telegram_id": str(telegram_id)},
                headers=_headers(),
            )
            if resp.status_code == 200:
                return resp.json()
            return None
    except Exception as e:
        logger.warning("SITE_USER_BY_TELEGRAM_ERROR: %s: %s", type(e).__name__, e)
        return None


async def link_telegram(token: str, telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    POST /api/bot/link {token, telegramId}

    Links Telegram account to site user. Returns user data dict on success, None on failure.
    """
    if not config.SITE_INTEGRATION_ENABLED:
        return None
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{config.SITE_API_URL}/api/bot/link",
                json={"token": token, "telegramId": telegram_id},
                headers=_headers(),
            )
            if resp.status_code in (200, 201):
                logger.info(
                    "SITE_LINK_TELEGRAM: user=%s token=%s...%s linked",
                    telegram_id, token[:4], token[-4:],
                )
                return resp.json()
            logger.warning(
                "SITE_LINK_TELEGRAM_FAILED: user=%s status=%s body=%s",
                telegram_id, resp.status_code, resp.text[:200],
            )
            return None
    except Exception as e:
        logger.warning("SITE_LINK_TELEGRAM_ERROR: user=%s %s: %s", telegram_id, type(e).__name__, e)
        return None


async def extend_subscription(telegram_id: int, days: int) -> bool:
    """
    POST /api/bot/extend {telegramId, days}

    Extends subscription on site after bot payment.
    If VPN key was deleted on site — site regenerates it and credits referrer.
    Returns True on success.
    """
    if not config.SITE_INTEGRATION_ENABLED:
        return False
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{config.SITE_API_URL}/api/bot/extend",
                json={"telegramId": telegram_id, "days": days},
                headers=_headers(),
            )
            success = resp.status_code in (200, 201)
            if success:
                logger.info(
                    "SITE_EXTEND: user=%s days=%s extended on site",
                    telegram_id, days,
                )
            else:
                logger.warning(
                    "SITE_EXTEND_FAILED: user=%s days=%s status=%s body=%s",
                    telegram_id, days, resp.status_code, resp.text[:200],
                )
            return success
    except Exception as e:
        logger.warning(
            "SITE_EXTEND_ERROR: user=%s days=%s %s: %s",
            telegram_id, days, type(e).__name__, e,
        )
        return False
