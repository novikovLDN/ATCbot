"""Подписки — точка входа. Реализация разложена по соседям.

ЧТО ЗДЕСЬ
    Ничего, кроме реэкспорта. Файл оставлен потому, что через
    `database.subscriptions` к подпискам обращаются database/__init__.py,
    admin_access, balance_purchases, discounts, gift_subscriptions,
    referral_reward, сервисный слой оплат и полтора десятка тестов.
    Переписывать все эти импорты — отдельная работа с отдельной проверкой.

ГДЕ ЧТО ЛЕЖИТ
    database/subscription_audit.py     журнал: аудит, история, сигнал сторожу
    database/subscription_queries.py   чтения по подпискам и платежам
    database/subscription_state.py     истечение, флаги, тариф, подмена uuid
    database/subscription_reissue.py   перевыпуск ключа
    database/subscription_grant.py     выдача и продление доступа
    database/subscription_pricing.py   расчёт цены со скидками
    database/purchase_finalization.py  проведение оплаты и выдача товара

    Разложено так, потому что в одном файле на 3367 строк лежало семь
    вещей, которые правят по разным поводам. Две функции — grant_access и
    _finalize_purchase_locked — занимали половину файла вдвоём.

ПЕРЕЕХАЛО РАНЬШЕ, реэкспортируется отсюда же
    database/promo.py              промокоды
    database/pending_purchases.py  учёт намерения оплатить
    database/trials_queries.py     триал и спецпредложения
    database/reminders_queries.py  напоминания об истечении
    database/referral_analytics.py отчёты по рефералам

ЧТО ЛЕГКО СЛОМАТЬ
    Список ниже дублируется в database/__init__.py. Уберёшь отсюда имя,
    которое там перечислено, — импорт пакета упадёт при старте бота.
    Уберёшь имя, которое там НЕ перечислено (их тут много), — упадёт не
    импорт, а первый же вызов: на живом платеже или на выдаче доступа.

    Хелперы из database.core (get_pool, _to_db_utc и прочие) проходят
    сквозь этот файл намеренно: код и тесты годами брали их отсюда.

    Патчить в тестах надо модуль, где функция ОПРЕДЕЛЕНА, а не этот файл.
    Подмена database.subscriptions.get_pool больше ни на что не влияет:
    реализация берёт зависимости из своего пространства имён, и тест
    молча пройдёт мимо проверяемого кода.
"""
# Хелперы работы с БД: сюда за ними ходит существующий код.
from database.core import (  # noqa: F401
    get_pool,
    _to_db_utc,
    _from_db_utc,
    _ensure_utc,
    _normalize_subscription_row,
    _generate_subscription_uuid,
    safe_int,
    mark_payment_notification_sent,
    retry_async,
)

# Журнал жизненного цикла: аудит, история подписок, сигнал сторожу.
from database.subscription_audit import (  # noqa: F401
    _notify_watchdog_expires_at,
    _log_audit_event_atomic,
    _log_audit_event_atomic_standalone,
    _log_vpn_lifecycle_audit_async,
    _log_vpn_lifecycle_audit_fire_and_forget,
    _log_subscription_history_atomic,
)

# Точечные операции над строкой подписки.
from database.subscription_state import (  # noqa: F401
    check_and_disable_expired_subscription,
    ensure_bypass_only_subscription,
    set_combo_flag,
    set_bypass_only_flag,
    admin_switch_tariff,
    update_subscription_uuid,
)

# Чтения по подпискам и платежам.
from database.subscription_queries import (  # noqa: F401
    get_payment,
    get_last_approved_payment,
    get_pending_payments,
    get_subscription,
    get_subscription_any,
    get_active_subscription,
    get_all_active_subscriptions,
    has_any_subscription,
    has_any_payment,
    is_user_first_purchase,
    get_admin_stats,
)

# Перевыпуск ключа.
from database.subscription_reissue import (  # noqa: F401
    reissue_subscription_key,
    reissue_vpn_key_atomic,
)

# Расчёт цены со скидками.
from database.subscription_pricing import (  # noqa: F401
    calculate_final_price,
    _calculate_subscription_days,
)

# Выдача и продление доступа.
from database.subscription_grant import grant_access  # noqa: F401

# Проведение оплаты и выдача товара. Доменные исключения тоже отсюда:
# по ним сервисный слой отличает повторный вебхук от расхождения суммы.
from database.purchase_finalization import (  # noqa: F401
    finalize_purchase,
    _finalize_purchase_locked,
    _publish_payment_approved,
    PaymentAlreadyProcessed,
    PaymentAmountMismatch,
    PurchaseLocked,
    PurchaseInvalidStatus,
)

# Отложенные покупки — учёт намерения оплатить.
from database.pending_purchases import (  # noqa: F401
    create_pending_balance_topup_purchase,
    create_pending_purchase,
    get_pending_purchase,
    get_pending_purchase_by_id,
    cancel_pending_purchases,
    update_pending_purchase_invoice_id,
    mark_pending_purchase_paid,
    has_purchased_proxy,
    mark_proxy_purchased,
    PURCHASE_TYPES,
    TARIFF_VALUES,
    TARIFF_PREFIXES,
    _PURCHASE_TYPES_SQL,
    _TARIFF_VALUES_SQL,
    _TARIFF_PREFIXES_SQL,
)

# Триалы и спецпредложения.
from database.trials_queries import (  # noqa: F401
    has_trial_used,
    get_trial_info,
    get_active_paid_subscription,
    mark_trial_used,
    is_eligible_for_trial,
    is_trial_available,
    set_special_offer,
    get_special_offer_info,
    has_active_special_offer,
)

