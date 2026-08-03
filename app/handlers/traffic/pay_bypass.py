"""Выставление счёта за пакет обхода белых списков.

ЧТО ЗДЕСЬ
    Обработчики bypass_pay_* — карта, СБП, Telegram Stars, CryptoBot, Lava.
    Создают pending_purchase с purchase_type='traffic_pack' и
    tariff='bypass_{N}gb'.

ПОЧЕМУ ОТДЕЛЬНО ОТ ТРАФИКА
    Префикс тарифа другой, набор провайдеров шире (Stars и крипта есть
    только здесь), кнопка «назад» ведёт на свой экран. Единый модуль на два
    продукта путает префиксы, а неверный tariff — это выдача не того товара.

ЧТО ЛЕГКО СЛОМАТЬ
    Stars — единственный провайдер, где в price_kopecks кладётся не сумма в
    копейках, а КОЛИЧЕСТВО ЗВЁЗД (валюта XTR, provider_token пустой).
    Трактовка этого поля как копеек ломает и счёт, и последующую сверку.

    _bypass_price возвращает пару (price, pack) и (None, None) для
    неизвестного объёма, а проверка идёт по `if not price` — нулевая цена
    тоже считается отказом, и это защищает от бесплатной выдачи при кривом
    конфиге.

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
from ._shared import _auto_delete_lava_msg

pay_bypass_router = Router()
logger = logging.getLogger(__name__)


@pay_bypass_router.callback_query(F.data.startswith("bypass_pay_balance:"))
async def callback_bypass_pay_balance(callback: CallbackQuery):
    """Оплата bypass-only пакета с баланса.

    Метод отключён — оставляем хендлер только чтобы старые
    клавиатуры в чатах юзеров не «молчали»: отвечаем алертом,
    что опция больше недоступна, и не списываем баланс.
    """
    await callback.answer(
        "Оплата с баланса для пакетов трафика больше недоступна. "
        "Выберите другой способ: карта, СБП или Telegram Stars.",
        show_alert=True,
    )
    return


def _get_bypass_pack(gb: int):
    """Get bypass pack from TRAFFIC_PACKS or TRAFFIC_PACKS_EXTENDED."""
    return config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)


async def _bypass_price(telegram_id: int, gb: int):
    """Calculate bypass pack price with discount."""
    pack = _get_bypass_pack(gb)
    if not pack:
        return None, None
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price
    return price, pack


@pay_bypass_router.callback_query(F.data.startswith("bypass_pay_card:"))
async def callback_bypass_pay_card(callback: CallbackQuery):
    """Pay for bypass-only pack via card (Telegram Payments)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    price, pack = await _bypass_price(telegram_id, gb)
    if not price:
        return

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    price_kopecks = price * 100
    MIN_PAYMENT_AMOUNT_KOPECKS = 6400
    if price_kopecks < MIN_PAYMENT_AMOUNT_KOPECKS:
        await callback.answer(i18n_get_text(language, "errors.payment_min_amount"), show_alert=True)
        return

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"bypass_{gb}gb",
            period_days=0,
            price_kopecks=price_kopecks,
            purchase_type="traffic_pack",
        )

        payload = f"purchase:{purchase_id}"
        prices = [LabeledPrice(label=f"Bypass {gb} GB", amount=price_kopecks)]

        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=f"Atlas Secure — Bypass {gb} GB",
            description=f"Bypass whitelist traffic — {gb} GB",
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
        )
        logger.info("BYPASS_CARD_INVOICE_SENT user=%s purchase_id=%s gb=%s price=%s", telegram_id, purchase_id, gb, price)
        await callback.answer()

    except Exception as e:
        logger.exception("BYPASS_CARD_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@pay_bypass_router.callback_query(F.data.startswith("bypass_pay_sbp:"))
async def callback_bypass_pay_sbp(callback: CallbackQuery):
    """Pay for bypass-only pack via SBP (Platega, +11%)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    price, pack = await _bypass_price(telegram_id, gb)
    if not price:
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.sbp_unavailable"), show_alert=True)
        return

    price_kopecks = price * 100

    try:
        sbp_price_kopecks = platega_service.apply_sbp_markup(price_kopecks)

        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"bypass_{gb}gb",
            period_days=0,
            price_kopecks=sbp_price_kopecks,
            purchase_type="traffic_pack",
        )

        sbp_price_rubles = sbp_price_kopecks / 100.0

        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description=f"Atlas Secure — Bypass {gb} GB",
            purchase_id=purchase_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id))
        except Exception as e:
            logger.error("Failed to save SBP tx_id: purchase_id=%s error=%s", purchase_id, e)

        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "payment.sbp_pay_button"), url=redirect_url)],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="buy_bypass_only")],
        ])
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.exception("BYPASS_SBP_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@pay_bypass_router.callback_query(F.data.startswith("bypass_pay_stars:"))
async def callback_bypass_pay_stars(callback: CallbackQuery):
    """Pay for bypass-only pack via Telegram Stars."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    price, pack = await _bypass_price(telegram_id, gb)
    if not price:
        return

    # Convert RUB to Stars (+70% markup, ~1.85 RUB per star)
    price_stars = math.ceil(price * 1.7 / 1.85)
    if price_stars < 1:
        price_stars = 1

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"bypass_{gb}gb",
            period_days=0,
            price_kopecks=price_stars,
            purchase_type="traffic_pack",
        )

        payload = f"purchase:{purchase_id}"
        prices = [LabeledPrice(label=f"Bypass {gb} GB", amount=price_stars)]

        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=f"Atlas Secure — Bypass {gb} GB",
            description=f"Bypass whitelist traffic — {gb} GB",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
        )
        logger.info("BYPASS_STARS_INVOICE_SENT user=%s purchase_id=%s gb=%s stars=%s", telegram_id, purchase_id, gb, price_stars)
        await callback.answer()

    except Exception as e:
        logger.exception("BYPASS_STARS_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@pay_bypass_router.callback_query(F.data.startswith("bypass_pay_crypto:"))
async def callback_bypass_pay_crypto(callback: CallbackQuery):
    """Pay for bypass-only pack via CryptoBot."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    price, pack = await _bypass_price(telegram_id, gb)
    if not price:
        return

    import cryptobot_service
    if not cryptobot_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
        return

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"bypass_{gb}gb",
            period_days=0,
            price_kopecks=price * 100,
            purchase_type="traffic_pack",
        )

        invoice = await cryptobot_service.create_invoice(
            amount_rubles=float(price),
            description=f"Atlas Secure — Bypass {gb} GB",
            purchase_id=purchase_id,
        )

        pay_url = invoice.get("pay_url") or invoice.get("bot_invoice_url")
        if not pay_url:
            raise ValueError("No pay_url in CryptoBot response")

        text = i18n_get_text(language, "payment.crypto_waiting", amount=float(price))
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "payment.crypto_pay_button"), url=pay_url)],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="buy_bypass_only")],
        ])
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.exception("BYPASS_CRYPTO_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@pay_bypass_router.callback_query(F.data.startswith("bypass_pay_lava:"))
async def callback_bypass_pay_lava(callback: CallbackQuery):
    """Pay for bypass-only pack via Lava (card)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    price, pack = await _bypass_price(telegram_id, gb)
    if not price:
        return

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.lava_unavailable"), show_alert=True)
        return

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"bypass_{gb}gb",
            period_days=0,
            price_kopecks=price * 100,
            purchase_type="traffic_pack",
        )

        price_rubles = float(price)

        invoice_data = await lava_service.create_invoice(
            amount_rubles=price_rubles,
            purchase_id=purchase_id,
            comment=f"Atlas Secure — Bypass {gb} GB",
        )

        invoice_id = invoice_data["invoice_id"]
        payment_url = invoice_data["payment_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error("Failed to save Lava invoice_id: purchase_id=%s error=%s", purchase_id, e)

        text = i18n_get_text(language, "payment.lava_waiting", amount=price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "payment.lava_pay_button"), url=payment_url)],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="buy_bypass_only")],
        ])
        lava_msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        asyncio.create_task(_auto_delete_lava_msg(callback.bot, telegram_id, lava_msg))
        await callback.answer()

    except Exception as e:
        logger.exception("BYPASS_LAVA_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
