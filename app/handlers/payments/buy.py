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
    """/buy — the same screen as «Продлить VPN» / «Купить VPN» (08 M13): a
    subscriber gets «Управление подпиской», anyone else the tariff screen."""
    if not await ensure_db_ready_message(message):
        return
    from app.handlers.payments.callbacks import open_buy_or_manage
    await open_buy_or_manage(message, bot, state)
