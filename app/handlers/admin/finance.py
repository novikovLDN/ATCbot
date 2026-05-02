"""
Admin finance handlers: balance management, discount creation, incident management.
"""
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions.service import is_subscription_active
from app.handlers.common.states import (
    AdminBalanceManagement,
    AdminCreditBalance,
    AdminDebitBalance,
    AdminDiscountCreate,
    AdminTrafficDiscountCreate,
    IncidentEdit,
)
from app.handlers.admin.keyboards import (
    get_admin_back_keyboard,
    get_admin_discount_percent_keyboard,
    get_admin_discount_expires_keyboard,
    get_admin_traffic_discount_percent_keyboard,
    get_admin_traffic_discount_expires_keyboard,
)
from app.handlers.common.utils import safe_edit_text

admin_finance_router = Router()
logger = logging.getLogger(__name__)



@admin_finance_router.callback_query(F.data.startswith("admin:discount_create:"))
async def callback_admin_discount_create(callback: CallbackQuery):
    """Обработчик кнопки 'Назначить скидку'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)

    try:
        user_id = int(callback.data.split(":")[2])

        # Проверяем, есть ли уже скидка
        existing_discount = await database.get_user_discount(user_id)
        if existing_discount:
            discount_percent = existing_discount["discount_percent"]
            text = f"❌ У пользователя уже есть персональная скидка {discount_percent}%.\n\nСначала удалите существующую скидку."
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Скидка уже существует", show_alert=True)
            return
        
        text = f"🎯 Назначить скидку\n\nВыберите процент скидки:"
        await callback.message.edit_text(text, reply_markup=get_admin_discount_percent_keyboard(user_id), parse_mode="HTML")
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_create: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.callback_query(F.data.startswith("admin:discount_percent:"))
async def callback_admin_discount_percent(callback: CallbackQuery):
    """Обработчик выбора процента скидки"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        
        text = f"🎯 Назначить скидку {discount_percent}%\n\nВыберите срок действия скидки:"
        await callback.message.edit_text(text, reply_markup=get_admin_discount_expires_keyboard(user_id, discount_percent), parse_mode="HTML")
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_percent: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.callback_query(F.data.startswith("admin:discount_percent_manual:"))
async def callback_admin_discount_percent_manual(callback: CallbackQuery, state: FSMContext):
    """Обработчик для ввода процента скидки вручную"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)

    try:
        user_id = int(callback.data.split(":")[2])

        await state.update_data(discount_user_id=user_id)
        await state.set_state(AdminDiscountCreate.waiting_for_percent)

        text = "🎯 Назначить скидку\n\nВведите процент скидки (число от 1 до 99):"
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_percent_manual: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.message(AdminDiscountCreate.waiting_for_percent)
async def process_admin_discount_percent(message: Message, state: FSMContext):
    """Обработка введённого процента скидки"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"), parse_mode="HTML")
        await state.clear()
        return
    
    try:
        data = await state.get_data()
        user_id = data.get("discount_user_id")
        
        try:
            discount_percent = int(message.text.strip())
            if discount_percent < 1 or discount_percent > 99:
                await message.answer("Процент скидки должен быть от 1 до 99. Попробуйте снова:", parse_mode="HTML")
                return
        except ValueError:
            await message.answer("Введите число от 1 до 99:", parse_mode="HTML")
            return
        
        await state.update_data(discount_percent=discount_percent)
        
        text = f"🎯 Назначить скидку {discount_percent}%\n\nВыберите срок действия скидки:"
        await message.answer(text, reply_markup=get_admin_discount_expires_keyboard(user_id, discount_percent), parse_mode="HTML")
        await state.set_state(AdminDiscountCreate.waiting_for_expires)
        
    except Exception as e:
        logging.exception(f"Error in process_admin_discount_percent: {e}")
        await message.answer("Ошибка. Проверь логи.", parse_mode="HTML")
        await state.clear()


