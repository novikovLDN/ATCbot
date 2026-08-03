"""Источник истины по выручке в отчётах.

Дефект: отчёты считали выручку по таблице payments. Строка там создаётся
только внутри finalize_purchase, то есть для подписок. Товары мини-магазина
(Stars, Telegram Premium, Steam, Spotify, Apple ID, прокси) помечаются
оплаченными через mark_pending_purchase_paid и в payments не попадают вовсе —
их выручка отсутствовала в отчётах.

Вдобавок payments двоил деньги: пополнение баланса писало строку, и покупка
с этого же баланса писала вторую — один рубль считался дважды.
"""
import inspect
import re

import pytest

import database.admin as admin_mod
import database.analytics as analytics_mod

# Функции, которые обязаны считать деньги по pending_purchases.
REVENUE_FUNCS_ANALYTICS = [
    "get_total_revenue",
    "get_paying_users_count",
    "get_user_ltv",
    "get_average_ltv",
    "get_arpu",
    "get_extended_bot_stats",
]
REVENUE_FUNCS_ADMIN = [
    "get_daily_timeseries",
    "get_hourly_timeseries",
    "get_ltv",
    "get_daily_summary",
    "get_monthly_summary",
]


def _sql_of(mod, name):
    return inspect.getsource(getattr(mod, name))


@pytest.mark.parametrize("name", REVENUE_FUNCS_ANALYTICS)
def test_analytics_uses_pending_purchases(name):
    src = _sql_of(analytics_mod, name)
    assert "pending_purchases" in src, f"{name}: выручка должна считаться по pending_purchases"


@pytest.mark.parametrize("name", REVENUE_FUNCS_ANALYTICS)
def test_analytics_does_not_sum_payments_amount(name):
    src = _sql_of(analytics_mod, name)
    assert "SUM(amount)" not in src, f"{name}: суммирование payments.amount занижает выручку"


@pytest.mark.parametrize("name", REVENUE_FUNCS_ADMIN)
def test_admin_uses_pending_purchases(name):
    src = _sql_of(admin_mod, name)
    assert "pending_purchases" in src, f"{name}: выручка должна считаться по pending_purchases"


@pytest.mark.parametrize("name", REVENUE_FUNCS_ADMIN)
def test_admin_does_not_sum_payments_amount(name):
    src = _sql_of(admin_mod, name)
    assert "SUM(amount)" not in src, f"{name}: суммирование payments.amount занижает выручку"


def test_paid_status_used_not_approved():
    """У pending_purchases статус называется paid, а не approved."""
    for mod, names in ((analytics_mod, REVENUE_FUNCS_ANALYTICS), (admin_mod, REVENUE_FUNCS_ADMIN)):
        for name in names:
            src = _sql_of(mod, name)
            if "pending_purchases" not in src:
                continue
            for m in re.finditer(r"FROM pending_purchases(.{0,200})", src, re.S):
                assert "status = 'paid'" in m.group(1), (
                    f"{name}: выборка из pending_purchases без status='paid'"
                )


def test_payments_still_used_where_it_belongs():
    """payments остаётся источником для идемпотентности и истории платежей —
    это про сами платежи, а не про выручку.

    Проверка идемпотентности живёт в денежном ядре, которое переехало из
    database/admin.py в database/balance_purchases.py.
    """
    from database import balance_purchases as balance_mod

    src = inspect.getsource(balance_mod)
    assert "telegram_payment_charge_id" in src, "проверка идемпотентности не должна была уехать"


def test_business_metrics_no_longer_counts_payment_statuses():
    """Раньше здесь жила «доля подтверждённых платежей» по payments.

    Метрику убрали: числитель и знаменатель совпадали по построению —
    строка в payments не может остаться неподтверждённой. Разбор — в
    tests/services/test_approval_rate_removed.py.
    """
    src = _sql_of(analytics_mod, "get_business_metrics")
    body = src.split('"""', 2)[-1]  # докстринг объясняет дефект, его не смотрим
    assert "FROM payments" not in body
