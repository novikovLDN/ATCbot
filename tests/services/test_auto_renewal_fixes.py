"""Автопродление: изоляция ошибок, цена и текст уведомления.

Три дефекта в одном воркере:

1. Весь батч (до 100 подписок) шёл в одной транзакции. Ошибка Postgres на
   одном пользователе переводила соединение в failed-состояние: все
   следующие запросы падали, COMMIT падал, и откатывался весь батч — включая
   уже успешно продлённых. Плюс внутри транзакции дёргался Telegram, а
   значит строки, взятые SELECT ... FOR UPDATE, оставались заблокированными
   на время сетевого запроса.

2. Цена считалась своей формулой мимо общего калькулятора: автопродление не
   видело админских price override и глобальной скидки (миграция 069) и
   списывало не ту сумму, которую человек видит на витрине.

3. В тексте уведомления читался ключ "amount", которого в payload нет —
   человеку всегда приходило «списано 0 ₽». Рядом ветки «Комбо Plus» /
   «Комбо Basic» были недостижимы: ключ is_combo тоже не клался.

Тест статический: воркер ходит в живую БД и в Telegram, поднять его в
юнит-тестах нельзя. Проверяем структуру кода — именно она и была сломана.
"""
import re
from pathlib import Path

import pytest

SRC = Path("auto_renewal.py")


@pytest.fixture(scope="module")
def source():
    return SRC.read_text(encoding="utf-8")


def test_each_user_runs_in_its_own_savepoint(source):
    """Вложенная транзакция обязана открываться внутри цикла по подпискам."""
    loop_at = source.index("for i, sub_row in enumerate(subscriptions)")
    tail = source[loop_at:]
    assert "async with conn.transaction():" in tail, (
        "нет вложенной транзакции — ошибка одного пользователя снова "
        "откатит весь батч"
    )


def test_telegram_calls_are_not_made_inside_the_transaction(source):
    """Сеть внутри транзакции держит строки батча заблокированными."""
    tx_start = source.index("            async with conn.transaction():")
    phase_b = source.index("# PHASE B:")
    inside = source[tx_start:phase_b]
    for call in ("await alert_payment_failure(", "await send_alert("):
        assert call not in inside, f"{call} вызывается внутри транзакции"


def test_alerts_are_collected_and_flushed_after_commit(source):
    assert "alerts_to_send = []" in source
    assert source.count("alerts_to_send.append(") >= 3, (
        "не все алерты отложены до фазы B"
    )
    phase_b = source[source.index("# PHASE B:"):]
    assert "for alert in alerts_to_send:" in phase_b


def test_rollback_sentinel_exists_and_is_handled(source):
    """Отказ от продления после записи last_auto_renewal_at обязан
    откатывать savepoint, иначе пользователь выпадет до следующего окна."""
    assert "class _RollbackUser(Exception):" in source
    assert "raise _RollbackUser()" in source
    assert "except _RollbackUser:" in source


def test_price_comes_from_the_shared_calculator(source):
    assert "calculate_price(" in source, "цена снова считается своей формулой"
    assert "base_price * 0.70" not in source, "ручная VIP-скидка вернулась"
    assert "discount_percent / 100" not in source, "ручная персональная скидка вернулась"


def test_notification_reads_the_real_amount_key(source):
    assert 'item.get("amount_rubles"' in source
    assert 'item.get("amount", 0)' not in source, "снова читается несуществующий ключ"
    assert "amount_val > 1000" not in source, (
        "эвристика «больше 1000 — копейки» делила годовую подписку на сто"
    )


class TestComboRenewal:
    """Комбо = подписка + пакет ГБ обхода, со своей ценой.

    Признак лежит в отдельной колонке subscriptions.is_combo, а
    subscription_type содержит базовый тариф ('plus'). Автопродление про это
    не знало: списывало цену обычного тарифа (Комбо Plus на месяц — 499 ₽
    против 449 ₽ у Plus) и не пополняло гигабайты обхода. Человек платил за
    комбо и получал голую подписку.
    """

    def test_price_comes_from_combo_table(self, source):
        assert "combo_price_rubles(" in source
        assert "base_price_override_rubles=combo_price" in source, (
            "цена комбо не передаётся в калькулятор — спишется цена обычного тарифа"
        )

    def test_bypass_gb_are_topped_up_after_commit(self, source):
        """Начисление вынесено в app/services/combo_traffic — одну функцию на
        все пути оплаты. Здесь проверяем, что автопродление её зовёт: своей
        копии расчёта тут быть не должно, копии расходились молча."""
        phase_b = source[source.index("# PHASE B:"):]
        assert "grant_combo_traffic(" in phase_b, "ГБ обхода не начисляются при продлении"
        assert "add_bypass_traffic(" not in source, "вернулась своя копия начисления"

    def test_unknown_combo_period_degrades_to_plain_subscription(self, source):
        """Периода нет в таблице комбо — продлеваем как обычную подписку,
        а не берём цену наугад."""
        assert "AUTO_RENEWAL_COMBO_UNKNOWN_PERIOD" in source

    def test_combo_helpers_return_expected_values(self):
        """Код опирается на эти значения — проверяем их напрямую."""
        from app.constants import tariffs

        assert tariffs.combo_price_rubles("combo_plus", 30) == 499
        assert tariffs.combo_bypass_gb("combo_plus", 30) == 75
        assert tariffs.combo_price_rubles("combo_plus", 45) is None
        assert tariffs.display_name("plus", is_combo=True) == "Комбо Плюс"


def test_combo_flag_is_actually_put_into_payload(source):
    """Иначе ветки «Комбо …» в тексте недостижимы."""
    payload = source[source.index("notifications_to_send.append("):]
    payload = payload[: payload.index("})")]
    assert re.search(r'"is_combo":', payload), "is_combo не кладётся в payload"
