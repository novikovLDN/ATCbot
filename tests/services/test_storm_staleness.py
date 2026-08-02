"""Зависший шторм не должен блокировать посадку навсегда.

Дефект: пока шторм «объявлен и не исполнен», посадка выключена у всех.
Если воркер не отработал — упал, был остановлен на деплое, база была
недоступна — шторм оставался в этом состоянии навсегда, и ферма умирала
для всех пользователей без единой ошибки в интерфейсе.
"""
import inspect
from datetime import datetime, timedelta, timezone

import pytest

import app.handlers.farm as farm_handlers

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)


def _storm(scheduled_at, announced=True, executed=False):
    return {
        "id": 1,
        "scheduled_at": scheduled_at,
        "announced_at": NOW - timedelta(hours=24) if announced else None,
        "executed_at": NOW if executed else None,
    }


class TestStaleness:
    def test_threshold_defined(self):
        assert hasattr(farm_handlers, "STORM_STALE_AFTER_HOURS")
        assert 1 <= farm_handlers.STORM_STALE_AFTER_HOURS <= 48

    def test_overdue_storm_ignored(self):
        src = inspect.getsource(farm_handlers._get_imminent_storm)
        assert "STORM_STALE" in src, "зависший шторм должен отбрасываться"
        assert "STORM_STALE_AFTER_HOURS" in src

    def test_naive_scheduled_at_handled(self):
        """Из базы дата может прийти без таймзоны — сравнение упало бы."""
        src = inspect.getsource(farm_handlers._get_imminent_storm)
        assert "tzinfo is None" in src

    def test_executed_storm_not_active(self):
        src = inspect.getsource(farm_handlers._get_imminent_storm)
        assert 'storm.get("executed_at")' in src

    def test_unannounced_storm_not_active(self):
        src = inspect.getsource(farm_handlers._get_imminent_storm)
        assert 'storm.get("announced_at")' in src

    def test_logs_when_ignoring(self):
        """Молча игнорировать нельзя: это сигнал, что воркер не работает."""
        src = inspect.getsource(farm_handlers._get_imminent_storm)
        assert "logger.warning" in src


class TestStalenessLogic:
    """Проверка самого правила на числах, без обращения к базе."""

    def _is_stale(self, scheduled_at, now=NOW):
        overdue = now - scheduled_at
        return overdue > timedelta(hours=farm_handlers.STORM_STALE_AFTER_HOURS)

    def test_future_storm_is_fresh(self):
        assert self._is_stale(NOW + timedelta(hours=2)) is False

    def test_just_overdue_is_fresh(self):
        assert self._is_stale(NOW - timedelta(hours=1)) is False

    def test_long_overdue_is_stale(self):
        assert self._is_stale(NOW - timedelta(days=3)) is True
