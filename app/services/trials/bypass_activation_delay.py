"""Send «Обход подключён» notification exactly 5 minutes after trial activation.

Fast path — вызывается прямо из `callback_activate_trial`:
`asyncio.create_task(sleep(300) + _send_bypass_activated)`. Так уведомление
летит точно через 5 минут после клика юзера, не через 5–10 (worst case
scheduler'а с 5-минутным тиком).

Backup path — тот же сценарий покрыт scheduler'ом в trial_notifications.py
(inline-блок в `_process_single_trial_notification`). Если бот
перезапустится между активацией и sleep — task потеряется, scheduler
подхватит на ближайшем тике.

Идемпотентность: обновляем `trial_notif_bypass_activated_sent = TRUE`
через `WHERE flag = FALSE RETURNING id`. Кто выиграл race — тот и шлёт;
проигравший тихо выходит.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import database
from app import i18n
from app.services.language_service import resolve_user_language
from app.utils.telegram_safe import safe_send_message

logger = logging.getLogger(__name__)

DELAY_SECONDS = 300  # ровно 5 минут


def schedule_bypass_activated_notification(bot: Bot, telegram_id: int) -> None:
    """Fire-and-forget: планирует уведомление через 5 минут.

    Не блокирует активацию триала; не поднимает исключения к caller'у.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "schedule_bypass_activated_notification: no running loop, skipping user=%s",
            telegram_id,
        )
        return
    loop.create_task(_delayed_send(bot, telegram_id))


async def _delayed_send(bot: Bot, telegram_id: int) -> None:
    try:
        await asyncio.sleep(DELAY_SECONDS)
        await try_send_bypass_activated(bot, telegram_id)
    except Exception as e:
        logger.exception(
            "bypass_activated delayed task failed user=%s: %s", telegram_id, e,
        )


async def try_send_bypass_activated(bot: Bot, telegram_id: int) -> bool:
    """Idempotently шлёт «Обход подключён». Возвращает True если отправили,
    False если уже было или race проигран.

    Внутри:
      • acquire-lock через UPDATE ... WHERE flag=FALSE RETURNING id
        — атомарно, безопасно относительно scheduler'а
      • шлёт сообщение через safe_send_message (учитывает Forbidden/blocked)
      • если сообщение упало permanently — флаг остаётся TRUE (не ретраим
        зомби-юзеров бесконечно)
      • если flooded/timeout — оставляем флаг TRUE (лучше пропустить одно
        уведомление, чем спамить)
    """
    pool = await database.get_pool()
    if pool is None:
        logger.warning("bypass_activated: DB pool unavailable user=%s", telegram_id)
        return False

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """UPDATE subscriptions
                   SET trial_notif_bypass_activated_sent = TRUE
                   WHERE telegram_id = $1
                     AND source = 'trial'
                     AND status = 'active'
                     AND COALESCE(trial_notif_bypass_activated_sent, FALSE) = FALSE
                   RETURNING id""",
                telegram_id,
            )
        except Exception as e:
            # Если колонка ещё не создана миграцией — не падаем, просто
            # логируем. Уведомление придёт после накатки миграции через
            # scheduler.
            logger.warning(
                "bypass_activated: DB update failed user=%s: %s "
                "(migration 062 may not be applied yet)",
                telegram_id, e,
            )
            return False

    if not row:
        # Race lost или триала нет / не активен → тихий выход.
        logger.debug(
            "bypass_activated: skip user=%s (no active trial or already sent)",
            telegram_id,
        )
        return False

    language = await resolve_user_language(telegram_id)
    text = i18n.get_text(language, "trial.bypass_activated")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "trial.bypass_activated_btn_setup"),
            callback_data="bypass_setup_open",
        )],
        [InlineKeyboardButton(
            text=i18n.get_text(language, "trial.bypass_activated_btn_help"),
            callback_data="menu_help",
        )],
    ])

    sent = await safe_send_message(bot, telegram_id, text, reply_markup=keyboard)
    if sent is None:
        logger.info(
            "bypass_activated: user=%s permanently unreachable (Forbidden/blocked); "
            "flag stays TRUE to avoid retries",
            telegram_id,
        )
        return False

    logger.info(
        "bypass_activated_sent: user=%s at %s (path=delayed_task)",
        telegram_id, datetime.now(timezone.utc).isoformat(),
    )
    return True


__all__ = [
    "schedule_bypass_activated_notification",
    "try_send_bypass_activated",
    "DELAY_SECONDS",
]
