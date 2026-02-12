"""
VPN Key Reissue Service

Handles forced UUID regeneration for admin operations.
Reissue ≠ Renewal: reissue ALWAYS regenerates UUID, never extends subscription.

MUST NOT go through grant_access() renewal detection branch.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any

import database

logger = logging.getLogger(__name__)

# Max parallel reissue tasks per batch (avoid rate limit)
BATCH_SIZE = 10


async def reissue_vpn_key_for_user(
    telegram_id: int,
    initiated_by: str = "admin"
) -> Dict[str, Any]:
    """
    Reissue VPN key for a single user with active subscription.

    ALWAYS calls VPN API /add-user. NEVER uses renewal logic.
    Replaces subscription.uuid and subscription.vpn_key.
    Keeps subscription_end and source unchanged.

    Args:
        telegram_id: Target user Telegram ID
        initiated_by: Admin Telegram ID (str or int)

    Returns:
        {
            "success": bool,
            "new_vpn_key": Optional[str],
            "old_vpn_key": Optional[str],
            "old_uuid": Optional[str],
            "new_uuid": Optional[str],
            "subscription_expires_at": Optional[datetime],
            "error": Optional[str]
        }
    """
    admin_id = int(initiated_by) if isinstance(initiated_by, str) and str(initiated_by).isdigit() else initiated_by

    logger.info(
        f"ADMIN_REISSUE_EXECUTION_STARTED [telegram_id={telegram_id}, initiated_by={admin_id}]"
    )

    try:
        new_vpn_key, old_vpn_key = await database.reissue_vpn_key_atomic(telegram_id, admin_id)

        if new_vpn_key is None:
            logger.warning(
                f"ADMIN_REISSUE_EXECUTION_FAILED [telegram_id={telegram_id}, reason=no_active_subscription_or_error]"
            )
            return {
                "success": False,
                "new_vpn_key": None,
                "old_vpn_key": old_vpn_key,
                "old_uuid": None,
                "new_uuid": None,
                "subscription_expires_at": None,
                "error": "No active subscription or VPN key creation failed"
            }

        subscription = await database.get_subscription(telegram_id)
        expires_at = subscription.get("expires_at") if subscription else None
        if expires_at and isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        new_uuid = subscription.get("uuid") if subscription else None
        new_uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
        expires_str = expires_at.isoformat() if expires_at else "N/A"

        logger.info(
            f"ADMIN_REISSUE_SUCCESS [telegram_id={telegram_id}, initiated_by={admin_id}, "
            f"new_uuid={new_uuid_preview}, subscription_expires_at={expires_str}]"
        )

        return {
            "success": True,
            "new_vpn_key": new_vpn_key,
            "old_vpn_key": old_vpn_key,
            "old_uuid": None,
            "new_uuid": new_uuid,
            "subscription_expires_at": expires_at,
            "error": None
        }

    except Exception as e:
        logger.exception(
            f"ADMIN_REISSUE_EXECUTION_FAILED [telegram_id={telegram_id}, initiated_by={admin_id}, error={str(e)[:100]}]"
        )
        return {
            "success": False,
            "new_vpn_key": None,
            "old_vpn_key": None,
            "old_uuid": None,
            "new_uuid": None,
            "subscription_expires_at": None,
            "error": str(e)
        }


async def reissue_vpn_keys_for_all_active_users(
    initiated_by: str = "admin"
) -> Dict[str, Any]:
    """
    Bulk reissue VPN keys for all users with active subscriptions.

    Uses safe async batching (max BATCH_SIZE parallel tasks).
    Never silently fails - collects stats per user.

    Args:
        initiated_by: Admin Telegram ID

    Returns:
        {
            "total": int,
            "success": int,
            "failed": int,
            "failed_users": List[int],
            "details": List[Dict]
        }
    """
    pool = await database.get_pool()
    now = datetime.now()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id FROM subscriptions
               WHERE status = 'active' AND expires_at > $1 AND uuid IS NOT NULL
               ORDER BY telegram_id""",
            now
        )

    telegram_ids = [row["telegram_id"] for row in rows]
    total = len(telegram_ids)

    if total == 0:
        return {"total": 0, "success": 0, "failed": 0, "failed_users": [], "details": []}

    success_count = 0
    failed_count = 0
    failed_users = []
    details = []

    for i in range(0, total, BATCH_SIZE):
        batch = telegram_ids[i:i + BATCH_SIZE]
        tasks = [reissue_vpn_key_for_user(tid, initiated_by) for tid in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for idx, result in enumerate(results):
            telegram_id = batch[idx]
            if isinstance(result, Exception):
                logger.error(
                    f"ADMIN_BULK_REISSUE_ITEM_FAILED [telegram_id={telegram_id}, error={str(result)[:80]}]"
                )
                failed_count += 1
                failed_users.append(telegram_id)
                details.append({"telegram_id": telegram_id, "success": False, "error": str(result)})
            elif result.get("success"):
                logger.info(f"ADMIN_BULK_REISSUE_ITEM_SUCCESS [telegram_id={telegram_id}]")
                success_count += 1
                details.append({"telegram_id": telegram_id, "success": True})
            else:
                logger.warning(
                    f"ADMIN_BULK_REISSUE_ITEM_FAILED [telegram_id={telegram_id}, reason={result.get('error', 'unknown')}]"
                )
                failed_count += 1
                failed_users.append(telegram_id)
                details.append({"telegram_id": telegram_id, "success": False, "error": result.get("error")})

        if i + BATCH_SIZE < total:
            await asyncio.sleep(1.5)

    return {
        "total": total,
        "success": success_count,
        "failed": failed_count,
        "failed_users": failed_users,
        "details": details
    }
