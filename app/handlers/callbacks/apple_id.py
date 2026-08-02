"""Пополнение Apple ID: выбор региона, номинала и оплата.

ЧТО ЗДЕСЬ ЕСТЬ
    Экраны выбора региона и суммы, подтверждение заказа и три способа
    оплаты — картой, СБП и через Lava. После оплаты заказ уходит админу
    на ручное исполнение: пополнение Apple ID автоматизировать нельзя.

КАК КОДИРУЕТСЯ ЗАКАЗ
    Регион и номинал зашиты в tariff покупки строкой вида
    apple_id_<регион>_<номинал>. Разбор идёт по позициям, поэтому менять
    формат нельзя, не поправив разбор в app/services/payments/confirmation.py —
    там ровно та же строка раскладывается обратно.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Выделено из navigation.py, где под навигацией лежало 2228 строк вместе
    с инструкциями подключения, мини-магазином и этими платежами.
"""
import asyncio
import io
import logging
import os

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.filters import StateFilter

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.callbacks.language import MAIN_PHOTO_FILE_ID as _MAIN_PHOTO_ID
from app.handlers.common.utils import format_text_with_incident, safe_edit_text
from app.handlers.common.screens import show_profile, _open_help_screen
from app.handlers.common.keyboards import (
    get_main_menu_keyboard,
    get_about_keyboard,
    get_service_status_keyboard,
    get_connect_keyboard,
)
router = Router()

logger = logging.getLogger(__name__)

# Курсы и номиналы для расчёта цены. Курс зашит в код намеренно: он
# закладывает маржу и меняется вручную вместе с прайсом, а не тянется
# из внешнего источника — иначе скачок курса менял бы цены без ведома
# владельца.
_APPLE_USD_RATE = 101   # RUB per 1 USD
_APPLE_TRY_RATE = 2.9   # RUB per 1 TRY

_APPLE_NOMINALS = {
    "usa": [2, 5, 10, 15, 20, 25, 50, 60, 70],
    "turkey": [100, 150, 200, 300, 500, 600],
    "russia": [500, 800, 1000, 1500, 2000, 2500, 3000],
    "india": [100, 200, 250, 500, 1000],
}
_APPLE_CURRENCIES = {"usa": "$", "turkey": "TL", "russia": "₽", "india": "INR"}
# Курс перевода номинала в рубли по регионам. Для регионов без записи
# используется запасное значение 93 в _apple_price_rub.
_APPLE_RATES = {"usa": _APPLE_USD_RATE, "turkey": _APPLE_TRY_RATE}
_APPLE_REGIONS = {
    "usa": "🇺🇸 USA",
    "turkey": "🇹🇷 Turkey",
    "russia": "🇷🇺 Russia",
    "india": "🇮🇳 India",
}

# Явные price-точки для регионов, где нет линейного rate-конвертирования.
# Ключ — nominal региона, значение — цена в рублях к оплате.
_APPLE_PRICES_EXPLICIT: dict[str, dict[int, int]] = {
    "russia": {
        500: 1400, 800: 2200, 1000: 2600, 1500: 3900,
        2000: 5200, 2500: 6400, 3000: 7700,
    },
    "india": {
        100: 149, 200: 249, 250: 299, 500: 599, 1000: 1099,
    },
}


def _apple_price_rub(region: str, nominal: int) -> float:
    """RUB-цена номинала для региона. Explicit-таблица приоритетнее rate."""
    table = _APPLE_PRICES_EXPLICIT.get(region)
    if table and nominal in table:
        return float(table[nominal])
    rate = _APPLE_RATES.get(region, 93)
    return round(nominal * rate, 2)


def _apple_nominal_label(region: str, nominal: int) -> str:
    """5$ / 500 TL / 500₽ / 100 INR — как показать номинал юзеру."""
    cur = _APPLE_CURRENCIES.get(region, "$")
    if cur == "$":
        return f"{nominal}$"
    if cur == "₽":
        return f"{nominal}₽"
    return f"{nominal} {cur}"


@router.callback_query(F.data == "apple_region")
async def callback_apple_region(callback: CallbackQuery):
    """Apple ID — region selection."""
    try:
        await callback.answer()
    except Exception:
        pass
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "shop.apple_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇸 USA", callback_data="apple_amount:usa")],
        [InlineKeyboardButton(text="🇹🇷 Turkey", callback_data="apple_amount:turkey")],
        [InlineKeyboardButton(text="🇷🇺 Russia", callback_data="apple_amount:russia")],
        [InlineKeyboardButton(text="🇮🇳 India", callback_data="apple_amount:india")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("apple_amount:"))
