"""
Admin promo creation FSM message handlers.
Must be included in admin router so messages in AdminCreatePromocode states are handled.
"""
import logging
from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.states import AdminCreatePromocode

admin_promo_fsm_router = Router()
logger = logging.getLogger(__name__)


@admin_promo_fsm_router.message(AdminCreatePromocode.waiting_for_code_name)
async def process_admin_promocode_code_name(message: Message, state: FSMContext):
    """Обработка имени промокода"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return

    language = await resolve_user_language(message.from_user.id)
    code_input = message.text.strip() if message.text else ""

    if not code_input:
        from database import generate_promo_code
        code = generate_promo_code(6)
    else:
        code = code_input.upper().strip()
        if len(code) < 3 or len(code) > 32:
            await message.answer(i18n_get_text(language, "admin.promocode_code_invalid"))
            return
        if not all(c.isalnum() for c in code):
            await message.answer(i18n_get_text(language, "admin.promocode_code_invalid"))
            return
        if await database.has_active_promo(code):
            await message.answer(i18n_get_text(language, "admin.promocode_code_exists"))
            return

    await state.update_data(promocode_code=code)
    await state.set_state(AdminCreatePromocode.waiting_for_discount_percent)
    logger.info("PROMO_STATE_SET waiting_for_discount_percent")

    text = i18n_get_text(language, "admin.promocode_discount_prompt")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:promocode_cancel")]
    ])
    await message.answer(text, reply_markup=keyboard)


@admin_promo_fsm_router.message(AdminCreatePromocode.waiting_for_discount_percent)
async def process_admin_promocode_discount(message: Message, state: FSMContext):
    """Обработка процента скидки"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return

    language = await resolve_user_language(message.from_user.id)
    try:
        discount_percent = int(message.text.strip())
        if discount_percent < 0 or discount_percent > 100:
            await message.answer(i18n_get_text(language, "admin.promocode_discount_invalid"))
            return
    except ValueError:
        await message.answer(i18n_get_text(language, "admin.promocode_discount_invalid"))
        return

    await state.update_data(promocode_discount=discount_percent)
    await state.set_state(AdminCreatePromocode.waiting_for_duration_unit)
    logger.info("PROMO_STATE_SET waiting_for_duration_unit")

    text = i18n_get_text(language, "admin.promocode_duration_unit_prompt")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Часы", callback_data="admin:promocode_unit:hours")],
        [InlineKeyboardButton(text="📅 Дни", callback_data="admin:promocode_unit:days")],
        [InlineKeyboardButton(text="🗓 Месяцы", callback_data="admin:promocode_unit:months")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:promocode_cancel")]
    ])
    await message.answer(text, reply_markup=keyboard)


@admin_promo_fsm_router.message(AdminCreatePromocode.waiting_for_duration_value)
async def process_admin_promocode_duration_value(message: Message, state: FSMContext):
    """Обработка значения длительности"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return

    language = await resolve_user_language(message.from_user.id)
    try:
        value = int(message.text.strip())
        if value <= 0:
            await message.answer(i18n_get_text(language, "admin.promocode_duration_invalid"))
            return
    except ValueError:
        await message.answer(i18n_get_text(language, "admin.promocode_duration_invalid"))
        return

    data = await state.get_data()
    unit = data.get("promocode_duration_unit")
    if unit == "hours":
        duration_seconds = value * 3600
    elif unit == "days":
        duration_seconds = value * 86400
    elif unit == "months":
        duration_seconds = value * 30 * 86400
    else:
        await message.answer("Ошибка: неверная единица времени")
        await state.clear()
        return

    await state.update_data(promocode_duration_seconds=duration_seconds)
    await state.set_state(AdminCreatePromocode.waiting_for_max_uses)
    logger.info("PROMO_STATE_SET waiting_for_max_uses")

    text = i18n_get_text(language, "admin.promocode_max_uses_prompt")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:promocode_cancel")]
    ])
    await message.answer(text, reply_markup=keyboard)


@admin_promo_fsm_router.message(AdminCreatePromocode.waiting_for_max_uses)
async def process_admin_promocode_max_uses(message: Message, state: FSMContext):
    """Обработка максимального количества использований"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return

    language = await resolve_user_language(message.from_user.id)
    try:
        max_uses = int(message.text.strip())
        if max_uses < 1:
            await message.answer(i18n_get_text(language, "admin.promocode_max_uses_invalid"))
            return
    except ValueError:
        await message.answer(i18n_get_text(language, "admin.promocode_max_uses_invalid"))
        return

    data = await state.get_data()
    code = data.get("promocode_code")
    discount_percent = data.get("promocode_discount")
    duration_seconds = data.get("promocode_duration_seconds")

    if duration_seconds < 3600:
        duration_str = f"{duration_seconds // 60} минут"
    elif duration_seconds < 86400:
        duration_str = f"{duration_seconds // 3600} часов"
    elif duration_seconds < 2592000:
        duration_str = f"{duration_seconds // 86400} дней"
    else:
        duration_str = f"{duration_seconds // 2592000} месяцев"

    await state.update_data(promocode_max_uses=max_uses)
    await state.set_state(AdminCreatePromocode.confirm_creation)
    logger.info("PROMO_STATE_SET confirm_creation")

    text = (
        f"🎟 Подтверждение создания промокода\n\n"
        f"Код: {code}\n"
        f"Скидка: {discount_percent}%\n"
        f"Срок действия: {duration_str}\n"
        f"Лимит использований: {max_uses}\n\n"
        f"Подтвердите создание:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.promocode_confirm"), callback_data="admin:promocode_confirm")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.promocode_cancel"), callback_data="admin:promocode_cancel")]
    ])
    await message.answer(text, reply_markup=keyboard)
