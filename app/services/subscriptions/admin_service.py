"""
Admin Subscription Service

Handles manual admin-paid access granting.
Separate from payment logic, trial logic, and reissue logic.

MUST NOT trigger referral reward, payment records, or balance changes.
"""

import logging
from datetime import timedelta, datetime
from typing import Dict, Any

import database

logger = logging.getLogger(__name__)


async def grant_paid_access_by_admin(
    telegram_id: int,
    duration_days: int,
    initiated_by: int
) -> Dict[str, Any]:
    """
    Grant paid access to user (admin manual action).

    If active subscription exists:
        - Extend expiration from max(current_expires, now)
        - Do NOT regenerate UUID (UUID stable)
    If no subscription:
        - Call grant_access with source="admin_paid"

    MUST NOT trigger: referral reward, payment logic, balance changes, pending purchase.

    Args:
        telegram_id: Target user Telegram ID
        duration_days: Duration in days (or minutes if duration_days < 1, use grant_paid_access_minutes)
        initiated_by: Admin Telegram ID

    Returns:
        {
            "success": bool,
            "expires_at": datetime,
            "vpn_key": Optional[str],
            "action": "renewal" | "new_issuance" | "pending_activation",
            "error": Optional[str]
        }
    """
    duration = timedelta(days=duration_days)

    try:
        result = await database.grant_access(
            telegram_id=telegram_id,
            duration=duration,
            source="admin_paid",
            admin_telegram_id=initiated_by,
            admin_grant_days=duration_days,
            conn=None
        )

        expires_at = result["subscription_end"]
        vpn_key = result.get("vless_url")

        if not vpn_key and result.get("action") == "renewal":
            subscription = await database.get_subscription(telegram_id)
            vpn_key = subscription.get("vpn_key") if subscription else result.get("uuid", "")

        logger.info(
            f"ADMIN_GRANTED_PAID_ACCESS [telegram_id={telegram_id}, initiated_by={initiated_by}, "
            f"duration_days={duration_days}, expires_at={expires_at.isoformat()}, action={result.get('action')}]"
        )

        return {
            "success": True,
            "expires_at": expires_at,
            "vpn_key": vpn_key,
            "action": result.get("action", "unknown"),
            "error": None
        }

    except Exception as e:
        logger.exception(
            f"ADMIN_GRANTED_PAID_ACCESS_FAILED [telegram_id={telegram_id}, initiated_by={initiated_by}, error={str(e)[:100]}]"
        )
        return {
            "success": False,
            "expires_at": None,
            "vpn_key": None,
            "action": None,
            "error": str(e)
        }


async def grant_paid_access_by_admin_duration(
    telegram_id: int,
    duration: timedelta,
    initiated_by: int
) -> Dict[str, Any]:
    """
    Grant paid access with custom duration (minutes, hours, or days).

    Wraps database.grant_access with source="admin_paid".
    """
    try:
        result = await database.grant_access(
            telegram_id=telegram_id,
            duration=duration,
            source="admin_paid",
            admin_telegram_id=initiated_by,
            admin_grant_days=None,
            conn=None
        )

        expires_at = result["subscription_end"]
        vpn_key = result.get("vless_url")

        if not vpn_key and result.get("action") == "renewal":
            subscription = await database.get_subscription(telegram_id)
            vpn_key = subscription.get("vpn_key") if subscription else result.get("uuid", "")

        logger.info(
            f"ADMIN_GRANTED_PAID_ACCESS [telegram_id={telegram_id}, initiated_by={initiated_by}, "
            f"duration={duration}, expires_at={expires_at.isoformat()}, action={result.get('action')}]"
        )

        return {
            "success": True,
            "expires_at": expires_at,
            "vpn_key": vpn_key,
            "action": result.get("action", "unknown"),
            "error": None
        }

    except Exception as e:
        logger.exception(
            f"ADMIN_GRANTED_PAID_ACCESS_FAILED [telegram_id={telegram_id}, initiated_by={initiated_by}, error={str(e)[:100]}]"
        )
        return {
            "success": False,
            "expires_at": None,
            "vpn_key": None,
            "action": None,
            "error": str(e)
        }
