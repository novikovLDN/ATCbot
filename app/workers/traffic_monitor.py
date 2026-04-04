"""
Traffic monitor worker — checks Remnawave traffic usage every 5 minutes
and sends notifications when thresholds are crossed.

Thresholds (from config.TRAFFIC_NOTIFY_THRESHOLDS):
    3 GB  → warning
    1 GB  → critical
    500 MB → urgent
    0     → traffic exhausted
"""
import asyncio
import logging
import time

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services import remnawave_api
from app.services.language_service import resolve_user_language

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300  # 5 minutes
BATCH_SIZE = 50
GB = 1024 * 1024 * 1024


def _format_bytes(b: int) -> str:
    """Format bytes to human-readable (GB or MB)."""
    if b >= GB:
        return f"{b / GB:.1f} \u0413\u0411"
    return f"{b / (1024 * 1024):.0f} \u041c\u0411"


async def _check_user_traffic(bot: Bot, telegram_id: int, remnawave_uuid: str) -> None:
    """Check a single user's traffic and send notification if threshold crossed."""
    try:
        traffic = await remnawave_api.get_user_traffic(remnawave_uuid)
        if not traffic:
            return

        used = traffic["usedTrafficBytes"]
        limit = traffic["trafficLimitBytes"]
        remaining = max(0, limit - used)

        # Get current notification flags
        flags = await database.get_traffic_notification_flags(telegram_id)
        if not flags:
            return  # User not found

        language = await resolve_user_language(telegram_id)

        # Check thresholds (from highest to lowest)
        for threshold_bytes, flag_key in config.TRAFFIC_NOTIFY_THRESHOLDS:
            if remaining <= threshold_bytes and not flags.get(flag_key, False):
                # Send notification
                await _send_traffic_notification(
                    bot, telegram_id, language, flag_key, remaining,
                )
                await database.set_traffic_notification_flag(telegram_id, flag_key)
                break  # Only send one notification per check

    except Exception as e:
        logger.warning("TRAFFIC_MONITOR: error checking user %s: %s", telegram_id, e)


async def _send_traffic_notification(
    bot: Bot,
    telegram_id: int,
    language: str,
    flag_key: str,
    remaining_bytes: int,
) -> None:
    """Send a traffic warning notification to user."""
    remaining_text = _format_bytes(remaining_bytes)

    # Map flag to i18n key
    key_map = {
        "3gb": "traffic.notify_3gb",
        "1gb": "traffic.notify_1gb",
        "500mb": "traffic.notify_500mb",
        "0": "traffic.notify_0",
    }
    i18n_key = key_map.get(flag_key, "traffic.notify_3gb")
    text = i18n_get_text(language, i18n_key, remaining=remaining_text)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.buy_button"),
            callback_data="buy_traffic",
        )],
    ])

    try:
        await bot.send_message(telegram_id, text, reply_markup=keyboard)
        logger.info(
            "TRAFFIC_NOTIFY: sent %s to user %s (remaining=%s)",
            flag_key, telegram_id, remaining_text,
        )
    except Exception as e:
        logger.warning("TRAFFIC_NOTIFY: failed to send to user %s: %s", telegram_id, e)


async def _provision_missing_users() -> int:
    """
    Auto-provision Remnawave accounts for existing active subscribers
    who don't have a remnawave_uuid yet.
    Returns number of users provisioned.
    """
    from app.services.remnawave_service import create_remnawave_user

    users = await database.get_active_users_without_remnawave()
    if not users:
        return 0

    provisioned = 0
    for user in users:
        telegram_id = user["telegram_id"]
        uuid = user["uuid"]
        expires_at = user["expires_at"]
        tariff = (user.get("subscription_type") or "basic").lower()

        try:
            await create_remnawave_user(telegram_id, uuid, expires_at, tariff)
            # Verify it actually worked (create_remnawave_user swallows errors)
            saved_uuid = await database.get_remnawave_uuid(telegram_id)
            if saved_uuid:
                provisioned += 1
                logger.info(
                    "TRAFFIC_PROVISION: created Remnawave for existing user %s (tariff=%s)",
                    telegram_id, tariff,
                )
            else:
                logger.warning("TRAFFIC_PROVISION: create returned no error but uuid not saved for %s", telegram_id)
        except Exception as e:
            logger.warning("TRAFFIC_PROVISION: failed for user %s: %s", telegram_id, e)

        # Rate limit
        if provisioned % BATCH_SIZE == 0:
            await asyncio.sleep(1)

    return provisioned


async def traffic_monitor_iteration(bot: Bot) -> int:
    """
    Single iteration:
    1. Auto-provision Remnawave for existing subscribers without it
    2. Check traffic usage for all active Remnawave users
    Returns number of users checked.
    """
    # Step 1: provision missing users
    try:
        provisioned = await _provision_missing_users()
        if provisioned > 0:
            logger.info("TRAFFIC_MONITOR: provisioned %d existing users", provisioned)
    except Exception as e:
        logger.warning("TRAFFIC_MONITOR: provisioning error: %s", e)

    # Step 2: check traffic for all Remnawave users
    users = await database.get_active_remnawave_users()
    if not users:
        return 0

    checked = 0
    for user in users:
        telegram_id = user["telegram_id"]
        uuid = user["remnawave_uuid"]
        await _check_user_traffic(bot, telegram_id, uuid)
        checked += 1
        # Rate limit API calls
        if checked % BATCH_SIZE == 0:
            await asyncio.sleep(1)

    return checked


async def traffic_monitor_task(bot: Bot) -> None:
    """
    Main worker loop. Runs every 5 minutes.
    Checks traffic usage for all active Remnawave users.
    """
    iteration = 0
    while True:
        try:
            if not config.REMNAWAVE_ENABLED:
                await asyncio.sleep(INTERVAL_SECONDS)
                continue

            if not database.DB_READY:
                await asyncio.sleep(30)
                continue

            iteration += 1
            start = time.time()
            checked = await traffic_monitor_iteration(bot)
            duration_ms = (time.time() - start) * 1000

            if checked > 0:
                logger.info(
                    "TRAFFIC_MONITOR: iteration=%d checked=%d duration=%.0fms",
                    iteration, checked, duration_ms,
                )
        except Exception as e:
            logger.exception("TRAFFIC_MONITOR: iteration=%d error=%s", iteration, e)
        finally:
            await asyncio.sleep(INTERVAL_SECONDS)
