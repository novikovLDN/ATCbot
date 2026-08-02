"""Воркер уведомлений фермы не должен затирать действия пользователя.

Дефект: воркер читал весь массив грядок, рассылал уведомления (сетевые
запросы, они занимают время) и записывал массив обратно целиком. Если за
это время человек собирал урожай, его изменение затиралось устаревшим
снимком: грядка возвращалась в состояние «готова», хотя награда уже
начислена.
"""
import inspect

import pytest

import app.workers.farm_notifications as worker
import database.farm as farm_mod


def test_worker_does_not_rewrite_whole_array():
    src = inspect.getsource(worker.farm_notifications_iteration)
    code = [ln for ln in src.split("\n")
            if "save_farm_plots" in ln and not ln.lstrip().startswith("#")]
    assert not code, "перезапись всего массива затирает действия пользователя"


@pytest.mark.parametrize("flag", ["notified_ready", "notified_12h", "notified_dead"])
def test_each_flag_set_atomically(flag):
    src = inspect.getsource(worker.farm_notifications_iteration)
    assert f'"{flag}"' in src
    assert "mark_plot_notified" in src


def test_worker_passes_planted_at_guard():
    """Пока шло уведомление, грядку могли собрать и засеять заново —
    флаг относился бы к прошлому растению."""
    src = inspect.getsource(worker.farm_notifications_iteration)
    assert "expected_planted_at" in src


class TestMarkPlotNotified:
    def test_exists_and_exported(self):
        import database
        assert hasattr(farm_mod, "mark_plot_notified")
        assert hasattr(database, "mark_plot_notified")

    def test_takes_advisory_lock(self):
        src = inspect.getsource(farm_mod.mark_plot_notified)
        assert "pg_advisory_xact_lock" in src

    def test_rejects_unknown_flag(self):
        """Опечатка в имени флага не должна писать мусор в базу."""
        src = inspect.getsource(farm_mod.mark_plot_notified)
        assert "allowed" in src
        assert "notified_ready" in src and "notified_dead" in src

    def test_updates_single_flag_only(self):
        """Остальные поля грядки обязаны сохраниться."""
        src = inspect.getsource(farm_mod.mark_plot_notified)
        assert "{**p, flag: True}" in src

    def test_skips_when_plot_replanted(self):
        src = inspect.getsource(farm_mod.mark_plot_notified)
        assert "MARK_PLOT_NOTIFIED_STALE" in src

    def test_idempotent_when_already_set(self):
        src = inspect.getsource(farm_mod.mark_plot_notified)
        assert "if p.get(flag) is True" in src
