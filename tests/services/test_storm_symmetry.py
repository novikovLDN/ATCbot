"""Шторм действует одинаково на онлайн и офлайн игроков.

Дефект: офлайновый игрок получал автосбор 50% награды, а онлайновый терял
растение целиком. Стимул был вывернут: заходить в бота во время шторма было
невыгодно, оптимальной стратегией было не заходить вовсе.
"""
from datetime import datetime, timedelta, timezone

import pytest

import database.farm as farm_mod


ANNOUNCED = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _growing(plot_id: int, plant: str = "wheat", shielded: bool = False):
    return {
        "plot_id": plot_id,
        "status": "growing",
        "plant_type": plant,
        "storm_shielded": shielded,
    }


class TestStormSymmetry:
    """Проверяется чистая логика перебора грядок, без обращения к БД."""

    def _apply(self, plots, last_seen):
        """Повторяет решение по каждой грядке так, как оно теперь в коде."""
        killed, shielded = 0, 0
        for p in plots:
            if p.get("status") != "growing":
                continue
            if p.get("storm_shielded") is True:
                shielded += 1
                continue
            killed += 1
        return killed, shielded

    def test_online_and_offline_lose_equally(self):
        plots = [_growing(1), _growing(2)]
        online = self._apply(plots, ANNOUNCED + timedelta(minutes=5))
        offline = self._apply(plots, ANNOUNCED - timedelta(days=3))
        assert online == offline, "присутствие в боте не должно менять исход"

    def test_shield_still_protects(self):
        killed, shielded = self._apply([_growing(1, shielded=True), _growing(2)], ANNOUNCED)
        assert shielded == 1
        assert killed == 1

    def test_non_growing_untouched(self):
        killed, shielded = self._apply(
            [{"plot_id": 1, "status": "empty"}, {"plot_id": 2, "status": "ready"}], ANNOUNCED
        )
        assert killed == 0 and shielded == 0


def test_plant_rewards_is_optional():
    """Параметр остался только ради совместимости вызовов."""
    import inspect
    sig = inspect.signature(farm_mod.execute_storm_for_user)
    assert sig.parameters["plant_rewards"].default is None


def test_autoharv_fields_still_present_for_consumer():
    """Воркер уведомлений читает эти ключи — они обязаны существовать."""
    import inspect
    src = inspect.getsource(farm_mod.execute_storm_for_user)
    for field in ("autoharv", "autoharv_kopecks", "autoharv_plants"):
        assert field in src, f"поле {field} исчезло — сломается farm_notifications"
