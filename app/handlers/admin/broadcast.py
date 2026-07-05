"""
Admin broadcast handlers: create broadcasts, A/B tests, no-subscription broadcasts.
"""
import logging
import asyncio
import random
from datetime import datetime, timezone, timedelta

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.states import BroadcastCreate, AdminBroadcastNoSubscription
from app.handlers.admin.keyboards import (
    get_admin_back_keyboard,
    get_broadcast_test_type_keyboard,
    get_broadcast_segment_keyboard,
    get_broadcast_confirm_keyboard,
    get_broadcast_buttons_keyboard,
    get_ab_test_list_keyboard,
)
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.guards import ensure_db_ready_callback, ensure_db_ready_message
from app.services.user_subscription_links import get_user_bypass_url


# ── Preset: maintenance broadcast with bypass key + 20% traffic discount ──
# Lives here as a literal so the admin can fire it from the dashboard
# without retyping. {bypass_key} is substituted per recipient in _send_one.
_PRESET_MAINTENANCE_TITLE = (
    "![🛠](tg://emoji?id=5462921117423384478) "
    "<b>Тех. работы на основных серверах</b>"
)
_PRESET_MAINTENANCE_TEXT = (
    "До <b>25.05</b> просим временно использовать наши <b>серверы обхода "
    "белых списков</b> — они работают стабильно.\n\n"
    "![🎁](tg://emoji?id=5384578448633129482) <b>Скидка 20% на ГБ обхода</b> "
    "— забрать по кнопке <b>«Купить трафик»</b> ниже.\n\n"
    "━━━━━━━━━━━━━━\n"
    "![🔑](tg://emoji?id=5465443379917629504) <b>Ваш ключ обхода</b>\n\n"
    "<code>{bypass_key}</code>\n\n"
    "<i>Нажмите, чтобы скопировать.</i>\n"
    "━━━━━━━━━━━━━━\n\n"
    "📲 <b>Подключение через Happ</b>\n"
    "<blockquote>"
    "![1️⃣](tg://emoji?id=5382322671679708881) Скопируйте ключ выше одним "
    "нажатием по нему\n"
    "![2️⃣](tg://emoji?id=5381990043642502553) Откройте приложение\n"
    "![3️⃣](tg://emoji?id=5381879959335738545) Справа сверху нажмите "
    "<b>«+»</b> → <b>«Вставить из буфера»</b>\n"
    "![4️⃣](tg://emoji?id=5382054253403577563) Выберите сервера с пометкой "
    "<b>LTE</b> и включите соединение"
    "</blockquote>\n\n"
    "По окончании работ всё вернётся автоматически — переключать обратно "
    "не нужно. Спасибо за понимание "
    "![🧩](tg://emoji?id=5265120027853481187)"
)

admin_broadcast_router = Router()
logger = logging.getLogger(__name__)

# Production broadcast: controlled concurrency, rate limiting, event-loop safe
BROADCAST_CONCURRENCY = 15          # Safe under Telegram 30 msg/sec
BROADCAST_BATCH_SIZE = 200          # Soft batch limit
BROADCAST_BATCH_PAUSE = 2           # Seconds between batches
BROADCAST_RETRY_LIMIT = 3           # Retry per user


async def _safe_send(
    bot: Bot,
    user_id: int,
    text: str,
    semaphore: asyncio.Semaphore,
    photo_file_id: str | None = None,
    caption: str | None = None,
) -> int | None:
    """Send message or photo. Returns message_id on success, None on failure."""
    from app.utils.telegram_safe import convert_tg_emoji
    text = convert_tg_emoji(text)
    if caption:
        caption = convert_tg_emoji(caption)
    async with semaphore:
        for attempt in range(BROADCAST_RETRY_LIMIT):
            try:
                if photo_file_id:
                    result = await bot.send_photo(
                        user_id,
                        photo=photo_file_id,
                        caption=caption or text,
                        parse_mode="HTML",
                    )
                else:
                    result = await bot.send_message(user_id, text, parse_mode="HTML")
                return result.message_id
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                await asyncio.sleep(1)
        return None



async def _safe_send_with_buttons(
    bot: Bot,
    user_id: int,
    text: str,
    semaphore: asyncio.Semaphore,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo_file_id: str | None = None,
    caption: str | None = None,
) -> int | None:
    """Send message with optional inline buttons. Returns message_id on success, None on failure."""
    from app.utils.telegram_safe import convert_tg_emoji
    text = convert_tg_emoji(text)
    if caption:
        caption = convert_tg_emoji(caption)
    async with semaphore:
        for attempt in range(BROADCAST_RETRY_LIMIT):
            try:
                if photo_file_id:
                    result = await bot.send_photo(
                        user_id,
                        photo=photo_file_id,
                        caption=caption or text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                else:
                    result = await bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode="HTML")
                return result.message_id
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                await asyncio.sleep(1)
        return None


