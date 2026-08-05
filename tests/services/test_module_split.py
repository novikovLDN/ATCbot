"""Совместимость после разбивки database/subscriptions.py.

Файл разросся до 5162 строк и стал неподдерживаемым. Группы функций
выносятся в отдельные модули, но весь существующий код годами обращался
к ним через database.<name> и database.subscriptions.<name> — оба пути
обязаны продолжать работать.

Вторая волна довела файл с 3367 строк до фасада: выдача, оплата,
перевыпуск, цена, чтения, состояние и журнал разъехались по семи модулям
(см. SUBSCRIPTION_HOMES ниже).

ЗАЧЕМ ЭТОТ ФАЙЛ ВООБЩЕ
    Потерянный реэкспорт не роняет импорт. Он роняет вызов — на живом
    платеже или на выдаче доступа, через сутки после релиза. Здесь
    закреплено, что каждое имя доступно и по старому пути, и по новому,
    и что это ОДИН И ТОТ ЖЕ объект, а не копия.
"""
import pytest

PROMO_NAMES = [
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
]


@pytest.mark.parametrize("name", PROMO_NAMES)
def test_available_via_package(name):
    import database
    assert hasattr(database, name), f"database.{name} исчез после разбивки"


@pytest.mark.parametrize("name", PROMO_NAMES)
def test_available_via_subscriptions(name):
    import database.subscriptions as subs
    assert hasattr(subs, name), f"database.subscriptions.{name} исчез после разбивки"


REFERRAL_NAMES = [
    "get_admin_referral_stats",
    "get_admin_referral_detail",
    "get_referral_overall_stats",
    "get_referral_rewards_history",
    "get_referral_rewards_history_count",
]


@pytest.mark.parametrize("name", REFERRAL_NAMES)
def test_referral_available_via_package(name):
    import database
    assert hasattr(database, name), f"database.{name} исчез после разбивки"


@pytest.mark.parametrize("name", REFERRAL_NAMES)
def test_referral_available_via_subscriptions(name):
    import database.subscriptions as subs
    assert hasattr(subs, name)


@pytest.mark.parametrize("name", REFERRAL_NAMES)
def test_referral_defined_in_its_module(name):
    import database.referral_analytics as ra
    assert hasattr(ra, name)


@pytest.mark.parametrize("name", PROMO_NAMES)
def test_defined_in_promo_module(name):
    import database.promo as promo
    assert hasattr(promo, name)


REMINDER_NAMES = [
    "get_subscriptions_needing_reminder",
    "mark_reminder_sent",
    "mark_reminder_flag_sent",
    "mark_user_unreachable",
    "update_last_reminder_at",
    "get_subscriptions_for_reminders",
]


@pytest.mark.parametrize("name", REMINDER_NAMES)
def test_reminder_available_via_package(name):
    import database
    assert hasattr(database, name)


@pytest.mark.parametrize("name", REMINDER_NAMES)
def test_reminder_available_via_subscriptions(name):
    import database.subscriptions as subs
    assert hasattr(subs, name)


@pytest.mark.parametrize("name", REMINDER_NAMES)
def test_reminder_defined_in_its_module(name):
    import database.reminders_queries as rq
    assert hasattr(rq, name)


TRIAL_NAMES = [
    "has_trial_used",
    "get_trial_info",
    "get_active_paid_subscription",
    "mark_trial_used",
    "is_eligible_for_trial",
    "is_trial_available",
    "set_special_offer",
    "get_special_offer_info",
    "has_active_special_offer",
]


@pytest.mark.parametrize("name", TRIAL_NAMES)
def test_trial_available_via_package(name):
    import database
    assert hasattr(database, name)


@pytest.mark.parametrize("name", TRIAL_NAMES)
def test_trial_available_via_subscriptions(name):
    import database.subscriptions as subs
    assert hasattr(subs, name)


@pytest.mark.parametrize("name", TRIAL_NAMES)
def test_trial_defined_in_its_module(name):
    import database.trials_queries as tq
    assert hasattr(tq, name)


