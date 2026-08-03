"""Выдача доступа админом: дни, минуты, гибкий срок, год, уведомление.

ЧТО ЗДЕСЬ
    Все сценарии «дать пользователю доступ руками»: быстрые кнопки Basic и
    Plus, произвольный срок в минутах/часах/днях/месяцах, готовый год и
    ручной ввод количества, плюс экран «сообщить пользователю».

ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ
    Выдача — самая большая и самая рискованная часть админского раздела
    (915 строк из 2283). Она меняет оплаченный доступ, поэтому её правят
    отдельно от поиска пользователя и от отзыва доступа, рядом с которыми
    она лежала.

ЧТО ЛЕГКО СЛОМАТЬ
    Экран уведомления идёт последним шагом каждого сценария: если после
    выдачи не показать его, админ решит, что человека предупредили. Все
    сценарии обязаны заканчиваться либо отправкой уведомления, либо явным
    «не уведомлять» — молча выходить нельзя.
"""
import logging
import asyncio
import config
import database
import uuid
from aiogram import Router, F
from aiogram import Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from app.handlers.common.states import AdminGrantAccess, AdminGrantState, AdminRevokeAccess, AdminUserSearch
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from datetime import datetime, timedelta, timezone
from app.handlers.admin.keyboards import (
    get_admin_back_keyboard,
    get_admin_grant_days_keyboard,
    get_admin_grant_flex_unit_keyboard,
    get_admin_grant_flex_confirm_keyboard,
    get_admin_grant_flex_notify_keyboard,
)
from app.handlers.common.utils import safe_edit_text

admin_grant_router = Router()
logger = logging.getLogger(__name__)


GRANT_FLEX_UNIT_LABELS = {'minutes': 'минут', 'hours': 'часов', 'days': 'дней', 'months': 'месяцев'}


def _grant_flex_calculated_days(amount: float, unit: str) -> float:
    """Convert amount + unit to days. minutes → N/1440, hours → N/24, days → N, months → N*30."""
    if unit == "minutes":
        return amount / 1440.0
    if unit == "hours":
        return amount / 24.0
    if unit == "days":
        return amount
    if unit == "months":
        return amount * 30.0
    return amount


