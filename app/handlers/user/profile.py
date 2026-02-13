"""
User command: /profile
"""
import logging

import database
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_message
from app.handlers.common.screens import show_profile

user_router = Router()
logger = logging.getLogger(__name__)


@user_router.message(Command("profile"))
async def cmd_profile(message: Message):
    """Обработчик команды /profile"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_message(message):
        return
    
    telegram_id = message.from_user.id
    user = await database.get_user(telegram_id)
    
    if not user:
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.start_command", "error_start_command"))
        return
    
    language = await resolve_user_language(telegram_id)
    await show_profile(message, language)
