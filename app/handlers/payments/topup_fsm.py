"""
Top-up FSM message handlers: TopUpStates.waiting_for_amount
"""
import logging
import re
import unicodedata

from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_message
from app.handlers.common.states import TopUpStates

payments_router = Router()
logger = logging.getLogger(__name__)

# Строгий паттерн: только цифры, без пробелов, букв, юникода
_DIGITS_ONLY = re.compile(r"^\d{1,6}$")

# Максимум неудачных попыток ввода, после чего FSM сбрасывается
_MAX_ATTEMPTS = 3


def _sanitize_text(text: str) -> str:
    """Убирает control-символы, zero-width, RTL/LTR marks и прочий невидимый unicode."""
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith(("Cc", "Cf", "Co", "Cs")):
            continue
        cleaned.append(ch)
    return "".join(cleaned).strip()


@payments_router.message(TopUpStates.waiting_for_amount)
async def process_topup_amount(message: Message, state: FSMContext):
    """Обработка введенной суммы пополнения - показываем экран выбора способа оплаты"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_message(message):
        await state.clear()
        return

    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)

    # Игнорируем не-текстовые сообщения (фото, стикеры и т.д.)
    if not message.text:
        return

    raw_text = _sanitize_text(message.text.strip())

    # Строгая валидация: только ASCII-цифры, 1-6 символов
    if not _DIGITS_ONLY.match(raw_text):
        # Считаем неудачные попытки
        fsm_data = await state.get_data()
        attempts = fsm_data.get("topup_attempts", 0) + 1
        if attempts >= _MAX_ATTEMPTS:
            await state.clear()
            error_text = i18n_get_text(language, "main.topup_amount_invalid")
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "common.back"),
                    callback_data="topup_balance"
                )]
            ])
            await message.answer(error_text, reply_markup=back_kb, parse_mode="HTML")
            return
        await state.update_data(topup_attempts=attempts)
        error_text = i18n_get_text(language, "main.topup_amount_invalid")
        await message.answer(error_text, parse_mode="HTML")
        return

    amount = int(raw_text)

    # Проверяем минимальную сумму
    if amount < 100:
        error_text = i18n_get_text(language, "main.topup_amount_too_low")
        await message.answer(error_text, parse_mode="HTML")
        return

    # Проверяем максимальную сумму (технический лимит)
    if amount > 100000:
        error_text = i18n_get_text(language, "main.topup_amount_too_high")
        await message.answer(error_text, parse_mode="HTML")
        return

    # Очищаем FSM состояние
    await state.clear()

    # Показываем экран выбора способа оплаты
    text = i18n_get_text(language, "main.topup_select_payment_method", amount=amount)

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_with_card"),
            callback_data=f"topup_card:{amount}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.stars"),
            callback_data=f"topup_stars:{amount}"
        )],
    ]
    import lava_service
    if lava_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.lava"),
            callback_data=f"topup_lava:{amount}"
        )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="topup_balance"
    )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
