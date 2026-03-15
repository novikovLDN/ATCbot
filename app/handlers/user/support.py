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


@user_router.message(Command("help"))
async def cmd_help(message: Message, bot: Bot):
    """Обработчик команды /help — прямая ссылка на поддержку"""
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
    await message.answer(
        i18n_get_text(language, "main.support_text", "support_text"),
        reply_markup=keyboard,
    )


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
