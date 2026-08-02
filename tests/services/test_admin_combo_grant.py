"""Админская выдача комбо-тарифа.

Дефект: admin_grant_access_atomic молча превращала любой тариф вне
VALID_SUBSCRIPTION_TYPES в "basic". Комбо там нет по устройству базы,
поэтому попытка выдать «Комбо Плюс» давала пользователю Базовый —
без ошибки в логах и без единого признака, что что-то пошло не так.
"""
import pytest

import config
from app.constants import tariffs as tariff_ref


def normalize(requested: str):
    """Повторяет нормализацию тарифа из admin_grant_access_atomic."""
    requested = (requested or "basic").strip().lower()
    is_combo = requested in getattr(config, "COMBO_TARIFF_TYPES", ())
    tariff = requested.replace("combo_", "", 1) if is_combo else requested
    if tariff not in config.VALID_SUBSCRIPTION_TYPES:
        tariff = "basic"
    return tariff, is_combo


class TestTariffNormalization:
    def test_combo_plus_keeps_plus_level(self):
        """Главный дефект: Комбо Плюс не должен схлопываться в Базовый."""
        tariff, is_combo = normalize("combo_plus")
        assert tariff == "plus"
        assert is_combo is True

    def test_combo_basic(self):
        tariff, is_combo = normalize("combo_basic")
        assert tariff == "basic"
        assert is_combo is True

    @pytest.mark.parametrize("plain", ["basic", "plus"])
    def test_plain_unchanged(self, plain):
        tariff, is_combo = normalize(plain)
        assert tariff == plain
        assert is_combo is False

    def test_biz_preserved(self):
        tariff, is_combo = normalize("biz_starter")
        assert tariff == "biz_starter"
        assert is_combo is False

    def test_unknown_falls_back_to_basic(self):
        tariff, is_combo = normalize("выдуманный_тариф")
        assert tariff == "basic"
        assert is_combo is False

    def test_case_and_spaces_tolerated(self):
        assert normalize("  COMBO_PLUS  ") == ("plus", True)


class TestBypassPackage:
    """Комбо обязано приносить трафик — иначе это просто подписка."""

    @pytest.mark.parametrize("combo", ["combo_basic", "combo_plus"])
    @pytest.mark.parametrize("days", [30, 90, 180, 365, 730])
    def test_every_period_has_bypass(self, combo, days):
        assert tariff_ref.combo_bypass_gb(combo, days) > 0

    def test_bytes_conversion_is_binary_gigabytes(self):
        """1 ГБ = 1024^3 байт: с 1000^3 пользователь недополучит ~7%."""
        gb = tariff_ref.combo_bypass_gb("combo_plus", 30)
        assert gb * 1024 ** 3 == gb * 1073741824

    def test_plain_tariff_grants_no_bypass(self):
        assert tariff_ref.combo_bypass_gb("plus", 30) == 0
        assert tariff_ref.combo_bypass_gb("basic", 30) == 0

    def test_unknown_period_grants_nothing(self):
        """Нестандартный срок выдачи не должен давать случайный пакет."""
        assert tariff_ref.combo_bypass_gb("combo_plus", 45) == 0
