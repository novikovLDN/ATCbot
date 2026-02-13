"""
Language-related callback handlers: change_language, lang_*.
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import format_text_with_incident, safe_edit_text
from app.handlers.common.keyboards import get_language_keyboard, get_main_menu_keyboard

language_router = Router()


@language_router.callback_query(F.data == "change_language")
async def callback_change_language(callback: CallbackQuery):
    """Изменить язык"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Экран выбора языка (канонический вид)
    text = i18n_get_text(language, "lang.select")
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_language_keyboard(language),
        bot=callback.bot
    )
    await callback.answer()


@language_router.callback_query(F.data.startswith("lang_"))
async def callback_language(callback: CallbackQuery):
    """Обработчик выбора языка"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return

    language = callback.data.split("_")[1]
    telegram_id = callback.from_user.id

    await database.update_user_language(telegram_id, language)

    # Подтверждение смены языка на выбранном языке
    text = i18n_get_text(language, "main.welcome")
    text = await format_text_with_incident(text, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    await callback.answer(
        i18n_get_text(language, "lang.changed_toast"),
        show_alert=False
    )
