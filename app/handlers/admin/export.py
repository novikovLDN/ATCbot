"""
Admin export handlers: CSV export logic.
"""
import logging
import csv
import tempfile
import os
import asyncio
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.admin.keyboards import get_admin_export_keyboard, get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_export_router = Router()
logger = logging.getLogger(__name__)


def _generate_csv_file(data: list[dict], headers: list[str], key_mapping: dict[str, str]) -> str:
    """
    Synchronous CSV generator.
    Returns path to generated file.
    
    This function runs in a background thread to avoid blocking the event loop.
    """
    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.csv',
        delete=False,
        encoding='utf-8',
        newline=''
    ) as tmp_file:
        csv_file_path = tmp_file.name
        
        # Записываем CSV
        writer = csv.writer(tmp_file)
        writer.writerow(headers)
        
        for row in data:
            csv_row = []
            for header in headers:
                key = key_mapping[header]
                value = row.get(key)
                
                if key == "created_at" or key == "expires_at":
                    # Форматируем дату
                    if value:
                        if isinstance(value, datetime):
                            csv_row.append(value.strftime("%Y-%m-%d %H:%M:%S"))
                        elif isinstance(value, str):
                            csv_row.append(value)
                        else:
                            csv_row.append(str(value))
                    else:
                        csv_row.append("")
                elif key == "reminder_sent":
                    # Преобразуем boolean в строку
                    csv_row.append("Да" if value else "Нет")
                else:
                    csv_row.append(str(value) if value is not None else "")
            writer.writerow(csv_row)
        
        return csv_file_path


@admin_export_router.callback_query(F.data == "admin:export")
async def callback_admin_export(callback: CallbackQuery):
    """Раздел Экспорт данных"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    text = i18n_get_text(language, "admin.export_prompt")
    await callback.message.edit_text(text, reply_markup=get_admin_export_keyboard(language), parse_mode="HTML")
    await callback.answer()


@admin_export_router.callback_query(F.data.startswith("admin:export:"))
async def callback_admin_export_data(callback: CallbackQuery):
    """Обработка экспорта данных"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        export_type = callback.data.split(":")[2]  # users или subscriptions
        
        # Получаем данные из БД
        if export_type == "users":
            data = await database.get_all_users_for_export()
            filename = "users_export.csv"
            headers = ["ID", "Telegram ID", "Username", "Language", "Created At"]
        elif export_type == "subscriptions":
            data = await database.get_active_subscriptions_for_export()
            filename = "active_subscriptions_export.csv"
            headers = ["ID", "Telegram ID", "VPN Key", "Expires At", "Reminder Sent"]
        else:
            await callback.message.answer("Неверный тип экспорта", parse_mode="HTML")
            return
        
        if not data:
            await callback.message.answer("Нет данных для экспорта", parse_mode="HTML")
            return
        
        # Маппинг заголовков на ключи в данных
        if export_type == "users":
            key_mapping = {
                "ID": "id",
                "Telegram ID": "telegram_id",
                "Username": "username",
                "Language": "language",
                "Created At": "created_at"
            }
        else:  # subscriptions
            key_mapping = {
                "ID": "id",
                "Telegram ID": "telegram_id",
                "VPN Key": "vpn_key",
                "Expires At": "expires_at",
                "Reminder Sent": "reminder_sent"
            }
        
        # Генерируем CSV файл в фоновом потоке (неблокирующая операция)
        csv_file_path = None
        try:
            csv_file_path = await asyncio.to_thread(
                _generate_csv_file,
                data,
                headers,
                key_mapping
            )
            
            # Отправляем файл
            file_to_send = FSInputFile(csv_file_path, filename=filename)
            await callback.bot.send_document(
                config.ADMIN_TELEGRAM_ID,
                file_to_send,
                caption=f"📤 Экспорт: {export_type}"
            )
            await callback.message.answer("✅ Файл отправлен", parse_mode="HTML")
            
            # Логируем экспорт
            await database._log_audit_event_atomic_standalone(
                "admin_export_data",
                callback.from_user.id,
                None,
                f"Exported {export_type}: {len(data)} records"
            )
        finally:
            # Удаляем временный файл
            if csv_file_path:
                try:
                    os.remove(csv_file_path)
                except Exception as e:
                    logger.error(f"Error deleting temp file {csv_file_path}: {e}")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_export_data: {e}")
        await callback.message.answer("Ошибка при экспорте данных. Проверь логи.", parse_mode="HTML")
