"""
InlineKeyboardMarkup and ReplyKeyboardMarkup builders. Shared across all handler domains.
"""
import logging
import re
from datetime import datetime
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


def get_connect_keyboard(language: str = "ru"):
    """Клавиатура после активации: Подключиться + Помощь."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⚡️ Подключиться",
            callback_data="connect_instruction",
        )],
        [InlineKeyboardButton(
            text="💬 Нужна помощь",
            url="https://t.me/atlas_suppbot",
        )],
    ])


def get_language_keyboard(language: str = "ru"):
    """Клавиатура для выбора языка (языковые названия показываются в нативной форме)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_ru"), callback_data="lang_ru"),
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_en"), callback_data="lang_en"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_de"), callback_data="lang_de"),
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_kk"), callback_data="lang_kk"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_ar"), callback_data="lang_ar"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_uz"), callback_data="lang_uz"),
            InlineKeyboardButton(text=i18n_get_text(language, "lang.button_tj"), callback_data="lang_tj"),
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
    # Проверяем бизнес-подписку для специального меню
    is_biz_user = False
    is_bypass_only = False
    subscription = None
    has_active_sub = False
    if telegram_id and database.DB_READY:
        try:
            subscription = await database.get_subscription(telegram_id)
            has_active_sub = subscription is not None
            sub_type = (subscription.get("subscription_type") or "basic").strip().lower() if subscription else "basic"
            is_biz_user = config.is_biz_tariff(sub_type)
            is_bypass_only = bool(subscription and subscription.get("is_bypass_only"))
        except Exception as e:
            logger.warning(f"Error checking subscription for main menu: {e}")

    if is_biz_user:
        return _get_biz_main_menu_keyboard(language)

    has_proxy = False
    if telegram_id and database.DB_READY:
        try:
            has_proxy = await database.has_purchased_proxy(telegram_id)
        except Exception as e:
            logger.warning(f"Error checking proxy ownership for main menu: {e}")

    proxy_button = InlineKeyboardButton(
        text=("Мой прокси" if has_proxy else "Telegram MT Прокси"),
        callback_data="proxy_menu",
        icon_custom_emoji_id="5233479338791281256",  # ⭐️
    )

    buttons = []

    # === ПЕРВАЯ КНОПКА: 3 состояния ===
    if has_active_sub:
        # Состояние 2: Активная подписка → "Подключиться" (ведёт на экран инструкции)
        #
        # Bot API 9.4: ставим тестовое сочетание — premium custom emoji
        # слева (EMOJI["sub"] = 5330115548900501467) + style="primary"
        # (синий заливочный фон). Префикс «📲 » из текста снят, чтобы
        # на клиентах с поддержкой API 9.4 не оказалось два эмодзи
        # подряд. На старых клиентах кнопка выглядит как раньше, просто
        # без обычного префикса — лучше пустое место, чем плейсхолдер.
        buttons.append([InlineKeyboardButton(
            text="Подключиться",
            callback_data="connect_instruction",
            icon_custom_emoji_id="5330115548900501467",
            style="primary",
        )])
    elif telegram_id and database.DB_READY:
        # Проверяем trial
        trial_available = False
        try:
            trial_available = await trial_service.is_trial_available(telegram_id)
        except Exception as e:
            logger.warning(f"Error checking trial availability for user {telegram_id}: {e}")

        if trial_available:
            # Состояние 1: Новый пользователь → "Попробовать бесплатно"
            buttons.append([InlineKeyboardButton(
                text="🎁 Попробовать бесплатно — 3 дня",
                callback_data="activate_trial"
            )])

        # Кнопки покупки для пользователей без подписки
        # Проверяем спецпредложение для истекших подписок
        offer_shown = False
        try:
            special_offer = await database.get_special_offer_info(telegram_id)
            if special_offer:
                remaining = special_offer["remaining_text"]
                buttons.append([InlineKeyboardButton(
                    text=f"Продлить со скидкой 15% | ⏳ {remaining}",
                    callback_data="special_offer_buy",
                    icon_custom_emoji_id="5199785165735367039",  # ⚡️
                )])
                offer_shown = True
        except Exception as e:
            logger.warning(f"Error checking special offer for user {telegram_id}: {e}")

        if not offer_shown and not trial_available:
            # Нет триала и нет спецпредложения — обычные кнопки
            pass

        buttons.append([InlineKeyboardButton(
            text="Купить подписку",
            callback_data="menu_buy_vpn",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
        )])
        buttons.append([InlineKeyboardButton(
            text="🌐 Только обход блокировок",
            callback_data="buy_bypass_only"
        )])
        buttons.append([proxy_button])

    # Traffic button removed — traffic info is now in profile screen

    if has_active_sub:
        # === Кнопки для пользователей С подпиской ===
        buttons.append([InlineKeyboardButton(
            text=_strip_lead_emoji(i18n_get_text(language, "main.profile")),
            callback_data="menu_profile",
            icon_custom_emoji_id="6019503133288304110",  # 🧑‍💻
        )])
        if is_bypass_only:
            # Bypass-only: кнопки докупить трафик и купить подписку
            buttons.append([
                InlineKeyboardButton(
                    text="Купить ГБ обхода",
                    callback_data="buy_traffic",
                    icon_custom_emoji_id="5199785165735367039",  # ⚡️
                ),
                InlineKeyboardButton(
                    text="Купить VPN",
                    callback_data="menu_buy_vpn",
                    icon_custom_emoji_id="5199785165735367039",  # ⚡️
                ),
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    text="Продлить подписку",
                    callback_data="menu_buy_vpn",
                    icon_custom_emoji_id="5199785165735367039",  # ⚡️
                ),
                InlineKeyboardButton(
                    text="Подарить",
                    callback_data="gift_subscription",
                    icon_custom_emoji_id="5193085063998224234",  # 🎁
                ),
            ])
        buttons.append([
            InlineKeyboardButton(
                text="Игровой клуб",
                callback_data="games_menu",
                icon_custom_emoji_id="5262932983261699334",  # 🎮
            ),
            InlineKeyboardButton(
                text="Магазин",
                callback_data="mini_shop",
                icon_custom_emoji_id="5323510761077636002",  # 🛍
            ),
        ])
        buttons.append([InlineKeyboardButton(
            text="Заработать с нами",
            callback_data="menu_referral",
            icon_custom_emoji_id="5449601904147440135",  # 👑 premium (bag-of-money подойдёт лучше, но оставлю пока crown)
        )])
        buttons.append([proxy_button])
        buttons.append([
            InlineKeyboardButton(
                text="Настройки",
                callback_data="menu_settings",
                icon_custom_emoji_id="5350396951407895212",  # ⚙️
            ),
            InlineKeyboardButton(
                text="Помощь",
                callback_data="menu_help",
                icon_custom_emoji_id="5188540541922480562",  # ❓
            ),
        ])
    else:
        # === Кнопки для пользователей БЕЗ подписки ===
        #
        # Раньше здесь были только «Магазин» и «Помощь». Личный кабинет,
        # рефералка и настройки добавлялись исключительно в ветке с активной
        # подпиской — то есть человек без подписки не мог из меню попасть
        # ни к балансу, ни к своим подаркам, ни к смене языка: всё это живёт
        # на экране профиля. Пополнить баланс, чтобы потом купить подписку,
        # он тоже не мог. Обработчики этих экранов работают и без подписки,
        # недоставало только кнопок.
        buttons.append([InlineKeyboardButton(
            text=_strip_lead_emoji(i18n_get_text(language, "main.profile")),
            callback_data="menu_profile",
            icon_custom_emoji_id="6019503133288304110",  # 🧑‍💻
        )])
        buttons.append([
            InlineKeyboardButton(
                text="Магазин",
                callback_data="mini_shop",
                icon_custom_emoji_id="5323510761077636002",  # 🛍
            ),
            InlineKeyboardButton(
                text="Заработать с нами",
                callback_data="menu_referral",
                icon_custom_emoji_id="5449601904147440135",  # 👑
            ),
        ])
        buttons.append([
            InlineKeyboardButton(
                text="Настройки",
                callback_data="menu_settings",
                icon_custom_emoji_id="5350396951407895212",  # ⚙️
            ),
            InlineKeyboardButton(
                text="Помощь",
                callback_data="menu_help",
                icon_custom_emoji_id="5188540541922480562",  # ❓
            ),
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _get_biz_main_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура главного меню для бизнес-пользователей."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_my_business"),
            callback_data="biz_profile"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_control_panel"),
            callback_data="biz_control_panel"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_ecosystem"),
            callback_data="biz_ecosystem"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_personal_manager"),
            url="https://t.me/atlas_suppbot"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.settings", "main.settings"),
            callback_data="menu_settings"
        )],
    ])


