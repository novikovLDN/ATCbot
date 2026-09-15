"""
Gift subscription handlers: gift_subscription flow (tariff → period → payment → share link).
"""
import asyncio
import logging
import math
import time

import config
import database
from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    Message,
)
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.core.rate_limit import check_rate_limit
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.states import GiftState
from app.handlers.common.emoji import CE
from app.utils.text_format import build_share_url

gift_router = Router()
logger = logging.getLogger(__name__)

INVOICE_TIMEOUT = config.INVOICE_TIMEOUT_SECONDS
INVOICE_MSG_TIMEOUT = 15 * 60  # 15 minutes


async def _auto_delete_invoice_msg(bot, chat_id: int, msg):
    """Delete invoice message after timeout."""
    try:
        await asyncio.sleep(INVOICE_MSG_TIMEOUT)
        await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception:
        pass


async def _schedule_invoice_deletion(bot: Bot, chat_id: int, invoice_message: Message, timeout: int = INVOICE_TIMEOUT):
    """Удаляет сообщение с инвойсом через timeout секунд."""
    try:
        await asyncio.sleep(timeout)
        await bot.delete_message(chat_id=chat_id, message_id=invoice_message.message_id)
    except Exception:
        pass


def _tariff_display_name(tariff: str, language: str = "ru") -> str:
    """Человекочитаемое название тарифа — in the user's language (08 #21:
    «Комбо …» was RU for everyone)."""
    if tariff in ("basic", "plus", "combo_basic", "combo_plus"):
        return i18n_get_text(language, f"tariff.name_{tariff}")
    return tariff.capitalize()


def _period_display(period_days: int, language: str = "ru") -> str:
    """Человекочитаемый период (calendar months) — in the user's language (08 #21:
    EN saw «1 месяц»)."""
    from app.services.payments.success_message import period_display
    return period_display(language, period_days)


def _register_invoice_screen(purchase_id: str, telegram_id: int, msg) -> None:
    """Gift «Ждём платёж» screens are removed after the payment like every other
    invoice screen (08 #15): the confirmation deletes what is registered here."""
    try:
        from app.handlers.callbacks.payments_callbacks import _invoice_messages
        _invoice_messages[purchase_id] = (telegram_id, msg.message_id)
    except Exception:  # noqa: BLE001 — cosmetic, never blocks the payment
        pass


# ====================================================================================
# STEP 1: Начало — экран подарочной подписки
# ====================================================================================

