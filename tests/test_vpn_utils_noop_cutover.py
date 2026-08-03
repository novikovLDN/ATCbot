"""Заглушки vpn_utils после снятия samopis xray с эксплуатации.

Провижининг выполняет Remnawave (app.services.purchase_flow). Legacy-точки
входа обязаны не ходить в сеть и не падать: их ещё вызывают остаточные пути —
восстановление в auto_renewal, админский перевыпуск, очистка триалов.

Раньше поведение зависело от флага PURCHASE_FLOW_REMNAWAVE. Флаг удалён:
владелец подтвердил, что Remnawave — единственный бэкенд, и держать вторую
ветку означало держать непроверяемый код.
"""
from datetime import datetime, timezone

import pytest


@pytest.fixture
def vpn_utils_module():
    import vpn_utils as mod
    return mod


class TestAddVlessUser:
    @pytest.mark.asyncio
    async def test_returns_stub_without_network(self, vpn_utils_module):
        result = await vpn_utils_module.add_vless_user(
            telegram_id=42,
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            uuid="11111111-2222-3333-4444-555555555555",
            tariff="basic",
        )
        assert result["uuid"] == "11111111-2222-3333-4444-555555555555"
        assert result["vless_url"] == ""
        assert result["vless_url_plus"] is None
        assert result["subscription_type"] == "basic"

    @pytest.mark.asyncio
    async def test_generates_uuid_when_none_supplied(self, vpn_utils_module):
        """Остаточным вызывающим нужен непустой uuid, а не None."""
        result = await vpn_utils_module.add_vless_user(
            telegram_id=42,
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            uuid=None,
        )
        assert result["uuid"]
        assert len(result["uuid"]) >= 32

    @pytest.mark.asyncio
    async def test_tariff_passthrough(self, vpn_utils_module):
        result = await vpn_utils_module.add_vless_user(
            telegram_id=1,
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            tariff="plus",
        )
        assert result["subscription_type"] == "plus"

    @pytest.mark.asyncio
    async def test_empty_tariff_falls_back_to_basic(self, vpn_utils_module):
        result = await vpn_utils_module.add_vless_user(
            telegram_id=1,
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            tariff="",
        )
        assert result["subscription_type"] == "basic"


class TestUpdateAndRemove:
    @pytest.mark.asyncio
    async def test_update_is_noop(self, vpn_utils_module):
        assert await vpn_utils_module.update_vless_user(
            "11111111-2222-3333-4444-555555555555",
            datetime(2030, 1, 1, tzinfo=timezone.utc),
        ) is None

    @pytest.mark.asyncio
    async def test_remove_is_noop(self, vpn_utils_module):
        assert await vpn_utils_module.remove_vless_user(
            "11111111-2222-3333-4444-555555555555"
        ) is None

    @pytest.mark.asyncio
    async def test_remove_tolerates_empty_uuid(self, vpn_utils_module):
        """Логирование обрезает uuid — пустая строка не должна ронять вызов."""
        assert await vpn_utils_module.remove_vless_user("") is None


class TestFlagRemoved:
    def test_config_has_no_cutover_flag(self):
        """Флаг снят: вторая ветка провижининга больше не существует."""
        import config
        assert not hasattr(config, "PURCHASE_FLOW_REMNAWAVE")

    def test_dead_functions_are_gone(self, vpn_utils_module):
        """upgrade_vless_user и remove_plus_inbound не имели ни одного вызова."""
        assert not hasattr(vpn_utils_module, "upgrade_vless_user")
        assert not hasattr(vpn_utils_module, "remove_plus_inbound")


class TestXrayLeftoversRemoved:
    """Обвязка HTTP-вызовов к xray не должна вернуться вместе с копипастой.

    Опасность не в лишних строках, а в том, что они утверждают поведение,
    которого нет: пойманный VPNTimeoutError читается как «таймауты
    обрабатываются», а _validate_api_url_security — как «URL панели
    проверяется». Бросать и проверять давно нечего.
    """

    @pytest.mark.parametrize("name", [
        "VPNTimeoutError",
        "AuthError",
        "InvalidResponseError",
        "CriticalUUIDMismatchError",
        "_validate_uuid_no_prefix",
        "_validate_api_url_security",
        "_fire_and_forget",
        "_background_tasks",
        "VPN_HTTP_TIMEOUT",
        "HTTP_TIMEOUT",
        "MAX_RETRIES",
        "RETRY_DELAY",
        "retry_async",
        "weakref",
    ])
    def test_symbol_is_gone(self, vpn_utils_module, name):
        assert not hasattr(vpn_utils_module, name)

    def test_base_error_stays(self, vpn_utils_module):
        """VPNAPIError жив: его бросает safe_remove_vless_user_with_retry."""
        assert issubclass(vpn_utils_module.VPNAPIError, Exception)
