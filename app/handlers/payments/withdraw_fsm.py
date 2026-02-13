"""
Withdraw FSM message handlers: WithdrawStates.withdraw_amount, WithdrawStates.withdraw_requisites

FSM lifecycle: state is valid ONLY while user is on withdrawal screens.
Navigation away MUST clear state. No state leakage.
"""
import logging

from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_message
from app.handlers.common.states import WithdrawStates

payments_router = Router()
logger = logging.getLogger(__name__)

# --- User withdrawal flow ---
MIN_WITHDRAW_RUBLES = 500


@payments_router.message(StateFilter(WithdrawStates.withdraw_amount))
async def process_withdraw_amount(message: Message, state: FSMContext):
    """Обработка суммы вывода (мин 500 ₽, <= баланс). Только в контексте withdrawal screen."""
    # Safety guard: exit if state changed (user navigated away)
    current_state = await state.get_state()
    if current_state != WithdrawStates.withdraw_amount.state:
        return
    # Commands: clear state and let other handlers process
    text = (message.text or "").strip()
    if text.startswith("/"):
        await state.clear()
        return
    if not await ensure_db_ready_message(message):
        await state.clear()
        return
    language = await resolve_user_language(message.from_user.id)
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount < MIN_WITHDRAW_RUBLES:
            await message.answer(i18n_get_text(language, "withdraw.min_amount_error"))
            return
        balance = await database.get_user_balance(message.from_user.id)
        if amount > balance:
            await message.answer(i18n_get_text(language, "withdraw.insufficient_funds"))
            return
        await state.update_data(withdraw_amount=amount)
        await state.set_state(WithdrawStates.withdraw_confirm)
        text = i18n_get_text(language, "withdraw.confirm_amount", amount=amount)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.confirm"), callback_data="withdraw_confirm_amount")],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="withdraw_cancel")]
        ])
        await message.answer(text, reply_markup=keyboard)
    except ValueError:
        await message.answer(i18n_get_text(language, "errors.invalid_amount"))


@payments_router.message(StateFilter(WithdrawStates.withdraw_requisites))
async def process_withdraw_requisites(message: Message, state: FSMContext):
    """Обработка реквизитов → финальное подтверждение. Только в контексте withdrawal screen."""
    current_state = await state.get_state()
    if current_state != WithdrawStates.withdraw_requisites.state:
        return
    text = (message.text or "").strip()
    if text.startswith("/"):
        await state.clear()
        return
    if not await ensure_db_ready_message(message):
        await state.clear()
        return
    language = await resolve_user_language(message.from_user.id)
    requisites = (message.text or "").strip()
    if len(requisites) < 5:
        await message.answer("Укажите корректные реквизиты (минимум 5 символов).")
        return
    await state.update_data(withdraw_requisites=requisites)
    await state.set_state(WithdrawStates.withdraw_final_confirm)
    data = await state.get_data()
    amount = data["withdraw_amount"]
    text = i18n_get_text(language, "withdraw.final_confirm", amount=amount, requisites=requisites[:80])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.confirm"), callback_data="withdraw_final_confirm")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="withdraw_back_to_requisites")]
    ])
    await message.answer(text, reply_markup=keyboard)
