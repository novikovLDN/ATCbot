"""extend_remnawave_for_bypass PATCHes the bypass entity only when it needs it.

Production: every open of the setup screen (navigation.py) and every trial /
expiry transition PATCHed the bypass entity to expireAt = now + 10 years,
status ACTIVE — REMNAWAVE_BYPASS_EXTENDED on ordinary profile opens. The
function already GETs the entity: an ACTIVE entity whose expireAt is more than
~1 year away needs nothing. Near expiry, or not ACTIVE → one PATCH, as before.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import remnawave_service

PANEL_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
GIB = 1024 ** 3


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _entity(status: str, expire_in: timedelta | None) -> dict:
    now = datetime.now(timezone.utc)
    ent = {
        "id": 5, "vlessUuid": PANEL_UUID, "username": "42", "status": status,
        "trafficLimitBytes": 10 * GIB, "userTraffic": {"usedTrafficBytes": GIB},
    }
    if expire_in is not None:
        ent["expireAt"] = _iso(now + expire_in)
    return ent


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


@pytest.mark.parametrize("expire_in", [timedelta(days=3650), timedelta(days=400)])
async def test_far_future_active_entity_is_not_patched(wired, expire_in):
    update = wired(_entity("ACTIVE", expire_in))
    await remnawave_service.extend_remnawave_for_bypass(42)
    update.assert_not_awaited()


@pytest.mark.parametrize("status,expire_in", [
    ("ACTIVE", timedelta(days=30)),        # near expiry
    ("ACTIVE", timedelta(days=-1)),        # already past
    ("ACTIVE", None),                      # no expireAt in the answer
    ("DISABLED", timedelta(days=3650)),
    ("EXPIRED", timedelta(days=-2)),
    ("LIMITED", timedelta(days=3650)),
])
async def test_entity_that_needs_it_is_patched_once(wired, status, expire_in):
    update = wired(_entity(status, expire_in))
    await remnawave_service.extend_remnawave_for_bypass(42)
    update.assert_awaited_once()
    args, kwargs = update.call_args
    assert args == (PANEL_UUID,) and kwargs["status"] == "ACTIVE"
    new_expire = datetime.fromisoformat(kwargs["expireAt"].replace("Z", "+00:00"))
    assert new_expire > datetime.now(timezone.utc) + timedelta(days=3600)


async def test_no_entity_no_patch(wired):
    update = wired(None)
    await remnawave_service.extend_remnawave_for_bypass(42)
    update.assert_not_awaited()