def _build_broadcast_reply_markup(
    buttons: list[str],
    broadcast_id: int,
    discount: int | None = None,
) -> InlineKeyboardMarkup | None:
    """Build inline keyboard for broadcast message based on selected buttons."""
    if not buttons:
        return None

    rows = []
    for btn in buttons:
        if btn == "buy":
            rows.append([InlineKeyboardButton(
                text="Купить",
                callback_data="menu_buy_vpn",
                icon_custom_emoji_id="5199785165735367039",  # ⚡️
            )])
        elif btn == "promo_buy":
            label = f"🎁 Купить со скидкой {discount}%" if discount else "🎁 Купить со скидкой"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"broadcast_promo_buy:{broadcast_id}")])
        elif btn == "promo_traffic":
            label = f"📊 Купить трафик −{discount}%" if discount else "📊 Купить трафик"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"broadcast_promo_traffic:{broadcast_id}")])
        elif btn == "gift_3m":
            rows.append([InlineKeyboardButton(
                text="🎁 Скидка 30% на 3 месяца",
                callback_data="broadcast_gift_3m",
            )])
        elif btn == "gift_1y_40":
            rows.append([InlineKeyboardButton(
                text="🎁 1 год со скидкой 40%",
                callback_data="broadcast_gift_1y_40",
            )])
        elif btn == "bypass":
            rows.append([InlineKeyboardButton(text="🌐 Включить обход", callback_data="traffic_info")])
        elif btn == "channel":
            rows.append([InlineKeyboardButton(text="📢 Наш канал", url="https://t.me/ATC_VPN")])
        elif btn == "support":
            rows.append([InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/atlas_suppbot")])
        elif btn == "referral":
            rows.append([InlineKeyboardButton(text="👥 Пригласить друга", callback_data="menu_referral")])
        elif btn == "happ_ios":
            rows.append([InlineKeyboardButton(
                text="📲 Скачать Happ для iOS ⚡️",
                url="https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?l=en-GB",
            )])
        elif btn == "happ_android":
            rows.append([InlineKeyboardButton(
                text="📲 Скачать Happ для Android 🤖",
                url="https://play.google.com/store/apps/details?id=com.happproxy&hl=ru",
            )])
        elif btn == "web_client":
            rows.append([InlineKeyboardButton(
                text="🌐 Веб-клиент QoDev",
                url="https://qodev.dev",
            )])
        elif btn == "buy_combo":
            rows.append([InlineKeyboardButton(
                text="Купить Комбо",
                callback_data="buy_combo",
                icon_custom_emoji_id="5199785165735367039",  # ⚡️
            )])
        elif btn == "proxy":
            rows.append([InlineKeyboardButton(text="🌐 MT Прокси", callback_data="proxy_open")])
        elif btn == "share_discount":
            # Recipient таппает → переходит на экран «подари другу
            # скидку 30%» (callback share_discount_open). Там уже его
            # личная share-ссылка на t.me/share/url, открывающая
            # нативный picker Telegram.
            rows.append([InlineKeyboardButton(
                text="🎁 Поделиться скидкой",
                callback_data="share_discount_open",
            )])

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


@admin_broadcast_router.callback_query(F.data == "admin:bcast_preset_maintenance")
async def callback_admin_bcast_preset_maintenance(callback: CallbackQuery, state: FSMContext):
    """One-click maintenance broadcast: pre-fills the wizard at the confirm
    step with the bypass-key text, the «Купить трафик» button and a 20%
    traffic discount, then shows the standard preview. Confirm reuses the
    normal broadcast:confirm_send handler — same send pipeline as the
    manual wizard."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)

    await state.clear()
    await state.update_data(
        title=_PRESET_MAINTENANCE_TITLE,
        message=_PRESET_MAINTENANCE_TEXT,
        emoji="",
        type="custom",
        is_ab_test=False,
        has_photo=False,
        segment="active_subscriptions",
        broadcast_buttons=["promo_traffic"],
        broadcast_discount=20,
        broadcast_discount_hours=24,
        broadcast_discount_label="1 день",
    )
    await state.set_state(BroadcastCreate.waiting_for_confirm)

    body = f"{_PRESET_MAINTENANCE_TITLE}\n\n{_PRESET_MAINTENANCE_TEXT}"
    preview_text = (
        "👁 <b>Предпросмотр рассылки</b>\n\n"
        "<b>Сегмент:</b> Активные подписки\n"
        "<b>Кнопка:</b> 📊 Купить трафик −20%\n"
        "<b>Скидка:</b> 20% на ГБ обхода (24 часа после клика)\n\n"
        "━━━ Текст уведомления ━━━\n\n"
        f"{body}\n\n"
        "━━━━━━━━━━━━━━\n"
        "<i>В тексте плейсхолдер {bypass_key} — у каждого получателя "
        "подставится его персональная ссылка обхода. Пользователи без "
        "ключа будут пропущены.</i>"
    )

    await safe_edit_text(
        callback.message, preview_text,
        reply_markup=get_broadcast_confirm_keyboard(language),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_promo_buy:"))
async def callback_broadcast_promo_buy(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал 'Купить со скидкой' в уведомлении — автоматически применяем скидку"""
    await callback.answer()

    try:
        broadcast_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    telegram_id = callback.from_user.id

    try:
        # Get discount from DB
        discount = await database.get_broadcast_discount(broadcast_id)
        if not discount:
            # No discount found, just redirect to tariff selection.
            # force_new_message=True — сохраняем оригинал рассылки в чате,
            # экран тарифов уходит свежим сообщением сверху.
            from app.handlers.common.screens import show_tariffs_main_screen
            await show_tariffs_main_screen(callback, state, force_new_message=True)
            return

        discount_percent = discount.get("discount_percent", 0)
        discount_hours = discount.get("discount_hours", 168)  # default 7 days
        discount_label = discount.get("discount_label", "7 дней")

        # Auto-apply discount to user with configured duration
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(hours=discount_hours)
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
        )

        # Redirect to tariff screen. force_new_message=True — рассылка
        # остаётся (юзер видит, на какой именно акции кликнул).
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state, force_new_message=True)

        language = await resolve_user_language(telegram_id)
        await callback.message.answer(
            f"🎁 Скидка {discount_percent}% автоматически применена! Действует {discount_label}.",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(f"Error applying broadcast promo discount: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


_GIFT3M_DISCOUNT_PERCENT = 30
_GIFT3M_PERIOD_DAYS = 90


def _gift3m_price_rubles(tariff: str) -> int | None:
    """Discounted 3-month price in rubles for the four eligible tariffs."""
    if tariff in ("basic", "plus"):
        base = config.TARIFFS.get(tariff, {}).get(_GIFT3M_PERIOD_DAYS, {}).get("price")
    elif tariff in ("combo_basic", "combo_plus"):
        base = config.COMBO_TARIFFS.get(tariff, {}).get(_GIFT3M_PERIOD_DAYS, {}).get("price")
    else:
        return None
    if not base:
        return None
    return round(base * (100 - _GIFT3M_DISCOUNT_PERCENT) / 100)


def _gift3m_base_price_rubles(tariff: str) -> int | None:
    if tariff in ("basic", "plus"):
        return config.TARIFFS.get(tariff, {}).get(_GIFT3M_PERIOD_DAYS, {}).get("price")
    if tariff in ("combo_basic", "combo_plus"):
        return config.COMBO_TARIFFS.get(tariff, {}).get(_GIFT3M_PERIOD_DAYS, {}).get("price")
    return None


def _gift3m_menu_text_and_keyboard() -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"🎁 <b>Подарок: −{_GIFT3M_DISCOUNT_PERCENT}% на 3 месяца</b>",
        "",
    ]
    rows = []
    for tariff, label in (
        ("basic", "🌟 Basic"),
        ("plus", "⚡ Plus"),
        ("combo_basic", "🚀 Combo Basic"),
        ("combo_plus", "🚀 Combo Plus"),
    ):
        base = _gift3m_base_price_rubles(tariff)
        disc = _gift3m_price_rubles(tariff)
        if base is None or disc is None:
            continue
        lines.append(f"{label} 3м — было {base} ₽, стало <b>{disc} ₽</b>")
        rows.append([InlineKeyboardButton(
            text=f"🎁 {label} 3м · {disc} ₽",
            callback_data=f"bcg3m:buy:{tariff}",
        )])

    lines.append("")
    lines.append("⏰ Скидка действует здесь и сейчас.")
    rows.append([InlineKeyboardButton(text="ℹ️ О тарифах", callback_data="bcg3m:info")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _gift3m_info_text_and_keyboard() -> tuple[str, InlineKeyboardMarkup]:
    combo_basic_gb = config.COMBO_TARIFFS.get("combo_basic", {}).get(_GIFT3M_PERIOD_DAYS, {}).get("gb", 0)
    combo_plus_gb = config.COMBO_TARIFFS.get("combo_plus", {}).get(_GIFT3M_PERIOD_DAYS, {}).get("gb", 0)

    basic_disc = _gift3m_price_rubles("basic")
    plus_disc = _gift3m_price_rubles("plus")
    cbasic_disc = _gift3m_price_rubles("combo_basic")
    cplus_disc = _gift3m_price_rubles("combo_plus")

    text = (
        "📦 <b>О тарифах · 3 месяца</b>\n\n"

        f"🌟 <b>Basic — {basic_disc} ₽</b>\n"
        "<blockquote>🚀 Канал до 25 Гбит/с — YouTube 4K без тормозов\n"
        "🌐 10 ГБ обхода белых списков в подарок\n"
        "👨‍👩‍👧‍👦 До 10 устройств одновременно\n"
        "➕ Подключение в одно нажатие</blockquote>\n\n"

        f"⚡ <b>Plus — {plus_disc} ₽</b>\n"
        "<blockquote>⚡️ Канал до 75 Гбит/с — стримы и игры без лагов\n"
        "🔄 Резервные каналы — соединение работает всегда\n"
        "🌐 10 ГБ обхода белых списков в подарок\n"
        "👨‍👩‍👧‍👦 До 14 устройств одновременно</blockquote>\n\n"

        f"🚀 <b>Combo Basic — {cbasic_disc} ₽</b>\n"
        "<blockquote>🌐 Безлимит на основных серверах · до 25 Гбит/с\n"
        f"📊 <b>{combo_basic_gb} ГБ</b> обхода белых списков (LTE) в пакете\n"
        "👨‍👩‍👧‍👦 До 10 устройств одновременно\n"
        "<i>Пакет ГБ не сгорает — тратится только на LTE-серверах</i></blockquote>\n\n"

        f"🚀 <b>Combo Plus — {cplus_disc} ₽</b>\n"
        "<blockquote>🌐 Безлимит на приоритетных серверах · до 75 Гбит/с\n"
        "🔄 Резервные каналы — всегда онлайн\n"
        f"📊 <b>{combo_plus_gb} ГБ</b> обхода белых списков (LTE) в пакете\n"
        "👨‍👩‍👧‍👦 До 14 устройств одновременно\n"
        "<i>Пакет ГБ не сгорает — тратится только на LTE-серверах</i></blockquote>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад к скидке", callback_data="bcg3m:menu")],
    ])
    return text, keyboard


@admin_broadcast_router.callback_query(F.data == "broadcast_gift_3m")
async def callback_broadcast_gift_3m(callback: CallbackQuery, state: FSMContext):
    """User clicked the "🎁 Скидка 30% на 3 месяца" CTA in a broadcast.

    Shows a dedicated screen with 4 pre-discounted 3-month buttons
    (Basic, Plus, Combo Basic, Combo Plus). The discount is realised
    purely as a final_price_kopecks override carried in FSM into the
    standard payment-method screen — no personal_discount row is
    created, so the offer cannot leak to other periods or expire as
    stale DB state.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    text, keyboard = _gift3m_menu_text_and_keyboard()

    chat_id = callback.message.chat.id if callback.message and callback.message.chat else callback.from_user.id
    try:
        await callback.message.delete()
    except Exception:
        pass
    try:
        await callback.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.warning("BROADCAST_GIFT3M_RENDER_FAIL user=%s err=%s", callback.from_user.id, e)

    logger.info("BROADCAST_GIFT3M_SHOWN user=%s", callback.from_user.id)


@admin_broadcast_router.callback_query(F.data == "bcg3m:menu")
async def callback_broadcast_gift_3m_menu(callback: CallbackQuery, state: FSMContext):
    """Re-render the gift menu (used as 'back' from the info screen)."""
    try:
        await callback.answer()
    except Exception:
        pass

    text, keyboard = _gift3m_menu_text_and_keyboard()
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    except Exception as e:
        logger.warning("BROADCAST_GIFT3M_MENU_FAIL user=%s err=%s", callback.from_user.id, e)


@admin_broadcast_router.callback_query(F.data == "bcg3m:info")
async def callback_broadcast_gift_3m_info(callback: CallbackQuery, state: FSMContext):
    """Show full descriptions of all four 3-month gift tariffs."""
    try:
        await callback.answer()
    except Exception:
        pass

    text, keyboard = _gift3m_info_text_and_keyboard()
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    except Exception as e:
        logger.warning("BROADCAST_GIFT3M_INFO_FAIL user=%s err=%s", callback.from_user.id, e)


@admin_broadcast_router.callback_query(F.data.startswith("bcg3m:buy:"))
async def callback_broadcast_gift_3m_buy(callback: CallbackQuery, state: FSMContext):
    """User picked one of the four 3-month gift tariffs — jump straight to payment-method selection."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id

    try:
        tariff = callback.data.split(":", 2)[2]
    except IndexError:
        await callback.answer("Ошибка", show_alert=True)
        return

    price_rubles = _gift3m_price_rubles(tariff)
    if price_rubles is None:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    price_kopecks = price_rubles * 100
    if tariff in ("combo_basic", "combo_plus"):
        combo_info = config.COMBO_TARIFFS.get(tariff, {}).get(_GIFT3M_PERIOD_DAYS, {})
        base_tariff = combo_info.get("base_tariff")
        gb = combo_info.get("gb", 0)
    else:
        base_tariff = tariff
        gb = 0

    if base_tariff not in config.TARIFFS:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    from app.handlers.common.states import PurchaseState
    await state.update_data(
        tariff_type=base_tariff,
        period_days=_GIFT3M_PERIOD_DAYS,
        final_price_kopecks=price_kopecks,
        discount_percent=_GIFT3M_DISCOUNT_PERCENT,
        combo_bypass_gb=gb,
    )
    await state.set_state(PurchaseState.choose_payment_method)

    logger.info(
        "BROADCAST_GIFT3M_BUY user=%s tariff=%s base=%s combo_gb=%s price_kopecks=%s",
        telegram_id, tariff, base_tariff, gb, price_kopecks,
    )

    from handlers import show_payment_method_selection
    await show_payment_method_selection(callback, base_tariff, _GIFT3M_PERIOD_DAYS, price_kopecks)


# ──────────────────────────────────────────────────────────────────────
#  Gift 1 год −40% — скидка ТОЛЬКО на 365-дневный план
#
#  UX: рассылка → «🎁 1 год со скидкой 40%» → экран выбора тарифа
#  (Basic / Plus / Combo Basic / Combo Plus) → экран выбора периода
#  (30/90/180/365, где ТОЛЬКО 365 идёт со скидкой) → payment-method.
#
#  Скидка реализована как final_price_kopecks-override в FSM (одноразово,
#  как gift_3m). Никаких записей в user_discounts — если юзер закрыл
#  экран не купив, скидка «сгорает».
# ──────────────────────────────────────────────────────────────────────

