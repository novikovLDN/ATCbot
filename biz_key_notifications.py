"""
Worker: уведомления за 30 мин до истечения бизнес-клиентских ключей.

Запускается как фоновая задача, проверяет каждые 60 секунд.
"""
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import database

logger = logging.getLogger(__name__)

_BIZ_KEY_NOTIFIER_STARTED = False
_BIZ_KEY_NOTIFIER_LOCK = asyncio.Lock()

CHECK_INTERVAL_SECONDS = 60  # проверяем раз в минуту


async def start_biz_key_notifier(bot: Bot):
    """Запустить фоновый worker уведомлений о бизнес-ключах."""
    global _BIZ_KEY_NOTIFIER_STARTED
    async with _BIZ_KEY_NOTIFIER_LOCK:
        if _BIZ_KEY_NOTIFIER_STARTED:
            return
        _BIZ_KEY_NOTIFIER_STARTED = True

    logger.info("BIZ_KEY_NOTIFIER: started")
    while True:
        try:
            await _check_expiring_keys(bot)
        except Exception as e:
            logger.exception(f"BIZ_KEY_NOTIFIER error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def _check_expiring_keys(bot: Bot):
    """Проверить ключи, истекающие в ближайшие 30 минут."""
    if not database.DB_READY:
        return

    keys = await database.get_biz_keys_expiring_soon(minutes_before=30)
    for key in keys:
        try:
            await _send_expiry_notification(bot, key)
            await database.mark_biz_key_notified(key["id"])
        except Exception as e:
            logger.warning(
                f"BIZ_KEY_NOTIFIER: failed to notify owner={key['owner_telegram_id']}, "
                f"key_id={key['id']}: {e}"
            )


async def _send_expiry_notification(bot: Bot, key: dict):
    """Отправить уведомление владельцу о скором истечении ключа."""
    owner_id = key["owner_telegram_id"]
    name = key["client_name"] or f"Ключ #{key['id']}"
    key_id = key["id"]

    now = datetime.now(timezone.utc)
    expires_at = key["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    delta = expires_at - now
    minutes_left = max(0, int(delta.total_seconds() // 60))

    text = (
        f"⏰ <b>{name}</b> — осталось {minutes_left} мин\n\n"
        f"Ключ клиента скоро истечёт. Продлить или завершить?"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ Продлить", callback_data=f"biz_extend:{key_id}")],
        [InlineKeyboardButton(text="👌 Хорошо", callback_data=f"biz_key:{key_id}")],
    ])

    await bot.send_message(
        chat_id=owner_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    logger.info(f"BIZ_KEY_NOTIF_SENT: owner={owner_id}, key={key_id}, minutes_left={minutes_left}")
