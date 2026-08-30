"""
Regression: add_bypass_traffic must recover panel-orphaned entities.

Repro scenario (production incident, user 6214188086, 2026-08-12):
  1. DB.remnawave_uuid was cleared (dashboard delete, retry cleanup, etc.)
  2. Remnawave panel still holds the bypass entity with username=str(telegram_id)
  3. New traffic-pack purchase → add_bypass_traffic
  4. add_traffic() returns False (no uuid in DB)
  5. create_remnawave_user() → POST /api/users/create → HTTP 400 errorCode A019
     "User username already exists"
  6. User sees "⏳ Настраиваем обход блокировок... Нажмите 🔄"

Fix: before falling through to create, look up by username, cache uuid,
retry add_traffic. This test locks that behaviour.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import remnawave_service


PANEL_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _cfg():
    return SimpleNamespace(
        REMNAWAVE_ENABLED=True,
        REMNAWAVE_SQUAD_UUID="squad-uuid",
        TRAFFIC_LIMITS={"basic": {30: 50 * 1024**3}},
        DEVICE_LIMITS={"basic": 3},
    )


@pytest.mark.asyncio
async def test_add_bypass_traffic_recovers_orphaned_panel_entity(monkeypatch):
    tg = 6214188086
    extra_bytes = 15 * 1024**3

    # DB: no uuid cached
    db_uuid = {"value": None}

    async def get_uuid(_tg):
        return db_uuid["value"]

    async def set_uuid(_tg, u):
        db_uuid["value"] = u

    async def clear_uuid(_tg):
        db_uuid["value"] = None

    async def reset_flags(_tg):
        pass

    fake_db = SimpleNamespace(
        get_remnawave_uuid=get_uuid,
        set_remnawave_uuid=set_uuid,
        clear_remnawave_uuid=clear_uuid,
        reset_traffic_notification_flags=reset_flags,
    )

    # Panel: entity exists with username=str(tg), 0 bytes used, 5 GB limit
    existing_entity = {
        "uuid": PANEL_UUID,
        "username": str(tg),
        "trafficLimitBytes": 5 * 1024**3,
        "status": "ACTIVE",
        "activeInternalSquads": [{"uuid": "squad-uuid"}],
    }

    find_mock = AsyncMock(return_value=existing_entity)
    get_mock = AsyncMock(return_value=existing_entity)
    update_mock = AsyncMock(return_value=existing_entity)
    create_mock = AsyncMock()

    fake_api = SimpleNamespace(
        find_user_by_username=find_mock,
        get_user=get_mock,
        update_user=update_mock,
        create_user=create_mock,
    )

    with patch.object(remnawave_service, "config", _cfg()), \
         patch.object(remnawave_service, "database", fake_db), \
         patch.object(remnawave_service, "remnawave_api", fake_api):
        ok = await remnawave_service.add_bypass_traffic(
            tg,
            extra_bytes,
            subscription_type="basic",
            period_days=30,
        )

    assert ok is True, "recovery + top-up should succeed"
    find_mock.assert_awaited_once_with(str(tg))
    # Must NOT fall through to create — that would produce A019
    create_mock.assert_not_awaited()
    # Panel PATCH should have summed existing 5 GB + purchased 15 GB
    expected_new_limit = 5 * 1024**3 + 15 * 1024**3
    update_call_kwargs = update_mock.await_args_list[0].kwargs
    assert update_call_kwargs.get("trafficLimitBytes") == expected_new_limit
    # UUID should be cached in DB for next purchase
    assert db_uuid["value"] == PANEL_UUID