@admin_finance_router.callback_query(F.data.startswith("admin:discount_expires:"))
async def callback_admin_discount_expires(callback: CallbackQuery, bot: Bot):
    """Обработчик выбора срока действия скидки"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)

    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        expires_days = int(parts[4])

        # Рассчитываем expires_at
        expires_at = None
        if expires_days > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

        # Создаём скидку
        success = await database.create_user_discount(
            telegram_id=user_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=callback.from_user.id
        )

        if success:
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "бессрочно"
            text = f"✅ Персональная скидка {discount_percent}% назначена\n\nСрок действия: {expires_str}"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Скидка назначена", show_alert=True)
        else:
            text = "❌ Ошибка при создании скидки"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)

    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_expires: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.callback_query(F.data.startswith("admin:discount_expires_manual:"))
async def callback_admin_discount_expires_manual(callback: CallbackQuery, state: FSMContext):
    """Обработчик для ввода срока действия скидки вручную"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)

    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])

        await state.update_data(discount_user_id=user_id, discount_percent=discount_percent)
        await state.set_state(AdminDiscountCreate.waiting_for_expires)

        text = "🎯 Назначить скидку\n\nВведите количество дней действия скидки (или 0 для бессрочной):"
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_expires_manual: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.message(AdminDiscountCreate.waiting_for_expires)
async def process_admin_discount_expires(message: Message, state: FSMContext, bot: Bot):
    """Обработка введённого срока действия скидки"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"), parse_mode="HTML")
        await state.clear()
        return
    
    language = await resolve_user_language(message.from_user.id)

    try:
        data = await state.get_data()
        user_id = data.get("discount_user_id")
        discount_percent = data.get("discount_percent")

        try:
            expires_days = int(message.text.strip())
            if expires_days < 0:
                await message.answer("Количество дней должно быть неотрицательным. Попробуйте снова:", parse_mode="HTML")
                return
        except ValueError:
            await message.answer("Введите число (количество дней или 0 для бессрочной):", parse_mode="HTML")
            return
        
        # Рассчитываем expires_at
        expires_at = None
        if expires_days > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
        
        # Создаём скидку
        success = await database.create_user_discount(
            telegram_id=user_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=message.from_user.id
        )
        
        if success:
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "бессрочно"
            text = f"✅ Персональная скидка {discount_percent}% назначена\n\nСрок действия: {expires_str}"
            await message.answer(text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
        else:
            text = "❌ Ошибка при создании скидки"
            await message.answer(text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
        
        await state.clear()
        
    except Exception as e:
        logging.exception(f"Error in process_admin_discount_expires: {e}")
        await message.answer("Ошибка. Проверь логи.", parse_mode="HTML")
        await state.clear()


@admin_finance_router.callback_query(F.data.startswith("admin:discount_delete:"))
async def callback_admin_discount_delete(callback: CallbackQuery):
    """Обработчик кнопки 'Удалить скидку'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    language = await resolve_user_language(callback.from_user.id)

    try:
        user_id = int(callback.data.split(":")[2])

        # Удаляем скидку
        success = await database.delete_user_discount(
            telegram_id=user_id,
            deleted_by=callback.from_user.id
        )

        if success:
            text = "✅ Персональная скидка удалена"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Скидка удалена", show_alert=True)
        else:
            text = "❌ Скидка не найдена или уже удалена"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Скидка не найдена", show_alert=True)

    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_delete: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


# ====================================================================================
# TRAFFIC-PACK DISCOUNT (per-user discount on bypass GB purchases)
# ====================================================================================
#
# Mirrors the subscription discount flow but writes to user_traffic_discounts
# (consumed by app/handlers/traffic.py:get_user_traffic_discount).