async def callback_apple_amount(callback: CallbackQuery):
    """Apple ID — nominal selection."""
    try:
        await callback.answer()
    except Exception:
        pass
    region = callback.data.split(":")[1]
    language = await resolve_user_language(callback.from_user.id)
    nominals = _APPLE_NOMINALS.get(region, [])
    region_label = _APPLE_REGIONS.get(region, region)

    text = i18n_get_text(language, "shop.apple_amount_title", region=region_label)

    buttons = []
    row = []
    for nom in nominals:
        price_rub = round(_apple_price_rub(region, nom))
        row.append(InlineKeyboardButton(
            text=f"{_apple_nominal_label(region, nom)} — {price_rub}₽",
            callback_data=f"apple_confirm:{region}:{nom}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"), callback_data="apple_region",
    )])

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        bot=callback.bot, parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("apple_confirm:"))
async def callback_apple_confirm(callback: CallbackQuery):
    """Apple ID — confirmation screen with payment options."""
    try:
        await callback.answer()
    except Exception:
        pass
    parts = callback.data.split(":")
    region = parts[1]
    nominal = int(parts[2])
    language = await resolve_user_language(callback.from_user.id)

    region_label = _APPLE_REGIONS.get(region, region)
    price_rub = _apple_price_rub(region, nominal)
    nominal_str = _apple_nominal_label(region, nominal)

    text = i18n_get_text(language, "shop.apple_confirm",
                         region=region_label, nominal=nominal_str, price=price_rub)

    buttons = [
        [InlineKeyboardButton(text="💳 Банковская карта", callback_data=f"apple_pay_card:{region}:{nominal}")],
        [InlineKeyboardButton(text="📱 СБП 3%", callback_data=f"apple_pay_lava:{region}:{nominal}")],
        [InlineKeyboardButton(text="📱 СБП", callback_data=f"apple_pay_sbp:{region}:{nominal}")],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"), callback_data=f"apple_amount:{region}",
        )],
    ]

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        bot=callback.bot, parse_mode="HTML",
    )


# ── Apple ID Payment Handlers ────────────────────────────────────

@router.callback_query(F.data.startswith("apple_pay_lava:"))
async def callback_apple_pay_lava(callback: CallbackQuery):
    """Apple ID — pay via Lava (card)."""
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    region = parts[1]
    nominal = int(parts[2])
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    price_rub = _apple_price_rub(region, nominal)

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer("Оплата картой временно недоступна", show_alert=True)
        return

    region_label = _APPLE_REGIONS.get(region, region)
    nominal_label = _apple_nominal_label(region, nominal)

    purchase_id = await database.create_pending_purchase(
        telegram_id=telegram_id,
        tariff=f"apple_id_{region}_{nominal}",
        period_days=0,
        price_kopecks=round(price_rub * 100),
        purchase_type="apple_id",
    )

    invoice_data = await lava_service.create_invoice(
        amount_rubles=price_rub,
        purchase_id=purchase_id,
        comment=f"Apple ID {region_label} {nominal_label}",
    )

    payment_url = invoice_data["payment_url"]

    try:
        await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_data["invoice_id"]))
    except Exception:
        pass

    text = i18n_get_text(language, "payment.lava_waiting", amount=price_rub)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "payment.lava_pay_button"), url=payment_url)],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop")],
    ])

    lava_msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    async def _del(bot, cid, msg):
        try:
            await asyncio.sleep(15 * 60)
            await bot.delete_message(chat_id=cid, message_id=msg.message_id)
        except Exception:
            pass
    asyncio.create_task(_del(callback.bot, telegram_id, lava_msg))