@gift_router.callback_query(F.data == "gift_subscription")
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
            callback_data="gift_tariff:basic",
            style="primary",
        )],
        [InlineKeyboardButton(
            text="⚡ Plus",
            callback_data="gift_tariff:plus",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
            icon_custom_emoji_id=CE["back"],
            style="primary",
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

@gift_router.callback_query(F.data.startswith("gift_tariff:"), GiftState.choose_tariff)
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

    tariff_name = _tariff_display_name(tariff, language)
    tariff_prices = config.TARIFFS.get(tariff, {})

    text = i18n_get_text(language, "gift.choose_period", tariff_name=tariff_name)

    from app.handlers.payments.callbacks import _period_badge

    buttons = []
    for period_days in sorted(tariff_prices.keys()):
        price = tariff_prices[period_days]["price"]
        period_text = _period_display(period_days, language)
        badge = _period_badge(period_days)
        btn_text = f"{period_text} — {price} ₽"
        if badge:
            btn_text = f"{btn_text} {badge}"
        buttons.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"gift_period:{period_days}",
            style="primary",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="gift_subscription",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
    await state.set_state(GiftState.choose_period)


# ====================================================================================
# STEP 3: Выбор периода → экран выбора способа оплаты
# ====================================================================================

@gift_router.callback_query(F.data.startswith("gift_period:"), GiftState.choose_period)
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

    tariff_name = _tariff_display_name(tariff, language)
    period_text = _period_display(period_days, language)

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
            callback_data="gift_pay:balance",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_with_card"),
            callback_data="gift_pay:card",
            icon_custom_emoji_id=CE["buy"],
            style="success",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.stars", "⭐ Telegram Stars"),
            callback_data="gift_pay:stars",
            style="primary",
        )],
    ]

    # CryptoBot — если настроен и кнопка не скрыта (cryptobot_service.BUTTON_HIDDEN)
    import cryptobot_service
    if cryptobot_service.show_button():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.crypto", "🌎 CryptoBot"),
            callback_data="gift_pay:crypto",
            style="primary",
        )])

    # WATA (карта/СБП/T-Pay) — one button per cash desk (08 #20): with WATA on,
    # «Оплатить картой» (gift_pay:card) opened WATA too, so it gives way.
    import wata_service
    if wata_service.is_enabled():
        buttons = [row for row in buttons if row[0].callback_data != "gift_pay:card"]
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.lava"),
            callback_data="gift_pay:wata",
            style="success",
        )])
    # СБП — Platega, with its real markup (08 #12; «📱 СБП» was hardcoded).
    import platega_service
    if platega_service.is_enabled():
        from app.handlers.common.payment_labels import sbp_label
        buttons.append([InlineKeyboardButton(
            text=sbp_label(language),
            callback_data="gift_pay:sbp",
            style="primary",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="gift_subscription",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
    await state.set_state(GiftState.choose_payment_method)


# ====================================================================================
# STEP 4A: Оплата балансом
# ====================================================================================

@gift_router.callback_query(F.data == "gift_pay:balance", GiftState.choose_payment_method)
async def callback_gift_pay_balance(callback: CallbackQuery, state: FSMContext):
    """Оплата подарка с баланса."""
    telegram_id = callback.from_user.id

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    fsm_data = await state.get_data()
    tariff = fsm_data.get("gift_tariff")
    period_days = fsm_data.get("gift_period_days")
    price_kopecks = fsm_data.get("gift_price_kopecks")

    if not tariff or not period_days or not price_kopecks:
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.clear()
        return

    price_rubles = price_kopecks / 100.0
    balance = await database.get_user_balance(telegram_id)

    if balance < price_rubles:
        shortage = price_rubles - balance
        error_text = i18n_get_text(
            language, "errors.insufficient_balance",
            amount=price_rubles, balance=balance, shortage=shortage,
        )
        await callback.answer(error_text, show_alert=True)
        return

    # Защита от дублей
    current_state = await state.get_state()
    if current_state == GiftState.processing_payment.state:
        await callback.answer(i18n_get_text(language, "errors.session_expired_processing"), show_alert=True)
        return

    await callback.answer()
    await state.set_state(GiftState.processing_payment)

    debited = False
    gift_code = None
    try:
        # Списываем баланс
        success = await database.decrease_balance(
            telegram_id=telegram_id,
            amount=price_rubles,
            source="gift_subscription",
            description=f"Подарочная подписка {_tariff_display_name(tariff, language)} на {_period_display(period_days, language)}",
        )
        if not success:
            await callback.message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
            await state.clear()
            return
        debited = True

        # Создаём запись о подарке
        gift_purchase_id = f"gift_balance_{telegram_id}_{int(time.time())}"
        gift = await database.create_gift_subscription(
            buyer_telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            price_kopecks=price_kopecks,
            purchase_id=gift_purchase_id,
        )

        gift_code = gift["gift_code"]
        logger.info(f"GIFT_PAID_BALANCE buyer={telegram_id} code={gift_code} tariff={tariff} period={period_days}d")
        # Owner rule 2026-09-14 (N17): a purchase paid from balance earns referral
        # cashback too (once per purchase_id); never raises.
        await database.award_referral_cashback(
            buyer_id=telegram_id, purchase_id=gift_purchase_id, amount_rubles=price_rubles,
        )

        await _send_gift_success(callback.bot, telegram_id, language, gift_code, tariff, period_days)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error processing gift balance payment: user={telegram_id}, error={e}")
        if debited and gift_code is None:
            # Деньги списаны, а код не создан → вернуть на баланс + алерт (HOW_IT_WORKS P2).
            await _refund_gift_balance(callback.bot, telegram_id, price_rubles, tariff, period_days, e)
        await callback.message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        await state.clear()


async def _refund_gift_balance(bot, telegram_id: int, amount: float, tariff: str, period_days: int, error) -> None:
    """Gift paid from balance, gift code NOT created: give the money back,
    write payment_errors and send a forced admin alert. Never raises."""
    refunded = False
    try:
        refunded = bool(await database.increase_balance(
            telegram_id=telegram_id,
            amount=amount,
            source="refund",
            description=f"Возврат: подарочная подписка {tariff} на {period_days} дн. не создана",
        ))
    except Exception as refund_err:  # noqa: BLE001
        logger.error("GIFT_BALANCE_REFUND_FAILED user=%s amount=%s: %s", telegram_id, amount, refund_err)
    logger.critical(
        "GIFT_BALANCE_CODE_NOT_CREATED user=%s amount=%s refunded=%s", telegram_id, amount, refunded,
    )
    try:
        await database.log_payment_error(
            stage="gift_balance_code_not_created",
            telegram_id=telegram_id,
            payment_provider="balance",
            amount_rubles=amount,
            error_message=f"{type(error).__name__}: {error} (refunded={refunded})"[:500],
        )
    except Exception as log_err:  # noqa: BLE001
        logger.warning("GIFT_BALANCE_PAYMENT_ERROR_LOG_FAILED user=%s: %s", telegram_id, log_err)
    status = "refunded to the balance" if refunded else "NOT refunded — credit the balance manually!"
    text = (
        "Gift paid from balance: gift code was NOT created.\n"
        f"User TG ID: {telegram_id}\n"
        f"Gift: {tariff} {period_days} days\n"
        f"Amount: {amount} RUB — {status}\n"
        f"Error: {type(error).__name__}: {str(error)[:200]}"
    )
    try:
        from app.services.admin_alerts import send_alert
        await send_alert(bot, "payment", text, force=True)
    except Exception as alert_err:  # noqa: BLE001
        logger.warning("GIFT_BALANCE_ALERT_FAILED user=%s: %s", telegram_id, alert_err)


# ====================================================================================
# STEP 4B: Оплата картой
# ====================================================================================

@gift_router.callback_query(F.data == "gift_pay:card", GiftState.choose_payment_method)
async def callback_gift_pay_card(callback: CallbackQuery, state: FSMContext):
    """Оплата подарка картой через Telegram Payments."""
    telegram_id = callback.from_user.id

    # «Банковская карта» → универсальный инвойс Wata. Fallback на карту, если выкл.
    import wata_service
    if wata_service.is_enabled():
        return await callback_gift_pay_wata(callback, state)

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    fsm_data = await state.get_data()
    tariff = fsm_data.get("gift_tariff")
    period_days = fsm_data.get("gift_period_days")
    price_kopecks = fsm_data.get("gift_price_kopecks")

    if not tariff or not period_days or not price_kopecks:
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.clear()
        return

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    # Минимальная сумма для Telegram Payments — 64 RUB
    MIN_PAYMENT_AMOUNT_KOPECKS = 6400
    if price_kopecks < MIN_PAYMENT_AMOUNT_KOPECKS:
        await callback.answer(i18n_get_text(language, "errors.payment_min_amount"), show_alert=True)
        return

    try:
        # Создаём pending_purchase с типом gift
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            price_kopecks=price_kopecks,
            purchase_type="gift",
        )

        await state.update_data(gift_purchase_id=purchase_id)

        tariff_name = _tariff_display_name(tariff, language)
        period_text = _period_display(period_days, language)
        description = f"Подарочная подписка {tariff_name} на {period_text}"
        payload = f"purchase:{purchase_id}"

        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure — Подарок",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label="Подарочная подписка", amount=price_kopecks)],
        )
        await callback.bot.send_message(
            chat_id=telegram_id,
            text=i18n_get_text(language, "payment.invoice_timeout"),
            parse_mode="HTML",
        )
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg))
        await state.set_state(GiftState.processing_payment)
        await callback.answer()

    except Exception as e:
        logger.exception(f"Error creating gift card invoice: user={telegram_id}, error={e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.clear()


# ====================================================================================
# STEP 4C: Оплата Stars
# ====================================================================================

@gift_router.callback_query(F.data == "gift_pay:stars", GiftState.choose_payment_method)
async def callback_gift_pay_stars(callback: CallbackQuery, state: FSMContext):
    """Оплата подарка через Telegram Stars."""
    telegram_id = callback.from_user.id

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    fsm_data = await state.get_data()
    tariff = fsm_data.get("gift_tariff")
    period_days = fsm_data.get("gift_period_days")
    price_kopecks = fsm_data.get("gift_price_kopecks")

    if not tariff or not period_days or not price_kopecks:
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.clear()
        return

    # Получаем цену в Stars
    stars_tariff = config.TARIFFS_STARS.get(tariff, {})
    stars_price = stars_tariff.get(period_days, {}).get("price")
    if not stars_price:
        # Конвертируем из рублей
        stars_price = math.ceil(price_kopecks / 100 * 1.7 / 1.85)

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            price_kopecks=price_kopecks,
            purchase_type="gift",
        )

        await state.update_data(gift_purchase_id=purchase_id)

        tariff_name = _tariff_display_name(tariff, language)
        period_text = _period_display(period_days, language)
        description = f"Подарочная подписка {tariff_name} на {period_text}"
        payload = f"purchase:{purchase_id}"

        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure — Подарок",
            description=description,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Подарочная подписка", amount=stars_price)],
        )
        await callback.bot.send_message(
            chat_id=telegram_id,
            text=i18n_get_text(language, "payment.invoice_timeout"),
            parse_mode="HTML",
        )
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg))
        await state.set_state(GiftState.processing_payment)
        await callback.answer()

    except Exception as e:
        logger.exception(f"Error creating gift stars invoice: user={telegram_id}, error={e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.clear()


# ====================================================================================
# STEP 4D: Оплата криптовалютой (CryptoBot)
# ====================================================================================

@gift_router.callback_query(F.data == "gift_pay:crypto", GiftState.choose_payment_method)
async def callback_gift_pay_crypto(callback: CallbackQuery, state: FSMContext):
    """Оплата подарка через CryptoBot (криптовалюта)."""
    telegram_id = callback.from_user.id

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    fsm_data = await state.get_data()
    tariff = fsm_data.get("gift_tariff")
    period_days = fsm_data.get("gift_period_days")
    price_kopecks = fsm_data.get("gift_price_kopecks")

    if not tariff or not period_days or not price_kopecks:
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.clear()
        return

    import cryptobot_service
    if not cryptobot_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
        return

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            price_kopecks=price_kopecks,
            purchase_type="gift",
        )

        await state.update_data(gift_purchase_id=purchase_id)

        tariff_name = _tariff_display_name(tariff, language)
        period_text = _period_display(period_days, language)
        price_rubles = price_kopecks / 100.0

        invoice_data = await cryptobot_service.create_invoice(
            amount_rubles=price_rubles,
            description=f"Подарочная подписка {tariff_name} на {period_text}",
            purchase_id=purchase_id,
        )

        invoice_id = invoice_data["invoice_id"]
        pay_url = invoice_data["pay_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id), provider="cryptobot")
        except Exception as e:
            logger.error(f"Failed to save cryptobot invoice_id for gift: purchase_id={purchase_id}, error={e}")

        text = i18n_get_text(language, "payment.crypto_waiting", amount=price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.crypto_pay_button"),
                url=pay_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="gift_subscription",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )]
        ])

        msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        _register_invoice_screen(purchase_id, telegram_id, msg)
        asyncio.create_task(_auto_delete_invoice_msg(callback.bot, telegram_id, msg))
        await callback.answer()
        await state.set_state(None)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error creating gift crypto invoice: user={telegram_id}, error={e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.clear()


@gift_router.callback_query(F.data == "gift_pay:sbp", GiftState.choose_payment_method)
async def callback_gift_pay_sbp(callback: CallbackQuery, state: FSMContext):
    """Оплата подарка через СБП. Провайдер (Platega / Wata) выбирается
    через runtime-настройку в дашборде (см. app.services.sbp_router)."""
    telegram_id = callback.from_user.id

    # Живой выбор провайдера — прозрачно для пользователя.
    from app.services import sbp_router
    provider = await sbp_router.resolve_provider(telegram_id)
    if provider == "wata":
        logger.info(f"sbp_router: user {telegram_id} → wata (gift_pay:sbp)")
        return await callback_gift_pay_wata(callback, state)

    language = await resolve_user_language(telegram_id)
    fsm_data = await state.get_data()
    tariff = fsm_data.get("gift_tariff")
    period_days = fsm_data.get("gift_period_days")
    price_kopecks = fsm_data.get("gift_price_kopecks")
    if not tariff or not period_days or not price_kopecks:
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.clear()
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.sbp_unavailable"), show_alert=True)
        return

    try:
        # СБП через Platega применяет наценку (SBP_MARKUP_PERCENT).
        sbp_price_kopecks = platega_service.apply_sbp_markup(price_kopecks)
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            price_kopecks=sbp_price_kopecks,
            purchase_type="gift",
        )
        await state.update_data(gift_purchase_id=purchase_id)

        tariff_name = _tariff_display_name(tariff, language)
        period_text = _period_display(period_days, language)
        sbp_price_rubles = sbp_price_kopecks / 100.0

        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description=f"Подарочная подписка {tariff_name} на {period_text}",
            purchase_id=purchase_id,
            telegram_id=telegram_id,
        )
        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id), provider="platega")
        except Exception as e:
            logger.error(f"Failed to save platega tx_id for gift: purchase_id={purchase_id}, error={e}")

        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.sbp_pay_button"),
                url=redirect_url,
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="gift_subscription",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )],
        ])
        msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        _register_invoice_screen(purchase_id, telegram_id, msg)
        asyncio.create_task(_auto_delete_invoice_msg(callback.bot, telegram_id, msg))
        await callback.answer()
        await state.set_state(None)
        await state.clear()
    except Exception as e:
        logger.exception(f"Error creating gift SBP (platega) transaction: user={telegram_id}, error={e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.clear()


@gift_router.callback_query(F.data == "gift_pay:wata", GiftState.choose_payment_method)
async def callback_gift_pay_wata(callback: CallbackQuery, state: FSMContext):
    """Оплата подарка через Wata (admin-only beta)."""
    telegram_id = callback.from_user.id
    import wata_service
    language = await resolve_user_language(telegram_id)
    if not wata_service.is_visible_to(telegram_id):
        await callback.answer(i18n_get_text(language, "payment.wata_beta_only"), show_alert=True)
        return
    fsm_data = await state.get_data()
    tariff = fsm_data.get("gift_tariff")
    period_days = fsm_data.get("gift_period_days")
    price_kopecks = fsm_data.get("gift_price_kopecks")
    if not tariff or not period_days or not price_kopecks:
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.clear()
        return
    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            price_kopecks=price_kopecks,
            purchase_type="gift",
        )
        await state.update_data(gift_purchase_id=purchase_id)
        tariff_name = _tariff_display_name(tariff, language)
        period_text = _period_display(period_days, language)
        price_rubles = price_kopecks / 100.0
        invoice = await wata_service.create_invoice(
            amount_rubles=price_rubles,
            purchase_id=purchase_id,
            comment=f"Подарочная подписка {tariff_name} на {period_text}",
            user_id=telegram_id,
        )
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice["invoice_id"]), provider="wata")
        except Exception:
            pass
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.wata_pay_button", amount=f"{price_rubles:.0f}"),
                url=invoice["payment_url"],
            )],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="gift_subscription", icon_custom_emoji_id=CE["back"], style="primary")],
        ])
        # 08 #20 / #15: WATA is card / SBP / T-Pay (was «СБП 2», RU only);
        # the screen is removed after the payment.
        msg = await callback.message.answer(
            i18n_get_text(language, "payment.wata_waiting", amount=f"{price_rubles:.0f}"),
            reply_markup=keyboard, parse_mode="HTML",
        )
        _register_invoice_screen(purchase_id, telegram_id, msg)
        asyncio.create_task(_auto_delete_invoice_msg(callback.bot, telegram_id, msg))
        await callback.answer()
        await state.set_state(None)
        await state.clear()
    except Exception as e:
        logger.exception(f"Error creating gift wata invoice: user={telegram_id}, error={e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.clear()


# ====================================================================================
# Отправка сообщения с подарочной ссылкой
# ====================================================================================

async def _send_gift_success(bot: Bot, telegram_id: int, language: str, gift_code: str, tariff: str, period_days: int):
    """Отправляет сообщение с подарочной ссылкой и кнопками шаринга."""
    bot_info = await bot.get_me()
    bot_username = bot_info.username
    gift_link = f"https://t.me/{bot_username}?start=gift_{gift_code}"

    tariff_name = _tariff_display_name(tariff, language)
    period_text = _period_display(period_days, language)

    text = i18n_get_text(
        language, "gift.success",
        tariff_name=tariff_name,
        period=period_text,
        gift_link=gift_link,
    )

    # Текст для шаринга. t.me/share/url кладёт его в поле ввода пользователя
    # как обычный текст: HTML и премиум-эмодзи там не парсятся, поэтому
    # build_share_url прогоняет его через html_to_plain и кодирует.
    share_text = i18n_get_text(
        language, "gift.share_text",
        tariff_name=tariff_name,
        period=period_text,
        gift_link=gift_link,
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "gift.btn_share", "📤 Отправить ссылку"),
            url=build_share_url(gift_link, share_text),
        )],
        # 08 #19: «Мои подарки» had no entry point — the link can be found again there.
        [InlineKeyboardButton(
            text=i18n_get_text(language, "gift.btn_my_gifts"),
            callback_data="my_gifts:0",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ])

    await bot.send_message(chat_id=telegram_id, text=text, reply_markup=keyboard, parse_mode="HTML")


