"""Общая арифметика админских сверок.

Дефект: четыре экрана сверки (audit_subs, audit_db_dates, recovery_premium,
reconcile) содержали дословные копии одного и того же ядра — три копии
_compute_real_end и четыре копии разбора даты из панели. Функция считает
ДЕНЬГИ в днях доступа: правка правила продления в одной копии оставляла три
другие со старым поведением, и два админских экрана начинали показывать
разные ответы про одного пользователя.

Тесты фиксируют поведение, которое было у копий, — чтобы вынос в общий
модуль ничего не сдвинул.
"""
from datetime import datetime, timezone

import pytest

from app.handlers.admin._audit_base import compute_real_end, iso_z, parse_panel_dt


def _row(day, days):
    return {"created_at": datetime(2026, 1, day, tzinfo=timezone.utc), "period_days": days}


class TestComputeRealEnd:
    def test_renewal_inside_active_period_stacks(self):
        """Продление во время действующего периода добавляет дни к концу,
        а не начинает отсчёт заново — иначе человек терял оплаченное."""
        rows = [_row(1, 30), _row(15, 30)]
        assert compute_real_end(rows) == datetime(2026, 3, 2, tzinfo=timezone.utc)

    def test_purchase_after_expiry_starts_fresh(self):
        rows = [
            _row(1, 30),
            {"created_at": datetime(2026, 6, 1, tzinfo=timezone.utc), "period_days": 30},
        ]
        assert compute_real_end(rows) == datetime(2026, 7, 1, tzinfo=timezone.utc)

    def test_naive_datetime_is_treated_as_utc(self):
        """В базе встречаются naive-даты; локальная трактовка сдвинула бы
        срок на часовой пояс сервера."""
        rows = [{"created_at": datetime(2026, 1, 1), "period_days": 10}]
        assert compute_real_end(rows) == datetime(2026, 1, 11, tzinfo=timezone.utc)

    def test_zero_and_negative_periods_are_skipped(self):
        """Пополнения баланса и товары доступ не продлевают."""
        assert compute_real_end([_row(1, 0)]) is None
        assert compute_real_end([_row(1, -5)]) is None

    def test_empty_history_means_no_paid_access_ever(self):
        assert compute_real_end([]) is None

    def test_mixed_history_ignores_non_subscription_rows(self):
        rows = [_row(1, 30), _row(2, 0), _row(3, 30)]
        assert compute_real_end(rows) == datetime(2026, 3, 2, tzinfo=timezone.utc)


class TestParsePanelDt:
    def test_iso_with_trailing_z(self):
        assert parse_panel_dt("2026-07-01T10:00:00Z") == datetime(
            2026, 7, 1, 10, 0, tzinfo=timezone.utc
        )

    def test_iso_with_offset_is_normalised_to_utc(self):
        assert parse_panel_dt("2026-07-01T13:00:00+03:00") == datetime(
            2026, 7, 1, 10, 0, tzinfo=timezone.utc
        )

    def test_naive_is_treated_as_utc(self):
        assert parse_panel_dt("2026-07-01T10:00:00") == datetime(
            2026, 7, 1, 10, 0, tzinfo=timezone.utc
        )

    @pytest.mark.parametrize("bad", [None, "", "не дата", "2026-13-45", 0])
    def test_garbage_returns_none_instead_of_raising(self, bad):
        """Одна испорченная запись не должна ронять всю сверку —
        админ обязан увидеть остальные строки."""
        assert parse_panel_dt(bad) is None


class TestIsoZ:
    def test_roundtrip_with_parse(self):
        dt = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        assert iso_z(dt) == "2026-07-01T10:00:00Z"
        assert parse_panel_dt(iso_z(dt)) == dt

    def test_naive_is_treated_as_utc(self):
        assert iso_z(datetime(2026, 7, 1, 10, 0)) == "2026-07-01T10:00:00Z"

    def test_none_passes_through(self):
        assert iso_z(None) is None


def test_no_module_keeps_its_own_copy():
    """Копии не должны вернуться: иначе экраны снова разойдутся в ответах."""
    import ast
    from pathlib import Path

    for mod in ("audit_subs", "audit_db_dates", "recovery_premium", "reconcile"):
        src = Path(f"app/handlers/admin/{mod}.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        own = {
            n.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in ("_compute_real_end", "_parse_panel_dt", "_parse_rmn_dt", "_iso_z"):
            assert name not in own, f"{mod}: снова своя копия {name}"
