"""Оплата криптовалютой через CryptoBot.

ЧТО ЗДЕСЬ
    Один экран: создаёт покупку, выставляет счёт в CryptoBot (сумма
    задаётся в рублях, конвертацию делает провайдер) и отдаёт кнопку со
    ссылкой на оплату.

ПОЧЕМУ ВЫДЕЛЕНО
    Отдельный провайдер со своим SDK и своим форматом ответа
    (invoice_id / pay_url). Меняется независимо от остальных.

ЧТО ЛЕГКО СЛОМАТЬ
    invoice_id сохраняется в покупку отдельным запросом, и ошибка записи
    намеренно НЕ прерывает поток: счёт уже выставлен, отказ здесь оставил
    бы человека без ссылки. Но без invoice_id сверка с вебхуком
    провайдера теряет опору — поэтому ошибка обязана попадать в лог.

    FSM чистится в конце: человек ушёл платить по ссылке, и висящее
    состояние choose_payment_method потом отвечало бы «сессия истекла» на
    следующем шаге.
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
# Импорт _schedule_invoice_deletion стоял здесь с комментарием «общее для
# всех платёжных экранов», но функция ни разу не вызывалась: сообщение со
# ссылкой на оплату остаётся в чате навсегда. Импорт убран, чтобы он не
# читался как работающий механизм.
#
# Включать ли удаление — решение владельца (реестр, followup-2026-08-05).
# Если включать, то с 1800 с, а не с общими 900: столько живёт счёт
# CryptoBot (cryptobot_service.py:46, expires_in=1800), и таймаут по
# умолчанию убил бы сообщение на середине жизни счёта.

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "pay:crypto")
async def callback_pay_crypto(callback: CallbackQuery, state: FSMContext):
    """Оплата через CryptoBot (криптовалюта)

    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase
    - Создает invoice через CryptoBot API (fiat=RUB)
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

    # КРИТИЧНО: Проверяем FSM state
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:crypto: user={telegram_id}, state={current_state}")
        await state.set_state(None)
        return

    # Получаем данные из FSM state
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
        logger.error(f"Missing purchase data in FSM for crypto: user={telegram_id}")
        await state.set_state(None)
        return

    # Проверяем доступность CryptoBot
    import cryptobot_service
    if not cryptobot_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
        logger.error("CryptoBot not configured")
        return

    try:
        final_price_rubles = final_price_kopecks / 100.0

        # Создаем pending_purchase
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=final_price_kopecks,
            promo_code=promo_code,
            country=country,
            is_combo=fsm_data.get("combo_bypass_gb", 0) > 0,
        )

        await state.update_data(purchase_id=purchase_id, payment_method="crypto")

        logger.info(
            f"Purchase created for crypto payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, price={final_price_rubles}"
        )

        # Формируем описание
        months = period_days // 30
        if config.is_biz_tariff(tariff_type):
            tariff_name = "Business"
        elif tariff_type == "basic":
            tariff_name = "Basic"
        else:
            tariff_name = "Plus"

        description = f"Atlas Secure VPN — {tariff_name} {months}m"

        # Создаем invoice через CryptoBot API
        invoice_data = await cryptobot_service.create_invoice(
            amount_rubles=final_price_rubles,
            description=description,
            purchase_id=purchase_id,
        )

        invoice_id = invoice_data["invoice_id"]
        pay_url = invoice_data["pay_url"]

        # Сохраняем invoice_id в БД
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error(f"Failed to save cryptobot invoice_id to DB: purchase_id={purchase_id}, error={e}")

        logger.info(
            f"invoice_created: provider=cryptobot, user={telegram_id}, purchase_id={purchase_id}, "
            f"invoice_id={invoice_id}, price={final_price_rubles:.2f}"
        )

        # Отправляем пользователю ссылку на оплату
        text = i18n_get_text(language, "payment.crypto_waiting", amount=final_price_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.crypto_pay_button"),
                url=pay_url
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
        logger.exception(f"Error creating CryptoBot invoice: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.set_state(None)
