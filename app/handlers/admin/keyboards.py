"""
Admin keyboard builders. Shared across admin handlers.
"""
from datetime import datetime
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.i18n import get_text as i18n_get_text


def get_admin_dashboard_keyboard(language: str = "ru"):
    """Клавиатура главного экрана админ-дашборда (сгруппирована по категориям)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        # — Обзор —
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.dashboard"), callback_data="admin:dashboard")],
        # — Аналитика и статистика —
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.stats"), callback_data="admin:stats"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.analytics"), callback_data="admin:analytics"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.metrics"), callback_data="admin:metrics"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_stats"), callback_data="admin:referral_stats"),
        ],
        # — Управление пользователями —
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.user"), callback_data="admin:user"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.balance_management"), callback_data="admin:balance_management"),
        ],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.keys"), callback_data="admin:keys")],
        # — Маркетинг и уведомления —
        [InlineKeyboardButton(text="📣 Центр уведомлений", callback_data="admin:notifications")],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.create_promocode"), callback_data="admin:create_promocode"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.promo_stats"), callback_data="admin_promo_stats"),
        ],
        # — Система —
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.system"), callback_data="admin:system"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.audit"), callback_data="admin:audit"),
        ],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.export"), callback_data="admin:export"),
            InlineKeyboardButton(text="🌐 QoDev", callback_data="admin:qodev"),
        ],
        [InlineKeyboardButton(text="💬 Написать пользователю", callback_data="admin:chat")],
        [InlineKeyboardButton(text="🎁 Гифт-ссылки на ГБ", callback_data="admin:bgift")],
    ])
    return keyboard


def get_admin_bypass_gift_menu_keyboard(language: str = "ru"):
    """Главное меню раздела «Гифт-ссылки на ГБ»."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать ссылку", callback_data="admin:bgift_create")],
        [InlineKeyboardButton(text="📋 Все ссылки и статистика", callback_data="admin:bgift_list:0")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])


def get_admin_bypass_gift_validity_keyboard(language: str = "ru"):
    """Шаг 1 — выбор срока действия ссылки (дни)."""
    days_options = [1, 3, 5, 7, 10, 14]
    rows = []
    row = []
    for d in days_options:
        row.append(InlineKeyboardButton(
            text=f"{d} дн.",
            callback_data=f"admin:bgift_validity:{d}",
        ))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="admin:bgift_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_bypass_gift_gb_keyboard(language: str = "ru"):
    """Шаг 2 — выбор количества ГБ (preset + custom)."""
    gb_options = [1, 3, 5, 10, 20, 50]
    rows = []
    row = []
    for gb in gb_options:
        row.append(InlineKeyboardButton(
            text=f"{gb} ГБ",
            callback_data=f"admin:bgift_gb:{gb}",
        ))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✏️ Указать вручную", callback_data="admin:bgift_gb_custom")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="admin:bgift_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_bypass_gift_max_uses_keyboard(language: str = "ru"):
    """Шаг 3 — выбор максимального числа активаций."""
    use_options = [1, 5, 10, 50, 100]
    rows = []
    row = []
    for u in use_options:
        row.append(InlineKeyboardButton(
            text=f"{u}",
            callback_data=f"admin:bgift_uses:{u}",
        ))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✏️ Указать вручную", callback_data="admin:bgift_uses_custom")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="admin:bgift_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_bypass_gift_confirm_keyboard(language: str = "ru"):
    """Шаг 4 — подтверждение создания."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать ссылку", callback_data="admin:bgift_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:bgift_cancel")],
    ])


def get_admin_bypass_gift_link_actions_keyboard(link_id: int, language: str = "ru"):
    """Действия по конкретной ссылке."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Кто активировал", callback_data=f"admin:bgift_redemptions:{link_id}")],
        [InlineKeyboardButton(text="🗑 Удалить ссылку", callback_data=f"admin:bgift_delete:{link_id}")],
        [InlineKeyboardButton(text="📋 К списку", callback_data="admin:bgift_list:0")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:bgift")],
    ])


