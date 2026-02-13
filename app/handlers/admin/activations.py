"""
Admin activations handlers: /pending_activations, activation inspection.
"""
import logging
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_activations_router = Router()
logger = logging.getLogger(__name__)


@admin_activations_router.message(Command("pending_activations"))
async def cmd_pending_activations(message: Message):
    """Показать подписки с отложенной активацией (только для админа)"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized pending_activations attempt by user {message.from_user.id}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        return
    
    if not database.DB_READY:
        await message.answer("❌ База данных недоступна")
        return
    
    try:
        pool = await database.get_pool()
        if pool is None:
            await message.answer("❌ Не удалось подключиться к базе данных")
            return
        
        async with pool.acquire() as conn:
            # Получаем общее количество pending подписок
            total_count = await conn.fetchval(
                "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
            ) or 0
            
            # Получаем топ-5 старейших pending подписок
            oldest_pending = await conn.fetch(
                """SELECT id, telegram_id, activation_attempts, last_activation_error, activated_at
                   FROM subscriptions
                   WHERE activation_status = 'pending'
                   ORDER BY COALESCE(activated_at, '1970-01-01'::timestamp) ASC
                   LIMIT 5"""
            )
            
            # Формируем сообщение
            text_lines = [
                "⏳ **ОТЛОЖЕННЫЕ АКТИВАЦИИ VPN**\n",
                f"Всего pending подписок: **{total_count}**\n"
            ]
            
            if total_count == 0:
                text_lines.append("✅ Нет подписок с отложенной активацией")
            else:
                if oldest_pending:
                    text_lines.append("\n**Топ-5 старейших:**\n")
                    for idx, sub_row in enumerate(oldest_pending, 1):
                        subscription_id = sub_row["id"]
                        telegram_id = sub_row["telegram_id"]
                        attempts = sub_row["activation_attempts"]
                        error = sub_row.get("last_activation_error") or "N/A"
                        pending_since = sub_row.get("activated_at")
                        
                        if pending_since:
                            if isinstance(pending_since, str):
                                pending_since = datetime.fromisoformat(pending_since)
                            pending_since_str = pending_since.strftime("%d.%m.%Y %H:%M")
                        else:
                            pending_since_str = "N/A"
                        
                        error_preview = error[:50] + "..." if error and len(error) > 50 else error
                        
                        text_lines.append(
                            f"{idx}. ID: `{subscription_id}` | "
                            f"User: `{telegram_id}`\n"
                            f"   Попыток: {attempts} | "
                            f"С: {pending_since_str}\n"
                            f"   Ошибка: `{error_preview}`\n"
                        )
                else:
                    text_lines.append("\nНет данных о старейших подписках")
            
            text = "\n".join(text_lines)
            await message.answer(text, parse_mode="Markdown")
            
    except Exception as e:
        logger.exception(f"Error in cmd_pending_activations: {e}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.data_fetch", error=str(e)[:100], default=f"❌ Ошибка при получении данных: {str(e)[:100]}"))