PENDING_NAMES = [
    "create_pending_balance_topup_purchase",
    "create_pending_purchase",
    "get_pending_purchase",
    "get_pending_purchase_by_id",
    "cancel_pending_purchases",
    "update_pending_purchase_invoice_id",
    "mark_pending_purchase_paid",
    "has_purchased_proxy",
    "mark_proxy_purchased",
]


@pytest.mark.parametrize("name", PENDING_NAMES)
def test_pending_available_via_package(name):
    import database
    assert hasattr(database, name)


@pytest.mark.parametrize("name", PENDING_NAMES)
def test_pending_available_via_subscriptions(name):
    import database.subscriptions as subs
    assert hasattr(subs, name)


def test_finalize_purchase_stays_with_transactions():
    """finalize_purchase проводит деньги и выдаёт товар — она относится
    к транзакционной части и не должна была уехать вместе с учётом."""
    import database.subscriptions as subs
    import database.pending_purchases as pp
    assert hasattr(subs, "finalize_purchase")
    assert not hasattr(pp, "finalize_purchase")


def test_purchase_type_constants_moved_with_their_queries():
    """Перечни типов задают CHECK-констрейнты таблицы pending_purchases,
    поэтому живут рядом с запросами к ней."""
    import database.pending_purchases as pp
    assert "steam" in pp.PURCHASE_TYPES
    assert "farm_effect" in pp.PURCHASE_TYPES
    import database.subscriptions as subs
    assert subs.PURCHASE_TYPES is pp.PURCHASE_TYPES


BROADCAST_NAMES = [
    "create_broadcast",
    "get_broadcast",
    "get_users_by_segment",
    "log_broadcast_send",
    "get_broadcast_stats",
    "get_broadcast_analytics",
    "get_recent_broadcasts",
    "get_ab_test_broadcasts",
    "get_ab_test_stats",
    "set_incident_mode",
    "get_incident_settings",
]


@pytest.mark.parametrize("name", BROADCAST_NAMES)
def test_broadcast_available_via_package(name):
    import database
    assert hasattr(database, name)


@pytest.mark.parametrize("name", BROADCAST_NAMES)
def test_broadcast_available_via_admin(name):
    """Код годами обращался к рассылкам через database.admin."""
    import database.admin as adm
    assert hasattr(adm, name)


@pytest.mark.parametrize("name", BROADCAST_NAMES)
def test_broadcast_defined_in_its_module(name):
    import database.broadcasts as br
    assert hasattr(br, name)


def test_money_functions_live_in_their_own_modules():
    """Денежные операции лежат там, где задумано, и не растекаются.

    finalize_balance_* вынесены в database/balance_purchases.py: это две
    самые длинные функции админского слоя и единственные, где списываются
    и зачисляются деньги, — их правят по другим причинам, чем отчёты и
    рассылки. Админские операции над доступом остаются в admin.py.
    """
    import database
    import database.admin as adm
    import database.balance_purchases as bal
    import database.broadcasts as br

    for name in ("finalize_balance_purchase", "finalize_balance_topup"):
        assert hasattr(bal, name), f"{name} потерялась из balance_purchases"
        assert not hasattr(br, name), f"{name} ошибочно уехала в broadcasts"
        assert hasattr(database, name), f"{name} перестала реэкспортироваться"

    for name in ("admin_grant_access_atomic", "admin_revoke_access_atomic"):
        assert hasattr(adm, name), f"{name} потерялась из admin"
        assert not hasattr(br, name), f"{name} ошибочно уехала в broadcasts"


ANALYTICS_NAMES = [
    "get_business_metrics",
    "get_analytics_by_period",
    "get_revenue_for_period",
    "get_payments_breakdown",
    "get_recent_payments_feed",
    "get_user_purchases",
    "get_traffic_stats",
    "get_purchase_breakdown",
    "get_extended_bot_stats",
    "get_total_revenue",
    "get_paying_users_count",
    "get_user_ltv",
    "get_average_ltv",
    "get_arpu",
]


