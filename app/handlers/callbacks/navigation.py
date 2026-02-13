"""
Simple navigation callbacks: menu_main, back_to_main, settings, about, support, etc.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import format_text_with_incident, safe_edit_text
from app.handlers.common.keyboards import (
    get_main_menu_keyboard,
    get_about_keyboard,
    get_service_status_keyboard,
    get_admin_dashboard_keyboard,
)
from app.handlers.common.states import PromoCodeInput

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "menu_main")
async def callback_main_menu(callback: CallbackQuery, state: FSMContext):
    """Главное меню. Delete + answer to support navigation from photo message (loyalty screen)."""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    current_state = await state.get_state()
    if current_state == PromoCodeInput.waiting_for_promo.state:
        await state.clear()

    try:
        await callback.message.delete()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(callback.from_user.id)

    text = i18n_get_text(language, "main.welcome")
    text = await format_text_with_incident(text, language)
    keyboard = await get_main_menu_keyboard(language, callback.from_user.id)
    await callback.bot.send_message(callback.message.chat.id, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "back_to_main")
async def callback_back_to_main(callback: CallbackQuery):
    """Возврат в главное меню с экрана выдачи ключа"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "main.welcome")
    text = await format_text_with_incident(text, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "menu_ecosystem")
async def callback_ecosystem(callback: CallbackQuery):
    """⚪️ Наша экосистема"""
    language = await resolve_user_language(callback.from_user.id)
    title = i18n_get_text(language, "main.ecosystem_title", "main.ecosystem_title")
    text = i18n_get_text(language, "main.ecosystem_text", "main.ecosystem_text")
    full_text = f"{title}\n\n{text}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "main.about"), callback_data="menu_about")],
        [InlineKeyboardButton(text="✍️ Трекер Only (скоро)", callback_data="tracker_soon")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])
    await safe_edit_text(callback.message, full_text, reply_markup=keyboard, bot=callback.bot)
    await callback.answer()


@router.callback_query(F.data == "tracker_soon")
async def callback_tracker_soon(callback: CallbackQuery):
    """Трекер Only - в разработке"""
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "main.tracker_soon", "main.tracker_soon")
    await callback.answer(text, show_alert=False)


@router.callback_query(F.data == "menu_settings")
async def callback_settings(callback: CallbackQuery):
    """⚙️ Настройки"""
    language = await resolve_user_language(callback.from_user.id)
    title = i18n_get_text(language, "main.settings_title", "main.settings_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "lang.change"), callback_data="change_language")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])
    await safe_edit_text(callback.message, title, reply_markup=keyboard, bot=callback.bot)
    await callback.answer()


@router.callback_query(F.data == "menu_about")
async def callback_about(callback: CallbackQuery):
    """О сервисе. Entry from ecosystem."""
    from app.handlers.common.screens import _open_about_screen
    await _open_about_screen(callback, callback.bot)


@router.callback_query(F.data == "menu_service_status")
async def callback_service_status(callback: CallbackQuery):
    """Статус сервиса"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "main.service_status_text", "service_status_text")

    incident = await database.get_incident_settings()
    if incident["is_active"]:
        incident_text = incident.get("incident_text") or i18n_get_text(language, "main.incident_banner", "incident_banner")
        warning = i18n_get_text(language, "main.incident_status_warning", incident_text=incident_text)
        text = text + warning

    await safe_edit_text(callback.message, text, reply_markup=get_service_status_keyboard(language), bot=callback.bot)
    await callback.answer()


@router.callback_query(F.data == "about_privacy")
async def callback_privacy(callback: CallbackQuery):
    """Политика конфиденциальности"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "main.privacy_policy_text", "privacy_policy_text")
    await safe_edit_text(callback.message, text, reply_markup=get_about_keyboard(language), bot=callback.bot)
    await callback.answer()


@router.callback_query(F.data == "menu_instruction")
async def callback_instruction(callback: CallbackQuery):
    """Инструкция. Entry from inline button."""
    from app.handlers.common.screens import _open_instruction_screen
    await _open_instruction_screen(callback, callback.bot)


@router.callback_query(F.data == "admin:main")
async def callback_admin_main(callback: CallbackQuery):
    """Главный экран админ-дашборда"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.dashboard_title")
    await safe_edit_text(callback.message, text, reply_markup=get_admin_dashboard_keyboard(language), bot=callback.bot)
    await callback.answer()


@router.callback_query(F.data == "menu_support")
async def callback_support(callback: CallbackQuery):
    """Поддержка. Entry from inline button."""
    from app.handlers.common.screens import _open_support_screen
    await _open_support_screen(callback, callback.bot)
