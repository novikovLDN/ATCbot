"""
User command: /connect — открыть Mini App «Подключиться».
"""
import logging

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from app.handlers.common.keyboards import get_connect_keyboard
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

user_router = Router()
logger = logging.getLogger(__name__)


@user_router.message(Command("connect"))
async def cmd_connect(message: Message):
    """Отправить сообщение с кнопкой WebApp «Подключиться»."""
    if message.chat.type != "private":
        return
    language = await resolve_user_language(message.from_user.id)
    await message.answer(
        i18n_get_text(language, "connect.press_button"),
        reply_markup=get_connect_keyboard(),
    )
