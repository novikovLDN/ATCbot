"""Покупка грядки должна быть атомарной.

Дефект: списание денег, добавление грядки и обновление счётчика были тремя
отдельными запросами. Сбой между первым и вторым снимал деньги, не выдав
грядку; сбой между вторым и третьим давал грядку, которую не видел счётчик.
Параллельные клики читали одинаковый счётчик и создавали две грядки с одним
идентификатором.
"""
import inspect

import pytest

import app.handlers.farm as farm_handlers
import database.farm as farm_mod


class TestAtomicPurchase:
    def test_helper_exists_and_exported(self):
        import database
        assert hasattr(farm_mod, "buy_farm_plot_atomic")
        assert hasattr(database, "buy_farm_plot_atomic")

    def test_single_transaction_with_lock(self):
        src = inspect.getsource(farm_mod.buy_farm_plot_atomic)
        assert "conn.transaction()" in src
        assert "pg_advisory_xact_lock" in src

    def test_reads_state_inside_transaction(self):
        """Проверки лимита и баланса должны идти по свежему состоянию."""
        src = inspect.getsource(farm_mod.buy_farm_plot_atomic)
        assert "FOR UPDATE" in src

    def test_balance_and_plots_updated_together(self):
        src = inspect.getsource(farm_mod.buy_farm_plot_atomic)
        assert "balance = balance - $1" in src
        assert "farm_plots = $2::jsonb" in src
        assert "farm_plot_count = $3" in src

    def test_writes_balance_transaction(self):
        """История операций обязана содержать списание."""
        src = inspect.getsource(farm_mod.buy_farm_plot_atomic)
        assert "balance_transactions" in src

    def test_counter_drift_handled(self):
        """Счётчик мог разойтись с длиной массива из-за старых покупок."""
        src = inspect.getsource(farm_mod.buy_farm_plot_atomic)
        assert "max(int(row[\"farm_plot_count\"] or 0), len(plots))" in src

    @pytest.mark.parametrize("reason", [
        "max_plots_reached", "insufficient_balance", "user_not_found", "db_not_ready",
    ])
    def test_all_refusal_reasons_present(self, reason):
        src = inspect.getsource(farm_mod.buy_farm_plot_atomic)
        assert reason in src


class TestHandler:
    def test_uses_atomic_helper(self):
        src = inspect.getsource(farm_handlers.callback_farm_buy_plot)
        assert "buy_farm_plot_atomic" in src

    def test_no_separate_writes_left(self):
        src = inspect.getsource(farm_handlers.callback_farm_buy_plot)
        for call in ("decrease_balance", "save_farm_plots", "update_farm_plot_count"):
            code = [ln for ln in src.split("\n")
                    if call in ln and not ln.lstrip().startswith("#")]
            assert not code, f"{call} остался отдельным запросом"

    def test_refusals_shown_to_user(self):
        src = inspect.getsource(farm_handlers.callback_farm_buy_plot)
        assert "Максимальное количество грядок" in src
        assert "Недостаточно средств" in src