# ====================================================================================
# MY GIFTS: Карусель подарков пользователя
# ====================================================================================

GIFTS_PER_PAGE = 6  # 3 rows × 2 columns


@gift_router.callback_query(F.data.startswith("my_gifts:"))
async def callback_my_gifts(callback: CallbackQuery):
    """Экран «Мои подарки» — карусель купленных подарков."""
    if not await ensure_db_ready_callback(callback):
        return

    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    page_str = callback.data.split(":")[1]
    try:
        page = int(page_str)
    except ValueError:
        page = 0

    gifts = await database.get_user_gifts(telegram_id)

    if not gifts:
        text = i18n_get_text(language, "gift.my_gifts_empty", "🎁 У вас пока нет подарков.\n\nВы можете приобрести подарок в главном меню.")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.buy_gift_btn", "🎁 Подарить подписку"),
                callback_data="gift_subscription",
                icon_custom_emoji_id=CE["gift"],
                style="success",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.back_to_profile", "👤 Вернуться в профиль"),
                callback_data="menu_profile",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
        return

    total_pages = math.ceil(len(gifts) / GIFTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * GIFTS_PER_PAGE
    page_gifts = gifts[start:start + GIFTS_PER_PAGE]

    text = i18n_get_text(language, "gift.my_gifts_title", "🎁 <b>Мои подарки</b>")
    if total_pages > 1:
        text += f"\n\n📄 {page + 1}/{total_pages}"

    # Build 2-column grid (up to 3 rows)
    buttons = []
    for i in range(0, len(page_gifts), 2):
        row = []
        for gift in page_gifts[i:i + 2]:
            tariff_name = _tariff_display_name(gift["tariff"], language)
            period_text = _period_display(gift["period_days"], language)
            status_icon = "✅" if gift["status"] == "activated" else "❌"
            btn_text = f"{tariff_name} {period_text} {status_icon}"
            row.append(InlineKeyboardButton(
                text=btn_text,
                callback_data=f"gift_detail:{gift['id']}:{page}",
                style="primary",
            ))
        buttons.append(row)

    # Pagination: Назад / Дальше
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                text=i18n_get_text(language, "gift.page_prev", "⬅️ Назад"),
                callback_data=f"my_gifts:{page - 1}",
                style="primary",
            ))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                text=i18n_get_text(language, "gift.page_next", "Дальше ➡️"),
                callback_data=f"my_gifts:{page + 1}",
                style="primary",
            ))
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "gift.back_to_profile", "👤 Вернуться в профиль"),
        callback_data="menu_profile",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)


