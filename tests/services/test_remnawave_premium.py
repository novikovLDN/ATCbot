"""
Unit tests for app.services.remnawave_premium.

Covers:
  - username clamping / pattern fallback
  - create_premium_user_entity happy path (forced UUID accepted)
  - create_premium_user_entity retry on forced-UUID rejection
  - create_premium_user_entity terminal failure paths
  - PremiumCreateResult dataclass invariants

Network is never touched — remnawave_api.create_user is mocked.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import remnawave_premium


SAMPLE_UUID = "11111111-2222-3333-4444-555555555555"
PANEL_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# ── build_premium_username ─────────────────────────────────────────────

class TestBuildPremiumUsername:
    def test_default_pattern(self):
        assert remnawave_premium.build_premium_username(12345) == "tg_12345_premium"

    def test_clamps_to_32_chars(self):
        # 19-digit telegram id would overflow → clamped to 32
        long_id = 1234567890123456789
        result = remnawave_premium.build_premium_username(long_id)
        assert len(result) <= 32
        assert result.startswith("tg_")

    def test_existing_username_substitution(self):
        with patch.object(remnawave_premium, "config") as cfg:
            cfg.REMNAWAVE_PREMIUM_USERNAME_PATTERN = "{existing_username}_p"
            cfg.REMNAWAVE_ENABLED = True
            out = remnawave_premium.build_premium_username(99, existing_username="alice")
        assert out == "alice_p"

    def test_falls_back_on_bad_pattern(self):
        with patch.object(remnawave_premium, "config") as cfg:
            cfg.REMNAWAVE_PREMIUM_USERNAME_PATTERN = "{nope}"
            out = remnawave_premium.build_premium_username(77)
        assert out == "tg_77_premium"


# ── create_premium_user_entity ─────────────────────────────────────────

def _cfg_stub(**overrides):
    """Build a config mock with sensible defaults for the migration path."""
    cfg = type("Cfg", (), {})()
    cfg.REMNAWAVE_ENABLED = True
    cfg.REMNAWAVE_MAIN_SQUAD_UUID = "main-squad-uuid"
    cfg.REMNAWAVE_PREMIUM_FORCE_UUID = True
    cfg.REMNAWAVE_PREMIUM_DEVICE_LIMIT = 5
    cfg.REMNAWAVE_PREMIUM_USERNAME_PATTERN = "tg_{telegram_id}_premium"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


@pytest.mark.asyncio
async def test_create_premium_user_disabled_returns_failure():
    with patch.object(remnawave_premium, "config", _cfg_stub(REMNAWAVE_ENABLED=False)):
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result.ok is False
    assert result.error == "remnawave_disabled"
    assert result.panel_uuid is None


@pytest.mark.asyncio
async def test_create_premium_user_happy_path_forced_uuid_accepted():
    panel_response = {
        "ok": True,
        "status": 201,
        "response": {
            "uuid": SAMPLE_UUID,  # panel honoured our request
            "subscriptionUrl": "https://rmnw.atlassecure.ru/api/sub/abc123",
        },
        "body": None,
    }
    api_mock = AsyncMock(return_value=panel_response)
    with patch.object(remnawave_premium, "config", _cfg_stub()), \
         patch.object(remnawave_premium.remnawave_api, "create_user", api_mock):
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )

    assert result.ok is True
    assert result.panel_uuid == SAMPLE_UUID
    assert result.forced_uuid_accepted is True
    assert result.subscription_url.endswith("/abc123")
    api_mock.assert_called_once()
    # The first call must include the forced uuid kwarg
    kwargs = api_mock.call_args.kwargs
    assert kwargs["uuid"] == SAMPLE_UUID
    assert kwargs["squad_uuid"] == "main-squad-uuid"
    assert kwargs["traffic_limit_bytes"] == 0
    assert kwargs["telegram_id"] == 42
    assert kwargs["raw_response"] is True


@pytest.mark.asyncio
async def test_create_premium_user_falls_back_when_forced_uuid_rejected():
    # First call returns 400 (forced uuid rejected), second call (without uuid) succeeds.
    first_attempt = {"ok": False, "status": 400, "body": {"error": "bad uuid"}, "response": None}
    second_attempt = {
        "ok": True,
        "status": 201,
        "response": {"uuid": PANEL_UUID, "subscriptionUrl": "https://r/sub/x"},
        "body": None,
    }
    api_mock = AsyncMock(side_effect=[first_attempt, second_attempt])
    with patch.object(remnawave_premium, "config", _cfg_stub()), \
         patch.object(remnawave_premium.remnawave_api, "create_user", api_mock):
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )

    assert api_mock.call_count == 2
    # second call must NOT have the forced uuid
    assert api_mock.call_args_list[0].kwargs["uuid"] == SAMPLE_UUID
    assert api_mock.call_args_list[1].kwargs["uuid"] is None
    assert result.ok is True
    assert result.panel_uuid == PANEL_UUID
    assert result.forced_uuid_accepted is False


@pytest.mark.asyncio
async def test_create_premium_user_does_not_retry_on_5xx():
    """Server errors are NOT considered uuid-related — surface to caller."""
    first_attempt = {"ok": False, "status": 503, "body": "panel down", "response": None}
    api_mock = AsyncMock(return_value=first_attempt)
    with patch.object(remnawave_premium, "config", _cfg_stub()), \
         patch.object(remnawave_premium.remnawave_api, "create_user", api_mock):
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert api_mock.call_count == 1
    assert result.ok is False
    assert result.status == 503


@pytest.mark.asyncio
async def test_create_premium_user_skip_forced_uuid_when_disabled_in_config():
    cfg = _cfg_stub(REMNAWAVE_PREMIUM_FORCE_UUID=False)
    panel_response = {
        "ok": True,
        "status": 201,
        "response": {"uuid": PANEL_UUID, "subscriptionUrl": "https://r/sub/x"},
        "body": None,
    }
    api_mock = AsyncMock(return_value=panel_response)
    with patch.object(remnawave_premium, "config", cfg), \
         patch.object(remnawave_premium.remnawave_api, "create_user", api_mock):
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert api_mock.call_args.kwargs["uuid"] is None  # not forced
    assert result.ok is True
    assert result.forced_uuid_accepted is False  # never forced → never accepted


@pytest.mark.asyncio
async def test_create_premium_user_handles_naive_datetime():
    cfg = _cfg_stub()
    panel_response = {
        "ok": True,
        "status": 201,
        "response": {"uuid": PANEL_UUID, "subscriptionUrl": "u"},
        "body": None,
    }
    api_mock = AsyncMock(return_value=panel_response)
    with patch.object(remnawave_premium, "config", cfg), \
         patch.object(remnawave_premium.remnawave_api, "create_user", api_mock):
        await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1),  # naive
        )
    # expire_at must have been formatted to ISO Z
    sent = api_mock.call_args.kwargs["expire_at"]
    assert sent.endswith("Z")
    assert "2030-01-01" in sent