@admin_finance_router.callback_query(F.data.startswith("admin:tdiscount_create:"))
async def callback_admin_tdiscount_create(callback: CallbackQuery):
    """Назначить скидку на покупку ГБ обхода — выбор процента."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)
    try:
        user_id = int(callback.data.split(":")[2])

        existing = await database.get_user_traffic_discount(user_id)
        if existing:
            pct = existing.get("discount_percent", 0)
            text = (
                f"❌ У пользователя уже есть скидка <b>{pct}%</b> на ГБ обхода.\n\n"
                "Сначала удалите существующую."
            )
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
            await callback.answer("Скидка уже существует", show_alert=True)
            return

        text = "🌐 <b>Скидка на ГБ обхода</b>\n\nВыберите процент:"
        await safe_edit_text(
            callback.message, text,
            reply_markup=get_admin_traffic_discount_percent_keyboard(user_id, language),
            parse_mode="HTML",
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error in callback_admin_tdiscount_create: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.callback_query(F.data.startswith("admin:tdiscount_percent:"))
async def callback_admin_tdiscount_percent(callback: CallbackQuery):
    """Выбран процент — показать клавиатуру срока действия."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        if not (1 <= discount_percent <= 99):
            await callback.answer("Неверный процент", show_alert=True)
            return
        text = f"🌐 <b>Скидка {discount_percent}% на ГБ обхода</b>\n\nВыберите срок действия:"
        await safe_edit_text(
            callback.message, text,
            reply_markup=get_admin_traffic_discount_expires_keyboard(user_id, discount_percent, language),
            parse_mode="HTML",
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error in callback_admin_tdiscount_percent: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.callback_query(F.data.startswith("admin:tdiscount_percent_manual:"))
async def callback_admin_tdiscount_percent_manual(callback: CallbackQuery, state: FSMContext):
    """Ручной ввод процента."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)
    try:
        user_id = int(callback.data.split(":")[2])
        await state.update_data(tdiscount_user_id=user_id)
        await state.set_state(AdminTrafficDiscountCreate.waiting_for_percent)

        text = "🌐 <b>Скидка на ГБ обхода</b>\n\nВведите процент скидки (1–99):"
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error in callback_admin_tdiscount_percent_manual: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.message(AdminTrafficDiscountCreate.waiting_for_percent)
async def process_admin_tdiscount_percent(message: Message, state: FSMContext):
    """FSM: получили процент — переходим к выбору срока."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"), parse_mode="HTML")
        await state.clear()
        return
    language = await resolve_user_language(message.from_user.id)
    try:
        try:
            discount_percent = int((message.text or "").strip())
        except ValueError:
            await message.answer("Введите число от 1 до 99:", parse_mode="HTML")
            return
        if not (1 <= discount_percent <= 99):
            await message.answer("Процент должен быть от 1 до 99. Попробуйте ещё раз:", parse_mode="HTML")
            return

        data = await state.get_data()
        user_id = data.get("tdiscount_user_id")
        if not user_id:
            await message.answer("Сессия устарела. Откройте раздел пользователя заново.", parse_mode="HTML")
            await state.clear()
            return
        await state.update_data(tdiscount_percent=discount_percent)

        text = f"🌐 <b>Скидка {discount_percent}% на ГБ обхода</b>\n\nВыберите срок действия:"
        await message.answer(
            text,
            reply_markup=get_admin_traffic_discount_expires_keyboard(user_id, discount_percent, language),
            parse_mode="HTML",
        )
        await state.set_state(AdminTrafficDiscountCreate.waiting_for_expires)
    except Exception as e:
        logger.exception(f"Error in process_admin_tdiscount_percent: {e}")
        await message.answer("Ошибка. Проверь логи.", parse_mode="HTML")
        await state.clear()


