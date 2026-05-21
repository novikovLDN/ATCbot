"""
Standalone Telegram MTProto-proxy product.

A one-time, permanent purchase (config.PROXY_PRICE_RUBLES) that delivers a
single static proxy link shared by every buyer. It does NOT activate a VPN
subscription and is available to users without one.

Callbacks:
- proxy_menu      — sales screen (not owned) or delivery screen (owned)
- proxy_pay_sbp   — pay via SBP (Platega)
- proxy_pay_lava  — pay via card (Lava)

send_proxy_success() is invoked by the payment confirmation layer once a
proxy purchase webhook is confirmed.
"""
import asyncio
import logging

import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text

proxy_router = Router()
logger = logging.getLogger(__name__)

_LAVA_INVOICE_TIMEOUT = 15 * 60  # seconds


# ── Texts ───────────────────────────────────────────────────────────────

def _sales_text() -> str:
    return (
        "🧩 <b>Telegram-прокси Atlas Secure</b>\n\n"
        "Telegram замедлили или ограничили? Прокси вернёт мессенджеру "
        "нормальную скорость — без приложений, регистраций и настроек.\n\n"
        "<b>✨ Что вы получаете</b>\n"
        "⚡️ Подключение в одно касание\n"
        "🪶 Не расходует трафик и не влияет на скорость\n"
        "♾️ Разовая покупка — ссылка остаётся у вас навсегда\n\n"
        "<b>🛠 Как это работает</b>\n"
        "Вы оплачиваете — бот присылает кнопку «Подключить». Одно "
        "нажатие, и Telegram уже работает через прокси.\n\n"
        "<b>⚠️ Прокси и VPN конфликтуют</b>\n"
        "На устройстве с активным VPN Atlas Secure прокси не нужен. "
        "Используйте прокси там, где Telegram работает <b>без VPN</b>.\n\n"
        "<b>💡 Важно понимать</b>\n"
        "Прокси ускоряет <b>только Telegram</b>. Чтобы открыть сайты, "
        "сервисы и приложения целиком — нужен VPN Atlas Secure.\n\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        f"💳 Стоимость: <b>{config.PROXY_PRICE_RUBLES} ₽</b> — "
        "один раз и навсегда\n"
        "<i>При оплате через СБП банк добавляет небольшую комиссию.</i>"
    )


def _delivery_text() -> str:
    return (
        "🧩 <b>Ваш Telegram-прокси готов</b>\n\n"
        "Как подключить:\n"
        "1️⃣ Нажмите кнопку «🔌 Подключить прокси» ниже\n"
        "2️⃣ В открывшемся окне Telegram нажмите «Подключить»\n"
        "3️⃣ Готово — Telegram работает через прокси\n\n"
        "⚠️ Прокси ускоряет только Telegram. На устройстве с активным "
        "VPN Atlas Secure прокси включать не нужно — они могут "
        "конфликтовать.\n\n"
        "🌍 Нужен полный доступ ко всем сайтам и приложениям, а не "
        "только к Telegram? Оформите VPN Atlas Secure."
    )


def _sales_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🏦 Оплатить через СБП — {config.PROXY_PRICE_RUBLES} ₽",
            callback_data="proxy_pay_sbp",
        )],
        [InlineKeyboardButton(
            text=f"💳 Оплатить картой — {config.PROXY_PRICE_RUBLES} ₽",
            callback_data="proxy_pay_lava",
        )],
        [InlineKeyboardButton(text="⚡️ Купить VPN", callback_data="menu_buy_vpn")],
        [InlineKeyboardButton(text="← Назад", callback_data="menu_main")],
    ])


def _delivery_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔌 Подключить прокси", url=config.PROXY_HTTPS_LINK,
        )],
        [InlineKeyboardButton(text="⚡️ Купить VPN", callback_data="menu_buy_vpn")],
        [InlineKeyboardButton(text="← Назад", callback_data="menu_main")],
    ])


# ── Handlers ────────────────────────────────────────────────────────────