# ====================================================================================
# GIFT DETAIL: Экран отдельного подарка
# ====================================================================================

@gift_router.callback_query(F.data.startswith("gift_detail:"))
async def callback_gift_detail(callback: CallbackQuery):
    """Детальный экран подарка — ссылка + кнопка «Отправить»."""
    if not await ensure_db_ready_callback(callback):
        return

    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    parts = callback.data.split(":")
    try:
        gift_id = int(parts[1])
        back_page = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return

    # Fetch all user gifts and find the one by id
    gifts = await database.get_user_gifts(telegram_id)
    gift = next((g for g in gifts if g["id"] == gift_id), None)

    if not gift:
        await callback.answer(i18n_get_text(language, "gift.error_not_found"), show_alert=True)
        return

    tariff_name = _tariff_display_name(gift["tariff"], language)
    period_text = _period_display(gift["period_days"], language)
    gift_code = gift["gift_code"]

    bot_info = await callback.bot.get_me()
    bot_username = bot_info.username
    gift_link = f"https://t.me/{bot_username}?start=gift_{gift_code}"

    if gift["status"] == "activated":
        status_text = i18n_get_text(language, "gift.status_activated", "✅ Активирован")
        text = i18n_get_text(
            language, "gift.detail_activated",
            f"🎁 <b>{tariff_name} — {period_text}</b>\n\n{status_text}",
            tariff_name=tariff_name,
            period=period_text,
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.back_to_gifts", "🎁 Назад к подаркам"),
                callback_data=f"my_gifts:{back_page}",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )],
        ])
    else:
        status_text = i18n_get_text(language, "gift.status_pending", "❌ Не активирован")
        text = i18n_get_text(
            language, "gift.detail_pending",
            f"🎁 <b>Отправьте подарок близкому!</b>\n\n📦 Тариф: {tariff_name}\n⏳ Срок: {period_text}\n\n{status_text}\n\n🔗 Ссылка для активации:\n<code>{gift_link}</code>",
            tariff_name=tariff_name,
            period=period_text,
            gift_link=gift_link,
        )

        share_text = i18n_get_text(
            language, "gift.share_text",
            tariff_name=tariff_name,
            period=period_text,
            gift_link=gift_link,
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.btn_share", "📤 Отправить ссылку"),
                url=build_share_url(gift_link, share_text),
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.back_to_gifts", "🎁 Назад к подаркам"),
                callback_data=f"my_gifts:{back_page}",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )],
        ])

    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)


