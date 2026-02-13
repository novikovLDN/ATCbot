"""
User commands: /help, /instruction, /info
"""
import logging

from aiogram import Router, Bot
from aiogram.types import Message
from aiogram.filters import Command

from app.handlers.common.guards import ensure_db_ready_message
from app.handlers.common.screens import (
    _open_support_screen,
    _open_instruction_screen,
    _open_about_screen,
)

user_router = Router()
logger = logging.getLogger(__name__)


@user_router.message(Command("help"))
async def cmd_help(message: Message, bot: Bot):
    """Обработчик команды /help — открывает экран поддержки"""
    if not await ensure_db_ready_message(message):
        return
    await _open_support_screen(message, bot)


@user_router.message(Command("instruction"))
async def cmd_instruction(message: Message, bot: Bot):
    """Обработчик команды /instruction — открывает экран инструкции"""
    if not await ensure_db_ready_message(message):
        return
    await _open_instruction_screen(message, bot)


@user_router.message(Command("info"))
async def cmd_info(message: Message, bot: Bot):
    """Обработчик команды /info — открывает экран «О сервисе»"""
    if not await ensure_db_ready_message(message):
        return
    await _open_about_screen(message, bot)
