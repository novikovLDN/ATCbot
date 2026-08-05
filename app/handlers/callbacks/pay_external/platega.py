"""Оплата через Platega: карта РФ, международная карта и СБП.

ЧТО ЗДЕСЬ
    Три экрана одного провайдера и общий для них путь
    `_start_platega_payment`: проверка лимита, проверка состояния FSM,
    создание покупки, транзакция в Platega и кнопка со ссылкой на оплату.

ПОЧЕМУ ВЫДЕЛЕНО
    Три метода Platega отличаются ровно тремя вещами: кодом метода,
    наценкой и префиксом ключей i18n. Всё остальное у них общее — держать
    рядом с Telegram-инвойсами и криптой смысла нет.

ЧТО ЛЕГКО СЛОМАТЬ
    Наценка. `apply_markup` возвращает цену, которую человек реально
    заплатит, и она же уходит в pending_purchase. Разъедутся эти две
    цифры — в отчётности будет одна сумма, на счету другая.

    СБП живёт отдельной функцией, а не через общий путь, хотя выглядит
    почти так же: у неё вызов create_transaction без параметра method
    (метод по умолчанию на стороне Platega). Свести их в одну — значит
    поменять запрос к провайдеру, а это правка поведения, а не переноса.

    Ключи i18n собираются из префикса: `payment.{i18n_key}_waiting`,
    `_pay_button`, `_unavailable`. Опечатка в префиксе не падает — человек
    видит сырой ключ вместо текста.
"""
import logging

import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions import service as subscription_service
from app.core.rate_limit import check_rate_limit
from app.handlers.common.utils import get_promo_session
from app.handlers.common.states import PurchaseState

# ЗДЕСЬ АВТОУДАЛЕНИЯ СЧЁТА НЕТ — и это не забытая строка.
#
# Импорт _schedule_invoice_deletion стоял здесь неиспользованным: оба
# сообщения со ссылкой на оплату (карта и СБП) остаются в чате навсегда.
# Импорт убран, чтобы он не читался как работающий механизм.
#
# Включать ли удаление — решение владельца (реестр, followup-2026-08-05).
# Прежде чем включать, надо узнать срок жизни счёта у Platega: в
# platega_service.py его не передают, то есть подобрать таймаут не из чего.
# Взять общие 900 с наугад — значит, возможно, убирать сообщение, по
# которому ещё можно заплатить.

router = Router()
logger = logging.getLogger(__name__)


async def _start_platega_payment(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    method: int,
    apply_markup,
    i18n_key: str,
    log_tag: str,
):
    """Common entry path for any Platega payment method (SBP / Card / Intl).

    `apply_markup(price_kopecks) -> price_kopecks` returns the price with the
    method's markup applied (returns the same value if markup is 0).
    `i18n_key` is the prefix used for {key}_waiting / {key}_pay_button /
    {key}_unavailable lookups.
    """
    telegram_id = callback.from_user.id

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:{log_tag}: user={telegram_id}, state={current_state}")
        await state.set_state(None)
        return

    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    country = fsm_data.get("country")

    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None

    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM for {log_tag}: user={telegram_id}")
        await state.set_state(None)
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, f"payment.{i18n_key}_unavailable"), show_alert=True)
        logger.error("Platega not configured")
        return

    try:
        marked_price_kopecks = apply_markup(final_price_kopecks)

        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=marked_price_kopecks,
            promo_code=promo_code,
            country=country,
            is_combo=fsm_data.get("combo_bypass_gb", 0) > 0,
        )

        await state.update_data(purchase_id=purchase_id)

        logger.info(
            f"Purchase created for {log_tag} payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"base_price={final_price_kopecks}, marked_price={marked_price_kopecks}"
        )

        marked_price_rubles = marked_price_kopecks / 100.0

        tx_data = await platega_service.create_transaction(
            amount_rubles=marked_price_rubles,
            description=f"Atlas Secure VPN — {tariff_type} {period_days}d",
            purchase_id=purchase_id,
            method=method,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id))
        except Exception as e:
            logger.error(f"Failed to save transaction_id to DB: purchase_id={purchase_id}, error={e}")

        logger.info(
            f"invoice_created: provider=platega, method={method}, user={telegram_id}, "
            f"purchase_id={purchase_id}, transaction_id={transaction_id}, "
            f"price={marked_price_rubles:.2f}"
        )

        text = i18n_get_text(language, f"payment.{i18n_key}_waiting", amount=marked_price_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, f"payment.{i18n_key}_pay_button"),
                url=redirect_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_buy_vpn"
            )]
        ])

        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

        await state.set_state(None)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error creating Platega {log_tag} transaction: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.set_state(None)