_GIFT1Y40_DISCOUNT_PERCENT = 40
_GIFT1Y40_PERIOD_DAYS_DISCOUNTED = 365
_GIFT1Y40_PERIODS = (30, 90, 180, 365)
_GIFT1Y40_PERIOD_LABELS = {
    30: "1 месяц",
    90: "3 месяца",
    180: "6 месяцев",
    365: "1 год",
}
_GIFT1Y40_TARIFFS = (
    ("basic", "🌟 Basic"),
    ("plus", "⚡ Plus"),
    ("combo_basic", "🚀 Combo Basic"),
    ("combo_plus", "🚀 Combo Plus"),
)


def _gift1y40_base_price(tariff: str, period_days: int) -> int | None:
    if tariff in ("basic", "plus"):
        return config.TARIFFS.get(tariff, {}).get(period_days, {}).get("price")
    if tariff in ("combo_basic", "combo_plus"):
        return config.COMBO_TARIFFS.get(tariff, {}).get(period_days, {}).get("price")
    return None


def _gift1y40_final_price(tariff: str, period_days: int) -> int | None:
    """Финальная цена с учётом акции: 40% скидка ТОЛЬКО на 365 дней,
    остальные периоды по обычному прайсу."""
    base = _gift1y40_base_price(tariff, period_days)
    if base is None:
        return None
    if period_days == _GIFT1Y40_PERIOD_DAYS_DISCOUNTED:
        return round(base * (100 - _GIFT1Y40_DISCOUNT_PERCENT) / 100)
    return base


def _gift1y40_tariff_menu() -> tuple[str, InlineKeyboardMarkup]:
    """Первый экран: выбор тарифа."""
    lines = [
        f"🎁 <b>Скидка {_GIFT1Y40_DISCOUNT_PERCENT}% на 1 год</b>",
        "",
        "Годовой план — сразу с учётом скидки.",
        "Другие периоды доступны по обычной цене.",
        "",
        "<b>Выбери тариф ↓</b>",
    ]
    rows = []
    for tariff, label in _GIFT1Y40_TARIFFS:
        # Проверяем что тариф вообще существует в конфиге (защита от
        # рассинхрона config vs UI).
        if _gift1y40_base_price(tariff, _GIFT1Y40_PERIOD_DAYS_DISCOUNTED) is None:
            continue
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"bcg1y40:tariff:{tariff}",
        )])
    rows.append([InlineKeyboardButton(
        text="ℹ️ О тарифах",
        callback_data="bcg1y40:info",
    )])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _gift1y40_period_menu(tariff: str) -> tuple[str, InlineKeyboardMarkup] | None:
    """Второй экран: выбор периода для конкретного тарифа."""
    tariff_label = next(
        (label for t, label in _GIFT1Y40_TARIFFS if t == tariff),
        tariff.capitalize(),
    )
    lines = [
        f"{tariff_label}",
        "",
        "Выбери срок ↓",
        "",
    ]
    rows = []
    have_any = False
    for period_days in _GIFT1Y40_PERIODS:
        base = _gift1y40_base_price(tariff, period_days)
        final = _gift1y40_final_price(tariff, period_days)
        if base is None or final is None:
            continue
        have_any = True
        period_label = _GIFT1Y40_PERIOD_LABELS[period_days]
        if period_days == _GIFT1Y40_PERIOD_DAYS_DISCOUNTED:
            # 365 → с плашкой и зачёркнутой ценой
            lines.append(
                f"🎁 <b>{period_label}</b> — было <s>{base} ₽</s>, "
                f"стало <b>{final} ₽</b>  <i>−{_GIFT1Y40_DISCOUNT_PERCENT}%</i>"
            )
            btn_text = f"🎁 {period_label} · {final} ₽"
        else:
            lines.append(f"• {period_label} — {base} ₽")
            btn_text = f"{period_label} · {base} ₽"
        rows.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"bcg1y40:buy:{tariff}:{period_days}",
        )])
    if not have_any:
        return None
    rows.append([InlineKeyboardButton(
        text="← Назад к тарифам",
        callback_data="bcg1y40:menu",
    )])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _gift1y40_info_text_and_keyboard() -> tuple[str, InlineKeyboardMarkup]:
    combo_basic_gb = config.COMBO_TARIFFS.get("combo_basic", {}).get(
        _GIFT1Y40_PERIOD_DAYS_DISCOUNTED, {}).get("gb", 0)
    combo_plus_gb = config.COMBO_TARIFFS.get("combo_plus", {}).get(
        _GIFT1Y40_PERIOD_DAYS_DISCOUNTED, {}).get("gb", 0)

    basic_final = _gift1y40_final_price("basic", _GIFT1Y40_PERIOD_DAYS_DISCOUNTED)
    plus_final = _gift1y40_final_price("plus", _GIFT1Y40_PERIOD_DAYS_DISCOUNTED)
    cbasic_final = _gift1y40_final_price("combo_basic", _GIFT1Y40_PERIOD_DAYS_DISCOUNTED)
    cplus_final = _gift1y40_final_price("combo_plus", _GIFT1Y40_PERIOD_DAYS_DISCOUNTED)

    text = (
        "📦 <b>О тарифах · 1 год со скидкой 40%</b>\n\n"

        f"🌟 <b>Basic — {basic_final} ₽</b>\n"
        "<blockquote>🚀 Канал до 25 Гбит/с — YouTube 4K без тормозов\n"
        "🌐 10 ГБ обхода белых списков в подарок\n"
        "👨‍👩‍👧‍👦 До 10 устройств одновременно\n"
        "➕ Подключение в одно нажатие</blockquote>\n\n"

        f"⚡ <b>Plus — {plus_final} ₽</b>\n"
        "<blockquote>⚡️ Канал до 75 Гбит/с — стримы и игры без лагов\n"
        "🔄 Резервные каналы — соединение работает всегда\n"
        "🌐 10 ГБ обхода белых списков в подарок\n"
        "👨‍👩‍👧‍👦 До 14 устройств одновременно</blockquote>\n\n"

        f"🚀 <b>Combo Basic — {cbasic_final} ₽</b>\n"
        "<blockquote>🌐 Безлимит на основных серверах · до 25 Гбит/с\n"
        f"📊 <b>{combo_basic_gb} ГБ</b> обхода белых списков (LTE) в пакете\n"
        "👨‍👩‍👧‍👦 До 10 устройств одновременно\n"
        "<i>Пакет ГБ не сгорает — тратится только на LTE-серверах</i></blockquote>\n\n"

        f"🚀 <b>Combo Plus — {cplus_final} ₽</b>\n"
        "<blockquote>🌐 Безлимит на приоритетных серверах · до 75 Гбит/с\n"
        "🔄 Резервные каналы — всегда онлайн\n"
        f"📊 <b>{combo_plus_gb} ГБ</b> обхода белых списков (LTE) в пакете\n"
        "👨‍👩‍👧‍👦 До 14 устройств одновременно\n"
        "<i>Пакет ГБ не сгорает — тратится только на LTE-серверах</i></blockquote>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад к скидке", callback_data="bcg1y40:menu")],
    ])
    return text, keyboard