@admin_finance_router.callback_query(F.data.startswith("admin:tdiscount_expires:"))
async def callback_admin_tdiscount_expires(callback: CallbackQuery, bot: Bot):
    """Выбран срок — сохраняем скидку."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        expires_days = int(parts[4])

        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expires_days)
            if expires_days > 0 else None
        )

        success = await database.create_user_traffic_discount(
            telegram_id=user_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=callback.from_user.id,
        )

        if success:
            await database._log_audit_event_atomic_standalone(
                "admin_traffic_discount_created", callback.from_user.id, user_id,
                f"Traffic discount {discount_percent}% expires_days={expires_days}",
            )
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "бессрочно"
            text = (
                f"✅ Скидка <b>{discount_percent}%</b> на покупку ГБ обхода назначена\n\n"
                f"Пользователь: <code>{user_id}</code>\n"
                f"Срок действия: {expires_str}"
            )
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
            await callback.answer("Скидка назначена", show_alert=True)
        else:
            await safe_edit_text(callback.message, "❌ Ошибка при создании скидки", reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Ошибка", show_alert=True)
    except Exception as e:
        logger.exception(f"Error in callback_admin_tdiscount_expires: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.callback_query(F.data.startswith("admin:tdiscount_expires_manual:"))
async def callback_admin_tdiscount_expires_manual(callback: CallbackQuery, state: FSMContext):
    """Ручной ввод срока."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        await state.update_data(tdiscount_user_id=user_id, tdiscount_percent=discount_percent)
        await state.set_state(AdminTrafficDiscountCreate.waiting_for_expires)

        text = (
            "🌐 <b>Скидка на ГБ обхода</b>\n\n"
            "Введите количество дней действия скидки (или <code>0</code> для бессрочной):"
        )
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error in callback_admin_tdiscount_expires_manual: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.message(AdminTrafficDiscountCreate.waiting_for_expires)
async def process_admin_tdiscount_expires(message: Message, state: FSMContext, bot: Bot):
    """FSM: получили срок — сохраняем скидку."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"), parse_mode="HTML")
        await state.clear()
        return
    language = await resolve_user_language(message.from_user.id)
    try:
        data = await state.get_data()
        user_id = data.get("tdiscount_user_id")
        discount_percent = data.get("tdiscount_percent")
        if not user_id or not discount_percent:
            await message.answer("Сессия устарела. Откройте раздел пользователя заново.", parse_mode="HTML")
            await state.clear()
            return

        try:
            expires_days = int((message.text or "").strip())
        except ValueError:
            await message.answer("Введите число (количество дней или 0 для бессрочной):", parse_mode="HTML")
            return
        if expires_days < 0:
            await message.answer("Количество дней должно быть неотрицательным. Попробуйте ещё раз:", parse_mode="HTML")
            return

        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expires_days)
            if expires_days > 0 else None
        )

        success = await database.create_user_traffic_discount(
            telegram_id=user_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=message.from_user.id,
        )

        if success:
            await database._log_audit_event_atomic_standalone(
                "admin_traffic_discount_created", message.from_user.id, user_id,
                f"Traffic discount {discount_percent}% expires_days={expires_days}",
            )
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "бессрочно"
            text = (
                f"✅ Скидка <b>{discount_percent}%</b> на покупку ГБ обхода назначена\n\n"
                f"Пользователь: <code>{user_id}</code>\n"
                f"Срок действия: {expires_str}"
            )
            await message.answer(text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
        else:
            await message.answer("❌ Ошибка при создании скидки", reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")

        await state.clear()
    except Exception as e:
        logger.exception(f"Error in process_admin_tdiscount_expires: {e}")
        await message.answer("Ошибка. Проверь логи.", parse_mode="HTML")
        await state.clear()


@admin_finance_router.callback_query(F.data.startswith("admin:tdiscount_delete:"))
async def callback_admin_tdiscount_delete(callback: CallbackQuery):
    """Удалить скидку на ГБ обхода."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)
    try:
        user_id = int(callback.data.split(":")[2])
        success = await database.delete_user_traffic_discount(user_id)
        if success:
            await database._log_audit_event_atomic_standalone(
                "admin_traffic_discount_deleted", callback.from_user.id, user_id,
                "Traffic discount removed",
            )
            text = "✅ Скидка на ГБ обхода удалена"
            await callback.answer("Скидка удалена", show_alert=True)
        else:
            text = "❌ Скидка не найдена или уже удалена"
            await callback.answer("Скидка не найдена", show_alert=True)
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
    except Exception as e:
        logger.exception(f"Error in callback_admin_tdiscount_delete: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


# ==================== ОБРАБОТЧИКИ ДЛЯ СОЗДАНИЯ ПРОМОКОДОВ ====================


@admin_finance_router.callback_query(F.data == "admin:incident")
async def callback_admin_incident(callback: CallbackQuery):
    """Раздел управления инцидентом"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)

    incident = await database.get_incident_settings()
    is_active = incident["is_active"]
    incident_text = incident.get("incident_text") or "Текст не указан"
    
    status_text = i18n_get_text(language, "admin.incident_status_on", "admin_incident_status_on") if is_active else i18n_get_text(language, "admin.incident_status_off", "admin_incident_status_off")
    incident_title = i18n_get_text(language, "admin.incident_title", "admin_incident_title")
    incident_label = i18n_get_text(language, "admin.incident_text_label", "admin_incident_text_label")
    text = f"{incident_title}\n\n{status_text}\n\n{incident_label}\n{incident_text}"
    
    toggle_text = i18n_get_text(language, "admin.incident_enable", "admin_incident_enable") if not is_active else i18n_get_text(language, "admin.incident_disable", "admin_incident_disable")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=toggle_text,
            callback_data="admin:incident:toggle"
        )],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.incident_edit"), callback_data="admin:incident:edit")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone("admin_view_incident", callback.from_user.id, None, f"Viewed incident settings (active: {is_active})")


@admin_finance_router.callback_query(F.data == "admin:incident:toggle")
async def callback_admin_incident_toggle(callback: CallbackQuery):
    """Переключение режима инцидента"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    incident = await database.get_incident_settings()
    new_state = not incident["is_active"]
    
    await database.set_incident_mode(new_state)
    
    action = "включен" if new_state else "выключен"
    await callback.answer(f"Режим инцидента {action}", show_alert=True)
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone(
        "incident_mode_toggled",
        callback.from_user.id,
        None,
        f"Incident mode {'enabled' if new_state else 'disabled'}"
    )
    
    # Возвращаемся к экрану инцидента
    # Re-call the incident handler to refresh the screen
    language = await resolve_user_language(callback.from_user.id)
    incident = await database.get_incident_settings()
    is_active = incident["is_active"]
    incident_text = incident.get("incident_text") or "Текст не указан"
    
    status_text = i18n_get_text(language, "admin.incident_status_on", "admin_incident_status_on") if is_active else i18n_get_text(language, "admin.incident_status_off", "admin_incident_status_off")
    incident_title = i18n_get_text(language, "admin.incident_title", "admin_incident_title")
    incident_label = i18n_get_text(language, "admin.incident_text_label", "admin_incident_text_label")
    text = f"{incident_title}\n\n{status_text}\n\n{incident_label}\n{incident_text}"
    
    toggle_text = i18n_get_text(language, "admin.incident_enable", "admin_incident_enable") if not is_active else i18n_get_text(language, "admin.incident_disable", "admin_incident_disable")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=toggle_text,
            callback_data="admin:incident:toggle"
        )],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.incident_edit"), callback_data="admin:incident:edit")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)


