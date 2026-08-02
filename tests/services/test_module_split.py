"""Совместимость после разбивки database/subscriptions.py.

Файл разросся до 5162 строк и стал неподдерживаемым. Группы функций
выносятся в отдельные модули, но весь существующий код годами обращался
к ним через database.<name> и database.subscriptions.<name> — оба пути
обязаны продолжать работать.
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


def test_money_functions_stayed_in_admin():
    """Денежные операции не должны были уехать вместе с рассылками."""
    import database.admin as adm
    import database.broadcasts as br
    for name in ("finalize_balance_purchase", "finalize_balance_topup",
                 "admin_grant_access_atomic", "admin_revoke_access_atomic"):
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
                "database.referrals"):
        importlib.import_module(mod)


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
