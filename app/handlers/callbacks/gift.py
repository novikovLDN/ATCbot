"""
Gift subscription handlers: gift_subscription flow (tariff → period → payment → share link).
"""
import asyncio
import logging
import math
import time
from urllib.parse import quote

import config
import database
from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    Message,
    SwitchInlineQueryChosenChat,
)
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions import service as subscription_service
from app.core.rate_limit import check_rate_limit
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.states import GiftState

gift_router = Router()
logger = logging.getLogger(__name__)

INVOICE_TIMEOUT = config.INVOICE_TIMEOUT_SECONDS


async def _schedule_invoice_deletion(bot: Bot, chat_id: int, invoice_message: Message, timeout: int = INVOICE_TIMEOUT):
    """Удаляет сообщение с инвойсом через timeout секунд."""
    try:
        await asyncio.sleep(timeout)
        await bot.delete_message(chat_id=chat_id, message_id=invoice_message.message_id)
    except Exception:
        pass


def _tariff_display_name(tariff: str) -> str:
    """Человекочитаемое название тарифа."""
    names = {"basic": "Basic", "plus": "Plus", "combo_basic": "Комбо Basic", "combo_plus": "Комбо Plus"}
    return names.get(tariff, tariff.capitalize())


def _period_display(period_days: int) -> str:
    """Человекочитаемый период."""
    months = period_days // 30
    if months == 1:
        return "1 месяц"
    elif months in (2, 3, 4):
        return f"{months} месяца"
    else:
        return f"{months} месяцев"


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

    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
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

    tariff_name = _tariff_display_name(tariff)
    tariff_prices = config.TARIFFS.get(tariff, {})

    text = i18n_get_text(language, "gift.choose_period", tariff_name=tariff_name)

    buttons = []
    for period_days in sorted(tariff_prices.keys()):
        price = tariff_prices[period_days]["price"]
        period_text = _period_display(period_days)
        btn_text = f"{period_text} — {price} ₽"
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
            text=i18n_get_text(language, "payment.lava", "💳 Картой (Lava)"),
            callback_data="gift_pay:lava"
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="gift_subscription"
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

    try:
        # Списываем баланс
        success = await database.decrease_balance(
            telegram_id=telegram_id,
            amount=price_rubles,
            source="gift_subscription",
            description=f"Подарочная подписка {_tariff_display_name(tariff)} на {_period_display(period_days)}",
        )
        if not success:
            await callback.message.answer(i18n_get_text(language, "errors.payment_processing"))
            await state.clear()
            return

        # Создаём запись о подарке
        gift = await database.create_gift_subscription(
            buyer_telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            price_kopecks=price_kopecks,
            purchase_id=f"gift_balance_{telegram_id}_{int(time.time())}",
        )

        gift_code = gift["gift_code"]
        logger.info(f"GIFT_PAID_BALANCE buyer={telegram_id} code={gift_code} tariff={tariff} period={period_days}d")

        await _send_gift_success(callback.bot, telegram_id, language, gift_code, tariff, period_days)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error processing gift balance payment: user={telegram_id}, error={e}")
        await callback.message.answer(i18n_get_text(language, "errors.payment_processing"))
        await state.clear()


# ====================================================================================
# STEP 4B: Оплата картой
# ====================================================================================

@gift_router.callback_query(F.data == "gift_pay:card", GiftState.choose_payment_method)
async def callback_gift_pay_card(callback: CallbackQuery, state: FSMContext):
    """Оплата подарка картой через Telegram Payments."""
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

        tariff_name = _tariff_display_name(tariff)
        period_text = _period_display(period_days)
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

        tariff_name = _tariff_display_name(tariff)
        period_text = _period_display(period_days)
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

        tariff_name = _tariff_display_name(tariff)
        period_text = _period_display(period_days)
        price_rubles = price_kopecks / 100.0

        invoice_data = await cryptobot_service.create_invoice(
            amount_rubles=price_rubles,
            description=f"Подарочная подписка {tariff_name} на {period_text}",
            purchase_id=purchase_id,
        )

        invoice_id = invoice_data["invoice_id"]
        pay_url = invoice_data["pay_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
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
                callback_data="gift_subscription"
            )]
        ])

        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()
        await state.set_state(None)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error creating gift crypto invoice: user={telegram_id}, error={e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.clear()


# ====================================================================================
# STEP 4E: Оплата через Lava (карта)
# ====================================================================================

@gift_router.callback_query(F.data == "gift_pay:lava", GiftState.choose_payment_method)
async def callback_gift_pay_lava(callback: CallbackQuery, state: FSMContext):
    """Оплата подарка через Lava (карта)."""
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

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.lava_unavailable"), show_alert=True)
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

        tariff_name = _tariff_display_name(tariff)
        period_text = _period_display(period_days)
        price_rubles = price_kopecks / 100.0

        invoice_data = await lava_service.create_invoice(
            amount_rubles=price_rubles,
            purchase_id=purchase_id,
            comment=f"Подарочная подписка {tariff_name} на {period_text}",
        )

        invoice_id = invoice_data["invoice_id"]
        payment_url = invoice_data["payment_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error(f"Failed to save lava invoice_id for gift: purchase_id={purchase_id}, error={e}")

        text = i18n_get_text(language, "payment.lava_waiting", amount=price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.lava_pay_button"),
                url=payment_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="gift_subscription"
            )]
        ])

        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()
        await state.set_state(None)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error creating gift lava invoice: user={telegram_id}, error={e}")
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

    tariff_name = _tariff_display_name(tariff)
    period_text = _period_display(period_days)

    text = i18n_get_text(
        language, "gift.success",
        tariff_name=tariff_name,
        period=period_text,
        gift_link=gift_link,
    )

    # Текст для шаринга
    share_text = i18n_get_text(
        language, "gift.share_text",
        tariff_name=tariff_name,
        period=period_text,
        gift_link=gift_link,
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "gift.btn_share", "📤 Отправить ссылку"),
            url=f"https://t.me/share/url?url={quote(gift_link)}&text={quote(share_text)}",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
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
                callback_data="gift_subscription"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.back_to_profile", "👤 Вернуться в профиль"),
                callback_data="menu_profile"
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
            tariff_name = _tariff_display_name(gift["tariff"])
            period_text = _period_display(gift["period_days"])
            status_icon = "✅" if gift["status"] == "activated" else "❌"
            btn_text = f"{tariff_name} {period_text} {status_icon}"
            row.append(InlineKeyboardButton(
                text=btn_text,
                callback_data=f"gift_detail:{gift['id']}:{page}"
            ))
        buttons.append(row)

    # Pagination: Назад / Дальше
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                text=i18n_get_text(language, "gift.page_prev", "⬅️ Назад"),
                callback_data=f"my_gifts:{page - 1}"
            ))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                text=i18n_get_text(language, "gift.page_next", "Дальше ➡️"),
                callback_data=f"my_gifts:{page + 1}"
            ))
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "gift.back_to_profile", "👤 Вернуться в профиль"),
        callback_data="menu_profile"
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

    tariff_name = _tariff_display_name(gift["tariff"])
    period_text = _period_display(gift["period_days"])
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
                callback_data=f"my_gifts:{back_page}"
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
                url=f"https://t.me/share/url?url={quote(gift_link)}&text={quote(share_text)}",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.back_to_gifts", "🎁 Назад к подаркам"),
                callback_data=f"my_gifts:{back_page}"
            )],
        ])

    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
