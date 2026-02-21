"""
InlineKeyboardMarkup and ReplyKeyboardMarkup builders. Shared across all handler domains.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import config
import database
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.i18n import get_text as i18n_get_text
from app.services.trials import service as trial_service

logger = logging.getLogger(__name__)


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

    Кнопка "Пробный период 3 дня" показывается ТОЛЬКО если:
    - trial_used_at IS NULL
    - Нет активной подписки
    - Нет платных подписок в истории (source='payment')
    """
    buttons = []

    if telegram_id and database.DB_READY:
        try:
            is_available = await trial_service.is_trial_available(telegram_id)
            if is_available:
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(language, "trial.button"),
                    callback_data="activate_trial"
                )])
        except Exception as e:
            logger.warning(f"Error checking trial availability for user {telegram_id}: {e}")

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.profile"),
        callback_data="menu_profile"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.buy"),
        callback_data="menu_buy_vpn"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.instruction"),
        callback_data="menu_instruction"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.referral"),
        callback_data="menu_referral"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.game", "Игры 🎮"),
        callback_data="games_menu"
    )])
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.ecosystem", "main.ecosystem"),
            callback_data="menu_ecosystem"
        ),
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.help"),
            callback_data="menu_support"
        ),
    ])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.settings", "main.settings"),
        callback_data="menu_settings"
    )])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
):
    """Клавиатура профиля. Plus: кнопка «Подключиться» (deep link), иначе «Скопировать ключ»."""
    buttons = []

    if has_active_subscription:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "subscription.renew"),
            callback_data="menu_buy_vpn"
        )])

        if auto_renew:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "subscription.auto_renew_disable"),
                callback_data="toggle_auto_renew:off"
            )])
        else:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "subscription.auto_renew_enable"),
                callback_data="toggle_auto_renew:on"
            )])
    else:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.buy"),
            callback_data="menu_buy_vpn"
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "profile.topup_balance"),
        callback_data="topup_balance"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "profile.withdraw_funds"),
        callback_data="withdraw_start"
    )])
    if subscription_type == "plus" and vpn_key:
        # vpn_key for plus is subscription_url (https://.../sub/UUID) — Telegram accepts it
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "subscription.connect_button", "🔌 Подключиться"),
            url=vpn_key
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "profile.copy_key"),
            callback_data="copy_key"
        )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main"
    )])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_profile_keyboard_with_copy(language: str, last_tariff: str = None, is_vip: bool = False, has_subscription: bool = True):
    """Клавиатура профиля с кнопкой копирования ключа и историей (старая версия, для совместимости)"""
    return get_profile_keyboard(language, has_subscription)


def get_profile_keyboard_old(language: str):
    """Клавиатура с кнопками профиля и инструкции (после активации) - старая версия, переименована"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "main.profile"),
                callback_data="menu_profile"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "main.instruction"),
                callback_data="menu_instruction"
            ),
        ],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "profile.copy_key"),
            callback_data="copy_key"
        )]
    ])


def get_v2raytun_connect_deep_link(vpn_key: str) -> str:
    """Deep link for v2rayTUN auto-import. Plus tariff: vpn_key is Base64 subscription."""
    sub_url = f"sub://{vpn_key}"
    return f"v2rayTUN://install-sub?url={quote(sub_url, safe='')}"


def get_vpn_key_keyboard(
    language: str,
    subscription_type: str = "basic",
    vpn_key: Optional[str] = None,
):
    """Клавиатура для экрана выдачи VPN-ключа после оплаты. Plus: кнопка «Подключиться» (url=vpn_key)."""
    if subscription_type == "plus" and vpn_key:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "subscription.connect_button", "🔌 Подключиться"), url=vpn_key)],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.go_to_connection"),
                callback_data="menu_instruction"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "main.profile"),
                callback_data="go_profile"
            )],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.go_to_connection"),
            callback_data="menu_instruction"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "profile.copy_key"),
            callback_data="copy_vpn_key"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.profile"),
            callback_data="go_profile"
        )],
    ])


async def get_tariff_keyboard(language: str, telegram_id: int, promo_code: str = None, purchase_id: str = None):
    """Клавиатура выбора тарифа с учетом скидок

    DEPRECATED: Кнопки тарифов создаются в callback_tariff_type с использованием calculate_final_price.
    """
    buttons = []

    for tariff_key in config.TARIFFS.keys():
        base_text = i18n_get_text(language, "buy.tariff_button_" + str(tariff_key), f"tariff_button_{tariff_key}")
        buttons.append([InlineKeyboardButton(text=base_text, callback_data=f"tariff_type:{tariff_key}")])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "buy.enter_promo"),
        callback_data="enter_promo"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main"
    )])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_method_keyboard(language: str):
    """Клавиатура выбора способа оплаты"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.test", "payment_test"),
            callback_data="payment_test"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.sbp", "payment_sbp"),
            callback_data="payment_sbp"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_buy_vpn"
        )],
    ])


