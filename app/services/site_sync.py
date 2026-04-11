"""
Site Sync Service — Atlas Secure website ↔ ATCbot synchronization.

Implements the sync protocol per TZ:
- POST /api/bot/sync-balance — balance sync + pending cashback
- POST /api/bot/sync-referrals — referral data sync
- POST /api/bot/extend — notify site of subscription payment
- POST /api/bot/sync — overwrite site subscription
- GET  /api/bot/status — get full user status from site

All requests require X-Bot-Api-Key header.
"""
import logging
from typing import Optional, Dict, Any, List

import httpx

import config
import database

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0  # seconds


def is_enabled() -> bool:
    """Check if site sync is configured."""
    return bool(config.SITE_API_URL and config.SITE_BOT_API_KEY)


def _headers() -> Dict[str, str]:
    return {
        "X-Bot-Api-Key": config.SITE_BOT_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _post(endpoint: str, data: dict) -> Optional[dict]:
    """POST to site API. Returns parsed response or None on error."""
    if not is_enabled():
        return None
    url = f"{config.SITE_API_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=data, headers=_headers())
            if resp.status_code != 200:
                logger.error("SITE_SYNC_ERROR: %s status=%d body=%s", endpoint, resp.status_code, resp.text[:300])
                return None
            result = resp.json()
            if not result.get("success"):
                logger.error("SITE_SYNC_FAIL: %s error=%s", endpoint, result.get("error", "unknown"))
                return None
            return result.get("data", result)
    except Exception as e:
        logger.warning("SITE_SYNC_EXCEPTION: %s %s", endpoint, e)
        return None


async def _get(endpoint: str, params: dict = None) -> Optional[dict]:
    """GET from site API. Returns parsed response or None on error."""
    if not is_enabled():
        return None
    url = f"{config.SITE_API_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=_headers())
            if resp.status_code != 200:
                logger.error("SITE_SYNC_ERROR: %s status=%d body=%s", endpoint, resp.status_code, resp.text[:300])
                return None
            result = resp.json()
            if not result.get("success"):
                logger.error("SITE_SYNC_FAIL: %s error=%s", endpoint, result.get("error", "unknown"))
                return None
            return result.get("data", result)
    except Exception as e:
        logger.warning("SITE_SYNC_EXCEPTION: %s %s", endpoint, e)
        return None


# ── Balance Sync ────────────────────────────────────────────────

async def sync_balance(telegram_id: int) -> Optional[dict]:
    """Sync balance with site. Returns pending cashback to apply.

    1. Send bot's current balance to site
    2. Site returns pending cashback (from YooKassa referral payments)
    3. Bot applies pending cashback to local balance
    """
    balance_kopecks = round(await database.get_user_balance(telegram_id) * 100)

    data = await _post("sync-balance", {
        "telegramId": str(telegram_id),
        "balance": balance_kopecks,
    })
    if not data:
        return None

    # Apply pending cashback from site
    pending = data.get("pendingCashback", [])
    if pending:
        for cb in pending:
            amount_rubles = cb.get("amountRubles", 0)
            if amount_rubles > 0:
                await database.increase_balance(
                    telegram_id,
                    amount_rubles,
                    source="site_referral",
                    description=cb.get("description", "Кешбэк с сайта"),
                )
        logger.info(
            "SITE_SYNC_CASHBACK_APPLIED: user=%s items=%d total=%s kop",
            telegram_id, len(pending), data.get("pendingCashbackTotal", 0),
        )

    return data


async def check_balance(telegram_id: int) -> Optional[dict]:
    """GET /api/bot/sync-balance — check balance without modifying it."""
    return await _get("sync-balance", {"telegram_id": str(telegram_id)})


# ── Referral Sync ───────────────────────────────────────────────

async def sync_referrals(telegram_id: int) -> Optional[dict]:
    """Sync referral data with site (MAX merge — no data loss)."""
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                (SELECT COUNT(*) FROM referrals WHERE referrer_user_id = $1) AS referrals,
                (SELECT COUNT(*) FROM referrals WHERE referrer_user_id = $1 AND first_paid_at IS NOT NULL) AS paid_referrals
            """,
            telegram_id,
        )
    referrals = row["referrals"] if row else 0
    paid_referrals = row["paid_referrals"] if row else 0

    referral_code = await database.get_user_referral_code(telegram_id)

    return await _post("sync-referrals", {
        "telegramId": str(telegram_id),
        "referrals": referrals,
        "paidReferrals": paid_referrals,
        "referralCode": referral_code or "",
    })


# ── Subscription Extend ────────────────────────────────────────

async def notify_subscription_extend(
    telegram_id: int,
    days: int,
    plan: str = "basic",
    amount_rubles: float = 0,
    payment_id: str = "",
) -> Optional[dict]:
    """Notify site that bot activated/extended a subscription.

    Site will:
    - Extend subscription on its side
    - Calculate referral cashback if amount provided
    - Cashback appears in pendingCashback on next sync-balance call
    """
    payload = {
        "telegramId": str(telegram_id),
        "days": days,
        "plan": plan,
    }
    if amount_rubles > 0:
        payload["amount"] = round(amount_rubles, 2)
    if payment_id:
        payload["paymentId"] = payment_id

    return await _post("extend", payload)


# ── Subscription Overwrite ──────────────────────────────────────

async def sync_subscription(telegram_id: int, subscription_end: str, plan: str = "basic") -> Optional[dict]:
    """Overwrite site subscription data with bot values."""
    return await _post("sync", {
        "telegramId": str(telegram_id),
        "action": "overwrite_site",
        "subscriptionEnd": subscription_end,
        "plan": plan,
    })


# ── Status ──────────────────────────────────────────────────────

async def get_user_status(telegram_id: int) -> Optional[dict]:
    """Get full user status from site."""
    return await _get("status", {"telegram_id": str(telegram_id)})


# ── Link Telegram Account ──────────────────────────────────────

async def link_telegram_account(token: str, telegram_id: int) -> Optional[dict]:
    """Link Telegram account to site user via deep link token.

    Flow:
    1. Site generates link: t.me/bot?start=<telegramLinkToken>
    2. User clicks link → bot receives /start <token>
    3. Bot calls POST /api/bot/link { token, telegramId }
    4. Site associates telegram_id with the user who generated the token
    """
    return await _post("link", {
        "token": token,
        "telegramId": str(telegram_id),
    })


# ── Full Sync (convenience) ────────────────────────────────────

async def full_sync_after_payment(
    telegram_id: int,
    days: int,
    plan: str,
    amount_rubles: float,
    payment_id: str,
):
    """Called after subscription payment in bot. Performs full sync:
    1. Notify site of subscription extension
    2. Sync balance (apply any pending cashback from site)
    """
    if not is_enabled():
        return

    # 1. Notify subscription extend
    extend_result = await notify_subscription_extend(
        telegram_id, days, plan, amount_rubles, payment_id,
    )
    if extend_result:
        logger.info("SITE_SYNC_EXTEND: user=%s days=%d plan=%s", telegram_id, days, plan)

    # 2. Sync balance (picks up any pending cashback)
    await sync_balance(telegram_id)


async def periodic_sync(telegram_id: int):
    """Periodic sync per TZ:
    1. POST /api/bot/sync-balance → apply pendingCashback
    2. POST /api/bot/sync-referrals
    """
    if not is_enabled():
        return
    await sync_balance(telegram_id)
    await sync_referrals(telegram_id)
