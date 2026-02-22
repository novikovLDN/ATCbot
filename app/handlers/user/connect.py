"""
User command: /connect — открыть Mini App «Подключиться».
"""
import logging

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from app.handlers.common.keyboards import get_connect_keyboard

user_router = Router()
logger = logging.getLogger(__name__)


@user_router.message(Command("connect"))
async def cmd_connect(message: Message):
    """Отправить сообщение с кнопкой WebApp «Подключиться»."""
    await message.answer(
        "🚀 Нажмите кнопку ниже чтобы подключиться:",
        reply_markup=get_connect_keyboard(),
    )