def get_admin_bypass_gift_back_keyboard(language: str = "ru"):
    """Возврат в раздел гифт-ссылок."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список ссылок", callback_data="admin:bgift_list:0")],
        [InlineKeyboardButton(text="⬅️ В раздел", callback_data="admin:bgift")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])


def get_admin_bypass_gift_list_keyboard(
    links: list,
    page: int,
    has_next: bool,
    language: str = "ru",
):
    """Постраничная клавиатура списка ссылок: каждая ссылка — отдельная кнопка."""
    rows = []
    for link in links:
        code = link.get("code", "?")
        gb = link.get("gb_amount", 0)
        used = link.get("redemption_count", 0)
        total = link.get("max_uses", 0)
        deleted = link.get("deleted_at") is not None
        prefix = "🗑 " if deleted else ""
        rows.append([InlineKeyboardButton(
            text=f"{prefix}{code} · {gb} ГБ · {used}/{total}",
            callback_data=f"admin:bgift_view:{link.get('id')}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admin:bgift_list:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admin:bgift_list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="➕ Создать", callback_data="admin:bgift_create")])
    rows.append([InlineKeyboardButton(text="⬅️ В раздел", callback_data="admin:bgift")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def get_admin_user_keyboard(has_active_subscription: bool = False, user_id: int = None, has_discount: bool = False, is_vip: bool = False, subscription_type: str = "basic", language: str = "ru"):
    """Клавиатура для раздела пользователя. subscription_type нужен для кнопки «Заменить подписку»."""
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
        # Заменить подписку (Basic↔Plus) только при активной подписке
        sub_type = (subscription_type or "basic").strip().lower()
        if has_active_subscription and sub_type in ("basic", "plus"):
            if sub_type == "basic":
                buttons.append([InlineKeyboardButton(text="⭐️ Перевести на Plus", callback_data=f"admin_switch_plus:{user_id}")])
            else:
                buttons.append([InlineKeyboardButton(text="📦 Перевести на Basic", callback_data=f"admin_switch_basic:{user_id}")])
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
        # Кнопка управления трафиком
        buttons.append([InlineKeyboardButton(text="📊 Трафик обхода", callback_data=f"admin:traffic:{user_id}")])
        # Кнопка выдачи средств
        buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.credit_balance"), callback_data=f"admin:credit_balance:{user_id}")])
        # Кнопка удаления пользователя из БД
        buttons.append([InlineKeyboardButton(text="🗑 Удалить из БД", callback_data=f"admin:delete_user:{user_id}")])
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
        buttons.append([InlineKeyboardButton(text="🗑 Удалить из БД", callback_data=f"admin:delete_user:{user_id}")])
    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)




def get_broadcast_test_type_keyboard(language: str = "ru"):
    """Клавиатура выбора типа тестирования"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._normal"), callback_data="broadcast_test_type:normal")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._ab_test"), callback_data="broadcast_test_type:ab")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")],
    ])
    return keyboard


def get_broadcast_buttons_keyboard(language: str = "ru", selected: list = None):
    """Клавиатура выбора кнопок для уведомления"""
    selected = selected or []
    buttons = [
        ("🛒 Купить", "buy"),
        ("🎁 Купить со скидкой", "promo_buy"),
        ("📊 Купить трафик промо", "promo_traffic"),
        ("🌐 Включить обход", "bypass"),
        ("📢 Наш канал", "channel"),
        ("💬 Поддержка", "support"),
        ("👥 Пригласить друга", "referral"),
        ("📲 Скачать Happ для iOS ⚡️", "happ_ios"),
        ("📲 Скачать Happ для Android 🤖", "happ_android"),
        ("🌐 Веб-клиент QoDev", "web_client"),
        ("🏆 Купить Комбо", "buy_combo"),
    ]
    rows = []
    for label, key in buttons:
        check = "✅ " if key in selected else ""
        rows.append([InlineKeyboardButton(text=f"{check}{label}", callback_data=f"broadcast_btn:{key}")])
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="broadcast_btn:done")])
    rows.append([InlineKeyboardButton(text="⏭ Без кнопок", callback_data="broadcast_btn:none")])
    rows.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:broadcast")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_broadcast_segment_keyboard(language: str = "ru"):
    """Клавиатура выбора сегмента получателей"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._segment_all"), callback_data="broadcast_segment:all_users")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._segment_active"), callback_data="broadcast_segment:active_subscriptions")],
        [InlineKeyboardButton(text="🚫 Без подписки", callback_data="broadcast_segment:no_subscription")],
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
