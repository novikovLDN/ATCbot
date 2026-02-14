"""
Simple navigation callbacks: menu_main, back_to_main, settings, about, support, etc.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.filters import StateFilter

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions.service import check_and_disable_expired_subscription as check_subscription_expiry
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import format_text_with_incident, safe_edit_text
from app.handlers.common.screens import show_profile
from app.handlers.common.keyboards import (
    get_main_menu_keyboard,
    get_about_keyboard,
    get_service_status_keyboard,
)
router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "menu_main")
async def callback_main_menu(callback: CallbackQuery, state: FSMContext):
    """Главное меню. Delete + answer to support navigation from photo message (loyalty screen)."""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    # Clear all FSM state on navigation (withdrawal, promo, etc.)
    current_state = await state.get_state()
    if current_state is not None:
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
async def callback_back_to_main(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню с экрана выдачи ключа"""
    await state.clear()
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


@router.callback_query(F.data == "menu_support")
async def callback_support(callback: CallbackQuery):
    """Поддержка. Entry from inline button."""
    from app.handlers.common.screens import _open_support_screen
    await _open_support_screen(callback, callback.bot)


@router.callback_query(F.data == "go_profile", StateFilter(default_state))
@router.callback_query(F.data == "go_profile")
async def callback_go_profile(callback: CallbackQuery, state: FSMContext):
    """Переход в профиль с экрана выдачи ключа - работает независимо от FSM состояния"""
    telegram_id = callback.from_user.id
    
    # Немедленная обратная связь пользователю
    await callback.answer()
    
    # Очищаем FSM состояние, если пользователь был в каком-то процессе
    try:
        current_state = await state.get_state()
        if current_state is not None:
            await state.clear()
            logger.debug(f"Cleared FSM state for user {telegram_id}, was: {current_state}")
    except Exception as e:
        logger.debug(f"FSM state clear failed (may be already clear): {e}")
    
    try:
        logger.info(f"Opening profile via go_profile for user {telegram_id}")
        
        language = await resolve_user_language(telegram_id)
        
        await show_profile(callback, language)
        
        logger.info(f"Profile opened successfully via go_profile for user {telegram_id}")
    except Exception as e:
        logger.exception(f"Error opening profile via go_profile for user {telegram_id}: {e}")
        # Пытаемся отправить сообщение об ошибке
        try:
            user = await database.get_user(telegram_id)
            language = await resolve_user_language(callback.from_user.id)
            error_text = i18n_get_text(language, "errors.profile_load")
            await callback.message.answer(error_text)
        except Exception as e2:
            logger.exception(f"Error sending error message to user {telegram_id}: {e2}")


