"""
Payment command: /buy
"""
import logging

from aiogram import Router, Bot
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.handlers.common.guards import ensure_db_ready_message

payments_router = Router()
logger = logging.getLogger(__name__)


@payments_router.message(Command("buy"))
async def cmd_buy(message: Message, state: FSMContext, bot: Bot):
    """Обработчик команды /buy — открывает экран покупки"""
    if not await ensure_db_ready_message(message):
        return
    from app.handlers.common.screens import _open_buy_screen
    await _open_buy_screen(message, bot, state)