@admin_broadcast_router.callback_query(F.data == "broadcast_gift_1y_40")
async def callback_broadcast_gift_1y_40(callback: CallbackQuery, state: FSMContext):
    """User clicked «🎁 1 год со скидкой 40%» in a broadcast → tariff menu.

    Скидка одноразовая (FSM-override), не пишется в user_discounts.
    Реализация зеркальная callback_broadcast_gift_3m — тот же
    компактный, безопасный паттерн.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    text, keyboard = _gift1y40_tariff_menu()
    chat_id = callback.message.chat.id if callback.message and callback.message.chat else callback.from_user.id
    try:
        await callback.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.warning("BROADCAST_GIFT1Y40_RENDER_FAIL user=%s err=%s", callback.from_user.id, e)

    logger.info("BROADCAST_GIFT1Y40_SHOWN user=%s", callback.from_user.id)


@admin_broadcast_router.callback_query(F.data == "bcg1y40:menu")
async def callback_broadcast_gift_1y_40_menu(callback: CallbackQuery, state: FSMContext):
    """Re-render меню тарифов (used as «back» from info / period screens)."""
    try:
        await callback.answer()
    except Exception:
        pass
    text, keyboard = _gift1y40_tariff_menu()
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    except Exception as e:
        logger.warning("BROADCAST_GIFT1Y40_MENU_FAIL user=%s err=%s", callback.from_user.id, e)


@admin_broadcast_router.callback_query(F.data == "bcg1y40:info")
async def callback_broadcast_gift_1y_40_info(callback: CallbackQuery, state: FSMContext):
    """Full descriptions всех четырёх годовых тарифов со скидкой."""
    try:
        await callback.answer()
    except Exception:
        pass
    text, keyboard = _gift1y40_info_text_and_keyboard()
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    except Exception as e:
        logger.warning("BROADCAST_GIFT1Y40_INFO_FAIL user=%s err=%s", callback.from_user.id, e)


@admin_broadcast_router.callback_query(F.data.startswith("bcg1y40:tariff:"))
async def callback_broadcast_gift_1y_40_tariff(callback: CallbackQuery, state: FSMContext):
    """Выбран тариф — показываем экран периодов (30/90/180/365)."""
    try:
        await callback.answer()
    except Exception:
        pass
    try:
        tariff = callback.data.split(":", 2)[2]
    except IndexError:
        await callback.answer("Ошибка", show_alert=True)
        return
    menu = _gift1y40_period_menu(tariff)
    if menu is None:
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    text, keyboard = menu
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    except Exception as e:
        logger.warning("BROADCAST_GIFT1Y40_TARIFF_FAIL user=%s err=%s", callback.from_user.id, e)


@admin_broadcast_router.callback_query(F.data.startswith("bcg1y40:buy:"))
async def callback_broadcast_gift_1y_40_buy(callback: CallbackQuery, state: FSMContext):
    """User picked tariff + period — jump to payment-method selection.

    Скидка (40% на 365) закладывается в FSM `final_price_kopecks`.
    Остальные периоды летят по обычной цене. Никаких мутаций
    user_discounts.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    _, _, tariff, period_str = parts
    try:
        period_days = int(period_str)
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    if period_days not in _GIFT1Y40_PERIODS:
        await callback.answer("Неверный период", show_alert=True)
        return

    price_rubles = _gift1y40_final_price(tariff, period_days)
    if price_rubles is None:
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    price_kopecks = price_rubles * 100

    if tariff in ("combo_basic", "combo_plus"):
        combo_info = config.COMBO_TARIFFS.get(tariff, {}).get(period_days, {})
        base_tariff = combo_info.get("base_tariff")
        gb = combo_info.get("gb", 0)
    else:
        base_tariff = tariff
        gb = 0

    if base_tariff not in config.TARIFFS:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    from app.handlers.common.states import PurchaseState
    fsm_update = dict(
        tariff_type=base_tariff,
        period_days=period_days,
        final_price_kopecks=price_kopecks,
        combo_bypass_gb=gb,
    )
    # discount_percent пишем только для 365 — на других периодах цена
    # обычная, discount-показ в чекауте не нужен.
    if period_days == _GIFT1Y40_PERIOD_DAYS_DISCOUNTED:
        fsm_update["discount_percent"] = _GIFT1Y40_DISCOUNT_PERCENT
    await state.update_data(**fsm_update)
    await state.set_state(PurchaseState.choose_payment_method)

    logger.info(
        "BROADCAST_GIFT1Y40_BUY user=%s tariff=%s base=%s period=%s "
        "combo_gb=%s price_kopecks=%s discounted=%s",
        telegram_id, tariff, base_tariff, period_days, gb, price_kopecks,
        period_days == _GIFT1Y40_PERIOD_DAYS_DISCOUNTED,
    )

    from handlers import show_payment_method_selection
    await show_payment_method_selection(callback, base_tariff, period_days, price_kopecks)


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_promo_traffic:"))
async def callback_broadcast_promo_traffic(callback: CallbackQuery):
    """User clicked 'Купить трафик промо' in broadcast — apply 1-day traffic discount."""
    await callback.answer()

    try:
        broadcast_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    telegram_id = callback.from_user.id

    try:
        discount = await database.get_broadcast_discount(broadcast_id)
        discount_percent = discount.get("discount_percent", 0) if discount else 0

        if discount_percent > 0:
            # Apply 1-day traffic discount
            from datetime import timedelta
            expires_at = datetime.now(timezone.utc) + timedelta(days=1)
            await database.create_user_traffic_discount(
                telegram_id=telegram_id,
                discount_percent=discount_percent,
                expires_at=expires_at,
                created_by=config.ADMIN_TELEGRAM_ID,
            )

        # Build traffic packs message with discount applied
        language = await resolve_user_language(telegram_id)

        subscription = await database.get_subscription(telegram_id)
        if not subscription:
            await callback.message.answer(
                i18n_get_text(language, "traffic.no_subscription"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=i18n_get_text(language, "traffic.buy_subscription"),
                        callback_data="menu_buy_vpn",
                    )],
                ]),
                parse_mode="HTML",
            )
            return

        import math

        def _strikethrough(text: str) -> str:
            return "".join(ch + "\u0336" for ch in str(text))

        buttons = []
        for gb, pack in config.TRAFFIC_PACKS.items():
            base_price = pack["price"]
            if discount_percent > 0:
                final_price = math.ceil(base_price * (1 - discount_percent / 100))
                label = f"{gb} ГБ — {final_price} ₽  {_strikethrough(str(base_price))} ₽  (−{discount_percent}%)"
            else:
                label = f"{gb} ГБ — {base_price} ₽"
                if pack.get("discount"):
                    label += f"  {pack['discount']}"
            buttons.append([InlineKeyboardButton(
                text=label,
                callback_data=f"buy_traffic_pack:{gb}",
            )])

        buttons.append([InlineKeyboardButton(
            text="📦 Больше объёма →",
            callback_data=f"broadcast_promo_traffic_ext:{broadcast_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="traffic_info",
        )])

        text = i18n_get_text(language, "traffic.buy_title")
        if discount_percent > 0:
            text = f"🎁 Скидка {discount_percent}% на трафик применена! Действует 24 часа.\n\n" + text

        await callback.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(f"Error applying broadcast traffic promo discount: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


# ──────────────────────────────────────────────────────────────────
# Gift Reveal — кнопка «Посмотреть подарок» в рассылке.
#
# UX flow:
#  1. Юзер кликает красную кнопку «Посмотреть подарок» в сообщении
#     рассылки.
#  2. В чат отправляется premium-эмодзи 👀 (id 5210956306952758910) —
#     один символ, эффект интриги.
#  3. Через 2 секунды — текст «Для тебя подарок 20% скидка на любую
#     подписку!» с premium 🎁 (id 5449800250032143374).
#  4. Через ~30 ms (просто отделить от reveal-сообщения, не моргание) —
#     экран выбора тарифов с уже применённой скидкой.
#
# Скидка: 20%, 48 часов. Параметры зафиксированы (не из dashboard-
# конфига broadcast'а) — это тематический подарок, единый для всех
# таких кнопок.
#
# Скидка применяется через стандартную `create_user_discount` — она
# работает на все основные тарифы (basic / plus / combo_basic /
# combo_plus) автоматически на экране тарифов через `get_user_discount`.

_GIFT_REVEAL_PERCENT_DEFAULT = 20  # fallback для рассылок без gift_reveal_percent в DB
_GIFT_REVEAL_HOURS = 48
_GIFT_REVEAL_PERCENT_CHOICES = (20, 25, 30, 35, 40)
_GIFT_REVEAL_EMOJI = '<tg-emoji emoji-id="5210956306952758910">👀</tg-emoji>'
_GIFT_REVEAL_PRESENT = '<tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji>'


@admin_broadcast_router.callback_query(F.data.startswith("gift_reveal_pct:"))
async def callback_gift_reveal_percent_select(callback: CallbackQuery, state: FSMContext):
    """Admin выбрал процент для «🎁 Посмотреть подарок» в визарде рассылки."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    parts = callback.data.split(":")
    if len(parts) != 2:
        return
    val = parts[1]

    data = await state.get_data()
    buttons = data.get("broadcast_buttons", [])

    if val == "cancel":
        # Возврат к выбору кнопок, gift_reveal НЕ добавляется.
        selected_label = ", ".join(_btn_label(b) for b in buttons) if buttons else "нет"
        await callback.message.edit_text(
            f"Выбранные кнопки: {selected_label}\n\n"
            "Выберите ещё кнопки или нажмите «Готово»:",
            reply_markup=get_broadcast_buttons_keyboard(language, selected=buttons),
            parse_mode="HTML",
        )
        return

    try:
        percent = int(val)
    except ValueError:
        return
    if percent not in _GIFT_REVEAL_PERCENT_CHOICES:
        return

    if "gift_reveal" not in buttons:
        buttons.append("gift_reveal")
    await state.update_data(
        broadcast_buttons=buttons,
        gift_reveal_percent=percent,
    )

    selected_label = ", ".join(_btn_label(b) for b in buttons)
    await callback.message.edit_text(
        f"Выбранные кнопки: {selected_label}\n\n"
        f"🎁 <b>«Посмотреть подарок»</b> → скидка <b>{percent}%</b> на 48 часов после клика.\n\n"
        "Выберите ещё кнопки или нажмите «Готово»:",
        reply_markup=get_broadcast_buttons_keyboard(language, selected=buttons),
        parse_mode="HTML",
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_gift_reveal:"))
async def callback_broadcast_gift_reveal(callback: CallbackQuery, state: FSMContext):
    """Кликнули «Посмотреть подарок» в рассылке — играем reveal-сценку
    и применяем скидку на 48ч, открываем экран тарифов.

    Процент скидки берётся из `broadcast_discounts.gift_reveal_percent`
    (админ выбрал в визарде: 20/25/30/35/40). Если по какой-то причине
    там пусто (старая рассылка до миграции 063, DB-ошибка) — fallback
    на legacy 20%, чтобы не оставлять юзера ни с чем.
    """
    await callback.answer()

    telegram_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else telegram_id

    # Определяем процент из БД. broadcast_id — второй элемент callback_data.
    percent = _GIFT_REVEAL_PERCENT_DEFAULT
    broadcast_id = None
    try:
        broadcast_id = int(callback.data.split(":", 1)[1])
        discount_row = await database.get_broadcast_discount(broadcast_id)
        gr_pct = (discount_row or {}).get("gift_reveal_percent")
        if gr_pct:
            percent = int(gr_pct)
            logger.info(
                "GIFT_REVEAL_CLICK broadcast_id=%s user=%s pct=%s (from DB)",
                broadcast_id, telegram_id, percent,
            )
        else:
            logger.warning(
                "GIFT_REVEAL_CLICK broadcast_id=%s user=%s pct=%s (FALLBACK — "
                "discount_row=%s, gift_reveal_percent=%s). Возможно: миграция 063 "
                "ещё не накатана / save упал при create /рассылка создана до фичи.",
                broadcast_id, telegram_id, percent,
                discount_row is not None, gr_pct,
            )
    except Exception as e:
        logger.warning(
            "GIFT_REVEAL_LOOKUP_FAIL broadcast_id=%s callback=%s err=%s — using default %s%%",
            broadcast_id, callback.data, e, _GIFT_REVEAL_PERCENT_DEFAULT,
        )

    try:
        # 1) эмодзи 👀 — интрига. Сохраняем message_id, чтобы удалить
        # его одновременно с появлением reveal-сообщения через 2 сек.
        eyes_msg = await callback.bot.send_message(
            chat_id,
            _GIFT_REVEAL_EMOJI,
            parse_mode="HTML",
        )

        # 2) держим паузу 2 секунды для эффекта
        await asyncio.sleep(2.0)

        # 3) удаляем «👀» (исчезает) и тут же шлём reveal — визуально
        # одно сменяется другим. Если delete упал (юзер сам удалил
        # сообщение или Telegram отказал) — это не критично, продолжаем.
        try:
            await callback.bot.delete_message(chat_id, eyes_msg.message_id)
        except Exception:
            pass

        # 4) reveal-сообщение с динамическим процентом
        await callback.bot.send_message(
            chat_id,
            f"<b>Для тебя подарок {percent}% скидка на любую подписку!</b> {_GIFT_REVEAL_PRESENT}",
            parse_mode="HTML",
        )

        # 5) применяем скидку %/48ч
        expires_at = datetime.now(timezone.utc) + timedelta(hours=_GIFT_REVEAL_HOURS)
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=percent,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
        )

        # 5) короткая пауза перед экраном тарифов — отделить визуально
        await asyncio.sleep(0.03)

        # 6) показываем экран выбора тарифов — get_user_discount внутри
        # автоматически подставит -20% на basic / plus / combo_basic /
        # combo_plus. Маркер `from_broadcast=True` нужен, чтобы кнопка
        # «Назад» с экрана выбора периода возвращала на этот же экран
        # выбора тарифов (а не на «Управление подпиской», куда
        # `menu_buy_vpn` уводит юзеров с активной подпиской).
        await state.update_data(from_broadcast=True)
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state)

    except Exception as e:
        logger.exception(f"Error in broadcast_gift_reveal: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


@admin_broadcast_router.callback_query(F.data == "broadcast_back_to_tariffs")
async def callback_broadcast_back_to_tariffs(callback: CallbackQuery, state: FSMContext):
    """«Назад» с экрана выбора периода → обратно на экран выбора тарифа.

    Используется только в broadcast-flow (gift_reveal и подобных), где
    юзер ходит между «выбрать тариф → посмотреть период → назад». В
    обычном flow «Назад» по-прежнему ведёт на menu_buy_vpn («Управление
    подпиской»), это поведение не меняется.

    Маркер `from_broadcast=True` НЕ снимаем — юзер ещё внутри flow и
    может зайти в другой тариф. Снимется естественно при выходе из
    state (main menu, cabinet и т.п.).
    """
    try:
        await callback.answer()
    except Exception:
        pass
    from app.handlers.common.screens import show_tariffs_main_screen
    await show_tariffs_main_screen(callback, state)


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_promo_traffic_ext:"))
async def callback_broadcast_promo_traffic_ext(callback: CallbackQuery):
    """Расширенные паки трафика (300+ ГБ) со скидкой из broadcast.

    Юзер нажимает «📦 Больше объёма →» на экране промо-трафика — попадает
    сюда. Скидка уже применена при первом клике (broadcast_promo_traffic),
    здесь только рендерим экран с extended-паками.
    """
    await callback.answer()

    try:
        broadcast_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        discount = await database.get_broadcast_discount(broadcast_id)
        discount_percent = discount.get("discount_percent", 0) if discount else 0

        import math

        def _strikethrough(text: str) -> str:
            return "".join(ch + "̶" for ch in str(text))

        buttons = []
        for gb, pack in config.TRAFFIC_PACKS_EXTENDED.items():
            base_price = pack["price"]
            if discount_percent > 0:
                final_price = math.ceil(base_price * (1 - discount_percent / 100))
                label = f"{gb} ГБ — {final_price} ₽  {_strikethrough(str(base_price))} ₽  (−{discount_percent}%)"
            else:
                label = f"{gb} ГБ — {base_price} ₽"
                if pack.get("discount"):
                    label += f"  {pack['discount']}"
            buttons.append([InlineKeyboardButton(
                text=label,
                callback_data=f"buy_traffic_pack:{gb}",
            )])

        buttons.append([InlineKeyboardButton(
            text="← Основные паки",
            callback_data=f"broadcast_promo_traffic:{broadcast_id}",
        )])

        text = "📦 <b>Большие паки трафика</b>\n\nЧем больше пак — тем дешевле каждый гигабайт."
        if discount_percent > 0:
            text = f"🎁 Скидка {discount_percent}% активна 24 часа.\n\n" + text

        await callback.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(f"Error rendering extended traffic packs in broadcast: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


@admin_broadcast_router.message(Command("notify_no_subscription"))
async def cmd_notify_no_subscription(message: Message, state: FSMContext):
    """Broadcast to users without active subscription or trial (admin only). Silently ignore non-admin."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    await state.set_state(AdminBroadcastNoSubscription.waiting_for_text)
    await message.answer(i18n_get_text(language, "broadcast._no_sub_enter_text"), parse_mode="HTML")


@admin_broadcast_router.message(AdminBroadcastNoSubscription.waiting_for_text)
async def process_no_sub_broadcast_text(message: Message, state: FSMContext):
    """Process broadcast text, show preview, ask confirmation."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    if message.text and message.text.strip().lower() in ("/cancel", "cancel", "отмена"):
        await state.clear()
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.operation_cancelled"), parse_mode="HTML")
        return
    if not message.text or not message.text.strip():
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "broadcast._no_sub_enter_text"), parse_mode="HTML")
        return
    text = message.text.strip()
    try:
        users = await database.get_eligible_no_subscription_broadcast_users()
        total = len(users)
    except Exception as e:
        logger.exception(f"Error fetching no_sub broadcast users: {e}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.check_logs"), parse_mode="HTML")
        return
    if total == 0:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "broadcast._no_sub_zero_recipients"), parse_mode="HTML")
        await state.clear()
        return
    await state.update_data(broadcast_text=text)
    await state.set_state(AdminBroadcastNoSubscription.waiting_for_confirmation)
    language = await resolve_user_language(message.from_user.id)
    preview = text[:500] + ("..." if len(text) > 500 else "")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.confirm"), callback_data="no_sub_broadcast:confirm")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="no_sub_broadcast:cancel")],
    ])
    await message.answer(
        i18n_get_text(language, "broadcast._no_sub_preview", preview=preview, total=total),
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@admin_broadcast_router.callback_query(F.data.startswith("no_sub_broadcast:"))
async def callback_no_sub_broadcast_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle confirm/cancel for no-subscription broadcast."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer()
        return
    action = callback.data.split(":")[1]
    await callback.answer()
    if action == "cancel":
        await state.clear()
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(i18n_get_text(language, "admin.operation_cancelled"), parse_mode="HTML")
        return
    if action != "confirm":
        return
    data = await state.get_data()
    text = data.get("broadcast_text")
    if not text:
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(i18n_get_text(language, "broadcast._validation_message_empty"), parse_mode="HTML")
        await state.clear()
        return
    try:
        users = await database.get_eligible_no_subscription_broadcast_users()
        total = len(users)
    except Exception:
        total = 0
    language = await resolve_user_language(callback.from_user.id)
    await callback.message.edit_text(
        i18n_get_text(language, "broadcast._no_sub_sending", total=total),
        parse_mode="HTML",
    )
    await state.clear()

    async def _run_broadcast():
        try:
            from broadcast_service import run_no_subscription_broadcast
            await run_no_subscription_broadcast(
                bot, text, callback.from_user.id, notify_admin_on_complete=True
            )
        except asyncio.CancelledError:
            logger.info("no_sub_broadcast task cancelled")
        except Exception as e:
            logger.exception(f"no_sub_broadcast failed: {e}")
            try:
                await bot.send_message(
                    callback.from_user.id,
                    i18n_get_text(
                        await resolve_user_language(callback.from_user.id),
                        "admin.check_logs"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass

    asyncio.create_task(_run_broadcast())


@admin_broadcast_router.callback_query(F.data == "admin:broadcast")
async def callback_admin_broadcast(callback: CallbackQuery):
    """Раздел уведомлений"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    text = i18n_get_text(language, "broadcast._section_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._create"), callback_data="broadcast:create")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._ab_stats"), callback_data="broadcast:ab_stats")],
        [InlineKeyboardButton(text="🗑 Удалить уведомление", callback_data="broadcast:delete_list")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:notifications")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    await callback.answer()

    # Логируем действие
    await database._log_audit_event_atomic_standalone("admin_broadcast_view", callback.from_user.id, None, "Admin viewed broadcast section")


@admin_broadcast_router.callback_query(F.data == "broadcast:create")
async def callback_broadcast_create(callback: CallbackQuery, state: FSMContext):
    """Начать создание уведомления"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    await state.set_state(BroadcastCreate.waiting_for_title)
    await callback.message.answer(
        i18n_get_text(language, "broadcast._enter_title"),
        parse_mode="HTML",
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_title)
async def process_broadcast_title(message: Message, state: FSMContext):
    """Обработка заголовка уведомления"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(title=message.text)
    await state.set_state(BroadcastCreate.waiting_for_test_type)
    await message.answer(
        i18n_get_text(language, "broadcast._select_type"),
        reply_markup=get_broadcast_test_type_keyboard(language),
        parse_mode="HTML",
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_test_type:"))
async def callback_broadcast_test_type(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа тестирования"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)
    test_type = callback.data.split(":")[1]
    
    await state.update_data(is_ab_test=(test_type == "ab"))
    
    if test_type == "ab":
        await state.set_state(BroadcastCreate.waiting_for_message_a)
        await callback.message.edit_text(
            i18n_get_text(language, "broadcast._enter_variant_a"),
            parse_mode="HTML",
        )
    else:
        await state.set_state(BroadcastCreate.waiting_for_message)
        await callback.message.edit_text(
            i18n_get_text(language, "broadcast._enter_message"),
            parse_mode="HTML",
        )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_message_a)
async def process_broadcast_message_a(message: Message, state: FSMContext):
    """Обработка текста варианта A"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message_a=message.text)
    await state.set_state(BroadcastCreate.waiting_for_message_b)
    await message.answer(
        i18n_get_text(language, "broadcast._enter_variant_b"),
        parse_mode="HTML",
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_message_b)
async def process_broadcast_message_b(message: Message, state: FSMContext):
    """Обработка текста варианта B"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message_b=message.text)
    await state.set_state(BroadcastCreate.waiting_for_emoji)
    await message.answer(
        "Отправьте эмодзи для уведомления (любой смайлик):\n\n"
        "Популярные: 📢 🔥 🎉 💰 ⚡ 🎁 🚀 ❗ 💎 🏆\n\n"
        "Или нажмите /skip чтобы отправить без эмодзи.",
        parse_mode="HTML",
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_message, F.text | F.photo)
async def process_broadcast_message(message: Message, state: FSMContext):
    """Обработка текста/фото уведомления"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)

    # Поддержка отмены только для текстовых сообщений
    if message.text and message.text.strip().lower() in ("/cancel", "cancel", "отмена"):
        await state.clear()
        await message.answer(i18n_get_text(language, "admin.operation_cancelled"), parse_mode="HTML")
        return

    # Принимаем либо фото (с подписью), либо текст
    if message.photo:
        photo_file_id = message.photo[-1].file_id
        caption = message.caption or ""
        await state.update_data(
            message=None,
            has_photo=True,
            photo_file_id=photo_file_id,
            caption=caption,
        )
    elif message.text and message.text.strip():
        await state.update_data(
            message=message.text,
            has_photo=False,
            photo_file_id=None,
            caption=None,
        )
    else:
        await message.answer(i18n_get_text(language, "broadcast._enter_message"), parse_mode="HTML")
        return

    await state.set_state(BroadcastCreate.waiting_for_emoji)
    await message.answer(
        "Отправьте эмодзи для уведомления (любой смайлик):\n\n"
        "Популярные: 📢 🔥 🎉 💰 ⚡ 🎁 🚀 ❗ 💎 🏆\n\n"
        "Или нажмите /skip чтобы отправить без эмодзи.",
        parse_mode="HTML",
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_emoji)
async def process_broadcast_emoji(message: Message, state: FSMContext):
    """Обработка выбора эмодзи"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)

    if not message.text or not message.text.strip():
        await message.answer("Отправьте эмодзи или /skip:", parse_mode="HTML")
        return

    text = message.text.strip()

    if text.lower() in ("/skip", "skip"):
        await state.update_data(emoji="", type="custom")
    else:
        if len(text) > 10:
            await message.answer("Слишком длинный текст. Отправьте эмодзи (1-2 символа) или /skip:", parse_mode="HTML")
            return
        await state.update_data(emoji=text, type="custom")
    await state.set_state(BroadcastCreate.waiting_for_buttons)
    await message.answer(
        "Выберите кнопки для уведомления:",
        reply_markup=get_broadcast_buttons_keyboard(language),
        parse_mode="HTML",
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_btn:"))
async def callback_broadcast_buttons(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора кнопок для уведомления"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)
    btn_type = callback.data.split(":")[1]

    if btn_type == "none":
        await state.update_data(broadcast_buttons=[])
        await state.set_state(BroadcastCreate.waiting_for_segment)
        await callback.message.edit_text(
            "Выберите сегмент получателей:",
            reply_markup=get_broadcast_segment_keyboard(language),
            parse_mode="HTML",
        )
    elif btn_type in ("promo_buy", "promo_traffic"):
        # Need to ask for discount percentage
        data = await state.get_data()
        buttons = data.get("broadcast_buttons", [])
        if btn_type not in buttons:
            buttons.append(btn_type)
        await state.update_data(broadcast_buttons=buttons, _pending_promo_type=btn_type)
        await state.set_state(BroadcastCreate.waiting_for_discount)
        if btn_type == "promo_traffic":
            await callback.message.edit_text(
                "Введите процент скидки на трафик для акции (число от 1 до 99):",
                parse_mode="HTML",
            )
        else:
            await callback.message.edit_text(
                "Введите процент скидки для акции (число от 1 до 99):",
                parse_mode="HTML",
            )
    elif btn_type in ("gift_3m", "gift_1y_40"):
        # Preset: скидка зашита в коде (30%/3мес или 40%/1год) — extra
        # ввод не нужен, просто toggle в списке.
        data = await state.get_data()
        buttons = data.get("broadcast_buttons", [])
        if btn_type in buttons:
            buttons.remove(btn_type)
        else:
            buttons.append(btn_type)
        await state.update_data(broadcast_buttons=buttons)
        await callback.message.edit_text(
            f"Выбранные кнопки: {', '.join(_btn_label(b) for b in buttons)}\n\n"
            "Выберите ещё кнопки или нажмите «Готово»:",
            reply_markup=get_broadcast_buttons_keyboard(language, selected=buttons),
            parse_mode="HTML",
        )
    elif btn_type == "gift_reveal":
        # «🎁 Посмотреть подарок» — админ выбирает процент 20/25/30/35/40
        # (48ч фиксировано в коде callback'а).
        data = await state.get_data()
        buttons = data.get("broadcast_buttons", [])
        if btn_type in buttons:
            # Убираем — сбрасываем и процент
            buttons.remove(btn_type)
            await state.update_data(
                broadcast_buttons=buttons, gift_reveal_percent=None,
            )
            selected_label = ", ".join(_btn_label(b) for b in buttons) if buttons else "нет"
            await callback.message.edit_text(
                f"Выбранные кнопки: {selected_label}\n\n"
                "Выберите ещё кнопки или нажмите «Готово»:",
                reply_markup=get_broadcast_buttons_keyboard(language, selected=buttons),
                parse_mode="HTML",
            )
        else:
            # Показать пикер процентов
            percent_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="20 %", callback_data="gift_reveal_pct:20"),
                    InlineKeyboardButton(text="25 %", callback_data="gift_reveal_pct:25"),
                    InlineKeyboardButton(text="30 %", callback_data="gift_reveal_pct:30"),
                ],
                [
                    InlineKeyboardButton(text="35 %", callback_data="gift_reveal_pct:35"),
                    InlineKeyboardButton(text="40 %", callback_data="gift_reveal_pct:40"),
                ],
                [
                    InlineKeyboardButton(text="↩️ Отмена", callback_data="gift_reveal_pct:cancel"),
                ],
            ])
            await callback.message.edit_text(
                "🎁 <b>«Посмотреть подарок»</b>\n\n"
                "Какую скидку показывать пользователю после reveal-анимации?\n"
                "<i>Действует 48 часов после клика.</i>",
                reply_markup=percent_kb,
                parse_mode="HTML",
            )
    elif btn_type == "done":
        # Finished selecting buttons, move to segment
        await state.set_state(BroadcastCreate.waiting_for_segment)
        await callback.message.edit_text(
            "Выберите сегмент получателей:",
            reply_markup=get_broadcast_segment_keyboard(language),
            parse_mode="HTML",
        )
    else:
        # Toggle button in list (add or remove)
        data = await state.get_data()
        buttons = data.get("broadcast_buttons", [])
        if btn_type in buttons:
            buttons.remove(btn_type)
        else:
            buttons.append(btn_type)
        await state.update_data(broadcast_buttons=buttons)
        # Show updated keyboard with selected buttons
        await callback.message.edit_text(
            f"Выбранные кнопки: {', '.join(_btn_label(b) for b in buttons)}\n\n"
            "Выберите ещё кнопки или нажмите «Готово»:",
            reply_markup=get_broadcast_buttons_keyboard(language, selected=buttons),
            parse_mode="HTML",
        )