@router.callback_query(F.data == "copy_key")
async def callback_copy_key(callback: CallbackQuery):
    """Копировать VPN-ключ - отправляет ключ как отдельное сообщение"""
    # B3.1 - SOFT DEGRADATION: Read-only awareness (informational only, does not affect flow)
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        db_ready = database.DB_READY
        import config
        
        # Build SystemState for awareness (read-only)
        if db_ready:
            db_component = healthy_component(last_checked_at=now)
        else:
            db_component = unavailable_component(
                error="DB not ready (degraded mode)",
                last_checked_at=now
            )
        
        # VPN API component
        if config.VPN_ENABLED and config.XRAY_API_URL:
            vpn_component = healthy_component(last_checked_at=now)
        else:
            vpn_component = degraded_component(
                error="VPN API not configured",
                last_checked_at=now
            )
        
        # Payments component (always healthy)
        payments_component = healthy_component(last_checked_at=now)
        
        system_state = SystemState(
            database=db_component,
            vpn_api=vpn_component,
            payments=payments_component,
        )
        
        # PART D.5: Handlers log DEGRADED for VPN-related actions
        # PART D.5: NEVER block payments or DB flows
        if system_state.is_degraded:
            logger.info(
                f"[DEGRADED] system_state detected during callback_copy_key "
                f"(user={callback.from_user.id}, optional components degraded)"
            )
    except Exception:
        # Ignore system state errors - must not affect key copy flow
        pass
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Дополнительная защита: проверка истечения подписки
    await check_subscription_expiry(telegram_id)
    
    # Получаем активную подписку (проверка через subscriptions)
    subscription = await database.get_subscription(telegram_id)
    
    # PART 8: Fix pending activation UX - disable copy key button until active
    if subscription:
        activation_status = subscription.get("activation_status", "active")
        if activation_status == "pending":
            error_text = i18n_get_text(language, "main.error_activation_pending")
            logging.info(f"copy_key: Activation pending for user {telegram_id}")
            await callback.answer(error_text, show_alert=True)
            return
    
    if not subscription or not subscription.get("vpn_key"):
        error_text = i18n_get_text(language, "errors.no_active_subscription")
        logging.warning(f"copy_key: No active subscription or vpn_key for user {telegram_id}")
        await callback.answer(error_text, show_alert=True)
        return
    
    # Получаем VPN-ключ (from API only — no local validation)
    vpn_key = subscription["vpn_key"]
    
    # Отправляем VPN-ключ как отдельное сообщение (позволяет одно нажатие для копирования в Telegram)
    await callback.message.answer(
        f"<code>{vpn_key}</code>",
        parse_mode="HTML"
    )
    
    # Показываем toast уведомление о копировании
    success_text = i18n_get_text(language, "profile.vpn_key_copied_toast")
    await callback.answer(success_text, show_alert=False)


@router.callback_query(F.data == "copy_vpn_key")
async def callback_copy_vpn_key(callback: CallbackQuery):
    """Скопировать VPN-ключ - отправляет ключ как отдельное сообщение"""
    # B3.1 - SOFT DEGRADATION: Read-only awareness (informational only, does not affect flow)
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        db_ready = database.DB_READY
        import config
        
        # Build SystemState for awareness (read-only)
        if db_ready:
            db_component = healthy_component(last_checked_at=now)
        else:
            db_component = unavailable_component(
                error="DB not ready (degraded mode)",
                last_checked_at=now
            )
        
        # VPN API component
        if config.VPN_ENABLED and config.XRAY_API_URL:
            vpn_component = healthy_component(last_checked_at=now)
        else:
            vpn_component = degraded_component(
                error="VPN API not configured",
                last_checked_at=now
            )
        
        # Payments component (always healthy)
        payments_component = healthy_component(last_checked_at=now)
        
        system_state = SystemState(
            database=db_component,
            vpn_api=vpn_component,
            payments=payments_component,
        )
        
        # PART D.5: Handlers log DEGRADED for VPN-related actions
        # PART D.5: NEVER block payments or DB flows
        if system_state.is_degraded:
            logger.info(
                f"[DEGRADED] system_state detected during callback_copy_vpn_key "
                f"(user={callback.from_user.id}, optional components degraded)"
            )
    except Exception:
        # Ignore system state errors - must not affect key copy flow
        pass
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Дополнительная защита: проверка истечения подписки
    await check_subscription_expiry(telegram_id)
    
    # Получаем VPN-ключ из активной подписки (проверка через subscriptions)
    subscription = await database.get_subscription(telegram_id)
    
    if not subscription or not subscription.get("vpn_key"):
        error_text = i18n_get_text(language, "errors.no_active_subscription")
        logging.warning(f"copy_vpn_key: No active subscription or vpn_key for user {telegram_id}")
        await callback.answer(error_text, show_alert=True)
        return
    
    # Получаем VPN-ключ (from API only — no local validation)
    vpn_key = subscription["vpn_key"]
    
    # Отправляем VPN-ключ как отдельное сообщение (позволяет одно нажатие для копирования в Telegram)
    await callback.message.answer(
        f"<code>{vpn_key}</code>",
        parse_mode="HTML"
    )
    
    # Показываем toast уведомление о копировании
    success_text = i18n_get_text(language, "profile.vpn_key_copied_toast")
    await callback.answer(success_text, show_alert=False)
