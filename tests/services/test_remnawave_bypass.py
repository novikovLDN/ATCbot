"""
Unit tests for app.services.remnawave_bypass.

Mirrors the test patterns used by test_remnawave_premium.py:
  - patch config + remnawave_api functions
  - happy path / preflight-recovery / unrelated-conflict / 409 race
  - never raises to caller
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import remnawave_bypass


PANEL_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _cfg(**overrides):
    cfg = type("Cfg", (), {})()
    cfg.REMNAWAVE_ENABLED = True
    cfg.REMNAWAVE_CLIENTS_SQUAD_UUID = "clients-squad-uuid"
    cfg.REMNAWAVE_SQUAD_UUID = "clients-squad-uuid"
    cfg.REMNAWAVE_BYPASS_USERNAME_PATTERN = "{telegram_id}"
    cfg.REMNAWAVE_BYPASS_DEVICE_LIMIT = 5
    cfg.BYPASS_INFINITE_EXPIRE_ISO = "2099-12-31T23:59:59Z"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _patch(cfg, *, find=None, create=None):
    if find is None:
        find = AsyncMock(return_value=None)
    if create is None:
        create = AsyncMock()
    return (
        patch.object(remnawave_bypass, "config", cfg),
        patch.object(remnawave_bypass.remnawave_api, "find_user_by_username", find),
        patch.object(remnawave_bypass.remnawave_api, "create_user", create),
        find, create,
    )


# ── build_bypass_username ─────────────────────────────────────────────

class TestBuildBypassUsername:
    def test_default_pattern_is_plain_telegram_id(self):
        with patch.object(remnawave_bypass, "config", _cfg()):
            assert remnawave_bypass.build_bypass_username(12345) == "12345"

    def test_prefixed_pattern_when_configured(self):
        cfg = _cfg(REMNAWAVE_BYPASS_USERNAME_PATTERN="tg_{telegram_id}_bypass")
        with patch.object(remnawave_bypass, "config", cfg):
            assert remnawave_bypass.build_bypass_username(42) == "tg_42_bypass"

    def test_clamps_to_32_chars(self):
        with patch.object(remnawave_bypass, "config", _cfg()):
            assert len(remnawave_bypass.build_bypass_username(1234567890123456789)) <= 32

    def test_falls_back_when_pattern_is_garbage(self):
        cfg = _cfg(REMNAWAVE_BYPASS_USERNAME_PATTERN="{nope}")
        with patch.object(remnawave_bypass, "config", cfg):
            assert remnawave_bypass.build_bypass_username(77) == "77"


# ── _is_our_entity ─────────────────────────────────────────────────────

class TestIsOurEntity:
    def test_matches_on_telegram_id_int(self):
        assert remnawave_bypass._is_our_entity({"telegramId": 42}, 42) is True

    def test_matches_on_description_marker(self):
        assert remnawave_bypass._is_our_entity({"description": "bypass via bot"}, 42) is True

    def test_rejects_unrelated(self):
        assert remnawave_bypass._is_our_entity(
            {"telegramId": 99, "description": "manually added"}, 42,
        ) is False

    def test_rejects_non_dict(self):
        assert remnawave_bypass._is_our_entity(None, 42) is False


# ── create_bypass_user_entity ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_bypass_disabled_returns_failure():
    with patch.object(remnawave_bypass, "config", _cfg(REMNAWAVE_ENABLED=False)):
        result = await remnawave_bypass.create_bypass_user_entity(
            42, traffic_limit_bytes=10 * 1024**3,
        )
    assert result.ok is False
    assert result.error == "remnawave_disabled"


@pytest.mark.asyncio
async def test_create_bypass_rejects_non_positive_traffic():
    with patch.object(remnawave_bypass, "config", _cfg()):
        result = await remnawave_bypass.create_bypass_user_entity(42, traffic_limit_bytes=0)
    assert result.ok is False
    assert result.error == "non_positive_traffic_limit"


@pytest.mark.asyncio
async def test_create_bypass_happy_path():
    panel_response = {
        "ok": True,
        "status": 201,
        "response": {
            "uuid": PANEL_UUID,
            "subscriptionUrl": "https://rmnw/sub/short123",
            "shortUuid": "short123",
            "telegramId": 42,
        },
    }
    find = AsyncMock(return_value=None)
    create = AsyncMock(return_value=panel_response)
    p_cfg, p_find, p_create, _, _ = _patch(_cfg(), find=find, create=create)
    with p_cfg, p_find, p_create:
        result = await remnawave_bypass.create_bypass_user_entity(
            42, traffic_limit_bytes=10 * 1024**3, description="Bypass via bot",
        )

    find.assert_awaited_once_with("42")  # default pattern
    create.assert_awaited_once()
    kwargs = create.call_args.kwargs
    assert kwargs["squad_uuid"] == "clients-squad-uuid"
    assert kwargs["traffic_limit_bytes"] == 10 * 1024**3
    assert kwargs["expire_at"] == "2099-12-31T23:59:59Z"
    assert kwargs["telegram_id"] == 42
    # Task 6: bypass entities must NEVER carry externalSquadUuid — they
    # stay on the Default subscription Template (the "Unlimited" template
    # is premium-only).
    assert "external_squad_uuid" not in kwargs or kwargs["external_squad_uuid"] is None
    assert result.ok is True
    assert result.panel_uuid == PANEL_UUID
    assert result.subscription_url.endswith("/short123")
    assert result.short_uuid == "short123"
    assert result.recovered is False


@pytest.mark.asyncio
async def test_create_bypass_recovers_existing_our_entity():
    existing = {
        "uuid": PANEL_UUID,
        "telegramId": 42,
        "description": "bypass via bot",
        "subscriptionUrl": "https://rmnw/sub/x",
        "shortUuid": "rec",
    }
    find = AsyncMock(return_value=existing)
    create = AsyncMock()
    p_cfg, p_find, p_create, _, _ = _patch(_cfg(), find=find, create=create)
    with p_cfg, p_find, p_create:
        result = await remnawave_bypass.create_bypass_user_entity(
            42, traffic_limit_bytes=10 * 1024**3,
        )
    assert result.ok is True
    assert result.recovered is True
    assert result.panel_uuid == PANEL_UUID
    create.assert_not_called()


@pytest.mark.asyncio
async def test_create_bypass_refuses_when_username_held_by_unrelated():
    unrelated = {
        "uuid": "other",
        "telegramId": 99,
        "description": "manually created by admin",
    }
    find = AsyncMock(return_value=unrelated)
    create = AsyncMock()
    p_cfg, p_find, p_create, _, _ = _patch(_cfg(), find=find, create=create)
    with p_cfg, p_find, p_create:
        result = await remnawave_bypass.create_bypass_user_entity(
            42, traffic_limit_bytes=10 * 1024**3,
        )
    assert result.ok is False
    assert result.error == "conflict_unrelated_user"
    create.assert_not_called()


@pytest.mark.asyncio
async def test_create_bypass_recovers_on_post_409_race():
    """Preflight saw no entity → POST → 409 (parallel run created it) → adopt."""
    first_post = {"ok": False, "status": 409, "body": "exists", "response": None}
    recovered = {
        "uuid": PANEL_UUID, "telegramId": 42, "description": "bypass via bot",
        "subscriptionUrl": "u", "shortUuid": "s",
    }
    find = AsyncMock(side_effect=[None, recovered])
    create = AsyncMock(return_value=first_post)
    p_cfg, p_find, p_create, _, _ = _patch(_cfg(), find=find, create=create)
    with p_cfg, p_find, p_create:
        result = await remnawave_bypass.create_bypass_user_entity(
            42, traffic_limit_bytes=10 * 1024**3,
        )
    assert result.ok is True
    assert result.recovered is True


# ── add_bypass_traffic ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_bypass_traffic_accumulates(monkeypatch):
    import sys
    from types import SimpleNamespace

    fake_db = SimpleNamespace(
        get_remnawave_bypass_cache=AsyncMock(return_value={"remnawave_uuid": PANEL_UUID}),
        get_remnawave_uuid=AsyncMock(return_value=PANEL_UUID),
    )
    monkeypatch.setitem(sys.modules, "database", fake_db)

    get_user_mock = AsyncMock(return_value={"trafficLimitBytes": 5 * 1024**3})
    update_mock = AsyncMock(return_value={"ok": True})

    with patch.object(remnawave_bypass, "config", _cfg()), \
         patch.object(remnawave_bypass.remnawave_api, "get_user", get_user_mock), \
         patch.object(remnawave_bypass.remnawave_api, "update_user", update_mock):
        result = await remnawave_bypass.add_bypass_traffic(42, extra_bytes=10 * 1024**3)

    assert result is True
    update_mock.assert_awaited_once()
    # New limit = 5 GB + 10 GB = 15 GB
    new_limit = update_mock.call_args.kwargs["trafficLimitBytes"]
    assert new_limit == 15 * 1024**3
    # status forced ACTIVE so disabled-due-to-zero-traffic users come back
    assert update_mock.call_args.kwargs["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_add_bypass_traffic_returns_false_when_no_existing_entity(monkeypatch):
    import sys
    from types import SimpleNamespace

    fake_db = SimpleNamespace(
        get_remnawave_bypass_cache=AsyncMock(return_value=None),
        get_remnawave_uuid=AsyncMock(return_value=None),
    )
    monkeypatch.setitem(sys.modules, "database", fake_db)

    with patch.object(remnawave_bypass, "config", _cfg()):
        result = await remnawave_bypass.add_bypass_traffic(42, extra_bytes=10 * 1024**3)
    assert result is False
