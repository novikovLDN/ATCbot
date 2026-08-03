"""
User commands: /help, /instruction, /info
"""
import logging

from aiogram import Router, Bot
from aiogram.types import Message
from aiogram.filters import Command

from app.handlers.common.guards import ensure_db_ready_message
from app.handlers.common.screens import (
    _open_help_screen,
    _open_about_screen,
)
# Локальный импорт внутри обработчика не нужен: connect_guide тянет
# screens, а screens — нет, кольца не будет.
from app.handlers.callbacks.connect_guide import _open_connect_screen

user_router = Router()
logger = logging.getLogger(__name__)


@user_router.message(Command("help"))
async def cmd_help(message: Message, bot: Bot):
    """Обработчик команды /help — открывает экран помощи (FAQ / Инструкции / Оператор) с фото."""
    if message.chat.type != "private":
        return
    if not await ensure_db_ready_message(message):
        return
    await _open_help_screen(message, bot)


@user_router.message(Command("instruction"))
async def cmd_instruction(message: Message, bot: Bot):
    """Команда /instruction — открывает пошаговую установку.

    Раньше вела на отдельный экран-заглушку с кнопкой в мини-приложение.
    Инструкций в боте было две, и та, что в меню (пошаговая установка),
    полезнее: выбор устройства, ссылки на приложения, ключи, QR. Кнопку в
    мини-приложение перенесли туда же, так что ничего не потеряно.
    """
    if message.chat.type != "private":
        return
    if not await ensure_db_ready_message(message):
        return
    await _open_connect_screen(message, bot)


@user_router.message(Command("info"))
async def cmd_info(message: Message, bot: Bot):
    """Обработчик команды /info — открывает экран «О сервисе»"""
    if message.chat.type != "private":
        return
    if not await ensure_db_ready_message(message):
        return
    await _open_about_screen(message, bot)
