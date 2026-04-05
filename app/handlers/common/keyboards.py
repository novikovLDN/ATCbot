"""
InlineKeyboardMarkup and ReplyKeyboardMarkup builders. Shared across all handler domains.
"""
import logging
from datetime import datetime
from typing import Optional

import config
import database
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from app.i18n import get_text as i18n_get_text
from app.services.trials import service as trial_service

logger = logging.getLogger(__name__)

MINI_APP_URL = config.env("MINI_APP_URL", default="https://atlas-miniapp-production.up.railway.app")


def get_connect_button():
    """Одна кнопка WebApp «Подключиться» (Mini App)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🚀 Подключиться",
            web_app=WebAppInfo(url=MINI_APP_URL),
        )
    ]])


def get_connect_keyboard(language: str = "ru"):
    """Клавиатура: Подключиться + Открыть мини апп + Профиль + Главный экран."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "connect.autosetup_btn"),
            callback_data="setup_device",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "connect.open_miniapp_btn"),
            web_app=WebAppInfo(url=MINI_APP_URL),
        )],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile")],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
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
    subscription = None
    has_active_sub = False
    if telegram_id and database.DB_READY:
        try:
            subscription = await database.get_subscription(telegram_id)
            has_active_sub = subscription is not None
            sub_type = (subscription.get("subscription_type") or "basic").strip().lower() if subscription else "basic"
            is_biz_user = config.is_biz_tariff(sub_type)
        except Exception as e:
            logger.warning(f"Error checking subscription for main menu: {e}")

    if is_biz_user:
        return _get_biz_main_menu_keyboard(language)

    buttons = []

    # === ПЕРВАЯ КНОПКА: 3 состояния ===
    if has_active_sub:
        # Состояние 2: Активная подписка → "Подключиться" (ведёт на экран инструкции)
        buttons.append([InlineKeyboardButton(
            text="📲 Подключиться",
            callback_data="connect_instruction",
        )])
    elif telegram_id and database.DB_READY:
        # Проверяем trial
        trial_available = False
        try:
            trial_available = await trial_service.is_trial_available(telegram_id)
        except Exception as e:
            logger.warning(f"Error checking trial availability for user {telegram_id}: {e}")

        if trial_available:
            # Состояние 1: Новый пользователь → "Пробный период"
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "trial.button"),
                callback_data="activate_trial"
            )])
        else:
            # Проверяем спецпредложение для истекших подписок
            offer_shown = False
            try:
                special_offer = await database.get_special_offer_info(telegram_id)
                if special_offer:
                    # Состояние 3: Спецпредложение -15% с таймером
                    remaining = special_offer["remaining_text"]
                    buttons.append([InlineKeyboardButton(
                        text=f"🔥 Спецпредложение -15% | ⏳ {remaining}",
                        callback_data="special_offer_buy"
                    )])
                    offer_shown = True
            except Exception as e:
                logger.warning(f"Error checking special offer for user {telegram_id}: {e}")

            if not offer_shown:
                # Предложение истекло или отсутствует — кнопка «Купить подписку»
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(language, "main.buy_new"),
                    callback_data="menu_buy_vpn"
                )])

    # Traffic button removed — traffic info is now in profile screen

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.profile"),
        callback_data="menu_profile"
    )])
    # Динамическая кнопка покупки + подарить подписку в одном ряду
    if subscription and subscription.get("subscription_type"):
        buy_text = i18n_get_text(language, "main.buy_renew")
    elif telegram_id and database.DB_READY and not subscription:
        buy_text = i18n_get_text(language, "main.buy_new")
    else:
        buy_text = i18n_get_text(language, "main.buy_new")
    buttons.append([
        InlineKeyboardButton(text=buy_text, callback_data="menu_buy_vpn"),
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.gift_subscription", "🎁 Подарить"),
            callback_data="gift_subscription"
        ),
    ])
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.instruction"),
            web_app=WebAppInfo(url=f"{MINI_APP_URL}?startapp=guide"),
        ),
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.game_club", "🎮 Игровой клуб"),
            callback_data="games_menu"
        ),
    ])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.referral"),
        callback_data="menu_referral"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "premium.main_button"),
        callback_data="premium_buy"
    )])
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "main.help"),
            url="https://t.me/Atlas_SupportSecurity"
        ),
    ])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.settings", "main.settings"),
        callback_data="menu_settings"
    )])

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
            url="https://t.me/Atlas_SupportSecurity"
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
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_copy_login"),
            callback_data="biz_copy_login"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_copy_password"),
            callback_data="biz_copy_password"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "biz.btn_personal_manager"),
            url="https://t.me/Atlas_SupportSecurity"
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
):
    """Личный кабинет: Купить ГБ + Продлить | Автопродление + Пополнить | Подарки | Назад."""
    buttons = []

    # Row 1: Купить ГБ + Продлить/Купить подписку
    row1 = []
    if show_traffic and not is_trial:
        row1.append(InlineKeyboardButton(
            text="🌐 Купить ГБ",
            callback_data="buy_traffic",
        ))
    buy_text = i18n_get_text(language, "main.buy_renew") if has_active_subscription else i18n_get_text(language, "main.buy_new")
    row1.append(InlineKeyboardButton(text=buy_text, callback_data="menu_buy_vpn"))
    buttons.append(row1)

    # Row 2: Автопродление + Пополнить
    row2 = []
    if has_active_subscription:
        ar_text = "🔄 Автопродление ✅" if auto_renew else "🔄 Автопродление"
        ar_data = "toggle_auto_renew:off" if auto_renew else "toggle_auto_renew:on"
        row2.append(InlineKeyboardButton(text=ar_text, callback_data=ar_data))
    row2.append(InlineKeyboardButton(text="💳 Пополнить", callback_data="topup_balance"))
    buttons.append(row2)

    # Row 3: Мои подарки
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "gift.my_gifts_btn", "🎁 Мои подарки"),
        callback_data="my_gifts:0"
    )])

    # Row 4: Назад
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back", "← Назад"),
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


def get_vpn_key_keyboard(
    language: str,
    subscription_type: str = "basic",
    vpn_key: Optional[str] = None,
):
    """Клавиатура после активации/оплаты: Подключиться (WebApp) + Профиль."""
    return get_connect_keyboard()


def get_payment_success_keyboard(
    language: str,
    subscription_type: str = "basic",
    is_renewal: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура после успешной оплаты: Подключиться + Профиль + Трафик."""
    buttons = [
        [InlineKeyboardButton(
            text="📲 Подключиться",
            callback_data="connect_instruction",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.profile"),
            callback_data="menu_profile",
        )],
    ]
    if config.REMNAWAVE_ENABLED and subscription_type in ("basic", "plus"):
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.traffic_btn"),
            callback_data="traffic_info",
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
            url="https://t.me/Atlas_SupportSecurity"
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
            callback_data="menu_main"
        )],
    ]
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