def get_biz_profile_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура профиля для бизнес-подписки."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_renew_config"),
            callback_data="menu_buy_vpn"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_topup"),
            callback_data="topup_balance"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_connect"),
            web_app=WebAppInfo(url=MINI_APP_URL)
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])


def get_biz_control_panel_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура панели управления для бизнес-подписки."""
    return InlineKeyboardMarkup(inline_keyboard=[
        # Одна кнопка вместо двух. Раньше «Скопировать логин» и
        # «Скопировать пароль» читали одно и то же поле vpn_key и
        # присылали одинаковое сообщение: отдельных логина и пароля у
        # бизнес-подписки не существует, есть только ссылка подключения.
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_copy_link"),
            callback_data="biz_copy_link"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_personal_manager"),
            url="https://t.me/atlas_suppbot"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])


def get_back_keyboard(language: str):
    """Кнопка Назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
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
    """Личный кабинет: основные CTA — success-зелёные, «Мои устройства» — danger-красная."""
    buttons = []

    if is_bypass_only and has_active_subscription:
        # Bypass-only: купить ГБ + купить подписку (не продлить)
        buttons.append([InlineKeyboardButton(
            text="Купить ГБ трафика",
            callback_data="buy_traffic",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️ premium
            style="success",
        )])
        buttons.append([InlineKeyboardButton(
            text="Купить подписку VPN",
            callback_data="menu_buy_vpn",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
            style="success",
        )])
    elif is_combo and has_active_subscription:
        # Комбо-подписка: трафик и продление основной
        buttons.append([InlineKeyboardButton(
            text="Купить ГБ трафика",
            callback_data="buy_traffic",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
            style="success",
        )])
        buttons.append([InlineKeyboardButton(
            text="Продлить основную подписку",
            callback_data="menu_buy_vpn",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
            style="success",
        )])
    else:
        # Row 1: [Купить ГБ] [Продлить/Купить подписку] — основные CTA, success
        row1 = []
        if show_traffic and not is_trial:
            row1.append(InlineKeyboardButton(
                text="Купить ГБ",
                callback_data="buy_traffic",
                icon_custom_emoji_id="5199785165735367039",  # ⚡️
                style="success",
            ))
        buy_text = _strip_lead_emoji(
            i18n_get_text(language, "main.buy_renew")
            if has_active_subscription
            else i18n_get_text(language, "main.buy_new")
        )
        row1.append(InlineKeyboardButton(
            text=buy_text,
            callback_data="menu_buy_vpn",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
            style="success",
        ))
        buttons.append(row1)

    # Row 2: Мои устройства — full width, danger-красная
    buttons.append([InlineKeyboardButton(
        text="🖥 Мои устройства",
        callback_data="user:devices",
        style="danger",
    )])

    # Row 3: Пополнить + Веб-клиент
    buttons.append([
        InlineKeyboardButton(text="💳 Пополнить", callback_data="topup_balance"),
        InlineKeyboardButton(text="🌐 Веб-клиент", url="https://qodev.dev"),
    ])

    # Row 4: Язык + Подарки
    buttons.append([
        InlineKeyboardButton(text="🗣 Язык", callback_data="change_language"),
        InlineKeyboardButton(
            text=i18n_get_text(language, "gift.my_gifts_btn", "🎁 Мои подарки"),
            callback_data="my_gifts:0",
        ),
    ])

    # Row 5: Автопродление (списывается с баланса; только при активной подписке)
    if has_active_subscription and not is_bypass_only:
        ar_text = "🔁 Автопродление с баланса ✅" if auto_renew else "🔁 Автопродление с баланса"
        ar_data = "toggle_auto_renew:off" if auto_renew else "toggle_auto_renew:on"
        buttons.append([InlineKeyboardButton(text=ar_text, callback_data=ar_data)])

    # Row 6: Назад
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "common.back", "← Назад"),
            callback_data="menu_main",
        ),
    ])

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
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "trial.activated_btn_support"),
            url="https://t.me/atlas_suppbot",
        )],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)




