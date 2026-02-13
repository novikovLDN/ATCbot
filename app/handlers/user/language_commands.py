"""
User command: /language
"""
import logging

from aiogram import Router, Bot
from aiogram.types import Message
from aiogram.filters import Command

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_message
from app.handlers.common.keyboards import get_language_keyboard

user_router = Router()
logger = logging.getLogger(__name__)


@user_router.message(Command("language"))
async def cmd_language(message: Message, bot: Bot):
    """Обработчик команды /language — открывает экран выбора языка"""
    if not await ensure_db_ready_message(message):
        return
    
    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)
    
    text = i18n_get_text(language, "lang.select")
    await bot.send_message(
        message.chat.id,
        text,
        reply_markup=get_language_keyboard(language)
    )
