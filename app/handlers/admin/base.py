"""
Admin base entry handlers: /admin command and dashboard callbacks.
"""
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.utils.security import require_admin
from app.handlers.admin.keyboards import get_admin_dashboard_keyboard, get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_base_router = Router()
logger = logging.getLogger(__name__)


@admin_base_router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Административный дашборд"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized admin dashboard attempt by user {message.from_user.id}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        return
    
    language = await resolve_user_language(message.from_user.id)
    text = i18n_get_text(language, "admin.dashboard_title")
    await message.answer(text, reply_markup=get_admin_dashboard_keyboard(language))


@admin_base_router.callback_query(F.data == "admin:dashboard")
async def callback_admin_dashboard(callback: CallbackQuery):
    """
    2. ADMIN DASHBOARD UI (TELEGRAM)
    
    Display real-time system health with severity indicator.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        from app.core.system_health import evaluate_system_health, get_error_summary_compact
        
        # Get system health report
        health_report = await evaluate_system_health()
        error_summary = await get_error_summary_compact()
        
        # Build dashboard text
        text = f"📊 Admin Dashboard\n\n"
        text += health_report.summary
        text += "\n\n"
        
        # Add error summary if any
        if error_summary:
            text += "⚠️ ACTIVE ISSUES:\n\n"
            for i, error in enumerate(error_summary[:5], 1):  # Limit to 5 issues
                text += f"{i}. {error['component'].upper()}: {error['reason']}\n"
                text += f"   → {error['impact']}\n\n"
        
        # Add refresh button
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.refresh"), callback_data="admin:dashboard")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_menu"), callback_data="admin:test_menu")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
        
        # Audit log
        await database._log_audit_event_atomic_standalone(
            "admin_dashboard_viewed",
            callback.from_user.id,
            None,
            f"Admin viewed dashboard: severity={health_report.level.value}, issues={len(error_summary)}"
        )
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_dashboard: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.dashboard_data"), show_alert=True)


@admin_base_router.callback_query(F.data == "admin:main")
async def callback_admin_main(callback: CallbackQuery):
    """Главный экран админ-дашборда"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.dashboard_title")
    await safe_edit_text(callback.message, text, reply_markup=get_admin_dashboard_keyboard(language))
    await callback.answer()