@admin_grant_router.callback_query(F.data.startswith("admin_grant_basic:"))
async def callback_admin_grant_basic(callback: CallbackQuery, state: FSMContext):
    """Entry: Admin selects «Выдать Basic». Ask for duration number, then unit."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        user_id = int(callback.data.split(":")[1])
        await state.update_data(grant_user_id=user_id, grant_tariff="basic")
        await state.set_state(AdminGrantState.waiting_amount)
        await callback.message.edit_text("Введите срок действия (число):", parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_basic: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_grant_router.callback_query(F.data.startswith("admin_grant_plus:"))
async def callback_admin_grant_plus(callback: CallbackQuery, state: FSMContext):
    """Entry: Admin selects «Выдать Plus». Ask for duration number, then unit."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        user_id = int(callback.data.split(":")[1])
        await state.update_data(grant_user_id=user_id, grant_tariff="plus")
        await state.set_state(AdminGrantState.waiting_amount)
        await callback.message.edit_text("Введите срок действия (число):", parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_plus: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_grant_router.message(StateFilter(AdminGrantState.waiting_amount), F.text)
async def process_admin_grant_flex_amount(message: Message, state: FSMContext):
    """After admin entered number, show unit selection keyboard."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await state.clear()
        return
    try:
        value = float(message.text.strip().replace(",", "."))
        if value <= 0:
            await message.answer("Введите положительное число.", parse_mode="HTML")
            return
        await state.update_data(grant_amount=value)
        await state.set_state(AdminGrantState.waiting_unit)
        language = await resolve_user_language(message.from_user.id)
        await message.answer("Выберите единицу срока:", reply_markup=get_admin_grant_flex_unit_keyboard(language), parse_mode="HTML")
    except ValueError:
        await message.answer("Введите число (например: 30).", parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in process_admin_grant_flex_amount: {e}")
        await message.answer("Ошибка.", parse_mode="HTML")
        await state.clear()


@admin_grant_router.callback_query(F.data.startswith("admin:grant_flex_unit:"), StateFilter(AdminGrantState.waiting_unit))
async def callback_admin_grant_flex_unit(callback: CallbackQuery, state: FSMContext):
    """Admin selected unit → show confirmation (N unit_label, total minutes/days)."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        # callback_data format: "admin:grant_flex_unit:minutes" → parts[2] = unit
        parts = callback.data.split(":")
        unit = parts[2] if len(parts) > 2 else ""
        if unit not in GRANT_FLEX_UNIT_LABELS:
            await callback.answer("Неизвестная единица", show_alert=True)
            return
        data = await state.get_data()
        amount = data.get("grant_amount")
        user_id = data.get("grant_user_id")
        tariff = data.get("grant_tariff", "basic")
        if amount is None or user_id is None:
            await callback.answer("Данные сессии потеряны. Начните заново.", show_alert=True)
            await state.clear()
            return
        calculated_days = _grant_flex_calculated_days(amount, unit)
        total_minutes = calculated_days * 24 * 60
        total_days = calculated_days
        unit_label = GRANT_FLEX_UNIT_LABELS[unit]
        tariff_label = "Basic" if tariff == "basic" else "Plus"
        await state.update_data(
            grant_unit=unit,
            grant_unit_label=unit_label,
            grant_calculated_days=calculated_days,
        )
        await state.set_state(AdminGrantState.waiting_confirm)
        text = (
            f"Выдать {tariff_label} на {int(amount) if amount == int(amount) else amount} {unit_label} пользователю {user_id}?\n"
            f"Это составит примерно {int(total_minutes)} минут / {total_days:.1f} дней.\n\n"
            "✅ Подтвердить   ❌ Отмена"
        )
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(text, reply_markup=get_admin_grant_flex_confirm_keyboard(language), parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_flex_unit: {e}")
        await callback.answer("Ошибка", show_alert=True)
        await state.clear()


@admin_grant_router.callback_query(F.data == "admin:grant_flex_confirm", StateFilter(AdminGrantState.waiting_confirm))
async def callback_admin_grant_flex_confirm(callback: CallbackQuery, state: FSMContext):
    """After confirm: show notify user choice, then execute grant in next step."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        data = await state.get_data()
        if not all([data.get("grant_user_id"), data.get("grant_tariff"), data.get("grant_calculated_days") is not None]):
            await callback.answer("Данные сессии потеряны.", show_alert=True)
            await state.clear()
            return
        await state.set_state(AdminGrantState.waiting_notify)
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(
            "Уведомить пользователя о выдаче доступа?",
            reply_markup=get_admin_grant_flex_notify_keyboard(language),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_flex_confirm: {e}")
        await callback.answer("Ошибка", show_alert=True)
        await state.clear()


@admin_grant_router.callback_query(F.data.startswith("admin:grant_flex_notify:"), StateFilter(AdminGrantState.waiting_notify))
async def callback_admin_grant_flex_notify(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Execute grant; if notify=yes send user message, then show admin confirmation."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        notify = callback.data.split(":")[-1].lower() == "yes"
        data = await state.get_data()
        user_id = data.get("grant_user_id")
        tariff = data.get("grant_tariff", "basic")
        amount = data.get("grant_amount")
        unit_label = data.get("grant_unit_label", "")
        calculated_days = data.get("grant_calculated_days", 0)
        grant_unit = data.get("grant_unit", "days")
        if not all([user_id, tariff, calculated_days is not None]):
            await callback.answer("Данные сессии потеряны.", show_alert=True)
            await state.clear()
            return
        if grant_unit in ("minutes", "hours") and calculated_days < 1:
            total_minutes = max(1, int(round(calculated_days * 1440)))
            expires_at, _ = await database.admin_grant_access_minutes_atomic(
                telegram_id=user_id,
                minutes=total_minutes,
                admin_telegram_id=callback.from_user.id,
            )
        else:
            days_int = max(1, int(round(calculated_days)))
            expires_at, _ = await database.admin_grant_access_atomic(
                telegram_id=user_id,
                days=days_int,
                admin_telegram_id=callback.from_user.id,
                tariff=tariff,
            )
        expires_date = expires_at.strftime("%d.%m.%Y")
        tariff_label = "Basic" if tariff == "basic" else "Plus"

        # Fire-and-forget: create/renew Remnawave bypass
        try:
            from app.services.remnawave_service import renew_remnawave_user_bg
            if tariff in ("basic", "plus"):
                renew_remnawave_user_bg(user_id, tariff, expires_at, period_days=days_int)
        except Exception as rmn_err:
            logger.warning("REMNAWAVE_ADMIN_GRANT_FAIL: tg=%s %s", user_id, rmn_err)

        # Site sync (fire-and-forget)
        try:
            from app.services.site_sync import notify_subscription_extend, sync_balance, is_enabled as _ss
            if _ss():
                sync_days = max(1, int(round(calculated_days)))
                asyncio.ensure_future(notify_subscription_extend(user_id, sync_days, tariff))
                asyncio.ensure_future(sync_balance(user_id))
        except Exception:
            pass

        text_admin = (
            f"✅ Выдан {tariff_label} доступ\n"
            f"👤 Пользователь: {user_id}\n"
            f"⏱ Срок: {int(amount) if amount == int(amount) else amount} {unit_label}\n"
            f"📅 До: {expires_date}"
        )
        if notify:
            try:
                await bot.send_message(
                    user_id,
                    f"🎁 Вам выдан доступ {tariff_label}\n📅 Действует до: {expires_date}",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.exception(f"Error sending grant notification to user {user_id}: {e}")
        language = await resolve_user_language(callback.from_user.id)
        await safe_edit_text(callback.message, text_admin, reply_markup=get_admin_back_keyboard(language))
        await database._log_audit_event_atomic_standalone(
            "admin_grant_access_flex",
            callback.from_user.id,
            user_id,
            f"Admin granted {tariff_label} {amount} {unit_label}, notify={notify}, expires={expires_date}",
        )
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_flex_notify: {e}")
        await callback.answer("Ошибка выдачи доступа", show_alert=True)
    await state.clear()


@admin_grant_router.callback_query(F.data == "admin:grant_flex_cancel")
async def callback_admin_grant_flex_cancel(callback: CallbackQuery, state: FSMContext):
    """Cancel flexible grant flow (from unit, confirm or notify step)."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer()
        return
    await callback.answer()
    await state.clear()
    language = await resolve_user_language(callback.from_user.id)
    await safe_edit_text(callback.message, "Отменено.", reply_markup=get_admin_back_keyboard(language))


@admin_grant_router.callback_query(F.data.startswith("admin:grant:") & ~F.data.startswith("admin:grant_custom:") & ~F.data.startswith("admin:grant_days:") & ~F.data.startswith("admin:grant_minutes:") & ~F.data.startswith("admin:grant_1_year:") & ~F.data.startswith("admin:grant_unit:") & ~F.data.startswith("admin:grant:notify:") & ~F.data.startswith("admin:notify:") & ~F.data.startswith("admin:grant_flex"))
async def callback_admin_grant(callback: CallbackQuery, state: FSMContext):
    """
    Entry point: Admin selects "Выдать доступ" for a user.
    Shows quick action buttons (1/7/14 days, 1 year, 10 minutes, custom).
    """
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Сохраняем user_id в состоянии
        await state.update_data(user_id=user_id)
        
        # Показываем клавиатуру выбора срока
        text = "Выберите срок доступа:"
        await callback.message.edit_text(text, reply_markup=get_admin_grant_days_keyboard(user_id), parse_mode="HTML")
        await state.set_state(AdminGrantAccess.waiting_for_days)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_days set for user {user_id}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_grant: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


async def _do_grant_1_year_setup(callback: CallbackQuery, state: FSMContext, language: str) -> None:
    """Shared logic: parse user_id, update FSM, show notify choice. Used by primary and fallback."""
    parts = callback.data.split(":")
    user_id = int(parts[2])
    await state.update_data(user_id=user_id, days=365, action_type="grant_1_year")
    text = "✅ Выдать доступ на 1 год\n\nУведомить пользователя?"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:notify:yes")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:notify:no")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(AdminGrantAccess.waiting_for_notify)


@admin_grant_router.callback_query(F.data.startswith("admin:grant_days:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_days(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    4️⃣ NOTIFY USER LOGIC (GRANT + REVOKE)
    
    Quick action: Grant access for N days.
    Ask for notify_user choice before executing.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        days = int(parts[3])
        
        # Save user_id and days in FSM, ask for notify choice
        await state.update_data(user_id=user_id, days=days, action_type="grant_days")
        
        text = f"✅ Выдать доступ на {days} дней\n\nУведомить пользователя?"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:notify:yes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:notify:no")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(AdminGrantAccess.waiting_for_notify)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_notify set for quick action (days={days})")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_days: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_grant_router.callback_query(F.data.startswith("admin:grant_minutes:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_minutes(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    1️⃣ FIX CONTRACT MISUSE: Execute grant BEFORE showing notify buttons.
    2️⃣ STORE NOTIFY CONTEXT EXPLICITLY: Encode data in callback_data.
    
    Quick action: Grant access for N minutes, then ask for notify choice.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        minutes = int(parts[3])
        
        # 1️⃣ FIX CONTRACT MISUSE: Execute grant FIRST (treat as side-effect only)
        try:
            await database.admin_grant_access_minutes_atomic(
                telegram_id=user_id,
                minutes=minutes,
                admin_telegram_id=callback.from_user.id
            )
            # If no exception → grant is successful (don't check return value)
        except Exception as e:
            logger.exception(f"CRITICAL: Failed to grant admin access (minutes) for user {user_id}, minutes={minutes}, admin={callback.from_user.id}: {e}")
            text = f"❌ Ошибка выдачи доступа: {str(e)[:100]}"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Ошибка создания ключа", show_alert=True)
            await state.clear()
            return
        
        # 2️⃣ STORE NOTIFY CONTEXT EXPLICITLY: Encode all data in callback_data
        # Format: admin:notify:yes:minutes:<user_id>:<minutes>
        text = f"✅ Доступ выдан на {minutes} минут\n\nУведомить пользователя?"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data=f"admin:notify:yes:minutes:{user_id}:{minutes}")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data=f"admin:notify:no:minutes:{user_id}:{minutes}")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        
        # Clear FSM - notify handlers will work without FSM
        await state.clear()
        
        logger.debug(f"Grant executed for user {user_id}, minutes={minutes}, waiting for notify choice")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_minutes: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_grant_router.callback_query(F.data.startswith("admin:grant_1_year:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_1_year(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    4️⃣ NOTIFY USER LOGIC (GRANT + REVOKE)
    
    Quick action: Grant access for 1 year (365 days).
    Ask for notify_user choice before executing.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        await _do_grant_1_year_setup(callback, state, language)
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_notify set for quick action (1 year)")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_1_year: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_grant_router.callback_query(F.data.startswith("admin:grant_1_year:"))
async def callback_admin_grant_1_year_fallback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    FSM fallback: when FSM cleared, grant_1_year callback would be Unhandled.
    Runs when primary (StateFilter waiting_for_days) does not match.
    Re-establishes notify choice flow statelessly from callback_data.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    logger.warning(
        "ADMIN_FSM_FALLBACK_EXECUTED "
        f"user={callback.from_user.id} "
        f"callback={callback.data}"
    )
    await callback.answer()
    
    try:
        await _do_grant_1_year_setup(callback, state, language)
        logger.debug("FSM: grant_1_year fallback - notify choice restored")
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_1_year_fallback: {e}")
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_grant_router.callback_query(F.data.startswith("admin:grant_custom:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_custom_from_days(callback: CallbackQuery, state: FSMContext):
    """
    2️⃣ CALLBACK HANDLERS — CRITICAL FIX
    
    Start custom grant flow from waiting_for_days state.
    This is the handler that was missing - works when FSM is in waiting_for_days.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        await state.update_data(user_id=user_id)
        
        text = "⚙️ Настройка доступа\n\nВыберите единицу времени:"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_minutes"), callback_data="admin:grant_unit:minutes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_hours"), callback_data="admin:grant_unit:hours")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_days"), callback_data="admin:grant_unit:days")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(AdminGrantAccess.waiting_for_unit)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_unit set for user {user_id} (from waiting_for_days state)")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_custom_from_days: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_grant_router.callback_query(F.data.startswith("admin:grant_custom:"))
async def callback_admin_grant_custom(callback: CallbackQuery, state: FSMContext):
    """
    2️⃣ CALLBACK HANDLERS — CRITICAL FIX
    
    Start custom grant flow - select duration unit first.
    Fallback handler (no state filter) - works from any state.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        await state.update_data(user_id=user_id)
        
        text = "⚙️ Настройка доступа\n\nВыберите единицу времени:"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_minutes"), callback_data="admin:grant_unit:minutes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_hours"), callback_data="admin:grant_unit:hours")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_days"), callback_data="admin:grant_unit:days")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(AdminGrantAccess.waiting_for_unit)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_unit set for user {user_id} (from any state)")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_custom: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_grant_router.callback_query(F.data.startswith("admin:grant_unit:"), StateFilter(AdminGrantAccess.waiting_for_unit))
async def callback_admin_grant_unit(callback: CallbackQuery, state: FSMContext):
    """
    2️⃣ CALLBACK HANDLERS — CRITICAL FIX
    
    Process duration unit selection, move to value input.
    Handler works ONLY in state waiting_for_unit.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        unit = callback.data.split(":")[2]  # minutes, hours, days (fixed: was [3], now [2] for admin:grant_unit:minutes)
        await state.update_data(duration_unit=unit)
        
        unit_text = {"minutes": "минут", "hours": "часов", "days": "дней"}.get(unit, unit)
        text = f"⚙️ Настройка доступа\n\nЕдиница: {unit_text}\n\nВведите количество (положительное число):"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:main")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(AdminGrantAccess.waiting_for_value)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_value set, unit={unit}")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_unit: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_grant_router.message(StateFilter(AdminGrantAccess.waiting_for_value))
async def process_admin_grant_value(message: Message, state: FSMContext):
    """
    PART 1: Process duration value input, move to notify choice.
    """
    language = await resolve_user_language(message.from_user.id)
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await message.answer(i18n_get_text(language, "admin.access_denied"), parse_mode="HTML")
        await state.clear()
        return
    
    try:
        value = int(message.text.strip())
        if value <= 0:
            await message.answer("❌ Введите положительное число", parse_mode="HTML")
            return
        
        data = await state.get_data()
        unit = data.get("duration_unit")
        unit_text = {"minutes": "минут", "hours": "часов", "days": "дней"}.get(unit, unit)
        
        await state.update_data(duration_value=value)
        
        text = f"⚙️ Настройка доступа\n\nПродолжительность: {value} {unit_text}\n\nУведомить пользователя?"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:grant:notify:yes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:grant:notify:no")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:main")],
        ])
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(AdminGrantAccess.waiting_for_notify)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_notify set, value={value}, unit={unit}")
        
    except ValueError:
        await message.answer("❌ Введите число", parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in process_admin_grant_value: {e}")
        await message.answer("Ошибка", parse_mode="HTML")
        await state.clear()


@admin_grant_router.callback_query(F.data.startswith("admin:grant:notify:"), StateFilter(AdminGrantAccess.waiting_for_notify))
async def callback_admin_grant_notify(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    PART 1: Execute grant access with notify_user choice.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        notify_user = callback.data.split(":")[3] == "yes"
        data = await state.get_data()
        user_id = data.get("user_id")
        duration_value = data.get("duration_value")
        duration_unit = data.get("duration_unit")
        
        if not all([user_id, duration_value, duration_unit]):
            await callback.answer("Ошибка: данные не найдены", show_alert=True)
            await state.clear()
            return
        
        # PART 3: Convert duration to timedelta
        from datetime import timedelta
        if duration_unit == "minutes":
            duration = timedelta(minutes=duration_value)
        elif duration_unit == "hours":
            duration = timedelta(hours=duration_value)
        else:  # days
            duration = timedelta(days=duration_value)
        
        logger.debug(f"FSM: Executing grant for user {user_id}, duration={duration}, notify_user={notify_user}")
        
        # PART 3: Execute grant_access
        try:
            result = await database.grant_access(
                telegram_id=user_id,
                duration=duration,
                source="admin",
                admin_telegram_id=callback.from_user.id,
                admin_grant_days=None  # Custom duration
            )
            
            expires_at = result["subscription_end"]
            vpn_key = result.get("vless_url") or result.get("uuid", "")
            
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            unit_text = {"minutes": "минут", "hours": "часов", "days": "дней"}.get(duration_unit, duration_unit)

            # Site sync (fire-and-forget)
            try:
                from app.services.site_sync import notify_subscription_extend, sync_balance, is_enabled as _ss
                if _ss():
                    sync_days = duration_value if duration_unit == "days" else (duration_value // 60 // 24 or 1)
                    asyncio.ensure_future(notify_subscription_extend(user_id, sync_days, "basic"))
                    asyncio.ensure_future(sync_balance(user_id))
            except Exception:
                pass

            # Уведомление пользователю отправляем ДО отчёта админу, чтобы
            # отчёт говорил о факте, а не о намерении.
            #
            # Раньше строка «Пользователь уведомлён» добавлялась по одному
            # флагу notify_user, а сама отправка шла ниже под условием
            # `notify_user and vpn_key`. При пустом ключе (активация ещё в
            # процессе) уведомление не уходило вовсе, а админ читал, что
            # человек предупреждён, — и не перезванивал ему.
            notified = False
            notify_skip_reason = None
            if notify_user:
                if not vpn_key:
                    notify_skip_reason = "ключ ещё не выдан (активация в процессе)"
                else:
                    import admin_notifications
                    # Текст на языке ПОЛУЧАТЕЛЯ, а не на русском: раньше
                    # уведомление собиралось русской f-строкой независимо от
                    # того, каким языком человек пользуется.
                    user_language = await resolve_user_language(user_id)
                    unit_label = i18n_get_text(
                        user_language, f"units.{duration_unit}", unit_text,
                    )
                    user_text = i18n_get_text(
                        user_language, "admin.user_granted_access",
                        value=duration_value,
                        unit=unit_label,
                        vpn_key=f"<code>{vpn_key}</code>",
                        date=expires_str,
                    )
                    notified = bool(await admin_notifications.send_user_notification(
                        bot=bot,
                        user_id=user_id,
                        message=user_text,
                        notification_type="admin_grant_custom",
                        parse_mode="HTML",
                    ))
                    if not notified:
                        notify_skip_reason = "отправка не удалась (бот заблокирован?)"

            text = f"✅ Доступ выдан на {duration_value} {unit_text}"
            if notified:
                text += "\nПользователь уведомлён."
            elif notify_user:
                text += f"\n⚠️ Уведомление НЕ отправлено: {notify_skip_reason}."
            else:
                text += "\nДействие выполнено без уведомления."
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))

            
            # PART 6: Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access_custom",
                callback.from_user.id,
                user_id,
                f"Admin granted {duration_value} {duration_unit} access, notify_user={notify_user}, expires_at={expires_str}"
            )
            
        except Exception as e:
            logger.exception(f"Error granting custom access: {e}")
            await callback.message.answer(f"❌ Ошибка: {str(e)[:100]}", reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
        
        await state.clear()
        logger.debug(f"FSM: AdminGrantAccess cleared after grant")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_notify: {e}")
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_grant_router.callback_query(F.data.startswith("admin:notify:yes:minutes:") | F.data.startswith("admin:notify:no:minutes:"))
async def callback_admin_grant_minutes_notify(callback: CallbackQuery, bot: Bot):
    """
    3️⃣ REGISTER EXPLICIT CALLBACK HANDLERS
    4️⃣ IMPLEMENT NOTIFY LOGIC
    
    Handle notify choice for minutes grant.
    Works WITHOUT FSM - all data encoded in callback_data.
    Format: admin:notify:yes|no:minutes:<user_id>:<minutes>
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # 3️⃣ REGISTER EXPLICIT CALLBACK HANDLERS: Parse callback_data
        parts = callback.data.split(":")
        if len(parts) != 6 or parts[1] != "notify" or parts[3] != "minutes":
            logger.warning(f"Invalid notify callback format: {callback.data}")
            await callback.answer("Ошибка формата команды", show_alert=True)
            return
        
        notify_choice = parts[2]  # "yes" or "no"
        user_id = int(parts[4])
        minutes = int(parts[5])
        
        notify = notify_choice == "yes"
        
        # 4️⃣ ЛОГИРОВАНИЕ: при выборе notify
        logger.info(f"ADMIN_GRANT_NOTIFY_SELECTED [notify={notify_choice}, user_id={user_id}, minutes={minutes}]")
        
        # 4️⃣ IMPLEMENT NOTIFY LOGIC: For admin:notify:yes
        if notify:
            # Use unified notification service
            import admin_notifications
            success = await admin_notifications.send_user_notification(
                bot=bot,
                user_id=user_id,
                message=f"Администратор выдал вам доступ на {minutes} минут",
                notification_type="admin_grant_minutes"
            )
            if success:
                logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, minutes={minutes}]")
        
        # 4️⃣ IMPLEMENT NOTIFY LOGIC: For admin:notify:no
        else:
            # 4️⃣ ЛОГИРОВАНИЕ: если notify=False
            logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, minutes={minutes}]")
        
        # 5️⃣ CLEAN TERMINATION: Edit admin message to "Готово"
        text = f"✅ Доступ выдан на {minutes} минут"
        if notify:
            text += "\nПользователь уведомлён."
        else:
            text += "\nДействие выполнено без уведомления."
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        
    except ValueError as e:
        logger.warning(f"Invalid callback data format: {callback.data}, error: {e}")
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
    except Exception as e:
        # 6️⃣ ERROR HANDLING: NO generic Exception raises, graceful exit
        logger.warning(f"Unexpected error in callback_admin_grant_minutes_notify: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)


@admin_grant_router.callback_query(
    (F.data == "admin:notify:yes") | (F.data == "admin:notify:no"),
    StateFilter(AdminGrantAccess.waiting_for_notify)
)
async def callback_admin_grant_quick_notify_fsm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Handle notify choice for grant_days and grant_1_year (FSM-based flow).
    This handler works WITH FSM state (unlike minutes handler which is FSM-free).
    
    FIX: Missing handler for admin:notify:yes and admin:notify:no used by grant_days and grant_1_year.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        notify = callback.data == "admin:notify:yes"
        data = await state.get_data()
        user_id = data.get("user_id")
        action_type = data.get("action_type")
        
        if not user_id or not action_type:
            logger.warning(f"Missing FSM data: user_id={user_id}, action_type={action_type}")
            await callback.answer("Ошибка: данные не найдены", show_alert=True)
            await state.clear()
            return
        
        logger.info(f"ADMIN_GRANT_NOTIFY_SELECTED [notify={notify}, user_id={user_id}, action_type={action_type}]")
        
        # Execute grant based on action_type (treat as side-effect, don't check return value)
        if action_type == "grant_days":
            days = data.get("days")
            if not days:
                logger.error(f"Missing days in FSM for grant_days")
                await callback.answer("Ошибка: данные не найдены", show_alert=True)
                await state.clear()
                return
            
            # FIX: Execute grant (treat as side-effect, don't check return value)
            try:
                await database.admin_grant_access_atomic(
                    telegram_id=user_id,
                    days=days,
                    admin_telegram_id=callback.from_user.id
                )
                # If no exception → grant is successful (don't check return value)
            except Exception as e:
                logger.exception(f"Failed to grant access: {e}")
                await callback.answer("Ошибка выдачи доступа", show_alert=True)
                await state.clear()
                return
            
            text = f"✅ Доступ выдан на {days} дней"

            # Site sync (fire-and-forget)
            try:
                from app.services.site_sync import notify_subscription_extend, sync_balance, is_enabled as _ss
                if _ss():
                    asyncio.ensure_future(notify_subscription_extend(user_id, days, data.get("tariff", "basic")))
                    asyncio.ensure_future(sync_balance(user_id))
            except Exception:
                pass

            if notify:
                try:
                    user_text = f"Администратор выдал вам доступ на {days} дней"
                    await bot.send_message(user_id, user_text, parse_mode="HTML")
                    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, days={days}]")
                    text += "\nПользователь уведомлён."
                except Exception as e:
                    logger.exception(f"Error sending notification: {e}")
                    text += "\nОшибка отправки уведомления."
            else:
                logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, days={days}]")
                text += "\nДействие выполнено без уведомления."
            
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access",
                callback.from_user.id,
                user_id,
                f"Admin granted {days} days access, notify_user={notify}"
            )
        
        elif action_type == "grant_1_year":
            # FIX: Execute grant (treat as side-effect, don't check return value)
            try:
                await database.admin_grant_access_atomic(
                    telegram_id=user_id,
                    days=365,
                    admin_telegram_id=callback.from_user.id
                )
                # If no exception → grant is successful (don't check return value)
            except Exception as e:
                logger.exception(f"Failed to grant access: {e}")
                await callback.answer("Ошибка выдачи доступа", show_alert=True)
                await state.clear()
                return
            
            text = "✅ Доступ на 1 год выдан"

            # Site sync (fire-and-forget)
            try:
                from app.services.site_sync import notify_subscription_extend, sync_balance, is_enabled as _ss
                if _ss():
                    asyncio.ensure_future(notify_subscription_extend(user_id, 365, "basic"))
                    asyncio.ensure_future(sync_balance(user_id))
            except Exception:
                pass

            if notify:
                # Use unified notification service
                import admin_notifications
                success = await admin_notifications.send_user_notification(
                    bot=bot,
                    user_id=user_id,
                    message="Администратор выдал вам доступ на 1 год",
                    notification_type="admin_grant_1_year"
                )
                if success:
                    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, duration=1_year]")
                    text += "\nПользователь уведомлён."
                else:
                    text += "\nОшибка отправки уведомления."
            else:
                logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, duration=1_year]")
                text += "\nДействие выполнено без уведомления."
            
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access_1_year",
                callback.from_user.id,
                user_id,
                f"Admin granted 1 year access, notify_user={notify}"
            )
        
        else:
            logger.warning(f"Unknown action_type: {action_type}")
            await callback.answer("Ошибка: неизвестный тип действия", show_alert=True)
        
        await state.clear()
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_quick_notify_fsm: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_grant_router.callback_query((F.data == "admin:notify:yes") | (F.data == "admin:notify:no"))
async def callback_admin_grant_notify_fallback(callback: CallbackQuery, state: FSMContext):
    """
    FSM fallback: when FSM cleared, notify:yes/no would be Unhandled.
    Runs when primary (StateFilter waiting_for_notify) does not match.
    Without FSM data we cannot execute grant; inform user to retry.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    logger.warning(
        "ADMIN_FSM_FALLBACK_EXECUTED "
        f"user={callback.from_user.id} "
        f"callback={callback.data}"
    )
    await callback.answer(
        "Сессия сброшена. Выберите пользователя заново и повторите действие.",
        show_alert=True
    )
