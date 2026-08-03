"""Выручка считается один раз — двойного учёта денег быть не должно.

Дефект: внутри бота одни и те же рубли делают несколько шагов —
пополнение баланса, покупка подписки с этого баланса, автопродление с него
же. Каждый шаг писал свою строку, а отчёты суммировали всё подряд: одни и
те же деньги попадали в выручку два-три раза. Реферальный кешбэк,
потраченный с баланса, превращался в «выручку» из воздуха. На тех же числах
срабатывал порог milestone-пуша (5k/10k/…) и цифра «Доход сегодня».

Правило: выручка = внешние поступления, то есть строки без
payment_provider='balance'. Тест держит это правило приклеенным к коду —
проверяет каждый денежный запрос, а не одну функцию.
"""
import re
from pathlib import Path

import pytest

FILTER = "COALESCE(payment_provider, '') <> 'balance'"

# Запросы, где фильтр сознательно НЕ нужен: это не деньги, а «что человеку
# выдано». Покупка с баланса — такая же полноценная покупка, и без неё
# восстановление премиума и сверка подписок сломаются.
ENTITLEMENT_QUERIES = (
    "get_user_paid_subscription_history",
    "get_paid_subscription_history_bulk",
    # Считает, СКОЛЬКО финансовых строк осталось после удаления профиля, —
    # для записи в audit_log. Здесь нужны все строки пользователя, включая
    # покупки с баланса: это инвентаризация, а не выручка.
    "admin_delete_user_complete",
)


def _money_lines(path: Path):
    """Строки SQL, где суммируются деньги из pending_purchases."""
    text = path.read_text(encoding="utf-8")
    for num, line in enumerate(text.splitlines(), 1):
        if "status = 'paid'" in line:
            yield num, line


@pytest.mark.parametrize("path", [
    Path("database/analytics.py"),
    Path("database/admin.py"),
])
def test_every_paid_query_declares_its_revenue_intent(path):
    """Каждая выборка по status='paid' либо фильтрует внутренние движения,
    либо относится к выдаче (перечислена в ENTITLEMENT_QUERIES)."""
    text = path.read_text(encoding="utf-8")
    offenders = []
    for num, line in _money_lines(path):
        if FILTER in line:
            continue
        # Ищем имя функции, внутри которой стоит запрос.
        head = text[: sum(len(x) + 1 for x in text.splitlines()[: num - 1])]
        names = re.findall(r"async def (\w+)", head)
        owner = names[-1] if names else "?"
        if owner in ENTITLEMENT_QUERIES:
            continue
        offenders.append(f"{path}:{num} ({owner}): {line.strip()}")
    assert not offenders, (
        "денежные запросы без фильтра внешних поступлений:\n" + "\n".join(offenders)
    )


def test_balance_funded_payments_are_marked():
    """Покупка с баланса и автопродление обязаны писать
    payment_provider='balance' — иначе строка неотличима от оплаты картой.

    Денежное ядро переехало из database/admin.py в
    database/balance_purchases.py — проверяем там.
    """
    balance = Path("database/balance_purchases.py").read_text(encoding="utf-8")
    renewal = Path("auto_renewal.py").read_text(encoding="utf-8")
    for src, name in ((balance, "finalize_balance_purchase"), (renewal, "auto_renewal")):
        assert "'approved', 'balance'" in src, (
            f"{name}: платёж с баланса не помечен провайдером"
        )


def test_migration_adds_provider_column_and_backfills():
    m = Path("migrations/072_payments_payment_provider.sql")
    assert m.exists()
    sql = m.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS payment_provider" in sql
    assert "UPDATE payments p" in sql, "нужен бэкфилл по связи с pending_purchases"
    assert "balance_topup" in sql, "пополнения баланса должны остаться выручкой"


def test_definition_is_documented_next_to_the_queries():
    """Правило должно быть записано рядом с кодом, иначе следующий отчёт
    напишут без фильтра и расхождение снова никто не заметит."""
    src = Path("database/analytics.py").read_text(encoding="utf-8")
    assert "REVENUE_EXTERNAL_ONLY_SQL" in src
    assert "ВНЕШНИЕ ПОСТУПЛЕНИЯ" in src
