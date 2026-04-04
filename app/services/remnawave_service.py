"""
Remnawave integration service — high-level operations for the bot.

Handles creating/deleting/updating Remnawave users alongside existing Xray operations.
All functions are safe to call as fire-and-forget (never raise, always log).
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import config
import database
from app.services import remnawave_api

logger = logging.getLogger(__name__)

# Background tasks set (same pattern as vpn_utils)
_background_tasks: set = set()


def _fire_and_forget(coro) -> None:
    """Schedule a coroutine as background task with proper cleanup."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            task = asyncio.create_task(coro)
            _background_tasks.add(task)

            def _done(t):
                _background_tasks.discard(t)
                if not t.cancelled() and t.exception():
                    logger.warning("REMNAWAVE background task failed: %s", t.exception())

            task.add_done_callback(_done)
    except Exception as e:
        logger.warning("Failed to schedule REMNAWAVE background task: %s", e)


def _traffic_limit_for_tariff(tariff: str) -> int:
    """Get traffic limit in bytes for a tariff."""
    t = tariff.lower().strip()
    if t in config.TRAFFIC_LIMITS:
        return config.TRAFFIC_LIMITS[t]
    # Biz tariffs get plus limits
    if t.startswith("biz_"):
        return config.TRAFFIC_LIMITS.get("plus", 25 * 1024**3)
    return config.TRAFFIC_LIMITS.get("basic", 15 * 1024**3)


# =========================================================================
# Create Remnawave user (after subscription creation)
# =========================================================================

async def create_remnawave_user(
    telegram_id: int,
    uuid: str,
    subscription_end: datetime,
    tariff: str = "basic",
) -> None:
    """
    Create user in Remnawave with appropriate traffic limit.
    Safe to call — logs errors but never raises.
    """
    if not config.REMNAWAVE_ENABLED:
        return

    try:
        traffic_limit = _traffic_limit_for_tariff(tariff)

        # Ensure subscription_end is UTC
        if subscription_end.tzinfo is None:
            subscription_end = subscription_end.replace(tzinfo=timezone.utc)

        result = await remnawave_api.create_user(
            username=str(telegram_id),
            short_uuid=uuid,
            traffic_limit_bytes=traffic_limit,
            expire_at=subscription_end,
        )
        if result:
            # Save the UUID that Remnawave uses for API lookups
            # Prefer their 'uuid' field, fallback to our shortUuid
            rmn_uuid = result.get("uuid") or result.get("shortUuid") or uuid
            await database.set_remnawave_uuid(telegram_id, rmn_uuid)
            await database.reset_traffic_notification_flags(telegram_id)
            logger.info(
                "REMNAWAVE_USER_CREATED: tg=%s rmn_uuid=%s our_uuid=%s limit=%d tariff=%s",
                telegram_id, rmn_uuid[:8], uuid[:8], traffic_limit, tariff,
            )
        else:
            logger.warning("REMNAWAVE_USER_CREATE_FAILED: tg=%s", telegram_id)
    except Exception as e:
        logger.exception("REMNAWAVE_USER_CREATE_ERROR: tg=%s error=%s", telegram_id, e)


def create_remnawave_user_bg(
    telegram_id: int,
    uuid: str,
    subscription_end: datetime,
    tariff: str = "basic",
) -> None:
    """Fire-and-forget version of create_remnawave_user."""
    _fire_and_forget(create_remnawave_user(telegram_id, uuid, subscription_end, tariff))


# =========================================================================
# Delete Remnawave user (on subscription expiry/revoke)
# =========================================================================

async def delete_remnawave_user(telegram_id: int) -> None:
    """
    Delete user from Remnawave and clear local UUID.
    Safe to call — logs errors but never raises.
    """
    if not config.REMNAWAVE_ENABLED:
        return

    try:
        uuid = await database.get_remnawave_uuid(telegram_id)
        if not uuid:
            return

        await remnawave_api.delete_user(uuid)
        await database.clear_remnawave_uuid(telegram_id)
        logger.info("REMNAWAVE_USER_DELETED: tg=%s uuid=%s", telegram_id, uuid[:8])
    except Exception as e:
        logger.exception("REMNAWAVE_USER_DELETE_ERROR: tg=%s error=%s", telegram_id, e)


