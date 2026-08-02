"""Справочник тарифов: basic, plus, combo_basic, combo_plus, biz_*.

Логика различения тарифов раньше была размазана по двенадцати файлам:
проверки startswith("combo_"), подсчёт ГБ обхода и названия для интерфейса
писались заново в каждом месте. Эти тесты фиксируют единое поведение.
"""
import pytest

from app.constants import tariffs as t


class TestIsComboTariff:
    @pytest.mark.parametrize("tariff", ["combo_basic", "combo_plus"])
    def test_combo_recognised(self, tariff):
        assert t.is_combo_tariff(tariff) is True

    @pytest.mark.parametrize("tariff", ["basic", "plus", "biz_starter", "trial", "", None])
    def test_non_combo_rejected(self, tariff):
        assert t.is_combo_tariff(tariff) is False


class TestBaseTariff:
    def test_combo_basic_base_is_basic(self):
        assert t.base_tariff_of("combo_basic") == "basic"

    def test_combo_plus_base_is_plus(self):
        """Продление комбо не должно понижать Плюс до Базового."""
        assert t.base_tariff_of("combo_plus") == "plus"

    def test_plain_tariff_is_its_own_base(self):
        assert t.base_tariff_of("plus") == "plus"

    def test_none_falls_back_to_basic(self):
        assert t.base_tariff_of(None) == "basic"


class TestComboBypassGb:
    def test_gb_matches_config(self):
        import config
        for combo in ("combo_basic", "combo_plus"):
            for period, info in config.COMBO_TARIFFS[combo].items():
                assert t.combo_bypass_gb(combo, period) == info["gb"]

    def test_plain_tariff_has_no_bypass(self):
        assert t.combo_bypass_gb("plus", 30) == 0

    def test_unknown_period_returns_zero(self):
        assert t.combo_bypass_gb("combo_plus", 999) == 0

    def test_none_period_returns_zero(self):
        assert t.combo_bypass_gb("combo_plus", None) == 0


class TestComboPrice:
    def test_price_matches_config(self):
        import config
        for combo in ("combo_basic", "combo_plus"):
            for period, info in config.COMBO_TARIFFS[combo].items():
                assert t.combo_price_rubles(combo, period) == info["price"]

    def test_plain_tariff_has_no_combo_price(self):
        assert t.combo_price_rubles("basic", 30) is None


class TestDisplayName:
    @pytest.mark.parametrize("tariff,expected", [
        ("basic", "Базовый"),
        ("plus", "Плюс"),
        ("combo_basic", "Комбо Базовый"),
        ("combo_plus", "Комбо Плюс"),
        ("trial", "Пробный"),
    ])
    def test_known_names(self, tariff, expected):
        assert t.display_name(tariff) == expected

    def test_legacy_combo_flag_upgrades_name(self):
        """В базе есть подписки с subscription_type='plus' и is_combo=true."""
        assert t.display_name("plus", is_combo=True) == "Комбо Плюс"
        assert t.display_name("basic", is_combo=True) == "Комбо Базовый"

    def test_combo_tariff_ignores_redundant_flag(self):
        assert t.display_name("combo_plus", is_combo=True) == "Комбо Плюс"

    def test_biz_tariff_readable(self):
        assert t.display_name("biz_starter").startswith("Бизнес")

    def test_empty_is_dash(self):
        assert t.display_name(None) == "—"


class TestAvailablePeriods:
    def test_combo_periods_sorted(self):
        periods = t.available_periods("combo_plus")
        assert periods == tuple(sorted(periods))
        assert 30 in periods

    def test_plain_periods_present(self):
        assert 30 in t.available_periods("basic")

    def test_unknown_tariff_has_no_periods(self):
        assert t.available_periods("nonexistent") == ()


class TestDescribe:
    def test_combo_description_complete(self):
        d = t.describe("combo_plus", 30)
        assert d["is_combo"] is True
        assert d["base_tariff"] == "plus"
        assert d["display_name"] == "Комбо Плюс"
        assert d["bypass_gb"] > 0
        assert d["price_rubles"] > 0

    def test_plain_description(self):
        d = t.describe("basic", 30)
        assert d["is_combo"] is False
        assert d["bypass_gb"] == 0

    def test_legacy_flag_reflected(self):
        d = t.describe("plus", 30, is_combo=True)
        assert d["is_combo"] is True
        assert d["display_name"] == "Комбо Плюс"


class TestConfigIntegrity:
    """Проверки самой таблицы тарифов: цены и ГБ должны быть осмысленными."""

    def test_every_combo_period_has_price_and_gb(self):
        import config
        for combo, periods in config.COMBO_TARIFFS.items():
            for period, info in periods.items():
                assert info.get("price", 0) > 0, f"{combo}/{period}: нет цены"
                assert info.get("gb", 0) > 0, f"{combo}/{period}: нет ГБ"
                assert info.get("base_tariff"), f"{combo}/{period}: нет base_tariff"

    def test_combo_costs_more_than_plain_subscription(self):
        """Комбо включает трафик, поэтому не может стоить дешевле подписки."""
        import config
        for combo, periods in config.COMBO_TARIFFS.items():
            base = combo.replace("combo_", "")
            for period, info in periods.items():
                plain = config.TARIFFS.get(base, {}).get(period, {}).get("price")
                if plain is None:
                    continue
                assert info["price"] > plain, (
                    f"{combo}/{period}: {info['price']}₽ не дороже {base} за {plain}₽"
                )

    def test_longer_period_gives_more_gb(self):
        """Больше срок — больше трафика: иначе выбор периода нелогичен."""
        import config
        for combo, periods in config.COMBO_TARIFFS.items():
            ordered = sorted(periods.items())
            gbs = [info["gb"] for _, info in ordered]
            assert gbs == sorted(gbs), f"{combo}: ГБ не растут с периодом: {gbs}"

    def test_plus_combo_not_cheaper_than_basic_combo(self):
        import config
        for period, plus_info in config.COMBO_TARIFFS["combo_plus"].items():
            basic_info = config.COMBO_TARIFFS["combo_basic"].get(period)
            if not basic_info:
                continue
            assert plus_info["price"] >= basic_info["price"], (
                f"период {period}: Комбо Плюс дешевле Комбо Базового"
            )
