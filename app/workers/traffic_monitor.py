"""
Background worker: check traffic usage and send threshold notifications.

Runs every 5 minutes. Gated by REMNAWAVE_ENABLED and DB_READY.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.services import remnawave_api
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.utils.telegram_safe import safe_send_message

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
            # Порог должен быть строго меньше лимита юзера — иначе он
            # триггерится СРАЗУ после активации: у trial'а лимит 500 МБ,
            # но пороги 8/5/3/1 ГБ все ≥ 500 МБ, поэтому за первые
            # 30 минут летели 6 уведомлений подряд с текстом «купите
            # дополнительный трафик». Строгое неравенство также
            # гарантирует что порог 500 МБ не сработает для юзера
            # с лимитом ровно 500 МБ на моменте активации.
            # Особый случай: порог 0 ГБ (закончился трафик) — всегда
            # актуален, любой юзер должен узнать что доступ отключился.
            if threshold_bytes > 0 and threshold_bytes >= limit:
                continue
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
        # safe_send_message: 403 marks the user unreachable, a short 429 is
        # retried once. The caller still sets the flag after the attempt (one
        # try per threshold — no per-5-min retries to a user who blocked the bot).
        sent = await safe_send_message(bot, telegram_id, text, reply_markup=kb, parse_mode="HTML")
        if sent:
            logger.info("TRAFFIC_NOTIFICATION_SENT: tg=%s flag=%s remaining=%d", telegram_id, flag_key, remaining_bytes)
        else:
            logger.warning("TRAFFIC_NOTIFICATION_NOT_DELIVERED: tg=%s flag=%s", telegram_id, flag_key)
    except Exception as e:
        logger.warning("TRAFFIC_NOTIFICATION_FAIL: tg=%s %s: %s", telegram_id, type(e).__name__, e)


async def traffic_monitor_iteration(bot: Bot) -> None:
    """Single iteration: check all active Remnawave users."""
    users = await database.get_active_remnawave_users()
    if not users:
        return

    for user in users:
        telegram_id = user["telegram_id"]
        # Prefer numeric id (3.x fast-path без UUID→id auto-resolve).
        # Fallback на uuid для legacy юзеров без забэкфильнутого id.
        panel_ref = user.get("remnawave_id") or user["remnawave_uuid"]
        await _check_user_traffic(bot, telegram_id, panel_ref)
        await asyncio.sleep(0.2)  # Rate limit API calls


async def traffic_monitor_task(bot: Bot) -> None:
    """Main loop — runs every INTERVAL_SECONDS."""
    logger.info("TRAFFIC_MONITOR: starting (interval=%ds)", INTERVAL_SECONDS)
    from app.core import runtime_health  # dashboard liveness (in-memory)
    # One iteration walks every user with a panel UUID at 0.2 s per call,
    # so it can run long: a generous interval keeps "stale" meaningful.
    runtime_health.register("traffic_monitor", interval_s=INTERVAL_SECONDS + 1800, initial_delay_s=30)
    await asyncio.sleep(30)  # Initial delay

    while True:
        try:
            if not database.DB_READY or not config.REMNAWAVE_ENABLED:
                runtime_health.record("traffic_monitor", "skipped")
                await asyncio.sleep(INTERVAL_SECONDS)
                continue

            await traffic_monitor_iteration(bot)
            runtime_health.beat("traffic_monitor")
        except asyncio.CancelledError:
            logger.info("TRAFFIC_MONITOR: cancelled")
            break
        except Exception as e:
            logger.error("TRAFFIC_MONITOR_ERROR: %s: %s", type(e).__name__, e)
            runtime_health.fail("traffic_monitor", e)

        await asyncio.sleep(INTERVAL_SECONDS)
