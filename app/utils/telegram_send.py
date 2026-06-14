"""
Telegram send helpers — длинные тексты с фото через split-fallback.

Если caption у photo превышает 1024 символа (Telegram-лимит по
entity-parsing), bot.send_photo вернёт `Bad Request: message caption is
too long`. Длинные broadcast-тексты с blockquote / expandable-блоками
часто пробивают этот лимит.

`send_with_long_caption_fallback` пробует обычный send_photo с caption,
а на caption_too_long делает split: фото — первым сообщением (без
caption), полный текст с разметкой и кнопками — вторым. Telegram-
лимит для send_message — 4096 символов.
"""

from __future__ import annotations

from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup


async def send_with_long_caption_fallback(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    photo_file_id: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "HTML",
) -> list[int]:
    """Send text (+ optional photo). Splits to 2 messages on caption_too_long.

    Returns:
        List of message_ids (1 or 2). Длина 2 — split-случай: [photo_id, text_id].
    """
    if not photo_file_id:
        m = await bot.send_message(
            chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode,
        )
        return [m.message_id]

    try:
        m = await bot.send_photo(
            chat_id,
            photo=photo_file_id,
            caption=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return [m.message_id]
    except TelegramBadRequest as e:
        if "caption is too long" not in (e.message or "").lower():
            raise
        # Split: фото без caption + текст с кнопками.
        photo_msg = await bot.send_photo(chat_id, photo=photo_file_id)
        text_msg = await bot.send_message(
            chat_id, text,
            reply_markup=reply_markup, parse_mode=parse_mode,
        )
        return [photo_msg.message_id, text_msg.message_id]
