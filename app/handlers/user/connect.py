"""
User commands: /connect, /white, /main.
"""
import logging

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from app.handlers.common.keyboards import get_connect_keyboard, get_main_menu_keyboard
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
        parse_mode="HTML",
        reply_markup=get_connect_keyboard(language),
    )


@user_router.message(Command("white"))
async def cmd_white(message: Message):
    """Показать экран «Мой трафик»."""
    if message.chat.type != "private":
        return

    from app.handlers.traffic import show_traffic_info_message
    await show_traffic_info_message(message)


@user_router.message(Command("main"))
async def cmd_main(message: Message):
    """Вернуться на главный экран."""
    if message.chat.type != "private":
        return

    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)

    from app.handlers.callbacks.navigation import _get_main_text
    text = await _get_main_text(telegram_id, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)
    await message.answer(text, reply_markup=keyboard)