def _btn_label(btn_type: str) -> str:
    """Human-readable label for button type"""
    labels = {
        "buy": "🛒 Купить",
        "promo_buy": "🎁 Купить со скидкой",
        "promo_traffic": "📊 Купить трафик промо",
        "gift_3m": "🎁 Скидка 30% на 3 месяца",
        "gift_1y_40": "🎁 1 год со скидкой 40%",
        "bypass": "🌐 Включить обход",
        "channel": "📢 Наш канал",
        "support": "💬 Поддержка",
        "referral": "👥 Реферальная программа",
        "happ_ios": "📲 Скачать Happ iOS",
        "happ_android": "📲 Скачать Happ Android",
        "web_client": "🌐 Веб-клиент QoDev",
        "buy_combo": "🏆 Купить Комбо",
        "proxy": "🌐 MT Прокси",
        "share_discount": "🎁 Поделиться скидкой",
    }
    return labels.get(btn_type, btn_type)


@admin_broadcast_router.message(BroadcastCreate.waiting_for_discount)
async def process_broadcast_discount(message: Message, state: FSMContext):
    """Обработка ввода скидки для кнопки 'Купить со скидкой'"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)

    try:
        discount = int(message.text.strip())
        if not 1 <= discount <= 99:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введите число от 1 до 99:", parse_mode="HTML")
        return

    data = await state.get_data()
    buttons = data.get("broadcast_buttons", [])
    pending_type = data.get("_pending_promo_type", "promo_buy")
    if pending_type not in buttons:
        buttons.append(pending_type)
    await state.update_data(broadcast_buttons=buttons, broadcast_discount=discount)
    await state.set_state(BroadcastCreate.waiting_for_discount_duration)

    duration_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="6 часов", callback_data="promo_duration:6h"),
            InlineKeyboardButton(text="12 часов", callback_data="promo_duration:12h"),
        ],
        [
            InlineKeyboardButton(text="1 день", callback_data="promo_duration:1d"),
            InlineKeyboardButton(text="3 дня", callback_data="promo_duration:3d"),
        ],
        [
            InlineKeyboardButton(text="7 дней", callback_data="promo_duration:7d"),
            InlineKeyboardButton(text="14 дней", callback_data="promo_duration:14d"),
        ],
        [
            InlineKeyboardButton(text="30 дней", callback_data="promo_duration:30d"),
        ],
    ])
    await message.answer(
        f"Скидка {discount}% установлена.\n\n⏱ Выберите время действия скидки:",
        reply_markup=duration_keyboard,
        parse_mode="HTML",
    )


_DURATION_MAP = {
    "6h": (6, "часов", "6 часов"),
    "12h": (12, "часов", "12 часов"),
    "1d": (24, "часов", "1 день"),
    "3d": (72, "часов", "3 дня"),
    "7d": (168, "часов", "7 дней"),
    "14d": (336, "часов", "14 дней"),
    "30d": (720, "часов", "30 дней"),
}


@admin_broadcast_router.callback_query(F.data.startswith("promo_duration:"))
async def callback_promo_duration(callback: CallbackQuery, state: FSMContext):
    """Выбор времени действия скидки"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)

    duration_key = callback.data.split(":")[1]
    duration_hours, _, duration_label = _DURATION_MAP.get(duration_key, (168, "часов", "7 дней"))

    data = await state.get_data()
    discount = data.get("broadcast_discount", 0)

    await state.update_data(broadcast_discount_hours=duration_hours, broadcast_discount_label=duration_label)
    await state.set_state(BroadcastCreate.waiting_for_segment)

    await callback.message.edit_text(
        f"Скидка {discount}% на {duration_label}.\n\nВыберите сегмент получателей:",
        reply_markup=get_broadcast_segment_keyboard(language),
        parse_mode="HTML",
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_segment:"))
async def callback_broadcast_segment(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора сегмента получателей"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    await callback.answer()
    segment = callback.data.split(":")[1]

    data_for_preview = await state.get_data()
    title = data_for_preview.get("title")
    emoji = data_for_preview.get("emoji", "📢")
    is_ab_test = data_for_preview.get("is_ab_test", False)
    has_photo = data_for_preview.get("has_photo", False)
    caption = data_for_preview.get("caption", "") if has_photo else ""
    buttons = data_for_preview.get("broadcast_buttons", [])
    discount = data_for_preview.get("broadcast_discount")

    segment_name = {
        "all_users": "Все пользователи",
        "active_subscriptions": "Только активные подписки",
        "no_subscription": "Без подписки",
        "no_remnawave": "Никогда не подключались",
        "started_7d_cold": "Холодные за 7 дней (нажали /start, без ключей)",
        "expired_1d": "Истёк 1 день назад",
        "expired_2d": "Истёк 2 дня назад",
        "expired_3d": "Истёк 3 дня назад",
    }

    prefix = f"{emoji} " if emoji else ""
    if is_ab_test:
        message_a = data_for_preview.get("message_a", "")
        message_b = data_for_preview.get("message_b", "")
        preview_text = (
            f"{prefix}{title}\n\n"
            f"🔬 A/B ТЕСТ\n\n"
            f"Вариант A:\n{message_a}\n\n"
            f"Вариант B:\n{message_b}\n\n"
            f"Сегмент: {segment_name.get(segment, segment)}"
        )
    else:
        message_text = data_for_preview.get("message", "")
        if has_photo:
            body = f"[📷 Фото]\n{caption}".strip()
        else:
            body = message_text
        preview_text = (
            f"{prefix}{title}\n\n"
            f"{body}\n\n"
            f"Сегмент: {segment_name.get(segment, segment)}"
        )

    if buttons:
        preview_text += f"\nКнопки: {', '.join(_btn_label(b) for b in buttons)}"
    if discount:
        preview_text += f"\nСкидка: {discount}%"

    await state.update_data(segment=segment)
    await state.set_state(BroadcastCreate.waiting_for_confirm)

    language = await resolve_user_language(callback.from_user.id)

    preview_confirm_text = i18n_get_text(language, "broadcast._preview_confirm", preview=preview_text)
    await callback.message.edit_text(
        preview_confirm_text,
        reply_markup=get_broadcast_confirm_keyboard(language),
        parse_mode="HTML",
    )


@admin_broadcast_router.callback_query(F.data == "broadcast:confirm_send")
async def callback_broadcast_confirm_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение и отправка уведомления"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    language = await resolve_user_language(callback.from_user.id)
    
    data = await state.get_data()
    title = data.get("title")
    message_text = data.get("message")
    message_a = data.get("message_a")
    message_b = data.get("message_b")
    is_ab_test = data.get("is_ab_test", False)
    has_photo = data.get("has_photo", False)
    photo_file_id = data.get("photo_file_id")
    caption = data.get("caption") or ""
    broadcast_type = data.get("type", "custom")
    segment = data.get("segment")
    emoji = data.get("emoji", "📢")
    broadcast_buttons = data.get("broadcast_buttons", [])
    broadcast_discount = data.get("broadcast_discount")

    # Проверка данных
    if not all([title, segment]):
        await callback.message.answer("Ошибка: не все данные заполнены. Начните заново.", parse_mode="HTML")
        await state.clear()
        return

    if is_ab_test:
        if not all([message_a, message_b]):
            await callback.message.answer("Ошибка: не заполнены тексты вариантов A и B. Начните заново.", parse_mode="HTML")
            await state.clear()
            return
    else:
        if not (message_text or has_photo):
            await callback.message.answer("Ошибка: не заполнен текст уведомления. Начните заново.", parse_mode="HTML")
            await state.clear()
            return

    try:
        # Создаем уведомление в БД
        broadcast_id = await database.create_broadcast(
            title, caption if has_photo else message_text, broadcast_type, segment, callback.from_user.id,
            is_ab_test=is_ab_test, message_a=message_a, message_b=message_b
        )

        # Save broadcast discount if set (for promo_buy or promo_traffic)
        if broadcast_discount and ("promo_buy" in broadcast_buttons or "promo_traffic" in broadcast_buttons):
            data_for_save = await state.get_data()
            _disc_hours = data_for_save.get("broadcast_discount_hours", 168)
            _disc_label = data_for_save.get("broadcast_discount_label", "7 дней")
            await database.save_broadcast_discount(broadcast_id, broadcast_discount, _disc_hours, _disc_label)

        # Save gift_reveal-скидка (админ выбрал 20/25/30/35/40 в визарде).
        # Отдельная колонка → не конфликтует с promo_buy-скидкой выше.
        if "gift_reveal" in broadcast_buttons:
            data_for_save = await state.get_data()
            _gr_percent = data_for_save.get("gift_reveal_percent") or _GIFT_REVEAL_PERCENT_DEFAULT
            try:
                await database.save_broadcast_gift_reveal_percent(broadcast_id, int(_gr_percent))
            except Exception as e:
                logger.warning(
                    "GIFT_REVEAL_PERSIST_FAIL broadcast_id=%s err=%s "
                    "(fallback to default %s%% at click-time)",
                    broadcast_id, e, _GIFT_REVEAL_PERCENT_DEFAULT,
                )

        prefix = f"{emoji} " if emoji else ""
        if is_ab_test:
            final_message_a = f"{prefix}{title}\n\n{message_a}"
            final_message_b = f"{prefix}{title}\n\n{message_b}"
        else:
            if has_photo:
                final_message = f"{prefix}{title}\n\n{caption}".strip()
            else:
                final_message = f"{prefix}{title}\n\n{message_text}"

        # Build inline keyboard for broadcast message
        reply_markup = _build_broadcast_reply_markup(broadcast_buttons, broadcast_id, broadcast_discount)

        # Получаем список пользователей по сегменту
        user_ids = await database.get_users_by_segment(segment)
        total = len(user_ids)

        logger.info(
            f"BROADCAST_START broadcast_id={broadcast_id} segment={segment} total_users={total}"
        )

        await callback.message.edit_text(
            i18n_get_text(language, "broadcast._sending", total=total),
            reply_markup=None,
            parse_mode="HTML",
        )

        # Prepare message variants before launching background task
        if is_ab_test:
            msg_variants = {"a": final_message_a, "b": final_message_b}
        elif has_photo:
            msg_variants = {"text": final_message, "photo_file_id": photo_file_id}
        else:
            msg_variants = {"text": final_message}

        admin_id = callback.from_user.id
        chat_id = callback.message.chat.id

        async def _run_broadcast_send():
            try:
                semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)
                sent_count = 0
                failed_list = []
                processed = 0

                async def _send_one(
                    user_id: int,
                    msg: str,
                    variant,
                    p_file_id: str | None = None,
                    cap: str | None = None,
                ):
                    # Per-user variable substitution (currently: {bypass_key}).
                    # Users without a bypass URL are skipped — sending them a
                    # message with a literal placeholder would be broken UX.
                    needs_key = "{bypass_key}" in (msg or "") or "{bypass_key}" in (cap or "")
                    if needs_key:
                        bypass_url = await get_user_bypass_url(user_id)
                        if not bypass_url:
                            logger.info(
                                f"BROADCAST_SKIP_NO_BYPASS_KEY user={user_id} broadcast_id={broadcast_id}"
                            )
                            return (user_id, variant, None)
                        # Escape so a URL containing &/</> can't break HTML parse_mode.
                        import html as _html
                        safe_url = _html.escape(bypass_url, quote=False)
                        if msg:
                            msg = msg.replace("{bypass_key}", safe_url)
                        if cap:
                            cap = cap.replace("{bypass_key}", safe_url)

                    msg_id = await _safe_send_with_buttons(
                        bot, user_id, msg, semaphore,
                        reply_markup=reply_markup,
                        photo_file_id=p_file_id, caption=cap,
                    )
                    return (user_id, variant, msg_id)

                for i in range(0, total, BROADCAST_BATCH_SIZE):
                    batch = user_ids[i:i + BROADCAST_BATCH_SIZE]
                    batch_items = []
                    for user_id in batch:
                        if is_ab_test:
                            variant = "A" if random.random() < 0.5 else "B"
                            msg = msg_variants["a"] if variant == "A" else msg_variants["b"]
                            batch_items.append((user_id, msg, variant, None, None))
                        else:
                            variant = None
                            if has_photo:
                                batch_items.append((user_id, msg_variants["text"], variant, msg_variants["photo_file_id"], msg_variants["text"]))
                            else:
                                batch_items.append((user_id, msg_variants["text"], variant, None, None))

                    tasks = [
                        _send_one(uid, msg, v, p_fid, cap)
                        for uid, msg, v, p_fid, cap in batch_items
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for r in results:
                        if isinstance(r, Exception):
                            logger.warning(f"BROADCAST_TASK_ERROR broadcast_id={broadcast_id} error={r}")
                            continue
                        uid, v, msg_id = r
                        if msg_id:
                            await database.log_broadcast_send(broadcast_id, uid, "sent", v, message_id=msg_id)
                            sent_count += 1
                        else:
                            failed_list.append({"telegram_id": uid, "error": "Send failed"})
                            await database.log_broadcast_send(broadcast_id, uid, "failed", v)

                    processed += len(batch)
                    logger.info(f"BROADCAST_PROGRESS processed={processed}/{total}")
                    await asyncio.sleep(BROADCAST_BATCH_PAUSE)

                failed_count = len(failed_list)
                total_users = total
                logger.info(f"BROADCAST_COMPLETED total={total}")

                await database._log_audit_event_atomic_standalone(
                    "broadcast_sent",
                    admin_id,
                    None,
                    f"Broadcast ID: {broadcast_id}, Segment: {segment}, Sent: {sent_count}, Failed: {failed_count}"
                )

                # Admin report (localized)
                if failed_count == 0:
                    result_text = i18n_get_text(language, "broadcast._report_success", total=total_users, sent=sent_count, broadcast_id=broadcast_id)
                else:
                    failed_lines = "\n".join(
                        f"{f['telegram_id']} — {f['error']}" for f in failed_list[:20]
                    )
                    if len(failed_list) > 20:
                        failed_lines += f"\n... and {len(failed_list) - 20} more"
                    result_text = i18n_get_text(language, "broadcast._report_partial", total=total_users, sent=sent_count, failed=failed_count, broadcast_id=broadcast_id, failed_list=failed_lines)

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=i18n_get_text(language, "admin.back_to_broadcast"), callback_data="admin:broadcast")],
                ])

                await bot.edit_message_text(result_text, chat_id=chat_id, message_id=callback.message.message_id, reply_markup=keyboard)

            except asyncio.CancelledError:
                logger.info(f"BROADCAST_CANCELLED broadcast_id={broadcast_id}")
                raise
            except Exception as e:
                logger.exception(f"Error in broadcast send: {e}")
                try:
                    await bot.send_message(chat_id, f"Ошибка при отправке уведомления: {e}", parse_mode="HTML")
                except Exception:
                    pass
                try:
                    from app.services.admin_alerts import send_alert
                    await send_alert(bot, "worker", f"Broadcast send error: {type(e).__name__}: {str(e)[:200]}")
                except Exception:
                    pass

        asyncio.create_task(_run_broadcast_send())

    except Exception as e:
        logger.exception(f"Error in broadcast send: {e}")
        await callback.message.answer(f"Ошибка при отправке уведомления: {e}", parse_mode="HTML")
        try:
            from app.services.admin_alerts import send_alert
            await send_alert(callback.bot, "worker", f"Broadcast send error: {type(e).__name__}: {str(e)[:200]}")
        except Exception:
            pass

    finally:
        await state.clear()


