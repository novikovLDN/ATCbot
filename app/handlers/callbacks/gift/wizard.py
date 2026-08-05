"""Подарочная подписка: мастер покупки (тариф → период → способ оплаты).

ЧТО ЗДЕСЬ
    Первые три шага: экран подарка, выбор тарифа, выбор периода со
    сборкой списка способов оплаты.

ПОЧЕМУ ВЫДЕЛЕНО
    Это чистая навигация: ни денег, ни записей о подарке. Соседний
    payment.py, наоборот, списывает баланс и выставляет счета.

ЧТО ЛЕГКО СЛОМАТЬ
    Шаги завязаны на состояние FSM (choose_tariff → choose_period →
    choose_payment_method), и обработчики фильтруются по нему. Уберёте
    состояние из фильтра — кнопка из старого сообщения снова откроет
    середину мастера с пустыми данными.

    Способы оплаты собираются по факту доступности провайдера
    (is_enabled). Выключенный провайдер обязан исчезать из клавиатуры, а
    не отвечать ошибкой после нажатия.

    Подарить можно только basic и plus — комбо здесь намеренно нет.
"""
import logging

import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.states import GiftState
from app.handlers.callbacks.gift.formatting import _period_display, _tariff_display_name

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "gift_subscription")
async def callback_gift_start(callback: CallbackQuery, state: FSMContext):
    """Экран подарочной подписки — выбор тарифа."""
    if not await ensure_db_ready_callback(callback):
        return

    await callback.answer()
    await state.clear()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "gift.intro")

    # Только basic и plus для подарков
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📦 Basic",
            callback_data="gift_tariff:basic"
        )],
        [InlineKeyboardButton(
            text="⚡ Plus",
            callback_data="gift_tariff:plus"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])

    # Photo screen: drop previous message (text or photo) and send a fresh
    # photo-with-caption.  _send_screen_photo falls back to text if needed.
    try:
        await callback.message.delete()
    except Exception:
        pass
    from app.handlers.common.screens import _send_screen_photo, GIFT_PHOTO_FILE_ID
    await _send_screen_photo(
        callback.bot, telegram_id, GIFT_PHOTO_FILE_ID, text,
        reply_markup=keyboard, parse_mode="HTML",
    )
    await state.set_state(GiftState.choose_tariff)


# ====================================================================================
# STEP 2: Выбор тарифа → экран выбора периода
# ====================================================================================

@router.callback_query(F.data.startswith("gift_tariff:"), GiftState.choose_tariff)
async def callback_gift_tariff(callback: CallbackQuery, state: FSMContext):
    """Выбор тарифа для подарка → показываем периоды."""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    tariff = callback.data.split(":")[1]
    if tariff not in ("basic", "plus"):
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return

    await callback.answer()
    await state.update_data(gift_tariff=tariff)

    tariff_name = _tariff_display_name(tariff)
    tariff_prices = config.TARIFFS.get(tariff, {})

    text = i18n_get_text(language, "gift.choose_period", tariff_name=tariff_name)

    from app.handlers.payments.callbacks import _period_badge

    buttons = []
    for period_days in sorted(tariff_prices.keys()):
        price = tariff_prices[period_days]["price"]
        period_text = _period_display(period_days)
        badge = _period_badge(period_days)
        btn_text = f"{period_text} — {price} ₽"
        if badge:
            btn_text = f"{btn_text} {badge}"
        buttons.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"gift_period:{period_days}"
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="gift_subscription"
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
    await state.set_state(GiftState.choose_period)


# ====================================================================================
# STEP 3: Выбор периода → экран выбора способа оплаты
# ====================================================================================

@router.callback_query(F.data.startswith("gift_period:"), GiftState.choose_period)
async def callback_gift_period(callback: CallbackQuery, state: FSMContext):
    """Выбор периода → показываем способы оплаты."""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    period_str = callback.data.split(":")[1]
    try:
        period_days = int(period_str)
    except ValueError:
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return

    fsm_data = await state.get_data()
    tariff = fsm_data.get("gift_tariff")
    if not tariff or tariff not in config.TARIFFS:
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        return

    if period_days not in config.TARIFFS[tariff]:
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return

    price_rubles = config.TARIFFS[tariff][period_days]["price"]
    price_kopecks = price_rubles * 100

    await callback.answer()
    await state.update_data(
        gift_period_days=period_days,
        gift_price_kopecks=price_kopecks,
    )

    tariff_name = _tariff_display_name(tariff)
    period_text = _period_display(period_days)

    text = i18n_get_text(
        language, "gift.choose_payment",
        tariff_name=tariff_name,
        period=period_text,
        price=price_rubles,
    )

    # Получаем баланс для кнопки
    balance = await database.get_user_balance(telegram_id)

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_balance", balance=balance),
            callback_data="gift_pay:balance"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_with_card"),
            callback_data="gift_pay:card"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.stars", "⭐ Telegram Stars"),
            callback_data="gift_pay:stars"
        )],
    ]

    # CryptoBot — если настроен
    import cryptobot_service
    if cryptobot_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.crypto", "🌎 CryptoBot"),
            callback_data="gift_pay:crypto"
        )])

    # Lava (card) — если настроен
    import lava_service
    if lava_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.lava", "📱 СБП 3%"),
            callback_data="gift_pay:lava"
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="gift_subscription"
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
    await state.set_state(GiftState.choose_payment_method)
