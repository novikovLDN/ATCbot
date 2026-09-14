"""
InlineKeyboardMarkup and ReplyKeyboardMarkup builders. Shared across all handler domains.
"""
import logging
import re
from typing import Optional

import config
import database
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from app.i18n import get_text as i18n_get_text
from app.services.trials import service as trial_service

logger = logging.getLogger(__name__)

# Bot API 9.4: когда кнопка получает icon_custom_emoji_id, нужно снять
# обычный эмодзи из её текста, иначе на новых клиентах получится два
# эмодзи подряд (custom + plain). Регулярка ловит любые ведущие
# не-словарные не-пробельные символы (\W в Unicode-режиме покрывает
# эмодзи, пиктограммы, decorative dingbats) и трейлинг-пробел.
_LEAD_EMOJI_RE = re.compile(r"^[^\w\s]+\s*", flags=re.UNICODE)


def _strip_lead_emoji(s: str) -> str:
    out = _LEAD_EMOJI_RE.sub("", s, count=1)
    return out or s

MINI_APP_URL = config.env("MINI_APP_URL", default="https://atlas-miniapp-production.up.railway.app")


# Premium custom emoji IDs — moved to app.handlers.common.emoji so any
# handler can import CE without pulling in keyboards.py (which depends on
# database). Re-exported here for backwards compatibility.
from app.handlers.common.emoji import CE  # noqa: E402,F401


def get_connect_keyboard(language: str = "ru"):
    """Клавиатура после активации: Подключиться + Помощь."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.btn_connect_short", "⚡️ Подключиться"),
            callback_data="connect_instruction",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.btn_need_help", "💬 Нужна помощь"),
            url="https://t.me/atlas_suppbot",
        )],
    ])


def get_language_keyboard(language: str = "ru"):
    """Клавиатура выбора языка — только ru + en."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_ru"), callback_data="lang_ru", style="primary"),
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_en"), callback_data="lang_en", style="primary"),
        ],
    ])
    return keyboard


