"""
Admin keyboard builders. Shared across admin handlers.
"""
from datetime import datetime
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.i18n import get_text as i18n_get_text


def get_admin_dashboard_keyboard(language: str = "ru"):
    """Клавиатура главного экрана админ-дашборда"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
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
    return keyboard


def get_admin_back_keyboard(language: str = "ru"):
    """Клавиатура с кнопкой 'Назад' для админ-разделов"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    return keyboard


def get_admin_export_keyboard(language: str = "ru"):
    """Клавиатура выбора типа экспорта"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.export_users"), callback_data="admin:export:users")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.export_subscriptions"), callback_data="admin:export:subscriptions")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    return keyboard


def get_admin_user_keyboard(has_active_subscription: bool = False, user_id: int = None, has_discount: bool = False, is_vip: bool = False, language: str = "ru"):
    """Клавиатура для раздела пользователя"""
    buttons = []
    if has_active_subscription:
        callback_data = f"admin:user_reissue:{user_id}" if user_id else "admin:user_reissue"
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.reissue_key"), callback_data=callback_data)])
    if user_id:
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.subscription_history"), callback_data=f"admin:user_history:{user_id}")])
        # Кнопки выдачи доступа (Basic / Plus) и лишения доступа
        buttons.append([
            InlineKeyboardButton(text="📦 Выдать Basic", callback_data=f"admin_grant_basic:{user_id}"),
            InlineKeyboardButton(text="⭐️ Выдать Plus", callback_data=f"admin_grant_plus:{user_id}"),
        ])
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_access"), callback_data=f"admin:revoke:user:{user_id}")])
        # Кнопки управления скидками
        if has_discount:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.delete_discount"), callback_data=f"admin:discount_delete:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.create_discount"), callback_data=f"admin:discount_create:{user_id}")])
        # Кнопки управления VIP-статусом
        if is_vip:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_vip"), callback_data=f"admin:vip_revoke:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_vip"), callback_data=f"admin:vip_grant:{user_id}")])
        # Кнопка выдачи средств
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.credit_balance"), callback_data=f"admin:credit_balance:{user_id}")])
    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard


def get_admin_user_keyboard_processing(user_id: int, has_discount: bool = False, is_vip: bool = False, language: str = "ru"):
    """Клавиатура во время перевыпуска ключа: кнопка «Перевыпуск» заменена на disabled состояние (callback_data=noop)"""
    buttons = []
    buttons.append([InlineKeyboardButton(text="⏳ Перевыпуск...", callback_data="noop")])
    if user_id:
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.subscription_history"), callback_data=f"admin:user_history:{user_id}")])
        buttons.append([
            InlineKeyboardButton(text="📦 Выдать Basic", callback_data=f"admin_grant_basic:{user_id}"),
            InlineKeyboardButton(text="⭐️ Выдать Plus", callback_data=f"admin_grant_plus:{user_id}"),
        ])
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.revoke_access"), callback_data=f"admin:revoke:user:{user_id}")])
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
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
    return keyboard


def get_broadcast_test_type_keyboard(language: str = "ru"):
    """Клавиатура выбора типа тестирования"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._normal"), callback_data="broadcast_test_type:normal")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._ab_test"), callback_data="broadcast_test_type:ab")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])
    return keyboard


def get_broadcast_type_keyboard(language: str = "ru"):
    """Клавиатура выбора типа уведомления"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_info"), callback_data="broadcast_type:info")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_maintenance"), callback_data="broadcast_type:maintenance")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_security"), callback_data="broadcast_type:security")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._type_promo"), callback_data="broadcast_type:promo")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])
    return keyboard


def get_broadcast_segment_keyboard(language: str = "ru"):
    """Клавиатура выбора сегмента получателей"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._segment_all"), callback_data="broadcast_segment:all_users")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._segment_active"), callback_data="broadcast_segment:active_subscriptions")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])
    return keyboard


def get_broadcast_confirm_keyboard(language: str = "ru"):
    """Клавиатура подтверждения отправки уведомления"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._confirm_send"), callback_data="broadcast:confirm_send")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])
    return keyboard


def get_ab_test_list_keyboard(ab_tests: list, language: str = "ru") -> InlineKeyboardMarkup:
    """Клавиатура списка A/B тестов"""
    buttons = []
    for test in ab_tests[:20]:  # Ограничиваем 20 тестами
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


def get_admin_grant_flex_unit_keyboard(language: str = "ru"):
    """Клавиатура выбора единицы срока для выдачи Basic/Plus (гибкий срок)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏱ Минуты", callback_data="admin:grant_flex_unit:minutes"),
            InlineKeyboardButton(text="🕐 Часы", callback_data="admin:grant_flex_unit:hours"),
        ],
        [
            InlineKeyboardButton(text="📅 Дни", callback_data="admin:grant_flex_unit:days"),
            InlineKeyboardButton(text="🗓 Месяцы", callback_data="admin:grant_flex_unit:months"),
        ],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:grant_flex_cancel")],
    ])


def get_admin_grant_flex_confirm_keyboard(language: str = "ru"):
    """Клавиатура подтверждения выдачи доступа (гибкий срок)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="admin:grant_flex_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin:grant_flex_cancel"),
        ],
    ])


def get_admin_grant_flex_notify_keyboard(language: str = "ru"):
    """Клавиатура выбора: уведомить пользователя о выдаче доступа или нет."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, уведомить", callback_data="admin:grant_flex_notify:yes")],
        [InlineKeyboardButton(text="🔕 Нет, тихо", callback_data="admin:grant_flex_notify:no")],
    ])


def get_admin_grant_days_keyboard(user_id: int, language: str = "ru"):
    """
    5. ADVANCED ACCESS CONTROL (GRANT / REVOKE)
    
    Keyboard for selecting access duration with quick options and custom duration.
    """
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_days_1"), callback_data=f"admin:grant_days:{user_id}:1"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_days_7"), callback_data=f"admin:grant_days:{user_id}:7"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_days_14"), callback_data=f"admin:grant_days:{user_id}:14"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_1_year"), callback_data=f"admin:grant_1_year:{user_id}"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_minutes_10"), callback_data=f"admin:grant_minutes:{user_id}:10"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_custom"), callback_data=f"admin:grant_custom:{user_id}"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:user"),
        ]
    ])
    return keyboard


def get_admin_discount_percent_keyboard(user_id: int, language: str = "ru"):
    """Клавиатура для выбора процента скидки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="10%", callback_data=f"admin:discount_percent:{user_id}:10"),
            InlineKeyboardButton(text="15%", callback_data=f"admin:discount_percent:{user_id}:15"),
        ],
        [
            InlineKeyboardButton(text="25%", callback_data=f"admin:discount_percent:{user_id}:25"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.discount_manual"), callback_data=f"admin:discount_percent_manual:{user_id}"),
        ],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    return keyboard


def get_admin_discount_expires_keyboard(user_id: int, discount_percent: int, language: str = "ru"):
    """Клавиатура для выбора срока действия скидки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.discount_expires_7"), callback_data=f"admin:discount_expires:{user_id}:{discount_percent}:7"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.discount_expires_30"), callback_data=f"admin:discount_expires:{user_id}:{discount_percent}:30"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.discount_expires_unlimited"), callback_data=f"admin:discount_expires:{user_id}:{discount_percent}:0"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.discount_manual"), callback_data=f"admin:discount_expires_manual:{user_id}:{discount_percent}"),
        ],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    return keyboard
