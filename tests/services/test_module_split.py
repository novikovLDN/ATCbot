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


def test_split_modules_import_cleanly():
    """Каждый выделенный модуль обязан импортироваться сам по себе:
    потерянный import внутри переноса иначе всплывёт только в проде."""
    import importlib
    for mod in ("database.promo", "database.referral_analytics",
                "database.reminders_queries", "database.trials_queries"):
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