async def get_main_menu_keyboard(language: str, telegram_id: int = None):
    """Клавиатура главного меню

    Args:
        language: Язык пользователя
        telegram_id: Telegram ID пользователя (обязательно для проверки trial availability)

    Логика первой кнопки (3 состояния):
    1. Новый пользователь (trial доступен) → "Пробный период 3 дня"
    2. Активная подписка → "🚀 Подключиться" (WebApp)
    3. Подписка истекла + спецпредложение → "🔥 -15% | ⏳ Xд Yч"
    """
    is_bypass_only = False
    subscription = None
    has_active_sub = False
    if telegram_id and database.DB_READY:
        try:
            subscription = await database.get_subscription(telegram_id)
            has_active_sub = subscription is not None
            is_bypass_only = bool(subscription and subscription.get("is_bypass_only"))
        except Exception as e:
            logger.warning(f"Error checking subscription for main menu: {e}")

    # У пользователя есть Remnawave bypass entity (остаток ГБ) — тогда
    # даже без активной подписки показываем экран «Моя подписка», чтобы
    # он мог посмотреть остаток трафика и продлить.
    has_bypass_history = False
    if telegram_id and database.DB_READY and config.REMNAWAVE_ENABLED:
        try:
            has_bypass_history = bool(await database.get_remnawave_uuid(telegram_id))
        except Exception as e:
            logger.warning(f"Error checking bypass history for main menu: {e}")

    show_my_sub = has_active_sub or has_bypass_history

    buttons = []

    if has_active_sub:
        # === Активная подписка ===
        # Row 1: Продлить VPN (🔄) / Купить VPN (🛒 — для bypass-only)
        if is_bypass_only:
            # N7: a paid premium that ended leaves the row active + bypass-only —
            # the −15 % offer button belongs here too (it was only in the no-sub branch).
            try:
                special_offer = await database.get_special_offer_info(telegram_id) if telegram_id else None
            except Exception as e:
                special_offer = None
                logger.warning(f"Error checking special offer for user {telegram_id}: {e}")
            if special_offer:
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(language, "main.btn_renew_discount_15", "Продлить со скидкой 15% | ⏳ {remaining}",
                                       remaining=special_offer["remaining_text"]),
                    callback_data="special_offer_buy",
                    icon_custom_emoji_id=CE["renew"],
                    style="success",
                )])
            buy_text = i18n_get_text(language, "main.btn_buy_vpn", "Купить VPN")
            buy_icon = CE["buy"]
        else:
            buy_text = i18n_get_text(language, "main.btn_renew_vpn", "Продлить VPN")
            buy_icon = CE["renew"]
        buttons.append([InlineKeyboardButton(
            text=buy_text,
            callback_data="menu_buy_vpn",
            icon_custom_emoji_id=buy_icon,
            style="success",
        )])
        # Row 2: Докупить ГБ обхода (📡)
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.btn_buy_gb", "Докупить ГБ обхода"),
            callback_data="buy_traffic",
            icon_custom_emoji_id=CE["traffic"],
            style="success",
        )])
    else:
        # === Без активной подписки ===
        trial_available = False
        if telegram_id and database.DB_READY:
            try:
                trial_available = await trial_service.is_trial_available(telegram_id)
            except Exception as e:
                logger.warning(f"Error checking trial availability for user {telegram_id}: {e}")

        # Первичный юзер (без подписки И без истории) — красные (danger)
        # акценты, чтобы кнопки выделялись максимально на первом экране.
        # Для юзеров с историей (была подписка / есть остаток ГБ) — зелёные.
        primary_cta_style = "danger" if not has_bypass_history else "success"

        if trial_available:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "main.btn_trial_free", "Попробовать бесплатно — 3 дня"),
                callback_data="activate_trial",
                icon_custom_emoji_id=CE["gift"],
                style=primary_cta_style,
            )])

        # Спецпредложение для истекших
        offer_shown = False
        try:
            special_offer = await database.get_special_offer_info(telegram_id) if telegram_id else None
            if special_offer:
                remaining = special_offer["remaining_text"]
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(language, "main.btn_renew_discount_15", "Продлить со скидкой 15% | ⏳ {remaining}", remaining=remaining),
                    callback_data="special_offer_buy",
                    icon_custom_emoji_id=CE["renew"],
                    style=primary_cta_style,
                )])
                offer_shown = True
        except Exception as e:
            logger.warning(f"Error checking special offer for user {telegram_id}: {e}")

        # Row 1: Купить VPN (🛒)
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.btn_buy_vpn", "Купить VPN"),
            callback_data="menu_buy_vpn",
            icon_custom_emoji_id=CE["buy"],
            style=primary_cta_style,
        )])

        # «Только обход блокировок» — только для юзеров без истории
        if not has_bypass_history:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "main.btn_bypass_only", "🌐 Только обход блокировок"),
                callback_data="buy_bypass_only",
                style=primary_cta_style,
            )])

    # === Общие ряды — только для юзеров с подпиской или историей ===
    # Первичный юзер (никогда не покупал и без активной подписки) видит
    # только офферы «Попробовать бесплатно / Купить VPN / Только обход» —
    # без Профиль/Магазин/Игры/Помощь. Как только он что-то купит или
    # активирует триал, появятся все ряды.
    if has_active_sub or has_bypass_history:
        # Row: Моя подписка (⛓️)
        if show_my_sub:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "main.btn_my_subscription", "Моя подписка"),
                callback_data="menu_my_subscription",
                icon_custom_emoji_id=CE["my_sub"],
                style="primary",
            )])

        # Row: Пригласить друзей (👤)
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.btn_invite_friends", "Пригласить друзей"),
            callback_data="menu_referral",
            icon_custom_emoji_id=CE["invite"],
            style="primary",
        )])

        # Row: Мой профиль (👤) | Магазин (🛒)
        buttons.append([
            InlineKeyboardButton(
                text=i18n_get_text(language, "main.btn_my_profile", "Мой профиль"),
                callback_data="menu_profile",
                icon_custom_emoji_id=CE["profile"],
                style="primary",
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "main.btn_shop", "Магазин"),
                callback_data="mini_shop",
                icon_custom_emoji_id=CE["shop"],
                style="primary",
            ),
        ])

        # Row: Игры (🎮)
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.btn_games", "Игры"),
            callback_data="games_menu",
            icon_custom_emoji_id=CE["games"],
            style="primary",
        )])

        # Row: Помощь (🆘)
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.btn_help", "Помощь"),
            callback_data="menu_help",
            icon_custom_emoji_id=CE["help"],
            style="primary",
        )])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_keyboard(language: str):
    """Кнопка Назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )]
    ])


def get_profile_keyboard(
    language: str,
    has_active_subscription: bool = False,
    auto_renew: bool = False,
    subscription_type: str = "basic",
    vpn_key: Optional[str] = None,
    vpn_key_plus: Optional[str] = None,
    show_traffic: bool = False,
    is_trial: bool = False,
    is_combo: bool = False,
    is_bypass_only: bool = False,
):
    """Личный кабинет: покупки — зелёные (success), навигация — синие (primary)."""
    buttons = []

    # Row 1: Продлить VPN (🔄) / Купить VPN (🛒)
    if has_active_subscription and not is_bypass_only:
        buy_text = i18n_get_text(language, "main.btn_renew_vpn", "Продлить VPN")
        buy_icon = CE["renew"]
    else:
        buy_text = i18n_get_text(language, "main.btn_buy_vpn", "Купить VPN")
        buy_icon = CE["buy"]
    buttons.append([InlineKeyboardButton(
        text=buy_text,
        callback_data="menu_buy_vpn",
        icon_custom_emoji_id=buy_icon,
        style="success",
    )])

    # Row 2: Докупить ГБ обхода (📡)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.btn_buy_gb", "Докупить ГБ обхода"),
        callback_data="buy_traffic",
        icon_custom_emoji_id=CE["traffic"],
        style="success",
    )])

    # Row 3: Мои устройства (💻)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.btn_devices", "Мои устройства"),
        callback_data="user:devices",
        icon_custom_emoji_id=CE["devices"],
        style="primary",
    )])

    # Row 4: Пополнить баланс (👛)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.btn_topup_balance", "Пополнить баланс"),
        callback_data="topup_balance",
        icon_custom_emoji_id=CE["wallet"],
        style="primary",
    )])

    # Row 4a: Автопродление с баланса — только при активной non-bypass подписке.
    # Тумблер: ✅ = включено, без галочки = выключено. Callback toggle_auto_renew.
    if has_active_subscription and not is_bypass_only:
        if auto_renew:
            ar_text = i18n_get_text(language, "main.btn_auto_renew_on", "🔁 Автопродление с баланса ✅")
            ar_data = "toggle_auto_renew:off"
        else:
            ar_text = i18n_get_text(language, "main.btn_auto_renew_off", "🔁 Автопродление с баланса")
            ar_data = "toggle_auto_renew:on"
        buttons.append([InlineKeyboardButton(
            text=ar_text,
            callback_data=ar_data,
            style="primary",
        )])

    # Row 5: Сменить язык (💬)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.btn_change_language_full", "Сменить язык / Change language"),
        callback_data="change_language",
        icon_custom_emoji_id=CE["language"],
        style="primary",
    )])

    # Row 6: Правила (🧑)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.btn_legal", "Правила"),
        callback_data="menu_legal",
        icon_custom_emoji_id=CE["legal"],
        style="primary",
    )])

    # Row 7: Назад (👈)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back", "Назад"),
        callback_data="menu_main",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_success_keyboard(
    language: str,
    subscription_type: str = "basic",
    is_renewal: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура после успешной оплаты/активации триала."""
    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "trial.activated_btn_connect"),
            callback_data="connect_instruction",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "trial.activated_btn_support"),
            url="https://t.me/atlas_suppbot",
        )],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_about_keyboard(language: str):
    """Клавиатура раздела 'О сервисе'"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.privacy_policy", "privacy_policy"),
            callback_data="about_privacy",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.our_channel"),
            url="https://t.me/atlas_secure"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ])


def get_instruction_keyboard(
    language: str,
    platform: str = "unknown",
    subscription_type: str = "basic",
    vpn_key: Optional[str] = None,
):
    """Клавиатура экрана 'Инструкция': кнопка перехода в мини-приложение + Назад."""
    guide_url = f"{MINI_APP_URL}?startapp=guide"
    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "instruction._open_guide", "📖 Инструкция по установке"),
            web_app=WebAppInfo(url=guide_url),
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_reissue_notification_keyboard(language: str = "ru"):
    """Клавиатура для уведомления о перевыпуске VPN-ключа"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.go_to_instruction"), callback_data="menu_instruction", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.copy_key"), callback_data="copy_vpn_key", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.my_profile"), callback_data="menu_profile", icon_custom_emoji_id=CE["profile"], style="primary")],
    ])


def _get_promo_error_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой 'Назад' при ошибке промокода"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="promo_back",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )
        ]
    ])