def get_sbp_payment_keyboard(language: str):
    """Клавиатура для оплаты СБП"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.paid_button", "paid_button"),
            callback_data="payment_paid"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])


def get_pending_payment_keyboard(language: str):
    """Клавиатура после нажатия 'Я оплатил'"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.support", "support"),
            callback_data="menu_support"
        )],
    ])


def get_about_keyboard(language: str):
    """Клавиатура раздела 'О сервисе'"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.privacy_policy", "privacy_policy"),
            callback_data="about_privacy"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.our_channel"),
            url="https://t.me/atlas_secure"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])


def get_service_status_keyboard(language: str):
    """Клавиатура экрана 'Статус сервиса'"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.support", "support"),
            callback_data="menu_support"
        )],
    ])


def get_support_keyboard(language: str):
    """Клавиатура раздела 'Поддержка'"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "support.write_button"),
            url="https://t.me/asc_support"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])


def get_instruction_keyboard(
    language: str,
    platform: str = "unknown",
    subscription_type: str = "basic",
    vpn_key: Optional[str] = None,
):
    """
    Клавиатура экрана 'Инструкция' для v2RayTun.
    Plus: первая строка — «Подключиться» (deep link). Иначе — кнопки платформ и «Скопировать ключ».
    """
    buttons = []
    if subscription_type == "plus" and vpn_key:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "subscription.connect_button", "🔌 Подключиться"),
            url=vpn_key
        )])

    buttons.extend([
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_android", "🤖 Android"),
                url="https://play.google.com/store/apps/details?id=com.v2raytun.android"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_desktop", "💻 Windows"),
                url="https://www.mediafire.com/folder/lpcbgr4ox8u5x/Atlas_Secure"
            ),
        ],
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_ios", "📱 iOS"),
                url="https://apps.apple.com/tr/app/v2raytun/id6476628951"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_macos", "🍎 MacOS"),
                url="https://apps.apple.com/tr/app/v2raytun/id6476628951"
            ),
        ],
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "instruction._download_tv", "📺 TV"),
                url="https://play.google.com/store/apps/details?id=com.v2raytun.android"
            ),
        ],
    ])

    if subscription_type == "plus" and vpn_key:
        pass  # connect button already at top
    else:
        buttons.append([
            InlineKeyboardButton(
                text=i18n_get_text(language, "profile.copy_key", "copy_key"),
                callback_data="copy_vpn_key"
            ),
        ])
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )
    ])
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.support", "support"),
            callback_data="menu_support"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_dashboard_keyboard(language: str = "ru"):
    """Клавиатура главного экрана админ-дашборда"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.dashboard"), callback_data="admin:dashboard")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.stats"), callback_data="admin:stats")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.analytics"), callback_data="admin:analytics")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.metrics"), callback_data="admin:metrics")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.audit"), callback_data="admin:audit")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.keys"), callback_data="admin:keys")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.user"), callback_data="admin:user")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.balance_management"), callback_data="admin:balance_management")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.system"), callback_data="admin:system")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.export"), callback_data="admin:export")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.broadcast"), callback_data="admin:broadcast")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.promo_stats"), callback_data="admin_promo_stats")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_stats"), callback_data="admin:referral_stats")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.create_promocode"), callback_data="admin:create_promocode")],
    ])


def get_admin_back_keyboard(language: str = "ru"):
    """Клавиатура 'Назад' для админ-панели"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])


def get_reissue_notification_keyboard(language: str = "ru"):
    """Клавиатура для уведомления о перевыпуске VPN-ключа"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.go_to_instruction"), callback_data="menu_instruction")],
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


