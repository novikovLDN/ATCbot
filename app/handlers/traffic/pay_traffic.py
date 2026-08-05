"""Выставление счёта за пакет трафика: карта, СБП, Lava.

ЧТО ЗДЕСЬ
    Обработчики traffic_pay_* — создают pending_purchase с
    purchase_type='traffic_pack' и tariff='traffic_{N}gb', затем отдают
    пользователю счёт провайдера.

ПОЧЕМУ ОТДЕЛЬНО ОТ ОБХОДА
    Тот же сценарий для обхода лежит в pay_bypass.py: там другой префикс
    тарифа (bypass_), шире набор провайдеров и другая кнопка «назад». Общий
    модуль на два продукта рано или поздно перепутает префиксы, а ошибка в
    tariff означает выдачу не того товара после оплаты.

ЧТО ЛЕГКО СЛОМАТЬ
    Скидка применяется ДО наценки СБП, а не после — иначе наценка берётся с
    недисконтированной суммы и пользователь платит больше, чем показала
    витрина.

    Цена в pending_purchase обязана совпадать с суммой в счёте провайдера:
    для СБП это сумма УЖЕ с наценкой. Разойдутся — сверка платежей не
    сойдётся и оплаченная покупка зависнет непроведённой.

    Оплата с баланса выключена, но обработчик оставлен намеренно: у
    пользователей в чатах висят старые клавиатуры, и без него кнопка молчит
    вместо понятного алерта.
"""
import asyncio
import logging
import math

import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
# Автоудаление счёта — общее для всех платёжных экранов, см. модуль.
from app.handlers.callbacks._invoice_cleanup import _schedule_invoice_deletion

pay_traffic_router = Router()
logger = logging.getLogger(__name__)


@pay_traffic_router.callback_query(F.data.startswith("traffic_pay_balance:"))
async def callback_traffic_pay_balance(callback: CallbackQuery):
    """Pay for traffic pack from balance.

    Метод отключён — кнопка убрана из меню покупки. Этот хендлер
    сохраняем только чтобы старые клавиатуры у юзеров отвечали
    нормальным алертом, а не молчали.
    """
    await callback.answer(
        "Оплата с баланса для пакетов трафика больше недоступна. "
        "Выберите другой способ: карта, СБП, Stars или CryptoBot.",
        show_alert=True,
    )
    return


@pay_traffic_router.callback_query(F.data.startswith("traffic_pay_card:"))
async def callback_traffic_pay_card(callback: CallbackQuery):
    """Pay for traffic pack via card (Telegram Payments / YooKassa)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    # Apply traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price
    price_kopecks = price * 100

    # Minimum Telegram payment: 64 RUB = 6400 kopecks
    MIN_PAYMENT_AMOUNT_KOPECKS = 6400
    if price_kopecks < MIN_PAYMENT_AMOUNT_KOPECKS:
        await callback.answer(i18n_get_text(language, "errors.payment_min_amount"), show_alert=True)
        return

    try:
        # Create pending_purchase with purchase_type='traffic_pack'
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"traffic_{gb}gb",
            period_days=0,
            price_kopecks=price_kopecks,
            purchase_type="traffic_pack",
        )

        payload = f"purchase:{purchase_id}"
        description = f"Atlas Secure — {gb} GB traffic"
        prices = [LabeledPrice(label=f"{gb} GB", amount=price_kopecks)]

        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=f"Atlas Secure — {gb} GB",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
        )

        logger.info(
            "TRAFFIC_CARD_INVOICE_SENT user=%s purchase_id=%s gb=%s price=%s",
            telegram_id, purchase_id, gb, price,
        )
        await callback.answer()

    except Exception as e:
        logger.exception("TRAFFIC_CARD_INVOICE_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@pay_traffic_router.callback_query(F.data.startswith("traffic_pay_sbp:"))
async def callback_traffic_pay_sbp(callback: CallbackQuery):
    """Pay for traffic pack via SBP (Platega, +11% markup)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.sbp_unavailable"), show_alert=True)
        return

    # Apply traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price
    price_kopecks = price * 100

    try:
        # Apply SBP markup (+11%)
        sbp_price_kopecks = platega_service.apply_sbp_markup(price_kopecks)

        # Create pending_purchase with purchase_type='traffic_pack'
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"traffic_{gb}gb",
            period_days=0,
            price_kopecks=sbp_price_kopecks,
            purchase_type="traffic_pack",
        )

        sbp_price_rubles = sbp_price_kopecks / 100.0

        # Create Platega transaction
        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description=f"Atlas Secure — {gb} GB traffic",
            purchase_id=purchase_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        # Save invoice_id
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id))
        except Exception as e:
            logger.error("Failed to save SBP transaction_id: purchase_id=%s error=%s", purchase_id, e)

        logger.info(
            "TRAFFIC_SBP_INVOICE_SENT user=%s purchase_id=%s gb=%s sbp_price=%.2f tx=%s",
            telegram_id, purchase_id, gb, sbp_price_rubles, transaction_id,
        )

        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.sbp_pay_button"),
                url=redirect_url,
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="buy_traffic",
            )],
        ])

        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.exception("TRAFFIC_SBP_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@pay_traffic_router.callback_query(F.data.startswith("traffic_pay_lava:"))
async def callback_traffic_pay_lava(callback: CallbackQuery):
    """Pay for traffic pack via Lava (card)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.lava_unavailable"), show_alert=True)
        return

    # Apply traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price
    price_kopecks = price * 100

    try:
        # Create pending_purchase with purchase_type='traffic_pack'
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"traffic_{gb}gb",
            period_days=0,
            price_kopecks=price_kopecks,
            purchase_type="traffic_pack",
        )

        price_rubles = price_kopecks / 100.0

        # Create Lava invoice
        invoice_data = await lava_service.create_invoice(
            amount_rubles=price_rubles,
            purchase_id=purchase_id,
            comment=f"Atlas Secure — {gb} GB traffic",
        )

        invoice_id = invoice_data["invoice_id"]
        payment_url = invoice_data["payment_url"]

        # Save invoice_id
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error("Failed to save Lava invoice_id: purchase_id=%s error=%s", purchase_id, e)

        logger.info(
            "TRAFFIC_LAVA_INVOICE_SENT user=%s purchase_id=%s gb=%s price=%.2f invoice=%s",
            telegram_id, purchase_id, gb, price_rubles, invoice_id,
        )

        text = i18n_get_text(language, "payment.lava_waiting", amount=price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.lava_pay_button"),
                url=payment_url,
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="buy_traffic",
            )],
        ])

        lava_msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, lava_msg.message_id))
        await callback.answer()

    except Exception as e:
        logger.exception("TRAFFIC_LAVA_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
