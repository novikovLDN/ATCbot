"""Мастер создания промокода — экраны выбора, подтверждение и отмена.

ВНИМАНИЕ: МАСТЕР НЕ ДОВЕДЁН ДО КОНЦА, СОЗДАТЬ ПРОМОКОД ЧЕРЕЗ НЕГО НЕЛЬЗЯ
    У состояний AdminCreatePromocode нет НИ ОДНОГО обработчика сообщений:
    admin:create_promocode переводит FSM в waiting_for_code_name, админ
    вводит код — и его сообщение ловить некому. Ключи, которые читает
    admin:promocode_confirm (promocode_code, promocode_discount,
    promocode_duration_seconds, promocode_max_uses), не пишет никто, поэтому
    подтверждение всегда упирается в «Ошибка: неполные данные».

    Код перенесён как есть: удаление раздела — решение владельца, а не
    рефакторинга. Не чините здесь «баг», не прочитав это: экраны выглядят
    рабочими, но сценарий обрывается на первом же вводе.

ПОЧЕМУ ОТДЕЛЬНО
    Единственный FSM-мастер, оставшийся в этом файле. Даже недоведённый, он
    не должен мешаться в экране входа: FSM-состояния — самая частая причина
    «бот завис и не отвечает на команды».

ЧТО ЛЕГКО СЛОМАТЬ
    Отмена обязана делать state.clear(). Забытая очистка оставляет админа в
    состоянии, где бот молча съедает все следующие сообщения.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.admin.keyboards import get_admin_dashboard_keyboard, get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.states import AdminCreatePromocode

admin_promocodes_router = Router()
logger = logging.getLogger(__name__)


@admin_promocodes_router.callback_query(F.data == "admin:create_promocode")
async def callback_admin_create_promocode(callback: CallbackQuery, state: FSMContext):
    """Начало создания промокода"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)
    await state.set_state(AdminCreatePromocode.waiting_for_code_name)
    logger.info("PROMO_STATE_SET waiting_for_code_name")
    text = i18n_get_text(language, "admin.promocode_code_prompt")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:promocode_cancel")]
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@admin_promocodes_router.callback_query(F.data.startswith("admin:promocode_unit:"))
async def callback_admin_promocode_unit(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора единицы времени"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)
    unit = callback.data.split(":")[2]  # hours, days, months
    
    unit_names = {
        "hours": "часов",
        "days": "дней",
        "months": "месяцев"
    }
    
    await state.update_data(promocode_duration_unit=unit)
    await state.set_state(AdminCreatePromocode.waiting_for_duration_value)
    logger.info("PROMO_STATE_SET waiting_for_duration_value unit=%s", unit)
    text = i18n_get_text(language, "admin.promocode_duration_value_prompt", unit=unit_names[unit])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:promocode_cancel")]
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@admin_promocodes_router.callback_query(F.data == "admin:promocode_confirm")
async def callback_admin_promocode_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение создания промокода"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)
    data = await state.get_data()
    
    code = data.get("promocode_code")
    discount_percent = data.get("promocode_discount")
    duration_seconds = data.get("promocode_duration_seconds")
    max_uses = data.get("promocode_max_uses")
    
    if not all([code, discount_percent is not None, duration_seconds, max_uses]):
        await callback.answer("Ошибка: неполные данные", show_alert=True)
        await state.clear()
        return
    
    # Создаем промокод
    result = await database.create_promocode_atomic(
        code=code,
        discount_percent=discount_percent,
        duration_seconds=duration_seconds,
        max_uses=max_uses,
        created_by=callback.from_user.id
    )
    
    if result:
        # Форматируем длительность для отображения
        if duration_seconds < 3600:
            duration_str = f"{duration_seconds // 60} минут"
        elif duration_seconds < 86400:
            duration_str = f"{duration_seconds // 3600} часов"
        elif duration_seconds < 2592000:
            duration_str = f"{duration_seconds // 86400} дней"
        else:
            duration_str = f"{duration_seconds // 2592000} месяцев"
        
        text = i18n_get_text(
            language, "admin.promocode_created",
            code=code,
            discount=discount_percent,
            duration=duration_str,
            max_uses=max_uses
        )
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer("✅ Промокод создан", show_alert=True)
    else:
        text = i18n_get_text(language, "admin.promocode_creation_failed")
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer("❌ Ошибка создания", show_alert=True)
    
    await state.clear()


@admin_promocodes_router.callback_query(F.data == "admin:promocode_cancel")
async def callback_admin_promocode_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена создания промокода"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)
    await state.clear()
    text = i18n_get_text(language, "admin.dashboard_title")
    await safe_edit_text(callback.message, text, reply_markup=get_admin_dashboard_keyboard(language))
    await callback.answer()
