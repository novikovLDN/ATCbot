"""
User commands: /help, /instruction, /info
"""
import logging

from aiogram import Router, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from app.handlers.common.guards import ensure_db_ready_message
from app.handlers.common.screens import (
    _open_instruction_screen,
    _open_about_screen,
)
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

user_router = Router()
logger = logging.getLogger(__name__)

# Support screen photo.  file_id is bot-specific (uploaded via the prod
# bot); on send failure we degrade to a plain text message so /help
# never breaks.
SUPPORT_PHOTO_FILE_ID = "AgACAgQAAxkBAAFOS6RqBZnLNfSlXv_jvcyVPoUlHNGdwQACog1rGyLfMFCCfQnI4woaSAEAAwIAA3kAAzsE"


@user_router.message(Command("help"))
async def cmd_help(message: Message, bot: Bot):
    """Обработчик команды /help — экран поддержки с фото."""
    if message.chat.type != "private":
        return
    if not await ensure_db_ready_message(message):
        return
    language = await resolve_user_language(message.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "support.write_button"),
            url="https://t.me/Atlas_SupportSecurity"
        )],
    ])
    text = i18n_get_text(language, "main.support_text", "support_text")
    # /help is always a fresh message in reply to a typed command — no
    # screen-transition concern.  Send photo + caption; fall back to a
    # plain text message if the file_id can't be used.
    try:
        await message.answer_photo(
            SUPPORT_PHOTO_FILE_ID,
            caption=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("SUPPORT_PHOTO_FALLBACK_TEXT user=%s err=%s", message.from_user.id, e)
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@user_router.message(Command("instruction"))
async def cmd_instruction(message: Message, bot: Bot):
    """Обработчик команды /instruction — открывает экран инструкции"""
    if message.chat.type != "private":
        return
    if not await ensure_db_ready_message(message):
        return
    await _open_instruction_screen(message, bot)


@user_router.message(Command("info"))
async def cmd_info(message: Message, bot: Bot):
    """Обработчик команды /info — открывает экран «О сервисе»"""
    if message.chat.type != "private":
        return
    if not await ensure_db_ready_message(message):
        return
    await _open_about_screen(message, bot)
