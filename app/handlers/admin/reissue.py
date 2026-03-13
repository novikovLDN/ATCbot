"""
Admin reissue handlers: /reissue_key, bulk reissue, grant/revoke access.
"""
import logging
import asyncio

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.states import AdminGrantAccess, AdminRevokeAccess
from app.handlers.admin.keyboards import (
    get_admin_grant_days_keyboard,
    get_admin_user_keyboard,
    get_admin_user_keyboard_processing,
    get_admin_back_keyboard,
)
from app.handlers.common.utils import safe_edit_text, get_reissue_lock, get_reissue_notification_text
from app.handlers.common.keyboards import get_reissue_notification_keyboard

admin_reissue_router = Router()
logger = logging.getLogger(__name__)


@admin_reissue_router.message(Command("reissue_key"))
async def cmd_reissue_key(message: Message):
    """Перевыпустить VPN-ключ для пользователя (только для админа)"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized reissue_key attempt by user {message.from_user.id}")
        await message.answer("Нет доступа")
        return
    
    try:
        # Парсим команду: /reissue_key <telegram_id>
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer("Использование: /reissue_key <telegram_id>")
            return
        
        try:
            target_telegram_id = int(parts[1])
        except ValueError:
            await message.answer("Неверный формат telegram_id. Используйте число.")
            return
        
        admin_telegram_id = message.from_user.id
        
        # Атомарно перевыпускаем ключ
        result = await database.reissue_vpn_key_atomic(target_telegram_id, admin_telegram_id)
        new_vpn_key, old_vpn_key = result
        
        if new_vpn_key is None:
            await message.answer(f"❌ Не удалось перевыпустить ключ для пользователя {target_telegram_id}.\nВозможные причины:\n- Нет активной подписки\n- Ошибка создания VPN-ключа")
            return
        
        # Уведомляем пользователя
        try:
            user_text = get_reissue_notification_text(new_vpn_key)
            keyboard = get_reissue_notification_keyboard()
            await message.bot.send_message(target_telegram_id, user_text, reply_markup=keyboard, parse_mode="HTML")
            logging.info(f"Reissue notification sent to user {target_telegram_id}")
        except Exception as e:
            logging.error(f"Error sending reissue notification to user {target_telegram_id}: {e}")
            await message.answer(f"✅ Ключ перевыпущен, но не удалось отправить уведомление пользователю: {e}")
            return
        
        await message.answer(
            f"✅ VPN-ключ успешно перевыпущен для пользователя {target_telegram_id}\n\n"
            f"Старый ключ: <code>{old_vpn_key[:20]}...</code>\n"
            f"Новый ключ: <code>{new_vpn_key}</code>",
            parse_mode="HTML"
        )
        logging.info(f"VPN key reissued for user {target_telegram_id} by admin {admin_telegram_id}")
        
    except Exception as e:
        logging.exception(f"Error in cmd_reissue_key: {e}")
        await message.answer("Ошибка при перевыпуске ключа. Проверь логи.")
