"""
User command: /referral
"""
import logging

from aiogram import Router
from aiogram.types import Message, Bot
from aiogram.filters import Command

from app.handlers.common.guards import ensure_db_ready_message
from app.handlers.common.screens import _open_referral_screen

user_router = Router()
logger = logging.getLogger(__name__)


@user_router.message(Command("referral"))
async def cmd_referral(message: Message, bot: Bot):
    """Обработчик команды /referral — открывает экран программы лояльности"""
    if not await ensure_db_ready_message(message):
        return
    await _open_referral_screen(message, bot)
