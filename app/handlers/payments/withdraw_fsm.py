"""
Withdraw FSM message handlers: WithdrawStates.withdraw_amount, WithdrawStates.withdraw_requisites

FSM lifecycle: state is valid ONLY while user is on withdrawal screens.
Navigation away MUST clear state. No state leakage.
"""
import logging
import re
import unicodedata

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
MAX_WITHDRAW_RUBLES = 1_000_000

# Строгий паттерн: только ASCII-цифры, опционально одна точка/запятая + копейки
_AMOUNT_PATTERN = re.compile(r"^\d{1,7}([.,]\d{1,2})?$")

# Реквизиты: номер карты (16-19 цифр, с пробелами) или телефон (+7XXXXXXXXXX)
# Макс 22 символа = 16 цифр карты + 6 на пробелы/дефисы
_REQUISITES_PATTERN = re.compile(r"^[\d\s\-+]{5,22}$")

# Максимум неудачных попыток ввода
_MAX_ATTEMPTS = 5


def _sanitize_text(text: str) -> str:
    """Убирает control-символы, zero-width, RTL/LTR marks и прочий невидимый unicode."""
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        # Cc=control, Cf=format (zero-width, RTL, etc.), Co=private use, Cs=surrogate
        if cat.startswith(("Cc", "Cf", "Co", "Cs")):
            continue
        cleaned.append(ch)
    return "".join(cleaned).strip()


@payments_router.message(StateFilter(WithdrawStates.withdraw_amount))
async def process_withdraw_amount(message: Message, state: FSMContext):
    """Обработка суммы вывода (мин 500 ₽, <= баланс). Только в контексте withdrawal screen."""
    current_state = await state.get_state()
    if current_state != WithdrawStates.withdraw_amount.state:
        return

    # Игнорируем не-текстовые сообщения (фото, стикеры и т.д.)
    if not message.text:
        return

    # Commands: clear state and let other handlers process
    raw = message.text.strip()
    if raw.startswith("/"):
        await state.clear()
        return
    if not await ensure_db_ready_message(message):
        await state.clear()
        return

    language = await resolve_user_language(message.from_user.id)

    # Санитизация: убираем невидимые юникод-символы
    cleaned = _sanitize_text(raw)

    # Строгая валидация: только ASCII-цифры + опционально копейки
    if not _AMOUNT_PATTERN.match(cleaned):
        fsm_data = await state.get_data()
        attempts = fsm_data.get("withdraw_amount_attempts", 0) + 1
        if attempts >= _MAX_ATTEMPTS:
            await state.clear()
            await message.answer(i18n_get_text(language, "withdraw.too_many_attempts"))
            return
        await state.update_data(withdraw_amount_attempts=attempts)
        await message.answer(i18n_get_text(language, "errors.invalid_amount"))
        return

    amount = float(cleaned.replace(",", "."))

    if amount < MIN_WITHDRAW_RUBLES:
        await message.answer(i18n_get_text(language, "withdraw.min_amount_error"))
        return
    if amount > MAX_WITHDRAW_RUBLES:
        await message.answer(i18n_get_text(language, "errors.invalid_amount"))
        return

    balance = await database.get_user_balance(message.from_user.id)
    if amount > balance:
        await message.answer(i18n_get_text(language, "withdraw.insufficient_funds"))
        return

    await state.update_data(withdraw_amount=amount, withdraw_amount_attempts=0)
    await state.set_state(WithdrawStates.withdraw_confirm)
    text = i18n_get_text(language, "withdraw.confirm_amount", amount=amount)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.confirm"), callback_data="withdraw_confirm_amount")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="withdraw_cancel")]
    ])
    await message.answer(text, reply_markup=keyboard)


@payments_router.message(StateFilter(WithdrawStates.withdraw_requisites))
async def process_withdraw_requisites(message: Message, state: FSMContext):
    """Обработка реквизитов → финальное подтверждение. Только в контексте withdrawal screen."""
    current_state = await state.get_state()
    if current_state != WithdrawStates.withdraw_requisites.state:
        return

    # Игнорируем не-текстовые сообщения
    if not message.text:
        return

    raw = message.text.strip()
    if raw.startswith("/"):
        await state.clear()
        return
    if not await ensure_db_ready_message(message):
        await state.clear()
        return

    language = await resolve_user_language(message.from_user.id)

    # Санитизация: убираем невидимые юникод-символы
    requisites = _sanitize_text(raw)

    # Строгая валидация: только допустимые символы, 5-120 знаков
    if not _REQUISITES_PATTERN.match(requisites):
        fsm_data = await state.get_data()
        attempts = fsm_data.get("withdraw_req_attempts", 0) + 1
        if attempts >= _MAX_ATTEMPTS:
            await state.clear()
            await message.answer(i18n_get_text(language, "withdraw.too_many_attempts"))
            return
        await state.update_data(withdraw_req_attempts=attempts)
        await message.answer(i18n_get_text(language, "withdraw.invalid_requisites"))
        return

    await state.update_data(withdraw_requisites=requisites, withdraw_req_attempts=0)
    await state.set_state(WithdrawStates.withdraw_final_confirm)
    data = await state.get_data()
    amount = data["withdraw_amount"]
    text = i18n_get_text(language, "withdraw.final_confirm", amount=amount, requisites=requisites)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.confirm"), callback_data="withdraw_final_confirm")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="withdraw_back_to_requisites")]
    ])
    await message.answer(text, reply_markup=keyboard)