@admin_finance_router.callback_query(F.data == "admin:incident:edit")
async def callback_admin_incident_edit(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования текста инцидента"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.incident_text_prompt")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:incident")],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await state.set_state(IncidentEdit.waiting_for_text)


@admin_finance_router.message(IncidentEdit.waiting_for_text)
async def process_incident_text(message: Message, state: FSMContext):
    """Обработка текста инцидента"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено", parse_mode="HTML")
        return
    
    incident_text = message.text
    
    # Включаем режим инцидента и сохраняем текст
    await database.set_incident_mode(True, incident_text)
    
    await message.answer(f"✅ Текст инцидента сохранён. Режим инцидента включён.", parse_mode="HTML")
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone(
        "incident_text_updated",
        message.from_user.id,
        None,
        f"Incident text updated: {incident_text[:50]}..."
    )
    
    await state.clear()


@admin_finance_router.callback_query(F.data == "admin:balance_management")
async def callback_admin_balance_management_start(callback: CallbackQuery, state: FSMContext):
    """💰 Управление балансом - запрос поиска пользователя"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.balance_management_prompt", "admin_balance_management_prompt")
    await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
    await state.set_state(AdminBalanceManagement.waiting_for_user_search)
    await callback.answer()


@admin_finance_router.message(AdminBalanceManagement.waiting_for_user_search)
async def process_admin_balance_user_search(message: Message, state: FSMContext):
    """Обработка поиска пользователя для управления балансом → показ профиля с ➕➖"""
    language = await resolve_user_language(message.from_user.id)
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await message.answer(i18n_get_text(language, "admin.access_denied"), parse_mode="HTML")
        await state.clear()
        return
    try:
        user_input = message.text.strip()
        try:
            target_user_id = int(user_input)
            user = await database.find_user_by_id_or_username(telegram_id=target_user_id)
        except ValueError:
            username = user_input.lstrip('@').lower()
            user = await database.find_user_by_id_or_username(username=username)
        if not user:
            await message.answer(i18n_get_text(language, "admin.user_not_found_check_id"), parse_mode="HTML")
            return
        target_user_id = user["telegram_id"]
        balance = await database.get_user_balance(target_user_id)
        subscription = await database.get_subscription(target_user_id)
        has_active = is_subscription_active(subscription) if subscription else False
        sub_text = i18n_get_text(language, "admin.no_active_subscription") if not has_active else "Подписка активна"
        text = (
            f"💰 Управление балансом\n\n"
            f"👤 Пользователь: {target_user_id}\n"
            f"📊 Баланс: {balance:.2f} ₽\n"
            f"📶 {sub_text}\n\n"
            f"Выберите действие:"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Пополнить", callback_data=f"admin:credit_balance:{target_user_id}")],
            [InlineKeyboardButton(text="➖ Снять", callback_data=f"admin:debit_balance:{target_user_id}")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
        ])
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await state.clear()
    except Exception as e:
        logging.exception(f"Error in process_admin_balance_user_search: {e}")
        await message.answer("Ошибка при поиске пользователя.", parse_mode="HTML")
        await state.clear()