def delete_remnawave_user_bg(telegram_id: int) -> None:
    """Fire-and-forget version of delete_remnawave_user."""
    _fire_and_forget(delete_remnawave_user(telegram_id))


# =========================================================================
# Update Remnawave user on renewal (reset traffic, update expiry)
# =========================================================================

async def renew_remnawave_user(
    telegram_id: int,
    subscription_end: datetime,
    tariff: str = "basic",
) -> None:
    """
    Update Remnawave user on subscription renewal:
    - Reset traffic counter to 0
    - Set new traffic limit based on tariff
    - Update expiry date
    - Reset notification flags
    """
    if not config.REMNAWAVE_ENABLED:
        return

    try:
        uuid = await database.get_remnawave_uuid(telegram_id)
        if not uuid:
            # User might not have a Remnawave account yet — create one
            sub = await database.get_subscription(telegram_id)
            xray_uuid = sub.get("uuid") if sub else None
            if xray_uuid:
                await create_remnawave_user(telegram_id, xray_uuid, subscription_end, tariff)
            return

        if subscription_end.tzinfo is None:
            subscription_end = subscription_end.replace(tzinfo=timezone.utc)

        traffic_limit = _traffic_limit_for_tariff(tariff)

        # Reset traffic counter
        await remnawave_api.reset_user_traffic(uuid)

        # Update limit + expiry + reactivate
        await remnawave_api.update_user(
            uuid,
            traffic_limit_bytes=traffic_limit,
            status="ACTIVE",
            expire_at=subscription_end,
        )

        await database.reset_traffic_notification_flags(telegram_id)
        logger.info(
            "REMNAWAVE_USER_RENEWED: tg=%s uuid=%s limit=%d tariff=%s",
            telegram_id, uuid[:8], traffic_limit, tariff,
        )
    except Exception as e:
        logger.exception("REMNAWAVE_USER_RENEW_ERROR: tg=%s error=%s", telegram_id, e)


def renew_remnawave_user_bg(
    telegram_id: int,
    subscription_end: datetime,
    tariff: str = "basic",
) -> None:
    """Fire-and-forget version of renew_remnawave_user."""
    _fire_and_forget(renew_remnawave_user(telegram_id, subscription_end, tariff))


# =========================================================================
# Add traffic after purchase
# =========================================================================

async def add_traffic(telegram_id: int, gb_amount: int) -> Optional[dict]:
    """
    Add purchased GB to user's traffic limit in Remnawave.
    Returns new traffic stats or None on error.
    """
    if not config.REMNAWAVE_ENABLED:
        return None

    try:
        uuid = await database.get_remnawave_uuid(telegram_id)
        if not uuid:
            logger.warning("REMNAWAVE_ADD_TRAFFIC: no uuid for tg=%s", telegram_id)
            return None

        # Get current limit
        traffic = await remnawave_api.get_user_traffic(uuid)
        if not traffic:
            logger.warning("REMNAWAVE_ADD_TRAFFIC: can't get traffic for tg=%s", telegram_id)
            return None

        current_limit = traffic["trafficLimitBytes"]
        added_bytes = gb_amount * 1024 * 1024 * 1024
        new_limit = current_limit + added_bytes

        result = await remnawave_api.update_user(uuid, traffic_limit_bytes=new_limit)
        if result:
            await database.reset_traffic_notification_flags(telegram_id)
            logger.info(
                "REMNAWAVE_TRAFFIC_ADDED: tg=%s +%dGB, old_limit=%d, new_limit=%d",
                telegram_id, gb_amount, current_limit, new_limit,
            )
            return {
                "usedTrafficBytes": traffic["usedTrafficBytes"],
                "trafficLimitBytes": new_limit,
            }
        return None
    except Exception as e:
        logger.exception("REMNAWAVE_ADD_TRAFFIC_ERROR: tg=%s error=%s", telegram_id, e)
        return None
