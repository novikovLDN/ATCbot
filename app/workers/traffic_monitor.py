"""
Background worker: check traffic usage and send threshold notifications.

Runs every 5 minutes. Gated by REMNAWAVE_ENABLED and DB_READY.
"""
import asyncio
import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.services import remnawave_api
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300  # 5 minutes


def _format_bytes(b: int) -> str:
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} ГБ"
    if b >= 1024**2:
        return f"{b / 1024**2:.0f} МБ"
    return f"{b / 1024:.0f} КБ"


async def _check_user_traffic(bot: Bot, telegram_id: int, rmn_uuid: str) -> None:
    """Check traffic thresholds and send one-shot notifications."""
    try:
        traffic = await remnawave_api.get_user_traffic(rmn_uuid)
        if not traffic:
            logger.warning("TRAFFIC_CHECK_NO_DATA: tg=%s uuid=%s", telegram_id, rmn_uuid[:8] if rmn_uuid else "N/A")
            return

        used = traffic["usedTrafficBytes"]
        limit = traffic["trafficLimitBytes"]
        if limit <= 0:
            return

        remaining = max(0, limit - used)

        flags = await database.get_traffic_notification_flags(telegram_id)
        if not flags:
            return

        for threshold_bytes, flag_key in config.TRAFFIC_NOTIFY_THRESHOLDS:
            if remaining <= threshold_bytes and not flags.get(flag_key, False):
                await _send_traffic_notification(bot, telegram_id, remaining, flag_key)
                await database.set_traffic_notification_flag(telegram_id, flag_key)
                break  # One notification per iteration

    except Exception as e:
        logger.warning("TRAFFIC_CHECK_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


async def _send_traffic_notification(
    bot: Bot,
    telegram_id: int,
    remaining_bytes: int,
    flag_key: str,
) -> None:
    """Send traffic warning notification to user."""
    try:
        language = await resolve_user_language(telegram_id)

        if flag_key == "traffic_notified_0":
            text = i18n_get_text(language, "traffic.notify_zero")
        elif flag_key == "traffic_notified_500mb":
            text = i18n_get_text(language, "traffic.notify_500mb", remaining=_format_bytes(remaining_bytes))
        elif flag_key == "traffic_notified_1gb":
            text = i18n_get_text(language, "traffic.notify_1gb")
        elif flag_key == "traffic_notified_3gb":
            text = i18n_get_text(language, "traffic.notify_3gb", remaining=_format_bytes(remaining_bytes))
        elif flag_key == "traffic_notified_5gb":
            text = i18n_get_text(language, "traffic.notify_5gb", remaining=_format_bytes(remaining_bytes))
        else:
            text = i18n_get_text(language, "traffic.notify_8gb", remaining=_format_bytes(remaining_bytes))

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.buy_traffic_btn"),
                callback_data="buy_traffic",
            )],
        ])
        await bot.send_message(telegram_id, text, reply_markup=kb)
        logger.info("TRAFFIC_NOTIFICATION_SENT: tg=%s flag=%s remaining=%d", telegram_id, flag_key, remaining_bytes)
    except Exception as e:
        logger.warning("TRAFFIC_NOTIFICATION_FAIL: tg=%s %s: %s", telegram_id, type(e).__name__, e)


async def traffic_monitor_iteration(bot: Bot) -> None:
    """Single iteration: check all active Remnawave users."""
    users = await database.get_active_remnawave_users()
    if not users:
        return

    for user in users:
        telegram_id = user["telegram_id"]
        rmn_uuid = user["remnawave_uuid"]
        await _check_user_traffic(bot, telegram_id, rmn_uuid)
        await asyncio.sleep(0.2)  # Rate limit API calls


async def traffic_monitor_task(bot: Bot) -> None:
    """Main loop — runs every INTERVAL_SECONDS."""
    logger.info("TRAFFIC_MONITOR: starting (interval=%ds)", INTERVAL_SECONDS)
    await asyncio.sleep(30)  # Initial delay

    while True:
        try:
            if not database.DB_READY or not config.REMNAWAVE_ENABLED:
                await asyncio.sleep(INTERVAL_SECONDS)
                continue

            await traffic_monitor_iteration(bot)
        except asyncio.CancelledError:
            logger.info("TRAFFIC_MONITOR: cancelled")
            break
        except Exception as e:
            logger.error("TRAFFIC_MONITOR_ERROR: %s: %s", type(e).__name__, e)

        await asyncio.sleep(INTERVAL_SECONDS)
