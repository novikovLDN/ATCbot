"""remnawave_service.disable_remnawave_user on the Remnawave 3.4.3 user shape.

3.4.3 GET /api/users/{id} (models/extended-users.schema.ts) carries used
traffic ONLY in userTraffic.usedTrafficBytes — there is no top-level
usedTrafficBytes. Reading the top level gave 0, so a bypass entity with its
GB used up was "kept active" instead of disabled.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import remnawave_service

PANEL_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
GIB = 1024 ** 3


def _entity(limit: int, used: int) -> dict:
    return {
        "id": 5,
        "vlessUuid": PANEL_UUID,
        "username": "42",
        "status": "LIMITED",
        "trafficLimitBytes": limit,
        "expireAt": "2099-12-31T23:59:59.000Z",
        "userTraffic": {"usedTrafficBytes": used, "lifetimeUsedTrafficBytes": used},
    }


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(remnawave_service, "config", SimpleNamespace(REMNAWAVE_ENABLED=True))
    monkeypatch.setattr(remnawave_service, "database", SimpleNamespace(
        get_remnawave_uuid=AsyncMock(return_value=PANEL_UUID),
        clear_remnawave_uuid=AsyncMock(),
    ))
    update = AsyncMock(return_value={"id": 5})

    def _install(entity):
        monkeypatch.setattr(remnawave_service.remnawave_api, "get_user", AsyncMock(return_value=entity))
        monkeypatch.setattr(remnawave_service.remnawave_api, "update_user", update)
        return update

    return _install


@pytest.mark.asyncio
async def test_disable_uses_nested_used_traffic_and_disables_exhausted(wired):
    update = wired(_entity(limit=10 * GIB, used=10 * GIB))
    await remnawave_service.disable_remnawave_user(42)
    update.assert_awaited_once_with(PANEL_UUID, status="DISABLED")


@pytest.mark.asyncio
async def test_disable_keeps_bypass_active_while_gb_remain(wired):
    update = wired(_entity(limit=10 * GIB, used=3 * GIB))
    await remnawave_service.disable_remnawave_user(42)
    update.assert_awaited_once()
    assert update.call_args.kwargs["status"] == "ACTIVE"
    assert "expireAt" in update.call_args.kwargs
