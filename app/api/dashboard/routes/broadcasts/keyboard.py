"""Рассылки: клавиатура письма и нормализация premium-эмодзи.

ЧТО ЗДЕСЬ
    `_BUTTON_TYPES` — что вообще разрешено просить у API;
    `_build_reply_markup` — сборка инлайн-клавиатуры по этому списку;
    `normalize_premium_emoji` — Markdown-формат premium-эмодзи → HTML.

ПОЧЕМУ ВЫДЕЛЕНО
    Кнопки правят чаще всего остального в рассылках (новый оффер — новая
    кнопка), и правка эта чисто вёрстечная: ни базы, ни фоновых задач.

ЧТО ЛЕГКО СЛОМАТЬ
    callback_data здесь — это адрес обработчика в боте. Опечатка не даёт
    ошибки: человек получает рассылку, жмёт кнопку, и ничего не
    происходит. Соответствие сторожит
    tests/services/test_broadcast_buttons_reachable.py.

    `_BUTTON_TYPES` и ветки `_build_reply_markup` должны совпадать:
    значение, разрешённое валидатором, но не собранное здесь, молча
    исчезает из письма.
"""
from __future__ import annotations

import re
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


# Telegram-клиент при копировании premium-эмодзи иногда вставляет их
# в Markdown image-синтаксисе  ![👑](tg://emoji?id=12345).  Бот шлёт
# broadcast только с parse_mode="HTML" — такой markdown отрисуется как
# plain text и сломает entity-парсер (отсюда 600/600 ошибок). Чтобы
# админ мог копи-пастить из любого источника, нормализуем оба формата
# к HTML-варианту  <tg-emoji emoji-id="12345">👑</tg-emoji>.
_MD_TG_EMOJI_RE = re.compile(r"!\[([^\]]+?)\]\(tg://emoji\?id=(\d+)\)")


def normalize_premium_emoji(text: str) -> str:
    """Convert Markdown `![emoji](tg://emoji?id=X)` → HTML `<tg-emoji>`.

    Idempotent on text that's already HTML.
    """
    if not text:
        return text
    return _MD_TG_EMOJI_RE.sub(
        lambda m: f'<tg-emoji emoji-id="{m.group(2)}">{m.group(1)}</tg-emoji>',
        text,
    )


_BUTTON_TYPES = {
    "buy",
    "promo_buy",
    "promo_traffic",
    "gift_reveal",
    "gift_1m",
    "gift_3m",
    "gift_1y_40",
    "support",
    "channel",
    "referral",
    "bypass",
    "happ_ios",
    "happ_android",
    "web_client",
    "buy_combo",
    "share_discount",
}


def _build_reply_markup(
    buttons: list[str],
    broadcast_id: int,
    discount: Optional[int],
) -> Optional[InlineKeyboardMarkup]:
    if not buttons:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for btn in buttons:
        if btn == "buy":
            rows.append([InlineKeyboardButton(text="🛒 Купить", callback_data="menu_buy_vpn")])
        elif btn == "promo_buy":
            label = f"🎁 Купить со скидкой {discount}%" if discount else "🎁 Купить со скидкой"
            rows.append([InlineKeyboardButton(
                text=label, callback_data=f"broadcast_promo_buy:{broadcast_id}",
            )])
        elif btn == "promo_traffic":
            label = (
                f"📊 Купить ГБ со скидкой {discount}%"
                if discount else "📊 Купить ГБ со скидкой"
            )
            rows.append([InlineKeyboardButton(
                text=label,
                callback_data=f"broadcast_promo_traffic:{broadcast_id}",
            )])
        elif btn == "gift_reveal":
            # «Посмотреть подарок» — теплично-CTA. Хардкоженная фишка:
            # 20% скидка на подписку, 48 часов. Параметры discount_percent /
            # discount_hours дашборда не используются — здесь свой реверс-
            # сюрприз flow с premium-эмодзи и delayed reveal в handler'е.
            # Красная кнопка задаётся явным style="danger" (см. monkey-patch
            # в app/utils/button_defaults.py — fallback по text-pattern
            # не сработает на эту фразу, передаём руками).
            rows.append([InlineKeyboardButton(
                text="Посмотреть подарок",
                callback_data=f"broadcast_gift_reveal:{broadcast_id}",
                style="danger",
                icon_custom_emoji_id="5210956306952758910",
            )])
        elif btn == "support":
            rows.append([InlineKeyboardButton(
                text="💬 Поддержка", url="https://t.me/atlas_suppbot",
            )])
        elif btn == "channel":
            rows.append([InlineKeyboardButton(
                text="📢 Наш канал", url="https://t.me/ATC_VPN",
            )])
        elif btn == "referral":
            rows.append([InlineKeyboardButton(
                text="👥 Пригласить друга", callback_data="menu_referral",
            )])
        elif btn == "bypass":
            rows.append([InlineKeyboardButton(
                text="🌐 Включить обход", callback_data="broadcast_bypass",
            )])
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
                text="🌐 Веб-клиент QoDev", url="https://qodev.dev",
            )])
        elif btn == "buy_combo":
            rows.append([InlineKeyboardButton(text="🏆 Купить Комбо", callback_data="buy_combo")])
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
            # «🎁 1 год со скидкой 40%». Открывает 2-шаговый flow: тариф →
            # период. Скидка применяется ТОЛЬКО к 365-дневному плану,
            # остальные периоды по обычной цене. Реализация в
            # app/handlers/admin/broadcast.py:callback_broadcast_gift_1y_40.
            rows.append([InlineKeyboardButton(
                text="🎁 1 год со скидкой 40%",
                callback_data="broadcast_gift_1y_40",
            )])
        elif btn == "share_discount":
            # Callback share_discount_open рендерится в referrals.py:
            # экран «Подари другу скидку 30%» + кнопка share с личной
            # refd-ссылкой получателя. broadcast_id здесь не нужен —
            # callback статический.
            rows.append([InlineKeyboardButton(
                text="🎁 Поделиться скидкой",
                callback_data="share_discount_open",
            )])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