def get_about_keyboard(
    language: str,
    *,
    back_to: str = "menu_main",
    show_privacy: bool = True,
):
    """Клавиатура раздела «О сервисе» и экрана политики.

    Два параметра появились из-за двух реальных дефектов:

    • show_privacy=False нужен на экране самой политики. Раньше он рисовался
      этой же клавиатурой, первой кнопкой которой была ссылка на политику —
      то есть на текущий экран. safe_edit_text при совпадении текста и
      разметки выходит досрочно, поэтому нажатие вообще ничего не делало:
      человек жал кнопку, а бот молчал.

    • back_to нужен, потому что «Назад» уводило в корень. Цепочка
      Главная → Настройки → Экосистема → О сервисе схлопывалась одним
      нажатием до главного меню, и вернуться на шаг назад было нельзя.
      Теперь вызывающий экран сам говорит, кто его родитель.
    """
    rows = []
    if show_privacy:
        rows.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.privacy_policy", "privacy_policy"),
            callback_data="about_privacy",
        )])
    rows.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.our_channel"),
        url="https://t.me/atlas_secure",
    )])
    rows.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=back_to,
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_service_status_keyboard(language: str):
    """Клавиатура экрана 'Статус сервиса'"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.support", "support"),
            url="https://t.me/atlas_suppbot"
        )],
    ])





# Здесь была get_instruction_keyboard — клавиатура удалённого экрана
# «Инструкция». Кнопка в мини-приложение теперь стоит на экране
# выбора устройства (connect_guide).


def get_reissue_notification_keyboard(language: str = "ru"):
    """Клавиатура уведомления о перевыпуске VPN-ключа.

    Кнопка инструкции ведёт на connect_instruction — пошаговую установку.
    Раньше она вела на menu_instruction: отдельный экран-заглушку, который
    удалён. После перевыпуска человеку нужно заново добавить ключ, и
    полезен именно пошаговый экран, а не страница со ссылкой.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.go_to_instruction"), callback_data="connect_instruction")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.copy_key"), callback_data="copy_vpn_key")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.my_profile"), callback_data="menu_profile")],
    ])


def _get_promo_error_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой 'Назад' при ошибке промокода"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="promo_back"
            )
        ]
    ])