@proxy_router.callback_query(F.data == "proxy_menu")
async def callback_proxy_menu(callback: CallbackQuery):
    """Sales screen for new buyers, delivery screen for existing owners."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id

    if await database.has_purchased_proxy(telegram_id):
        await safe_edit_text(
            callback.message, _delivery_text(),
            reply_markup=_delivery_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    await safe_edit_text(
        callback.message, _sales_text(),
        reply_markup=_sales_keyboard(), bot=callback.bot, parse_mode="HTML",
    )


@proxy_router.callback_query(F.data == "proxy_pay_sbp")
async def callback_proxy_pay_sbp(callback: CallbackQuery):
    """Pay for the proxy product via SBP (Platega)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id

    if await database.has_purchased_proxy(telegram_id):
        await callback.answer("Прокси уже куплен.", show_alert=True)
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer("Оплата через СБП временно недоступна.", show_alert=True)
        return

    try:
        sbp_price_kopecks = platega_service.apply_sbp_markup(
            config.PROXY_PRICE_RUBLES * 100
        )
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff="proxy",
            period_days=0,
            price_kopecks=sbp_price_kopecks,
            purchase_type="proxy",
        )
        sbp_price_rubles = sbp_price_kopecks / 100.0

        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description="Atlas Secure — Telegram-прокси",
            purchase_id=purchase_id,
        )
        try:
            await database.update_pending_purchase_invoice_id(
                purchase_id, str(tx_data["transaction_id"])
            )
        except Exception as e:
            logger.error("PROXY_SBP: failed to save tx_id purchase_id=%s: %s", purchase_id, e)

        text = (
            f"🏦 <b>Оплата через СБП</b>\n\n"
            f"Сумма к оплате: <b>{sbp_price_rubles:.2f} ₽</b>\n\n"
            "Нажмите кнопку ниже, оплатите — прокси придёт автоматически."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к оплате", url=tx_data["redirect_url"])],
            [InlineKeyboardButton(text="← Назад", callback_data="proxy_menu")],
        ])
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        logger.exception("PROXY_SBP_ERROR user=%s: %s", telegram_id, e)
        await callback.answer("Не удалось создать платёж. Попробуйте позже.", show_alert=True)


@proxy_router.callback_query(F.data == "proxy_pay_lava")
async def callback_proxy_pay_lava(callback: CallbackQuery):
    """Pay for the proxy product via card (Lava)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id

    if await database.has_purchased_proxy(telegram_id):
        await callback.answer("Прокси уже куплен.", show_alert=True)
        return

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer("Оплата картой временно недоступна.", show_alert=True)
        return

    try:
        price_rubles = float(config.PROXY_PRICE_RUBLES)
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff="proxy",
            period_days=0,
            price_kopecks=config.PROXY_PRICE_RUBLES * 100,
            purchase_type="proxy",
        )

        invoice_data = await lava_service.create_invoice(
            amount_rubles=price_rubles,
            purchase_id=purchase_id,
            comment="Atlas Secure — Telegram-прокси",
        )
        try:
            await database.update_pending_purchase_invoice_id(
                purchase_id, str(invoice_data["invoice_id"])
            )
        except Exception as e:
            logger.error("PROXY_LAVA: failed to save invoice_id purchase_id=%s: %s", purchase_id, e)

        text = (
            f"💳 <b>Оплата картой</b>\n\n"
            f"Сумма к оплате: <b>{price_rubles:.0f} ₽</b>\n\n"
            "Нажмите кнопку ниже, оплатите — прокси придёт автоматически."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к оплате", url=invoice_data["payment_url"])],
            [InlineKeyboardButton(text="← Назад", callback_data="proxy_menu")],
        ])
        lava_msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        asyncio.create_task(_auto_delete(callback.bot, telegram_id, lava_msg.message_id))
        await callback.answer()
    except Exception as e:
        logger.exception("PROXY_LAVA_ERROR user=%s: %s", telegram_id, e)
        await callback.answer("Не удалось создать платёж. Попробуйте позже.", show_alert=True)


async def _auto_delete(bot, chat_id: int, message_id: int):
    """Delete a Lava invoice message after it expires."""
    try:
        await asyncio.sleep(_LAVA_INVOICE_TIMEOUT)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def send_proxy_success(bot, telegram_id: int, purchase_id: str, pending: dict):
    """Deliver the proxy after a confirmed payment. Called from confirmation.py."""
    await database.mark_proxy_purchased(telegram_id)
    try:
        await bot.send_message(
            telegram_id, _delivery_text(),
            reply_markup=_delivery_keyboard(), parse_mode="HTML",
        )
    except Exception as e:
        logger.error("PROXY_DELIVERY_FAILED user=%s purchase_id=%s: %s", telegram_id, purchase_id, e)