@admin_finance_router.callback_query(F.data == "admin:credit_balance")
async def callback_admin_credit_balance_start(callback: CallbackQuery, state: FSMContext):
    """Начало процесса выдачи средств - запрос поиска пользователя (legacy entry)"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.credit_balance_prompt", "admin_credit_balance_prompt")
    await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
    await state.set_state(AdminCreditBalance.waiting_for_user_search)
    await callback.answer()


@admin_finance_router.callback_query(F.data.startswith("admin:credit_balance:"))
async def callback_admin_credit_balance_user(callback: CallbackQuery, state: FSMContext):
    """Начало процесса выдачи средств для конкретного пользователя"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        await state.update_data(target_user_id=user_id)
        
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        text = i18n_get_text(language, "admin.credit_balance_user_prompt", user_id=user_id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel", "admin_cancel"), callback_data=f"admin:user")]
        ])
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await state.set_state(AdminCreditBalance.waiting_for_amount)
        await callback.answer()
    except Exception as e:
        logging.exception(f"Error in callback_admin_credit_balance_user: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.message(AdminCreditBalance.waiting_for_user_search)
async def process_admin_credit_balance_user_search(message: Message, state: FSMContext):
    """Обработка поиска пользователя для выдачи средств"""
    language = await resolve_user_language(message.from_user.id)
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await message.answer(i18n_get_text(language, "admin.access_denied"), parse_mode="HTML")
        await state.clear()
        return
    
    try:
        user_input = message.text.strip()
        
        # Определяем, является ли ввод числом (ID) или строкой (username)
        try:
            target_user_id = int(user_input)
            user = await database.find_user_by_id_or_username(telegram_id=target_user_id)
        except ValueError:
            username = user_input.lstrip('@').lower()
            user = await database.find_user_by_id_or_username(username=username)
        
        if not user:
            await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.", parse_mode="HTML")
            await state.clear()
            return
        
        target_user_id = user["telegram_id"]
        await state.update_data(target_user_id=target_user_id)
        
        text = f"💰 Выдать средства\n\nПользователь: {target_user_id}\n\nВведите сумму в рублях:"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:main")]
        ])
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(AdminCreditBalance.waiting_for_amount)
        
    except Exception as e:
        logging.exception(f"Error in process_admin_credit_balance_user_search: {e}")
        await message.answer("Ошибка при поиске пользователя. Проверьте логи.", parse_mode="HTML")
        await state.clear()


@admin_finance_router.message(AdminCreditBalance.waiting_for_amount)
async def process_admin_credit_balance_amount(message: Message, state: FSMContext):
    """Обработка ввода суммы для выдачи средств"""
    language = await resolve_user_language(message.from_user.id)
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await message.answer(i18n_get_text(language, "admin.access_denied"), parse_mode="HTML")
        await state.clear()
        return
    
    try:
        amount = float(message.text.strip().replace(",", "."))
        
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительным числом.\n\nВведите сумму в рублях:", parse_mode="HTML")
            return

        # SECURITY: Limit single admin balance adjustment to prevent accidental/malicious large credits
        ADMIN_MAX_SINGLE_CREDIT = 50000  # 50,000 RUB max per operation
        if amount > ADMIN_MAX_SINGLE_CREDIT:
            await message.answer(
                f"❌ Максимальная сумма одной операции: {ADMIN_MAX_SINGLE_CREDIT:.0f} ₽\n\n"
                f"Для сумм свыше {ADMIN_MAX_SINGLE_CREDIT:.0f} ₽ выполните несколько операций.\n"
                f"Введите сумму в рублях:",
                parse_mode="HTML",
            )
            logger.warning(
                f"ADMIN_CREDIT_LIMIT_EXCEEDED: admin={message.from_user.id}, "
                f"attempted_amount={amount:.2f}, limit={ADMIN_MAX_SINGLE_CREDIT}"
            )
            return

        data = await state.get_data()
        target_user_id = data.get("target_user_id")

        if not target_user_id:
            await message.answer("Ошибка: пользователь не найден. Начните заново.", parse_mode="HTML")
            await state.clear()
            return

        # Сохраняем сумму и показываем подтверждение
        await state.update_data(amount=amount)
        
        user = await database.get_user(target_user_id)
        current_balance = await database.get_user_balance(target_user_id) if user else 0.0
        new_balance = current_balance + amount
        
        text = (
            f"💰 Подтверждение выдачи средств\n\n"
            f"👤 Пользователь: {target_user_id}\n"
            f"💳 Текущий баланс: {current_balance:.2f} ₽\n"
            f"➕ Сумма к выдаче: {amount:.2f} ₽\n"
            f"💵 Новый баланс: {new_balance:.2f} ₽\n\n"
            f"Подтвердите операцию:"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.confirm"), callback_data="admin:credit_balance_confirm"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:credit_balance_cancel")
            ]
        ])
        
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(AdminCreditBalance.waiting_for_confirmation)
        
    except ValueError:
        await message.answer("❌ Неверный формат суммы.\n\nВведите число (например: 500 или 100.50):", parse_mode="HTML")
    except Exception as e:
        logging.exception(f"Error in process_admin_credit_balance_amount: {e}")
        await message.answer("Ошибка при обработке суммы. Проверьте логи.", parse_mode="HTML")
        await state.clear()