@admin_broadcast_router.callback_query(F.data == "broadcast:delete_list")
async def callback_broadcast_delete_list(callback: CallbackQuery):
    """Список броадкастов для удаления у пользователей."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.answer()

    broadcasts = await database.get_recent_broadcasts(limit=10)
    if not broadcasts:
        await safe_edit_text(
            callback.message,
            "📭 Нет броадкастов для удаления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:broadcast")],
            ]),
            bot=callback.bot,
        )
        return

    lines = ["🗑 <b>Удалить уведомление у пользователей</b>\n"]
    buttons = []
    for b in broadcasts:
        bid = b["id"]
        title = (b["title"] or "—")[:30]
        sent = b["sent_count"] or 0
        has_ids = b["has_msg_ids"] or 0
        date_str = b["created_at"].strftime("%d.%m %H:%M") if b["created_at"] else "—"
        label = f"#{bid} {title} ({sent} отпр.)"
        if has_ids == 0:
            label += " ❌ нет ID"
        lines.append(f"• <b>#{bid}</b> {title} — {sent} отпр., {has_ids} с ID — {date_str}")
        if has_ids > 0:
            buttons.append([InlineKeyboardButton(
                text=f"🗑 #{bid} {title}",
                callback_data=f"broadcast:delete_confirm:{bid}",
            )])

    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin:broadcast")])
    text = "\n".join(lines)
    if not any("delete_confirm" in str(b) for row in buttons for b in row):
        text += "\n\n⚠️ Ни один броадкаст не имеет сохранённых message_id. Удаление доступно только для новых уведомлений."
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot)


@admin_broadcast_router.callback_query(F.data.startswith("broadcast:delete_confirm:"))
async def callback_broadcast_delete_confirm(callback: CallbackQuery):
    """Подтверждение удаления броадкаста."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.answer()

    broadcast_id = int(callback.data.split(":")[-1])
    pairs = await database.get_broadcast_message_ids(broadcast_id)

    if not pairs:
        await safe_edit_text(
            callback.message,
            f"❌ Броадкаст #{broadcast_id} — нет сообщений с сохранёнными ID для удаления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="broadcast:delete_list")],
            ]),
            bot=callback.bot,
        )
        return

    text = (
        f"🗑 <b>Удалить броадкаст #{broadcast_id}?</b>\n\n"
        f"Будет удалено <b>{len(pairs)}</b> сообщений из чатов пользователей.\n\n"
        f"⚠️ Это действие необратимо."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Удалить {len(pairs)} сообщений", callback_data=f"broadcast:delete_exec:{broadcast_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:delete_list")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)


@admin_broadcast_router.callback_query(F.data.startswith("broadcast:delete_exec:"))
async def callback_broadcast_delete_exec(callback: CallbackQuery):
    """Выполнение удаления броадкаста у пользователей."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.answer()

    broadcast_id = int(callback.data.split(":")[-1])
    pairs = await database.get_broadcast_message_ids(broadcast_id)

    if not pairs:
        await safe_edit_text(
            callback.message,
            f"❌ Нет сообщений для удаления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="broadcast:delete_list")],
            ]),
            bot=callback.bot,
        )
        return

    await safe_edit_text(
        callback.message,
        f"🗑 Удаляю {len(pairs)} сообщений броадкаста #{broadcast_id}...\n\n⏳ Это может занять несколько минут. Результат будет отправлен в чат.",
        bot=callback.bot,
    )

    # Run deletion in background to avoid webhook timeout
    async def _delete_in_background():
        bot = callback.bot
        deleted = 0
        failed = 0
        for telegram_id, message_id in pairs:
            try:
                await bot.delete_message(chat_id=telegram_id, message_id=message_id)
                deleted += 1
            except Exception:
                failed += 1
            if deleted % 30 == 0:
                await asyncio.sleep(1)  # Rate limit

        await database.mark_broadcast_messages_deleted(broadcast_id)

        text = (
            f"✅ <b>Броадкаст #{broadcast_id} удалён</b>\n\n"
            f"🗑 Удалено: {deleted}\n"
            f"❌ Не удалось: {failed}\n"
            f"📊 Всего: {len(pairs)}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К списку", callback_data="broadcast:delete_list")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:broadcast")],
        ])
        await bot.send_message(
            chat_id=config.ADMIN_TELEGRAM_ID, text=text,
            reply_markup=keyboard, parse_mode="HTML",
        )
        logger.info(f"BROADCAST_BULK_DELETE broadcast_id={broadcast_id} deleted={deleted} failed={failed} total={len(pairs)}")

    asyncio.create_task(_delete_in_background())


@admin_broadcast_router.callback_query(F.data == "broadcast:ab_stats")
async def callback_broadcast_ab_stats(callback: CallbackQuery):
    """Список A/B тестов"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        ab_tests = await database.get_ab_test_broadcasts()
        
        if not ab_tests:
            text = i18n_get_text(language, "broadcast._ab_stats_empty")
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language), bot=callback.bot)
            return
        
        text = i18n_get_text(language, "broadcast._ab_stats_select")
        keyboard = get_ab_test_list_keyboard(ab_tests, language)
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_ab_stats_list", callback.from_user.id, None, f"Viewed {len(ab_tests)} A/B tests")
    
    except Exception as e:
        logger.exception(f"Error in callback_broadcast_ab_stats: {e}")
        await callback.message.answer(
            i18n_get_text(language, "broadcast._ab_stats_error"),
            parse_mode="HTML",
        )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast:ab_stat:"))
