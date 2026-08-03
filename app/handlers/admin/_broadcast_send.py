"""Доставка сообщений рассылки: отправка, ретраи, сборка клавиатуры.

ЧТО ЗДЕСЬ
    Нижний уровень: как отправить одно сообщение, что делать при
    TelegramRetryAfter и как собрать клавиатуру рассылки из настроек.
    Ничего про то, ЧТО именно рассылаем, — это выше по стеку.

ЧТО ЛЕГКО СЛОМАТЬ
    Telegram ограничивает частоту и отвечает TelegramRetryAfter с точным
    временем ожидания. Игнорировать его нельзя: без паузы бот получает
    временную блокировку и рассылка встаёт целиком, а не для одного адресата.
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


logger = logging.getLogger(__name__)

# Параметры темпа рассылки: сколько отправок параллельно, каким батчем
# и с какой паузой между батчами. Подобраны под лимиты Telegram.
_GIFT_REVEAL_PERCENT_DEFAULT = 20
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
    animation_file_id: str | None = None,
    caption: str | None = None,
) -> int | None:
    """Send message with optional inline buttons.

    Приоритет media:
      1) animation_file_id (GIF/MP4) → send_animation
      2) photo_file_id → send_photo
      3) plain text → send_message

    Returns message_id on success, None on failure.
    """
    from app.utils.telegram_safe import convert_tg_emoji
    text = convert_tg_emoji(text)
    if caption:
        caption = convert_tg_emoji(caption)
    async with semaphore:
        for attempt in range(BROADCAST_RETRY_LIMIT):
            try:
                if animation_file_id:
                    result = await bot.send_animation(
                        user_id,
                        animation=animation_file_id,
                        caption=caption or text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                elif photo_file_id:
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
        elif btn == "gift_1m":
            rows.append([InlineKeyboardButton(
                text="🎁 −30% на 1 месяц",
                callback_data="broadcast_gift_1m",
            )])
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
            rows.append([InlineKeyboardButton(text="🌐 Включить обход", callback_data="broadcast_bypass")])
        elif btn == "channel":
            rows.append([InlineKeyboardButton(text="📢 Наш канал", url="https://t.me/ATC_VPN")])
        elif btn == "support":
            rows.append([InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/atlas_suppbot")])
        elif btn == "referral":
            rows.append([InlineKeyboardButton(text="👥 Пригласить друга", callback_data="menu_referral")])
        elif btn == "happ_ios":
            rows.append([InlineKeyboardButton(
                text="📲 Скачать Happ для iOS ⚡️",
                url="https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6788279553?l=en-GB",
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