@admin_finance_router.callback_query(F.data == "admin:credit_balance_confirm")
async def callback_admin_credit_balance_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение выдачи средств"""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        data = await state.get_data()
        target_user_id = data.get("target_user_id")
        amount = data.get("amount")
        
        if not target_user_id or not amount:
            await callback.answer("Ошибка: данные не найдены", show_alert=True)
            await state.clear()
            return
        
        # Начисляем баланс
        success = await database.increase_balance(
            telegram_id=target_user_id,
            amount=amount,
            source="admin",
            description=f"Выдача средств администратором {callback.from_user.id}"
        )
        
        if success:
            # Логируем операцию
            await database._log_audit_event_atomic_standalone(
                "admin_credit_balance",
                callback.from_user.id,
                target_user_id,
                f"Admin credited balance: {amount:.2f} RUB"
            )
            
            # Отправляем уведомление пользователю
            try:
                new_balance = await database.get_user_balance(target_user_id)
                notification_text = f"💰 Администратор начислил вам {amount:.2f} ₽ на баланс.\n\nТекущий баланс: {new_balance:.2f} ₽"
                await bot.send_message(chat_id=target_user_id, text=notification_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Failed to send balance credit notification to user {target_user_id}: {e}")
            
            new_balance = await database.get_user_balance(target_user_id)
            text = (
                f"✅ Средства успешно начислены\n\n"
                f"👤 Пользователь: {target_user_id}\n"
                f"➕ Сумма: {amount:.2f} ₽\n"
                f"💵 Новый баланс: {new_balance:.2f} ₽"
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
            ])
            
            # Site sync (fire-and-forget)
            try:
                from app.services.site_sync import sync_balance, is_enabled as _ss
                if _ss():
                    import asyncio
                    asyncio.ensure_future(sync_balance(target_user_id))
            except Exception:
                pass

            await safe_edit_text(callback.message, text, reply_markup=keyboard)
            await state.clear()
            await callback.answer("✅ Средства начислены", show_alert=True)
        else:
            await callback.answer("❌ Ошибка при начислении средств", show_alert=True)
            await state.clear()
            
    except Exception as e:
        logging.exception(f"Error in callback_admin_credit_balance_confirm: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)
        await state.clear()


@admin_finance_router.callback_query(F.data == "admin:credit_balance_cancel")
async def callback_admin_credit_balance_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена выдачи средств"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.message.edit_text(
        i18n_get_text(language, "admin.operation_cancelled"),
        reply_markup=get_admin_back_keyboard(language),
        parse_mode="HTML",
    )
    await state.clear()
    await callback.answer()


# --- Admin debit (снятие средств) ---


@admin_finance_router.callback_query(F.data.startswith("admin:debit_balance:"))
async def callback_admin_debit_balance_start(callback: CallbackQuery, state: FSMContext):
    """Начало процесса снятия средств с баланса пользователя"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    try:
        user_id = int(callback.data.split(":")[2])
        await state.update_data(target_user_id=user_id)
        language = await resolve_user_language(callback.from_user.id)
        balance = await database.get_user_balance(user_id)
        text = i18n_get_text(language, "admin.debit_prompt", user_id=user_id, balance=balance)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:main")]
        ])
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await state.set_state(AdminDebitBalance.waiting_for_amount)
        await callback.answer()
    except Exception as e:
        logging.exception(f"Error in callback_admin_debit_balance_start: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_finance_router.message(AdminDebitBalance.waiting_for_amount)
async def process_admin_debit_amount(message: Message, state: FSMContext):
    """Обработка ввода суммы для снятия средств"""
    language = await resolve_user_language(message.from_user.id)
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await message.answer(i18n_get_text(language, "admin.access_denied"), parse_mode="HTML")
        await state.clear()
        return
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительной.", parse_mode="HTML")
            return

        # SECURITY: Limit single admin debit to prevent accidental large debits
        ADMIN_MAX_SINGLE_DEBIT = 50000  # 50,000 RUB max per operation
        if amount > ADMIN_MAX_SINGLE_DEBIT:
            await message.answer(
                f"❌ Максимальная сумма одной операции: {ADMIN_MAX_SINGLE_DEBIT:.0f} ₽\n"
                f"Введите сумму в рублях:",
                parse_mode="HTML",
            )
            logger.warning(
                f"ADMIN_DEBIT_LIMIT_EXCEEDED: admin={message.from_user.id}, "
                f"attempted_amount={amount:.2f}, limit={ADMIN_MAX_SINGLE_DEBIT}"
            )
            return

        data = await state.get_data()
        target_user_id = data.get("target_user_id")
        if not target_user_id:
            await message.answer("Ошибка: пользователь не найден.", parse_mode="HTML")
            await state.clear()
            return
        balance = await database.get_user_balance(target_user_id)
        if amount > balance:
            await message.answer(i18n_get_text(language, "admin.debit_insufficient", balance=balance), parse_mode="HTML")
            return
        await state.update_data(amount=amount)
        text = (
            f"➖ Подтверждение снятия\n\n"
            f"👤 Пользователь: {target_user_id}\n"
            f"💳 Баланс: {balance:.2f} ₽\n"
            f"➖ Сумма к снятию: {amount:.2f} ₽\n"
            f"💵 Новый баланс: {balance - amount:.2f} ₽\n\n"
            f"Подтвердите операцию:"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=i18n_get_text(language, "admin.confirm"), callback_data="admin:debit_confirm"),
                InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:debit_cancel")
            ]
        ])
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(AdminDebitBalance.waiting_for_confirmation)
    except ValueError:
        await message.answer("❌ Неверный формат суммы. Введите число:", parse_mode="HTML")
    except Exception as e:
        logging.exception(f"Error in process_admin_debit_amount: {e}")
        await message.answer("Ошибка при обработке суммы.", parse_mode="HTML")
        await state.clear()