@router.callback_query(F.data == "pay:card_pl")
async def callback_pay_card_pl(callback: CallbackQuery, state: FSMContext):
    """Оплата банковской картой через Platega (paymentMethod=11)."""
    import platega_service
    await _start_platega_payment(
        callback, state,
        method=platega_service.PAYMENT_METHOD_CARD,
        apply_markup=platega_service.apply_card_markup,
        i18n_key="card_pl",
        log_tag="card_pl",
    )


@router.callback_query(F.data == "pay:intl_pl")
async def callback_pay_intl_pl(callback: CallbackQuery, state: FSMContext):
    """Международные платежи через Platega (paymentMethod=12)."""
    import platega_service
    await _start_platega_payment(
        callback, state,
        method=platega_service.PAYMENT_METHOD_INTL,
        apply_markup=platega_service.apply_intl_markup,
        i18n_key="intl_pl",
        log_tag="intl_pl",
    )


@router.callback_query(F.data == "pay:sbp")
async def callback_pay_sbp(callback: CallbackQuery, state: FSMContext):
    """Оплата через СБП (Platega.io, +11% наценка)

    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase с ценой +11%
    - Создает транзакцию через Platega API
    - Отправляет payment URL пользователю
    """
    telegram_id = callback.from_user.id

    # Rate limiting
    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    # КРИТИЧНО: Проверяем FSM state - должен быть choose_payment_method
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:sbp: user={telegram_id}, state={current_state}")
        await state.set_state(None)
        return

    # КРИТИЧНО: Получаем данные из FSM state
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    country = fsm_data.get("country")  # Страна для бизнес-тарифов

    # Получаем промо-сессию
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None

    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM for sbp: user={telegram_id}")
        await state.set_state(None)
        return

    # Проверяем доступность Platega
    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.sbp_unavailable"), show_alert=True)
        logger.error("Platega not configured")
        return

    try:
        # Применяем наценку +11% для СБП
        sbp_price_kopecks = platega_service.apply_sbp_markup(final_price_kopecks)

        # Создаем pending_purchase с ценой СБП (+11%)
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=sbp_price_kopecks,
            promo_code=promo_code,
            country=country,
            is_combo=fsm_data.get("combo_bypass_gb", 0) > 0,
        )

        await state.update_data(purchase_id=purchase_id)

        logger.info(
            f"Purchase created for SBP payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"base_price={final_price_kopecks}, sbp_price={sbp_price_kopecks}"
        )

        sbp_price_rubles = sbp_price_kopecks / 100.0

        # Создаем транзакцию через Platega API
        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description=f"Atlas Secure VPN — {tariff_type} {period_days}d",
            purchase_id=purchase_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        # Сохраняем invoice_id в БД
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id))
        except Exception as e:
            logger.error(f"Failed to save transaction_id to DB: purchase_id={purchase_id}, error={e}")

        logger.info(
            f"invoice_created: provider=platega, user={telegram_id}, purchase_id={purchase_id}, "
            f"transaction_id={transaction_id}, sbp_price={sbp_price_rubles:.2f}"
        )

        # Отправляем пользователю ссылку на оплату
        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_price_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.sbp_pay_button"),
                url=redirect_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_buy_vpn"
            )]
        ])

        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

        # Очищаем FSM state
        await state.set_state(None)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error creating Platega SBP transaction: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.set_state(None)
