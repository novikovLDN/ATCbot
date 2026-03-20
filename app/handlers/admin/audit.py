"""
Admin audit handlers: /admin_audit, system inspection.
"""
import logging
from datetime import datetime, timezone

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_audit_router = Router()
logger = logging.getLogger(__name__)


@admin_audit_router.message(Command("admin_audit"))
async def cmd_admin_audit(message: Message):
    """Показать последние записи audit_log (только для админа)"""
    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        logging.warning(f"Unauthorized admin_audit attempt by user {message.from_user.id}")
        await message.answer("Недостаточно прав")
        return
    
    try:
        # Получаем последние 10 записей из audit_log
        audit_logs = await database.get_last_audit_logs(limit=10)
        
        if not audit_logs:
            await message.answer("Аудит пуст. Действий не зафиксировано.")
            return
        
        # Формируем сообщение
        lines = ["📜 Audit Log", ""]
        
        for log in audit_logs:
            # Форматируем дату и время
            created_at = log["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif isinstance(created_at, datetime):
                pass
            else:
                created_at = datetime.now(timezone.utc)
            
            created_str = created_at.strftime("%Y-%m-%d %H:%M")
            
            lines.append(f"🕒 {created_str}")
            lines.append(f"Действие: {log['action']}")
            lines.append(f"Админ: {log['telegram_id']}")
            
            if log['target_user']:
                lines.append(f"Пользователь: {log['target_user']}")
            else:
                lines.append("Пользователь: —")
            
            if log['details']:
                lines.append(f"Детали: {log['details']}")
            else:
                lines.append("Детали: —")
            
            lines.append("")
            lines.append("⸻")
            lines.append("")
        
        # Убираем последний разделитель
        if lines[-1] == "" and lines[-2] == "⸻":
            lines = lines[:-2]
        
        text = "\n".join(lines)
        
        # Проверяем лимит Telegram (4096 символов на сообщение)
        if len(text) > 4000:
            # Если текст слишком длинный, обрезаем до первых записей
            # Попробуем уменьшить количество записей
            audit_logs = await database.get_last_audit_logs(limit=5)
            lines = ["📜 Audit Log", ""]
            
            for log in audit_logs:
                created_at = log["created_at"]
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif isinstance(created_at, datetime):
                    pass
                else:
                    created_at = datetime.now(timezone.utc)
                
                created_str = created_at.strftime("%Y-%m-%d %H:%M")
                
                lines.append(f"🕒 {created_str}")
                lines.append(f"Действие: {log['action']}")
                lines.append(f"Админ: {log['telegram_id']}")
                
                if log['target_user']:
                    lines.append(f"Пользователь: {log['target_user']}")
                else:
                    lines.append("Пользователь: —")
                
                if log['details']:
                    # Обрезаем детали если они слишком длинные
                    details = log['details']
                    if len(details) > 200:
                        details = details[:200] + "..."
                    lines.append(f"Детали: {details}")
                else:
                    lines.append("Детали: —")
                
                lines.append("")
                lines.append("⸻")
                lines.append("")
            
            if lines[-1] == "" and lines[-2] == "⸻":
                lines = lines[:-2]
            
            text = "\n".join(lines)
        
        await message.answer(text)
        logging.info(f"Admin audit log viewed by admin {message.from_user.id}")
        
    except Exception as e:
        logging.exception(f"Error in cmd_admin_audit: {e}")
        await message.answer("Ошибка при получении audit log. Проверь логи.")


@admin_audit_router.callback_query(F.data == "admin:audit")
async def callback_admin_audit(callback: CallbackQuery):
    """Раздел Аудит (переиспользование логики /admin_audit)"""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Получаем последние 10 записей из audit_log
        audit_logs = await database.get_last_audit_logs(limit=10)
        
        if not audit_logs:
            await callback.answer("Аудит пуст. Действий не зафиксировано.", show_alert=True)
            return
        
        # Формируем сообщение
        lines = ["📜 Audit Log", ""]
        
        for log in audit_logs:
            # Форматируем дату и время
            created_at = log["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif isinstance(created_at, datetime):
                pass
            else:
                created_at = datetime.now(timezone.utc)
            
            created_str = created_at.strftime("%Y-%m-%d %H:%M")
            
            lines.append(f"🕒 {created_str}")
            lines.append(f"Действие: {log['action']}")
            lines.append(f"Админ: {log['telegram_id']}")
            
            if log['target_user']:
                lines.append(f"Пользователь: {log['target_user']}")
            else:
                lines.append("Пользователь: —")
            
            if log['details']:
                lines.append(f"Детали: {log['details']}")
            else:
                lines.append("Детали: —")
            
            lines.append("")
            lines.append("⸻")
            lines.append("")
        
        # Убираем последний разделитель
        if lines[-1] == "" and lines[-2] == "⸻":
            lines = lines[:-2]
        
        text = "\n".join(lines)
        
        # Проверяем лимит Telegram (4096 символов на сообщение)
        if len(text) > 4000:
            # Если текст слишком длинный, обрезаем до первых записей
            # Попробуем уменьшить количество записей
            audit_logs = await database.get_last_audit_logs(limit=5)
            lines = ["📜 Audit Log", ""]
            
            for log in audit_logs:
                created_at = log["created_at"]
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif isinstance(created_at, datetime):
                    pass
                else:
                    created_at = datetime.now(timezone.utc)
                
                created_str = created_at.strftime("%Y-%m-%d %H:%M")
                
                lines.append(f"🕒 {created_str}")
                lines.append(f"Действие: {log['action']}")
                lines.append(f"Админ: {log['telegram_id']}")
                
                if log['target_user']:
                    lines.append(f"Пользователь: {log['target_user']}")
                else:
                    lines.append("Пользователь: —")
                
                if log['details']:
                    # Обрезаем детали если они слишком длинные
                    details = log['details']
                    if len(details) > 200:
                        details = details[:200] + "..."
                    lines.append(f"Детали: {details}")
                else:
                    lines.append("Детали: —")
                
                lines.append("")
                lines.append("⸻")
                lines.append("")
            
            if lines[-1] == "" and lines[-2] == "⸻":
                lines = lines[:-2]
            
            text = "\n".join(lines)
        
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_audit: {e}")
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.dashboard_data"), show_alert=True)