@admin_finance_router.callback_query(F.data == "admin:debit_confirm")
async def callback_admin_debit_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение снятия средств"""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    try:
        data = await state.get_data()
        target_user_id = data.get("target_user_id")
        amount = data.get("amount")
        if not target_user_id or not amount:
            await callback.answer("Ошибка: данные не найдены", show_alert=True)
            await state.clear()
            return
        success = await database.decrease_balance(
            telegram_id=target_user_id,
            amount=amount,
            source="admin",
            description=f"Снятие средств администратором {callback.from_user.id}"
        )
        if success:
            await database._log_audit_event_atomic_standalone(
                "admin_debit_balance", callback.from_user.id, target_user_id,
                f"Admin debited balance: {amount:.2f} RUB"
            )
            try:
                notif = i18n_get_text(language, "admin.debit_user_notification", amount=amount)
                await bot.send_message(chat_id=target_user_id, text=notif, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Failed to send debit notification to user {target_user_id}: {e}")
            new_balance = await database.get_user_balance(target_user_id)
            text = i18n_get_text(language, "admin.debit_success", user_id=target_user_id, amount=amount, new_balance=new_balance)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
            ])
            # Site sync (fire-and-forget)
            try:
                from app.services.site_sync import sync_balance, is_enabled as _ss
                if _ss():
                    import asyncio
                    asyncio.ensure_future(sync_balance(target_user_id))
            except Exception:
                pass

            await safe_edit_text(callback.message, text, reply_markup=keyboard)
            await state.clear()
            await callback.answer("✅ Средства сняты", show_alert=True)
        else:
            await callback.answer(i18n_get_text(language, "admin.debit_insufficient", balance=await database.get_user_balance(target_user_id)), show_alert=True)
    except Exception as e:
        logging.exception(f"Error in callback_admin_debit_confirm: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)
        await state.clear()


@admin_finance_router.callback_query(F.data == "admin:debit_cancel")
async def callback_admin_debit_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена снятия средств"""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.message.edit_text(i18n_get_text(language, "admin.operation_cancelled"), reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
    await state.clear()
    await callback.answer()


# ====================================================================================
# GLOBAL FALLBACK HANDLER: Обработка необработанных callback_query
# ====================================================================================