def get_broadcast_test_type_keyboard(language: str = "ru"):
    """Клавиатура выбора типа тестирования"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._normal"), callback_data="broadcast_test_type:normal")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._ab_test"), callback_data="broadcast_test_type:ab")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])


def get_broadcast_type_keyboard(language: str = "ru"):
    """Клавиатура выбора типа уведомления"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_info"), callback_data="broadcast_type:info")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_maintenance"), callback_data="broadcast_type:maintenance")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_security"), callback_data="broadcast_type:security")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_promo"), callback_data="broadcast_type:promo")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])


def get_broadcast_segment_keyboard(language: str = "ru"):
    """Клавиатура выбора сегмента получателей"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._segment_all"), callback_data="broadcast_segment:all_users")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._segment_active"), callback_data="broadcast_segment:active_subscriptions")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])


def get_broadcast_confirm_keyboard(language: str = "ru"):
    """Клавиатура подтверждения отправки уведомления"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._confirm_send"), callback_data="broadcast:confirm_send")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])


def get_ab_test_list_keyboard(ab_tests: list, language: str = "ru") -> InlineKeyboardMarkup:
    """Клавиатура списка A/B тестов"""
    buttons = []
    for test in ab_tests[:20]:
        test_id = test["id"]
        title = test["title"][:30] + "..." if len(test["title"]) > 30 else test["title"]
        created_at = test["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        date_str = created_at.strftime("%d.%m.%Y")
        button_text = f"#{test_id} {title} ({date_str})"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"broadcast:ab_stat:{test_id}")])

    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:broadcast")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_export_keyboard(language: str = "ru"):
    """Клавиатура выбора типа экспорта"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.export_users"), callback_data="admin:export:users")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.export_subscriptions"), callback_data="admin:export:subscriptions")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])


def get_admin_user_keyboard(has_active_subscription: bool = False, user_id: int = None, has_discount: bool = False, is_vip: bool = False, language: str = "ru"):
    """Клавиатура для раздела пользователя"""
    buttons = []
    if has_active_subscription:
        callback_data = f"admin:user_reissue:{user_id}" if user_id else "admin:user_reissue"
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.reissue_key"), callback_data=callback_data)])
    if user_id:
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.subscription_history"), callback_data=f"admin:user_history:{user_id}")])
        buttons.append([
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_access"), callback_data=f"admin:grant:{user_id}"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_access"), callback_data=f"admin:revoke:user:{user_id}")
        ])
        if has_discount:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.delete_discount"), callback_data=f"admin:discount_delete:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.create_discount"), callback_data=f"admin:discount_create:{user_id}")])
        if is_vip:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_vip"), callback_data=f"admin:vip_revoke:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_vip"), callback_data=f"admin:vip_grant:{user_id}")])
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.credit_balance"), callback_data=f"admin:credit_balance:{user_id}")])
    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_user_keyboard_processing(user_id: int, has_discount: bool = False, is_vip: bool = False, language: str = "ru"):
    """Клавиатура во время перевыпуска ключа: кнопка «Перевыпуск» заменена на disabled состояние"""
    buttons = []
    buttons.append([InlineKeyboardButton(text="⏳ Перевыпуск...", callback_data="noop")])
    if user_id:
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.subscription_history"), callback_data=f"admin:user_history:{user_id}")])
        buttons.append([
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_access"), callback_data=f"admin:grant:{user_id}"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_access"), callback_data=f"admin:revoke:user:{user_id}")
        ])
        if has_discount:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.delete_discount"), callback_data=f"admin:discount_delete:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.create_discount"), callback_data=f"admin:discount_create:{user_id}")])
        if is_vip:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_vip"), callback_data=f"admin:vip_revoke:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_vip"), callback_data=f"admin:vip_grant:{user_id}")])
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.credit_balance"), callback_data=f"admin:credit_balance:{user_id}")])
    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_payment_keyboard(payment_id: int, language: str = "ru"):
    """Клавиатура для администратора (подтверждение/отклонение платежа)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "admin.confirm", "admin_confirm"),
                callback_data=f"approve_payment:{payment_id}"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "admin.reject", "admin_reject"),
                callback_data=f"reject_payment:{payment_id}"
            ),
        ],
    ])
