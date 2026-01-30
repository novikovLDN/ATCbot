# -*- coding: utf-8 -*-
"""
Message lifecycle guard: avoid edit_text on photo messages, unified navigation.
- is_photo_message(message) -> bool
- safe_replace_screen(callback, text, reply_markup, ...): delete if photo, then send or edit.
"""
import logging
from typing import Optional, Callable, Awaitable

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


def is_photo_message(message: Message) -> bool:
    """True if the message contains a photo (sendPhoto)."""
    if not message:
        return False
    photo = getattr(message, "photo", None)
    return bool(photo and len(photo) > 0)


async def safe_replace_screen(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    *,
    send_func: Optional[Callable[[], Awaitable[None]]] = None,
    screen_name: str = "?",
    parse_mode: Optional[str] = None,
) -> None:
    """
    Replace current screen: if message has photo → delete then send (send_func or send_message);
    else → edit_text. Use for all transitions from screens that may be photo (main, profile, loyalty).
    """
    bot = callback.bot
    msg = callback.message
    chat_id = msg.chat.id

    if is_photo_message(msg):
        try:
            await msg.delete()
            logger.info(
                "SCREEN_MESSAGE_DELETED [reason=navigation, screen=%s]",
                screen_name,
            )
        except Exception as e:
            logger.debug("Message delete failed (non-critical): %s", e)
        if send_func is not None:
            await send_func()
        else:
            await bot.send_message(
                chat_id,
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
    else:
        try:
            await msg.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                return
            if any(
                k in err
                for k in [
                    "message to edit not found",
                    "message can't be edited",
                    "chat not found",
                    "there is no text in the message to edit",
                ]
            ):
                await bot.send_message(
                    chat_id,
                    text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                logger.info(
                    "SCREEN_MESSAGE_DELETED [reason=edit_fallback, screen=%s]",
                    screen_name,
                )
            else:
                raise
