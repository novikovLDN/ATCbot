"""Дашборд и тарифы: показ комбо и возможность его выдать.

Две проблемы, которые закрывают эти тесты:
1. Дашборд показывал сырое subscription_type — админ видел «plus» вместо
   «Комбо Плюс» и не мог отличить комбо от обычной подписки.
2. Валидация опиралась на VALID_SUBSCRIPTION_TYPES, где комбо нет, поэтому
   выдать комбо через дашборд было невозможно вовсе.
"""
import pytest
from pydantic import ValidationError

import config
from app.api.dashboard.routes.users import _describe_subscription


class TestDescribeSubscription:
    def test_combo_tariff_gets_readable_name(self):
        out = _describe_subscription({"subscription_type": "combo_plus", "period_days": 30})
        assert out["tariff_display"] == "Комбо Плюс"
        assert out["is_combo"] is True
        assert out["base_tariff"] == "plus"
        assert out["bypass_gb"] > 0

    def test_legacy_combo_flag_recognised(self):
        """Историческая форма: базовый тариф + отдельный флаг is_combo."""
        out = _describe_subscription(
            {"subscription_type": "plus", "is_combo": True, "period_days": 30}
        )
        assert out["tariff_display"] == "Комбо Плюс"
        assert out["is_combo"] is True

    def test_plain_plus_is_not_combo(self):
        """Плюс и Комбо Плюс — разные тарифы, путать их нельзя."""
        out = _describe_subscription({"subscription_type": "plus", "period_days": 30})
        assert out["tariff_display"] == "Плюс"
        assert out["is_combo"] is False
        assert out["bypass_gb"] == 0

    def test_original_fields_preserved(self):
        """Обогащение не должно ломать существующих потребителей."""
        src = {"subscription_type": "basic", "period_days": 30, "uuid": "u-1", "status": "active"}
        out = _describe_subscription(src)
        for key, value in src.items():
            assert out[key] == value

    def test_none_passes_through(self):
        assert _describe_subscription(None) is None

    def test_empty_dict_passes_through(self):
        """Пустой словарь означает отсутствие подписки — обогащать нечего."""
        assert _describe_subscription({}) == {}


class TestGrantableTariffs:
    @pytest.mark.parametrize("tariff", ["combo_basic", "combo_plus"])
    def test_combo_can_be_granted(self, tariff):
        assert tariff in config.GRANTABLE_TARIFF_TYPES

    @pytest.mark.parametrize("tariff", ["basic", "plus"])
    def test_plain_still_grantable(self, tariff):
        assert tariff in config.GRANTABLE_TARIFF_TYPES

    def test_biz_still_grantable(self):
        for t in config.BIZ_TARIFFS:
            assert t in config.GRANTABLE_TARIFF_TYPES

    def test_combo_absent_from_db_column_types(self):
        """В колонке subscription_type комбо не хранится — там базовый уровень."""
        for t in config.COMBO_TARIFF_TYPES:
            assert t not in config.VALID_SUBSCRIPTION_TYPES


class TestRequestValidation:
    def test_grant_accepts_combo(self):
        from app.api.dashboard.routes.users import GrantRequest
        assert GrantRequest(days=30, tariff="combo_plus").tariff == "combo_plus"

    def test_grant_rejects_garbage(self):
        from app.api.dashboard.routes.users import GrantRequest
        with pytest.raises(ValidationError):
            GrantRequest(days=30, tariff="не-существует")

    def test_switch_accepts_combo(self):
        from app.api.dashboard.routes.users import SwitchTariffRequest
        assert SwitchTariffRequest(tariff="combo_basic").tariff == "combo_basic"

    def test_switch_rejects_garbage(self):
        from app.api.dashboard.routes.users import SwitchTariffRequest
        with pytest.raises(ValidationError):
            SwitchTariffRequest(tariff="combo_unknown")