# Напоминания об истечении.
#
# ОСТОРОЖНО с первыми двумя именами — это legacy, их не вызывает никто:
#   • mark_reminder_sent(telegram_id) выставляет старый флаг
#     subscriptions.reminder_sent, который планировщик напоминаний не читает.
#     Живая отметка — app/services/notifications/service.py:mark_reminder_sent
#     (telegram_id, reminder_type, conn), её зовёт reminders.py; имена
#     совпадают, сигнатуры разные, перепутать легко и молча.
#   • get_subscriptions_needing_reminder — выборка по тому же старому флагу.
#     Живая выборка — get_subscriptions_for_reminders (окна 7д/3д/1д/3ч
#     + админские, флаги reminder_*_sent).
# Позвать legacy-пару вместо живой = повторная отправка напоминания
# пользователю либо, наоборот, молчание. Колонка reminder_sent остаётся в
# схеме и сбрасывается при каждой выдаче доступа (см. subscription_grant) —
# снос колонки это миграция схемы, решение владельца, а не правка кода.
from database.reminders_queries import (  # noqa: F401
    get_subscriptions_needing_reminder,
    mark_reminder_sent,
    mark_reminder_flag_sent,
    mark_user_unreachable,
    update_last_reminder_at,
    get_subscriptions_for_reminders,
)

# Реферальная аналитика.
from database.referral_analytics import (  # noqa: F401
    get_admin_referral_stats,
    get_admin_referral_detail,
    get_referral_overall_stats,
    get_referral_rewards_history,
    get_referral_rewards_history_count,
)

# Промокоды.
from database.promo import (  # noqa: F401
    get_promo_code,
    get_active_promo_by_code,
    has_active_promo,
    check_promo_code_valid,
    log_promo_code_usage,
    get_promo_stats,
    generate_promo_code,
    create_promocode_atomic,
    deactivate_promocode,
    reactivate_promocode,
    _consume_promo_in_transaction,
    validate_promocode_atomic,
    consume_promocode_atomic,
    _ACTIVE_PROMO_WHERE,
)

__all__ = [
    # database.core
    "get_pool",
    "_to_db_utc",
    "_from_db_utc",
    "_ensure_utc",
    "_normalize_subscription_row",
    "_generate_subscription_uuid",
    "safe_int",
    "mark_payment_notification_sent",
    "retry_async",
    # subscription_audit
    "_notify_watchdog_expires_at",
    "_log_audit_event_atomic",
    "_log_audit_event_atomic_standalone",
    "_log_vpn_lifecycle_audit_async",
    "_log_vpn_lifecycle_audit_fire_and_forget",
    "_log_subscription_history_atomic",
    # subscription_state
    "check_and_disable_expired_subscription",
    "ensure_bypass_only_subscription",
    "set_combo_flag",
    "set_bypass_only_flag",
    "admin_switch_tariff",
    "update_subscription_uuid",
    # subscription_queries
    "get_payment",
    "get_last_approved_payment",
    "get_pending_payments",
    "get_subscription",
    "get_subscription_any",
    "get_active_subscription",
    "get_all_active_subscriptions",
    "has_any_subscription",
    "has_any_payment",
    "is_user_first_purchase",
    "get_admin_stats",
    # subscription_reissue
    "reissue_subscription_key",
    "reissue_vpn_key_atomic",
    # subscription_pricing
    "calculate_final_price",
    "_calculate_subscription_days",
    # subscription_grant
    "grant_access",
    # purchase_finalization
    "finalize_purchase",
    "_finalize_purchase_locked",
    "_publish_payment_approved",
    "PaymentAlreadyProcessed",
    "PaymentAmountMismatch",
    "PurchaseLocked",
    "PurchaseInvalidStatus",
    # pending_purchases
    "create_pending_balance_topup_purchase",
    "create_pending_purchase",
    "get_pending_purchase",
    "get_pending_purchase_by_id",
    "cancel_pending_purchases",
    "update_pending_purchase_invoice_id",
    "mark_pending_purchase_paid",
    "has_purchased_proxy",
    "mark_proxy_purchased",
    "PURCHASE_TYPES",
    "TARIFF_VALUES",
    "TARIFF_PREFIXES",
    "_PURCHASE_TYPES_SQL",
    "_TARIFF_VALUES_SQL",
    "_TARIFF_PREFIXES_SQL",
    # trials_queries
    "has_trial_used",
    "get_trial_info",
    "get_active_paid_subscription",
    "mark_trial_used",
    "is_eligible_for_trial",
    "is_trial_available",
    "set_special_offer",
    "get_special_offer_info",
    "has_active_special_offer",
    # reminders_queries
    "get_subscriptions_needing_reminder",
    "mark_reminder_sent",
    "mark_reminder_flag_sent",
    "mark_user_unreachable",
    "update_last_reminder_at",
    "get_subscriptions_for_reminders",
    # referral_analytics
    "get_admin_referral_stats",
    "get_admin_referral_detail",
    "get_referral_overall_stats",
    "get_referral_rewards_history",
    "get_referral_rewards_history_count",
    # promo
    "get_promo_code",
    "get_active_promo_by_code",
    "has_active_promo",
    "check_promo_code_valid",
    "log_promo_code_usage",
    "get_promo_stats",
    "generate_promo_code",
    "create_promocode_atomic",
    "deactivate_promocode",
    "reactivate_promocode",
    "_consume_promo_in_transaction",
    "validate_promocode_atomic",
    "consume_promocode_atomic",
    "_ACTIVE_PROMO_WHERE",
]