@pytest.mark.parametrize("name", ANALYTICS_NAMES)
def test_analytics_available_via_package(name):
    import database
    assert hasattr(database, name)


@pytest.mark.parametrize("name", ANALYTICS_NAMES)
def test_analytics_available_via_admin(name):
    import database.admin as adm
    assert hasattr(adm, name)


@pytest.mark.parametrize("name", ANALYTICS_NAMES)
def test_analytics_defined_in_its_module(name):
    import database.analytics as an
    assert hasattr(an, name)


def test_analytics_module_is_read_only():
    """Отчётный слой не должен содержать операций записи: если сюда
    просочится UPDATE или ALTER, значит граница проведена неверно.
    Журнал ошибок оплаты — единственное исключение, он пишет свою таблицу."""
    import inspect
    import re
    import database.analytics as an
    src = inspect.getsource(an)
    without_error_log = re.sub(
        r"async def log_payment_error.*?(?=\nasync def |\Z)", "", src, flags=re.S
    )
    for verb in ("UPDATE ", "DELETE FROM", "ALTER TABLE"):
        assert verb not in without_error_log.upper(), (
            f"в аналитике найдена операция записи: {verb}"
        )


DISCOUNT_NAMES = [
    "get_user_discount",
    "create_user_discount",
    "has_claimed_referral_share_discount",
    "record_referral_share_discount_claim",
    "delete_user_discount",
    "is_vip_user",
    "grant_vip_status",
    "revoke_vip_status",
]


@pytest.mark.parametrize("name", DISCOUNT_NAMES)
def test_discount_available_via_package(name):
    import database
    assert hasattr(database, name)


@pytest.mark.parametrize("name", DISCOUNT_NAMES)
def test_discount_available_via_admin(name):
    import database.admin as adm
    assert hasattr(adm, name)


@pytest.mark.parametrize("name", DISCOUNT_NAMES)
def test_discount_defined_in_its_module(name):
    import database.discounts as d
    assert hasattr(d, name)


def test_is_vip_user_accepts_external_connection():
    """is_vip_user вызывают изнутри чужих транзакций — параметр conn
    обязан сохраниться, иначе чтение уйдёт мимо транзакции."""
    import inspect
    from database.discounts import is_vip_user
    assert "conn" in inspect.signature(is_vip_user).parameters


REFERRAL_NAMES = [
    "generate_referral_code",
    "create_user",
    "get_user_referral_code",
    "find_user_by_referral_code",
    "register_referral",
    "mark_referral_active",
    "get_referral_stats",
    "get_referral_cashback_percent",
    "get_effective_cashback_percent",
    "calculate_referral_percent",
    "get_referral_level_info",
    "get_total_cashback_earned",
    "calculate_referral_level",
    "process_referral_reward",
]


@pytest.mark.parametrize("name", REFERRAL_NAMES)
def test_referrals_available_via_package(name):
    import database
    assert hasattr(database, name)


@pytest.mark.parametrize("name", REFERRAL_NAMES)
def test_referrals_available_via_users(name):
    """Код годами обращался к рефералке через database.users."""
    import database.users as u
    assert hasattr(u, name)


def test_referral_code_is_deterministic_and_nonempty():
    """Перенос не должен был изменить сам алгоритм генерации кода:
    иначе у существующих пользователей поменялись бы их коды."""
    from database.referrals import generate_referral_code
    first = generate_referral_code(12345)
    assert first and generate_referral_code(12345) == first
    assert generate_referral_code(54321) != first


