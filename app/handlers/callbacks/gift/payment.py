"""Подарочная подписка: оплата и выдача ссылки на подарок.

ЧТО ЗДЕСЬ
    Пять способов оплаты (баланс, карта, Stars, CryptoBot, Lava) и
    отправка сообщения со ссылкой на подарок.

ПОЧЕМУ ВЫДЕЛЕНО
    Единственное место в подарках, где двигаются деньги. Мастер выбора
    (wizard.py) и просмотр купленных (my_gifts.py) ничего не списывают.

ЧТО ЛЕГКО СЛОМАТЬ
    Оплата с баланса — единственный способ, который создаёт подарок ЗДЕСЬ
    ЖЕ. Остальные лишь выставляют счёт: подарок создаст вебхук после
    оплаты. Перепутать порядок «списать → создать» нельзя: деньги уйдут, а
    кода подарка не будет.

    Код подарка — предъявительский токен: кто прочитал лог, тот и
    активирует чужую оплаченную подписку. В логи идёт только маска
    (mask_secret), цепочка собирается по buyer и id записи.

    `_send_gift_success` зовут снаружи — доставка товара после вебхука
    (app/handlers/payments/goods_delivery.py и
    app/services/payments/confirmation.py). Переименуете или спрячете —
    покупатель не получит ссылку на оплаченный подарок.

    Автоудаление счёта общее для всех платёжных экранов
    (_invoice_cleanup). Своя копия здесь уже была — шестая по счёту, с
    тем же телом, но без логов; пока копии расходились, правка таймаута
    попадала в один-два файла из шести.
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
)
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.core.rate_limit import check_rate_limit
from app.handlers.common.states import GiftState
from app.handlers.callbacks.gift.formatting import _period_display, _tariff_display_name
# Автоудаление инвойса — одна общая реализация на все платёжные экраны.
from app.handlers.callbacks._invoice_cleanup import _schedule_invoice_deletion

router = Router()
logger = logging.getLogger(__name__)

LAVA_INVOICE_TIMEOUT = 15 * 60  # 15 minutes


async def _auto_delete_lava_msg(bot, chat_id: int, msg):
    """Delete Lava invoice message after timeout."""
    try:
        await asyncio.sleep(LAVA_INVOICE_TIMEOUT)
        await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception:
        pass


@router.callback_query(F.data == "gift_pay:balance", GiftState.choose_payment_method)
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
            # Отказ списания не оставлял в логах ничего: человек видел
            # «ошибка обработки платежа», а по логам покупки не существовало
            # вовсе — разобрать обращение «купил подарок, денег нет / подарка
            # нет» было не по чему. Причина отказа здесь одна из двух —
            # нехватка баланса или сбой БД, и различать их надо в логе.
            logger.error(
                "GIFT_BALANCE_DEBIT_FAILED buyer=%s tariff=%s period=%sd price=%s ₽ — "
                "списание с баланса не прошло, подарок не создан",
                telegram_id, tariff, period_days, price_rubles,
            )
            await callback.message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
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
        # Код подарка не пишем целиком: это предъявительский токен на
        # оплаченную подписку — кто прочитал лог, тот её и активирует.
        # Для разбора цепочки есть buyer и id записи подарка.
        from app.utils.security import mask_secret
        logger.info(
            f"GIFT_PAID_BALANCE buyer={telegram_id} gift_id={gift.get('id')} "
            f"code={mask_secret(gift_code)} tariff={tariff} period={period_days}d"
        )

        await _send_gift_success(callback.bot, telegram_id, language, gift_code, tariff, period_days)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error processing gift balance payment: user={telegram_id}, error={e}")
        await callback.message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        await state.clear()


# ====================================================================================
# STEP 4B: Оплата картой
# ====================================================================================

@router.callback_query(F.data == "gift_pay:card", GiftState.choose_payment_method)
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

@router.callback_query(F.data == "gift_pay:stars", GiftState.choose_payment_method)
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

@router.callback_query(F.data == "gift_pay:crypto", GiftState.choose_payment_method)
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

@router.callback_query(F.data == "gift_pay:lava", GiftState.choose_payment_method)
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

        lava_msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        asyncio.create_task(_auto_delete_lava_msg(callback.bot, telegram_id, lava_msg))
        await callback.answer()
        await state.set_state(None)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error creating gift lava invoice: user={telegram_id}, error={e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.clear()


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