# ────────────────────────────────────────────────────────────────────────
# "Купить со скидкой 20%" CTA attached to admin gift notifications
# (see app/handlers/admin/bonus.py::_gift_keyboard).
# Activates a 20%-off personal discount valid for 3 days, then opens the
# main menu where the user picks a tariff — the discount applies
# automatically via calculate_final_price's personal_discount branch.
# Repeat clicks are idempotent: an already-active personal discount is
# not overwritten so the user keeps the strongest one they have.
# ────────────────────────────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone


_GIFT_OFFER_PERCENT = 20
_GIFT_OFFER_DAYS = 3


@gift_router.callback_query(F.data == "gift_offer:claim")
async def callback_gift_offer_claim(callback: CallbackQuery, state: FSMContext):
    """Activate the 20% personal discount tied to a gift notification."""
    telegram_id = callback.from_user.id

    try:
        await callback.answer()
    except Exception:
        pass

    # If the user already has an active personal discount, don't downgrade
    # them or shorten an existing offer — just remind that it's already on.
    try:
        existing = await database.get_user_discount(telegram_id)
    except Exception as e:
        logger.exception("GIFT_OFFER_DISCOUNT_LOOKUP_FAIL user=%s err=%s", telegram_id, e)
        existing = None

    if existing:
        existing_percent = int(existing.get("discount_percent") or 0)
        existing_expires = existing.get("expires_at")
        if existing_expires:
            try:
                if existing_expires.tzinfo is None:
                    existing_expires = existing_expires.replace(tzinfo=timezone.utc)
                hours = max(0, int((existing_expires - datetime.now(timezone.utc)).total_seconds() // 3600))
                tail = f" (осталось ≈{hours} ч)"
            except Exception:
                tail = ""
        else:
            tail = " (без срока)"
        await callback.answer(
            f"У вас уже активна персональная скидка {existing_percent}%{tail}. "
            "Можно сразу выбрать тариф.",
            show_alert=True,
        )
        return

    # Fresh activation: 20% for 3 days.
    expires_at = datetime.now(timezone.utc) + timedelta(days=_GIFT_OFFER_DAYS)
    try:
        ok = await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=_GIFT_OFFER_PERCENT,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
        )
    except Exception as e:
        logger.exception("GIFT_OFFER_DISCOUNT_CREATE_FAIL user=%s err=%s", telegram_id, e)
        ok = False

    if not ok:
        await callback.answer(
            "Не удалось активировать скидку. Попробуйте позже или напишите в поддержку.",
            show_alert=True,
        )
        return

    logger.info(
        "GIFT_OFFER_DISCOUNT_ACTIVATED user=%s percent=%s days=%s expires_at=%s",
        telegram_id, _GIFT_OFFER_PERCENT, _GIFT_OFFER_DAYS, expires_at.isoformat(),
    )

    text = (
        f"🎉 <b>Скидка {_GIFT_OFFER_PERCENT}% активирована!</b>\n\n"
        f"Действует {_GIFT_OFFER_DAYS} дня — успейте оформить подписку.\n"
        "Скидка применится автоматически при оплате."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Выбрать тариф", callback_data="menu_main", style="primary")],
    ])
    try:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.warning("GIFT_OFFER_ACK_SEND_FAIL user=%s err=%s", telegram_id, e)
