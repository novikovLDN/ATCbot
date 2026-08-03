"""Полив и удобрение: потолок ускорения и сдвиг dead_at.

Дефект. Полив снимал 6 часов, удобрение — 2, каждое раз в сутки на грядку, и
никакой нижней границы не было: 8 часов за каждые 24 часа реального времени —
это треть срока роста. Дуб созревал за 24 дня вместо 32, пассивный доход
фермы уезжал в полтора раза мимо расчёта экономики.

Второй дефект в том же месте: dead_at не пересчитывался. Он оставался равен
исходному ready_at + 24 часа, поэтому у прилежного игрока окно сбора было не
24 часа, а 24 часа плюс всё накопленное ускорение — ещё одна незаявленная
поблажка.
"""
from datetime import datetime, timedelta, timezone

import pytest

import app.handlers.farm as farm

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)


def _plot(plant_type="oak", planted_at=NOW):
    days = farm.PLANT_TYPES[plant_type]["days"]
    ready_at = planted_at + timedelta(days=days)
    return {
        "plot_id": 0,
        "status": "growing",
        "plant_type": plant_type,
        "planted_at": planted_at.isoformat(),
        "ready_at": ready_at.isoformat(),
        "dead_at": (ready_at + timedelta(hours=24)).isoformat(),
    }


def _ready(plot):
    return datetime.fromisoformat(plot["ready_at"])


def _dead(plot):
    return datetime.fromisoformat(plot["dead_at"])


class TestSingleBoost:
    def test_watering_moves_ready_at(self):
        plot = _plot()
        before = _ready(plot)

        assert farm._apply_growth_boost(plot, hours=6) is True
        assert _ready(plot) == before - timedelta(hours=6)

    def test_dead_at_moves_with_ready_at(self):
        """Окно сбора обязано остаться 24-часовым, а не растягиваться."""
        plot = _plot()

        farm._apply_growth_boost(plot, hours=6)
        farm._apply_growth_boost(plot, hours=2)

        assert _dead(plot) - _ready(plot) == timedelta(hours=24)


class TestCumulativeCap:
    def test_cap_constant_is_sane(self):
        assert 0 < farm.FARM_BOOST_MAX_FRACTION <= 0.5

    def test_total_speedup_never_exceeds_the_cap(self):
        """Ежедневный уход не должен сжимать срок роста без предела."""
        plot = _plot("oak")
        base_ready = _ready(plot)

        for _ in range(200):  # заведомо больше, чем позволит потолок
            farm._apply_growth_boost(plot, hours=6)
            farm._apply_growth_boost(plot, hours=2)

        grow_seconds = farm.PLANT_TYPES["oak"]["days"] * 86400
        speedup = (base_ready - _ready(plot)).total_seconds()
        assert speedup <= grow_seconds * farm.FARM_BOOST_MAX_FRACTION + 1

    def test_refuses_when_limit_is_used_up(self):
        """Отказ должен быть явным: обработчик показывает человеку алерт."""
        plot = _plot("tomato")  # 5 дней, потолок 24 часа

        assert farm._apply_growth_boost(plot, hours=6) is True
        assert farm._apply_growth_boost(plot, hours=6) is True
        assert farm._apply_growth_boost(plot, hours=6) is True
        assert farm._apply_growth_boost(plot, hours=6) is True
        assert farm._apply_growth_boost(plot, hours=6) is False

    def test_last_boost_is_trimmed_not_dropped(self):
        """Остался час допуска — снимаем час, а не отказываем целиком."""
        plot = _plot("tomato")
        base_ready = _ready(plot)
        # 23 часа из 24 уже сняты
        plot["ready_at"] = (base_ready - timedelta(hours=23)).isoformat()
        plot["dead_at"] = (base_ready - timedelta(hours=23) + timedelta(hours=24)).isoformat()

        assert farm._apply_growth_boost(plot, hours=6) is True
        assert (base_ready - _ready(plot)) == timedelta(hours=24)

    def test_oak_keeps_most_of_its_growth_time(self):
        """Смысл потолка в деньгах: дуб не должен созревать за 24 дня."""
        plot = _plot("oak")
        for _ in range(200):
            farm._apply_growth_boost(plot, hours=6)
            farm._apply_growth_boost(plot, hours=2)

        grown_days = (_ready(plot) - NOW).total_seconds() / 86400
        assert grown_days >= 25, f"дуб созревает за {grown_days:.1f} дней"


class TestBrokenData:
    def test_plot_without_planted_at_does_not_crash(self):
        """Грядки старого формата: посчитать потолок не от чего, но dead_at
        всё равно обязан ехать вместе с ready_at."""
        plot = _plot()
        plot["planted_at"] = None

        assert farm._apply_growth_boost(plot, hours=6) is True
        assert _dead(plot) - _ready(plot) == timedelta(hours=24)

    def test_unknown_plant_does_not_crash(self):
        plot = _plot()
        plot["plant_type"] = "no_such_plant"

        assert farm._apply_growth_boost(plot, hours=6) is True


class TestHandlersUseTheCap:
    @pytest.mark.parametrize(
        "handler", ["callback_farm_water", "callback_farm_fert"],
    )
    def test_handler_goes_through_the_helper(self, handler):
        """Прямое вычитание из ready_at в обработчике = потолок в обход."""
        import inspect

        src = inspect.getsource(getattr(farm, handler))
        assert "_apply_growth_boost" in src
        assert "timedelta(hours=" not in src, "ускорение считается мимо потолка"