async def callback_broadcast_ab_stat_detail(callback: CallbackQuery):
    """Статистика конкретного A/B теста"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)

    try:
        broadcast_id = int(callback.data.split(":")[2])

        # Получаем информацию об уведомлении
        broadcast = await database.get_broadcast(broadcast_id)
        if not broadcast:
            await callback.message.answer("Уведомление не найдено.", parse_mode="HTML")
            return
        
        # Получаем статистику
        stats = await database.get_ab_test_stats(broadcast_id)
        
        if not stats:
            text = f"📊 A/B статистика\n\nУведомление: #{broadcast_id}\n\nНедостаточно данных для анализа."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="broadcast:ab_stats")],
            ])
            await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
            return
        
        # Формируем текст статистики
        total_sent = stats["total_sent"]
        variant_a_sent = stats["variant_a_sent"]
        variant_b_sent = stats["variant_b_sent"]
        
        # Проценты
        if total_sent > 0:
            percent_a = round((variant_a_sent / total_sent) * 100)
            percent_b = round((variant_b_sent / total_sent) * 100)
        else:
            percent_a = 0
            percent_b = 0
        
        text = (
            f"📊 A/B статистика\n\n"
            f"Уведомление: #{broadcast_id}\n"
            f"Заголовок: {broadcast.get('title', '—')}\n\n"
            f"Вариант A:\n"
            f"— Отправлено: {variant_a_sent} ({percent_a}%)\n\n"
            f"Вариант B:\n"
            f"— Отправлено: {variant_b_sent} ({percent_b}%)\n\n"
            f"Всего отправлено: {total_sent}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="broadcast:ab_stats")],
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_ab_stat_detail", callback.from_user.id, None, f"Viewed A/B stats for broadcast {broadcast_id}")
    
    except (ValueError, IndexError) as e:
        logging.error(f"Error parsing broadcast ID: {e}")
        await callback.message.answer("Ошибка: неверный ID уведомления.", parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in callback_broadcast_ab_stat_detail: {e}")
        await callback.message.answer("Ошибка при получении статистики A/B теста. Проверь логи.", parse_mode="HTML")
