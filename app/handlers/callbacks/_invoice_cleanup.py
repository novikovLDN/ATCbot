"""Общее для платёжных экранов: автоудаление инвойса.

Инвойс живёт 15 минут (config.INVOICE_TIMEOUT_SECONDS). Сообщение с ним
нужно убрать по истечении срока, иначе человек нажмёт на просроченную
кнопку и получит ошибку провайдера вместо понятного объяснения.

Функция вынесена отдельно, потому что её используют все три группы
платёжных экранов — оплата с баланса, внешние провайдеры и пополнение.
Без своего модуля они тянули бы друг друга по кругу.
"""
import asyncio
import logging

import config
from aiogram import Bot
from aiogram.types import Message

logger = logging.getLogger(__name__)

INVOICE_TIMEOUT = config.INVOICE_TIMEOUT_SECONDS  # 15 минут


async def _schedule_invoice_deletion(bot: Bot, chat_id: int, invoice_message: Message, timeout: int = INVOICE_TIMEOUT):
    """Удаляет сообщение с инвойсом через timeout секунд."""
    try:
        await asyncio.sleep(timeout)
        await bot.delete_message(chat_id=chat_id, message_id=invoice_message.message_id)
        logger.info(f"INVOICE_EXPIRED: deleted invoice message_id={invoice_message.message_id} chat_id={chat_id}")
    except Exception as e:
        logger.debug(f"Failed to delete expired invoice: chat_id={chat_id}, error={e}")