async def send_apple_id_success(bot, telegram_id: int, region: str, nominal: int, price_rub: float):
    """Send user confirmation + admin notification for Apple ID purchase."""
    from datetime import datetime, timezone

    language = await resolve_user_language(telegram_id)
    region_label = _APPLE_REGIONS.get(region, region)
    nominal_str = _apple_nominal_label(region, nominal)
    price_str = f"{price_rub:.2f}"

    # User notification
    text = i18n_get_text(
        language, "shop.apple_success",
        region=region_label, nominal=nominal_str, price=price_str,
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/atlas_suppbot")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_main")],
    ])
    try:
        await bot.send_message(telegram_id, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error("APPLE_SUCCESS_MSG_FAILED user=%s error=%s", telegram_id, e)

    # Admin notification with chat button
    user = await database.get_user(telegram_id)
    buyer_username = f"@{user['username']}" if user and user.get("username") else "—"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    admin_text = i18n_get_text(
        "ru", "shop.apple_admin",
        buyer_id=telegram_id, buyer_username=buyer_username,
        region=region_label, nominal=nominal_str,
        price=price_str, date=now_str,
    )
    try:
        from app.handlers.admin.apple_id_delivery import build_apple_admin_keyboard
        admin_kb = build_apple_admin_keyboard(telegram_id, region, nominal)
        await bot.send_message(config.ADMIN_TELEGRAM_ID, admin_text, reply_markup=admin_kb, parse_mode="HTML")
    except Exception as e:
        logger.error("APPLE_ADMIN_NOTIFY_FAILED error=%s", e)


@router.callback_query(F.data.startswith("apple_pay_card:"))
async def callback_apple_pay_card(callback: CallbackQuery):
    """Apple ID — pay via YooKassa (Telegram Payments)."""
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    region = parts[1]
    nominal = int(parts[2])
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    price_rub = _apple_price_rub(region, nominal)
    price_kopecks = round(price_rub * 100)
    nominal_label = _apple_nominal_label(region, nominal)
    region_label = _APPLE_REGIONS.get(region, region)

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer("Оплата картой временно недоступна", show_alert=True)
        return

    MIN_PAYMENT_KOPECKS = 6400
    if price_kopecks < MIN_PAYMENT_KOPECKS:
        await callback.answer("Сумма ниже минимальной для оплаты картой (64₽)", show_alert=True)
        return

    purchase_id = await database.create_pending_purchase(
        telegram_id=telegram_id,
        tariff=f"apple_id_{region}_{nominal}",
        period_days=0,
        price_kopecks=price_kopecks,
        purchase_type="apple_id",
    )

    from aiogram.types import LabeledPrice
    payload = f"purchase:{purchase_id}"

    try:
        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=f"Apple ID {region_label} {nominal_label}",
            description=f"Пополнение Apple ID {region_label} на {nominal_label}",
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=f"Apple ID {nominal_label}", amount=price_kopecks)],
        )
        await callback.bot.send_message(
            chat_id=telegram_id,
            text=i18n_get_text(language, "payment.invoice_timeout"),
            parse_mode="HTML",
        )

        async def _del_invoice(bot, cid, msg):
            try:
                await asyncio.sleep(15 * 60)
                await bot.delete_message(chat_id=cid, message_id=msg.message_id)
            except Exception:
                pass
        asyncio.create_task(_del_invoice(callback.bot, telegram_id, invoice_msg))
        await callback.answer()
    except Exception as e:
        logger.exception("APPLE_CARD_INVOICE_ERROR user=%s: %s", telegram_id, e)
        await callback.answer("Ошибка создания платежа", show_alert=True)


@router.callback_query(F.data.startswith("apple_pay_sbp:"))
async def callback_apple_pay_sbp(callback: CallbackQuery):
    """Apple ID — pay via SBP (Platega)."""
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    region = parts[1]
    nominal = int(parts[2])
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    price_rub = _apple_price_rub(region, nominal)
    price_kopecks = round(price_rub * 100)
    nominal_label = _apple_nominal_label(region, nominal)
    region_label = _APPLE_REGIONS.get(region, region)

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer("СБП временно недоступен", show_alert=True)
        return

    try:
        sbp_price_kopecks = platega_service.apply_sbp_markup(price_kopecks)
        sbp_price_rubles = sbp_price_kopecks / 100.0

        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"apple_id_{region}_{nominal}",
            period_days=0,
            price_kopecks=sbp_price_kopecks,
            purchase_type="apple_id",
        )

        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description=f"Apple ID {region_label} {nominal_label}",
            purchase_id=purchase_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id))
        except Exception:
            pass

        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.sbp_pay_button"),
                url=redirect_url,
            )],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop")],
        ])
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    except Exception as e:
        logger.exception("APPLE_SBP_ERROR user=%s: %s", telegram_id, e)
        await callback.answer("Ошибка создания платежа СБП", show_alert=True)