def test_split_modules_import_cleanly():
    """Каждый выделенный модуль обязан импортироваться сам по себе:
    потерянный import внутри переноса иначе всплывёт только в проде."""
    import importlib
    for mod in ("database.promo", "database.referral_analytics",
                "database.reminders_queries", "database.trials_queries",
                "database.pending_purchases", "database.broadcasts",
                "database.analytics", "database.discounts",
                "database.referrals",
                "database.subscription_audit", "database.subscription_queries",
                "database.subscription_state", "database.subscription_reissue",
                "database.subscription_grant", "database.subscription_pricing",
                "database.purchase_finalization"):
        importlib.import_module(mod)


# ── Разбивка database/subscriptions.py (было 3367 строк) ────────────────
#
# Каждая пара «имя → модуль» ниже — это граница, проведённая осознанно.
# Тест ловит не опечатку, а тихую потерю реэкспорта: функция исчезает из
# фасада, импорт при этом проходит, и падает всё на первом вызове —
# то есть на живом платеже или на выдаче доступа.

SUBSCRIPTION_HOMES = {
    # журнал жизненного цикла — только пишет следы, ничего не решает
    "_notify_watchdog_expires_at": "database.subscription_audit",
    "_log_audit_event_atomic": "database.subscription_audit",
    "_log_audit_event_atomic_standalone": "database.subscription_audit",
    "_log_vpn_lifecycle_audit_async": "database.subscription_audit",
    "_log_vpn_lifecycle_audit_fire_and_forget": "database.subscription_audit",
    "_log_subscription_history_atomic": "database.subscription_audit",
    # чтения
    "get_payment": "database.subscription_queries",
    "get_last_approved_payment": "database.subscription_queries",
    "get_pending_payments": "database.subscription_queries",
    "get_subscription": "database.subscription_queries",
    "get_subscription_any": "database.subscription_queries",
    "get_active_subscription": "database.subscription_queries",
    "get_all_active_subscriptions": "database.subscription_queries",
    "has_any_subscription": "database.subscription_queries",
    "has_any_payment": "database.subscription_queries",
    "is_user_first_purchase": "database.subscription_queries",
    "get_admin_stats": "database.subscription_queries",
    # состояние одной строки подписки
    "check_and_disable_expired_subscription": "database.subscription_state",
    "ensure_bypass_only_subscription": "database.subscription_state",
    "set_combo_flag": "database.subscription_state",
    "set_bypass_only_flag": "database.subscription_state",
    "admin_switch_tariff": "database.subscription_state",
    "update_subscription_uuid": "database.subscription_state",
    # перевыпуск ключа
    "reissue_subscription_key": "database.subscription_reissue",
    "reissue_vpn_key_atomic": "database.subscription_reissue",
    # выдача и продление
    "grant_access": "database.subscription_grant",
    # цена
    "calculate_final_price": "database.subscription_pricing",
    "_calculate_subscription_days": "database.subscription_pricing",
    # проведение оплаты
    "finalize_purchase": "database.purchase_finalization",
    "_finalize_purchase_locked": "database.purchase_finalization",
    "_publish_payment_approved": "database.purchase_finalization",
    "PaymentAlreadyProcessed": "database.purchase_finalization",
    "PaymentAmountMismatch": "database.purchase_finalization",
    "PurchaseLocked": "database.purchase_finalization",
    "PurchaseInvalidStatus": "database.purchase_finalization",
}


@pytest.mark.parametrize("name", sorted(SUBSCRIPTION_HOMES))
def test_subscription_name_available_via_subscriptions(name):
    """Фасад database.subscriptions обязан отдавать всё, что отдавал."""
    import database.subscriptions as subs
    assert hasattr(subs, name), (
        f"database.subscriptions.{name} исчез после разбивки — "
        "упадёт не импорт, а первый вызов"
    )


@pytest.mark.parametrize("name", sorted(SUBSCRIPTION_HOMES))
def test_subscription_name_defined_in_its_module(name):
    """И лежит там, где задумано, а не расползлось обратно."""
    import importlib
    mod = importlib.import_module(SUBSCRIPTION_HOMES[name])
    assert hasattr(mod, name)


@pytest.mark.parametrize("name", sorted(SUBSCRIPTION_HOMES))
def test_subscription_name_is_the_same_object(name):
    """Реэкспорт отдаёт ту же функцию, а не копию: иначе подмена в тестах
    и любая проверка identity начнут врать."""
    import importlib
    import database.subscriptions as subs
    mod = importlib.import_module(SUBSCRIPTION_HOMES[name])
    assert getattr(subs, name) is getattr(mod, name)


PACKAGE_LEVEL_SUBSCRIPTION_NAMES = [
    # ровно то, что перечислено в database/__init__.py — уберёшь оттуда,
    # и импорт пакета упадёт при старте бота
    "get_payment", "get_last_approved_payment",
    "check_and_disable_expired_subscription", "get_subscription",
    "get_subscription_any", "admin_switch_tariff", "has_any_subscription",
    "has_any_payment", "get_active_subscription", "update_subscription_uuid",
    "get_all_active_subscriptions", "reissue_subscription_key",
    "_log_audit_event_atomic", "_log_vpn_lifecycle_audit_async",
    "_log_vpn_lifecycle_audit_fire_and_forget",
    "_log_subscription_history_atomic", "_log_audit_event_atomic_standalone",
    "reissue_vpn_key_atomic", "grant_access", "_calculate_subscription_days",
    "get_pending_payments", "is_user_first_purchase", "get_admin_stats",
    "calculate_final_price", "finalize_purchase", "set_combo_flag",
    "set_bypass_only_flag", "ensure_bypass_only_subscription",
]


@pytest.mark.parametrize("name", PACKAGE_LEVEL_SUBSCRIPTION_NAMES)
def test_subscription_name_available_via_package(name):
    import database
    assert hasattr(database, name), f"database.{name} исчез после разбивки"


def test_subscriptions_facade_holds_no_implementation():
    """В subscriptions.py не должно остаться ни одной функции.

    Иначе граница поплывёт обратно: кто-то допишет «маленький хелпер»
    рядом с реэкспортами, и через год файл снова будет на три тысячи строк.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("database/subscriptions.py").read_text(encoding="utf-8"))
    defined = [
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert defined == [], f"в фасаде завелась реализация: {defined}"


def test_grant_and_finalize_are_not_in_one_file_anymore():
    """Две самые длинные функции проекта занимали половину файла вдвоём.
    Держать их порознь — весь смысл этой разбивки."""
    import database.subscription_grant as g
    import database.purchase_finalization as f
    assert hasattr(g, "grant_access") and not hasattr(g, "finalize_purchase")
    assert hasattr(f, "finalize_purchase") and "grant_access" in dir(f), (
        "finalize_purchase зовёт grant_access — импорт обязан остаться"
    )


def test_audit_module_never_raises_by_design():
    """Функции журнала глушат ошибки намеренно: аудит не имеет права
    отменить выдачу доступа или уже проведённый платёж. Проверяем, что
    try/except из них не «почистили», чтобы увидеть ошибку."""
    import inspect
    import database.subscription_audit as a

    for name in ("_log_audit_event_atomic", "_log_audit_event_atomic_standalone",
                 "_log_vpn_lifecycle_audit_async",
                 "_log_vpn_lifecycle_audit_fire_and_forget",
                 "_notify_watchdog_expires_at"):
        src = inspect.getsource(getattr(a, name))
        assert "except" in src, f"{name} потеряла защиту от исключений"


def test_same_object_everywhere():
    """Реэкспорт должен отдавать ту же функцию, а не копию."""
    import database
    import database.promo as promo
    import database.subscriptions as subs
    for name in PROMO_NAMES:
        assert getattr(database, name) is getattr(promo, name)
        assert getattr(subs, name) is getattr(promo, name)


def test_generate_promo_code_still_works():
    """Проверка, что перенос не сломал саму логику."""
    from database.promo import generate_promo_code
    code = generate_promo_code()
    assert isinstance(code, str) and len(code) == 6
    assert generate_promo_code(10) != generate_promo_code(10)
