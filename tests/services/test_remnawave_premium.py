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


def _patch_api(cfg, *, find=None, create=None):
    """Patch config + remnawave_api.find_user_by_username + create_user.

    Returns the tuple of mocks so individual tests can introspect call counts.
    """
    if find is None:
        find = AsyncMock(return_value=None)
    if create is None:
        create = AsyncMock()
    return (
        patch.object(remnawave_premium, "config", cfg),
        patch.object(remnawave_premium.remnawave_api, "find_user_by_username", find),
        patch.object(remnawave_premium.remnawave_api, "create_user", create),
        find,
        create,
    )


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
            # Remnawave v2.7+: `uuid` is panel-assigned, `vlessUuid` is the
            # field that reflects our forced value.
            "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "vlessUuid": SAMPLE_UUID,  # panel honoured our forced UUID
            "shortUuid": "abc123",
            "subscriptionUrl": "https://rmnw.atlassecure.ru/api/sub/abc123",
        },
        "body": None,
    }
    find_mock = AsyncMock(return_value=None)
    create_mock = AsyncMock(return_value=panel_response)
    p_cfg, p_find, p_create, _, _ = _patch_api(_cfg_stub(), find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )

    find_mock.assert_awaited_once_with("tg_42_premium")
    assert result.ok is True
    # panel_uuid is the INTERNAL panel id, not our forced value.
    assert result.panel_uuid == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert result.forced_uuid_accepted is True  # vlessUuid matched
    assert result.recovered is False
    assert result.short_uuid == "abc123"
    assert result.subscription_url.endswith("/abc123")
    create_mock.assert_awaited_once()
    kwargs = create_mock.call_args.kwargs
    # remnawave_api.create_user takes `uuid` as the forced-vless-uuid param;
    # the body field rename to `vlessUuid` happens inside create_user itself.
    assert kwargs["uuid"] == SAMPLE_UUID
    assert kwargs["squad_uuid"] == "main-squad-uuid"
    assert kwargs["traffic_limit_bytes"] == 0
    assert kwargs["telegram_id"] == 42
    assert kwargs["raw_response"] is True


@pytest.mark.asyncio
async def test_forced_uuid_accepted_is_false_when_panel_substitutes_value():
    """Panel returns its own vlessUuid → forced_uuid_accepted is False."""
    panel_response = {
        "ok": True,
        "status": 201,
        "response": {
            "uuid": "panel-internal",
            "vlessUuid": "panel-generated-different-uuid",
            "shortUuid": "xyz",
            "subscriptionUrl": "u",
        },
    }
    find_mock = AsyncMock(return_value=None)
    create_mock = AsyncMock(return_value=panel_response)
    p_cfg, p_find, p_create, _, _ = _patch_api(_cfg_stub(), find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result.ok is True
    assert result.forced_uuid_accepted is False
    assert result.short_uuid == "xyz"


@pytest.mark.asyncio
async def test_create_premium_user_falls_back_when_forced_uuid_rejected():
    # 400 (forced uuid rejected), then second call (without uuid) succeeds.
    first_attempt = {"ok": False, "status": 400, "body": {"error": "bad uuid"}, "response": None}
    second_attempt = {
        "ok": True,
        "status": 201,
        "response": {
            "uuid": PANEL_UUID,
            "vlessUuid": "panel-fresh-vless",
            "shortUuid": "xs",
            "subscriptionUrl": "https://r/sub/x",
        },
        "body": None,
    }
    find_mock = AsyncMock(return_value=None)
    create_mock = AsyncMock(side_effect=[first_attempt, second_attempt])
    p_cfg, p_find, p_create, _, _ = _patch_api(_cfg_stub(), find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )

    assert create_mock.call_count == 2
    assert create_mock.call_args_list[0].kwargs["uuid"] == SAMPLE_UUID
    assert create_mock.call_args_list[1].kwargs["uuid"] is None
    assert result.ok is True
    assert result.panel_uuid == PANEL_UUID
    assert result.forced_uuid_accepted is False
    assert result.recovered is False
    assert result.short_uuid == "xs"


@pytest.mark.asyncio
async def test_create_premium_user_does_not_retry_on_5xx():
    """Server errors are NOT considered uuid-related — surface to caller."""
    first_attempt = {"ok": False, "status": 503, "body": "panel down", "response": None}
    find_mock = AsyncMock(return_value=None)
    create_mock = AsyncMock(return_value=first_attempt)
    p_cfg, p_find, p_create, _, _ = _patch_api(_cfg_stub(), find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert create_mock.call_count == 1
    assert result.ok is False
    assert result.status == 503


@pytest.mark.asyncio
async def test_create_premium_user_skip_forced_uuid_when_disabled_in_config():
    cfg = _cfg_stub(REMNAWAVE_PREMIUM_FORCE_UUID=False)
    panel_response = {
        "ok": True,
        "status": 201,
        "response": {
            "uuid": PANEL_UUID,
            "vlessUuid": "panel-vless",
            "shortUuid": "shrt",
            "subscriptionUrl": "https://r/sub/x",
        },
        "body": None,
    }
    find_mock = AsyncMock(return_value=None)
    create_mock = AsyncMock(return_value=panel_response)
    p_cfg, p_find, p_create, _, _ = _patch_api(cfg, find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert create_mock.call_args.kwargs["uuid"] is None  # not forced
    assert result.ok is True
    assert result.forced_uuid_accepted is False
    assert result.recovered is False
    assert result.short_uuid == "shrt"


@pytest.mark.asyncio
async def test_create_premium_user_handles_naive_datetime():
    cfg = _cfg_stub()
    panel_response = {
        "ok": True,
        "status": 201,
        "response": {"uuid": PANEL_UUID, "vlessUuid": "v", "shortUuid": "s", "subscriptionUrl": "u"},
        "body": None,
    }
    find_mock = AsyncMock(return_value=None)
    create_mock = AsyncMock(return_value=panel_response)
    p_cfg, p_find, p_create, _, _ = _patch_api(cfg, find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1),  # naive
        )
    sent = create_mock.call_args.kwargs["expire_at"]
    assert sent.endswith("Z")
    assert "2030-01-01" in sent


# ── Preflight + recovery paths (added in follow-up review) ────────────

@pytest.mark.asyncio
async def test_preflight_recovers_our_entity_without_posting():
    """An entity with our description marker already exists → adopt it.

    Adoption triggers an idempotent PATCH (expireAt + status) so the
    panel reflects the caller's intent — see _ensure_premium_entity_state.
    """
    existing = {
        "uuid": PANEL_UUID,
        "vlessUuid": SAMPLE_UUID,
        "shortUuid": "rec123",
        "username": "tg_42_premium",
        "telegramId": 42,
        "description": "Imported from samopis vpnapi",
        "subscriptionUrl": "https://r/sub/from-recovery",
    }
    find_mock = AsyncMock(return_value=existing)
    create_mock = AsyncMock()
    update_mock = AsyncMock(return_value={"ok": True})
    p_cfg, p_find, p_create, _, _ = _patch_api(_cfg_stub(), find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create, patch.object(
        remnawave_premium.remnawave_api, "update_user", update_mock,
    ):
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result.ok is True
    assert result.recovered is True
    assert result.forced_uuid_accepted is False
    assert result.panel_uuid == PANEL_UUID
    assert result.subscription_url == "https://r/sub/from-recovery"
    assert result.short_uuid == "rec123"
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_refuses_when_username_owned_by_unrelated_user():
    """Existing entity with different telegramId and no marker → refuse (no overwrite)."""
    unrelated = {
        "uuid": PANEL_UUID,
        "username": "tg_42_premium",
        "telegramId": 99,
        "description": "manually created by admin",
        "subscriptionUrl": "https://r/sub/x",
    }
    find_mock = AsyncMock(return_value=unrelated)
    create_mock = AsyncMock()
    p_cfg, p_find, p_create, _, _ = _patch_api(_cfg_stub(), find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result.ok is False
    assert result.error == "conflict_unrelated_user"
    assert result.recovered is False
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_post_409_triggers_username_lookup_and_recovers():
    """Preflight saw nothing → POST → 409 (created by concurrent run) → adopt."""
    first_call = {"ok": False, "status": 409, "body": "username taken", "response": None}
    existing_after_race = {
        "uuid": PANEL_UUID,
        "vlessUuid": SAMPLE_UUID,
        "shortUuid": "racesh",
        "username": "tg_42_premium",
        "telegramId": 42,
        "subscriptionUrl": "https://r/sub/race",
    }
    find_mock = AsyncMock(side_effect=[None, existing_after_race])  # first preflight, then 409-recovery
    create_mock = AsyncMock(return_value=first_call)
    update_mock = AsyncMock(return_value={"ok": True})
    p_cfg, p_find, p_create, _, _ = _patch_api(_cfg_stub(), find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create, patch.object(
        remnawave_premium.remnawave_api, "update_user", update_mock,
    ):
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert find_mock.await_count == 2
    create_mock.assert_called_once()
    assert result.ok is True
    assert result.recovered is True
    assert result.panel_uuid == PANEL_UUID


@pytest.mark.asyncio
async def test_post_409_with_unrelated_entity_fails_without_overwrite():
    first_call = {"ok": False, "status": 409, "body": "username taken", "response": None}
    unrelated = {
        "uuid": PANEL_UUID,
        "username": "tg_42_premium",
        "telegramId": 7,
        "description": "manual",
    }
    find_mock = AsyncMock(side_effect=[None, unrelated])
    create_mock = AsyncMock(return_value=first_call)
    p_cfg, p_find, p_create, _, _ = _patch_api(_cfg_stub(), find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result.ok is False
    assert result.recovered is False
    # Only the initial POST was made — no forced-uuid retry, because 409
    # never falls into the 400/422 retry branch.
    assert create_mock.call_count == 1


@pytest.mark.asyncio
async def test_preflight_exception_does_not_block_post():
    """Transient panel error during preflight → fall through to POST as usual."""
    find_mock = AsyncMock(side_effect=RuntimeError("panel timeout"))
    panel_response = {
        "ok": True, "status": 201,
        "response": {"uuid": PANEL_UUID, "vlessUuid": "v", "shortUuid": "s", "subscriptionUrl": "u"},
        "body": None,
    }
    create_mock = AsyncMock(return_value=panel_response)
    p_cfg, p_find, p_create, _, _ = _patch_api(_cfg_stub(), find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result.ok is True
    assert result.recovered is False
    create_mock.assert_called_once()


# ── _is_our_entity unit checks ────────────────────────────────────────

class TestIsOurEntity:
    def test_matches_on_telegram_id_int(self):
        assert remnawave_premium._is_our_entity({"telegramId": 42}, 42) is True

    def test_matches_on_telegram_id_str(self):
        assert remnawave_premium._is_our_entity({"telegramId": "42"}, 42) is True

    def test_matches_on_snake_case_telegram_id(self):
        assert remnawave_premium._is_our_entity({"telegram_id": 42}, 42) is True

    def test_matches_on_description_marker(self):
        assert remnawave_premium._is_our_entity(
            {"description": "Imported from samopis vpnapi (2026-05-12)"}, 42,
        ) is True

    def test_rejects_unrelated_user(self):
        assert remnawave_premium._is_our_entity(
            {"telegramId": 99, "description": "manually added"}, 42,
        ) is False

    def test_rejects_non_dict_input(self):
        assert remnawave_premium._is_our_entity(None, 42) is False
        assert remnawave_premium._is_our_entity("nope", 42) is False


# ── Task 6: externalSquadUuid on premium entities ─────────────────────

EXT_SQUAD_UUID = "894f4c66-a725-4a24-b0ba-f6ca2b6b5a79"


@pytest.mark.asyncio
async def test_create_premium_passes_external_squad_uuid_when_configured():
    """Task 6: POST forwards REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID
    to remnawave_api.create_user as the `external_squad_uuid` kwarg."""
    cfg = _cfg_stub(REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID=EXT_SQUAD_UUID)
    panel_response = {
        "ok": True, "status": 201,
        "response": {"uuid": PANEL_UUID, "vlessUuid": SAMPLE_UUID,
                     "shortUuid": "s", "subscriptionUrl": "u"},
        "body": None,
    }
    find_mock = AsyncMock(return_value=None)
    create_mock = AsyncMock(return_value=panel_response)
    p_cfg, p_find, p_create, _, _ = _patch_api(cfg, find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result.ok is True
    assert create_mock.call_args.kwargs["external_squad_uuid"] == EXT_SQUAD_UUID


@pytest.mark.asyncio
async def test_create_premium_omits_external_squad_uuid_when_unset():
    """When the env var is unset, create_user gets external_squad_uuid=None
    and the body field is omitted entirely (verified in api tests)."""
    cfg = _cfg_stub()  # no REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID
    panel_response = {
        "ok": True, "status": 201,
        "response": {"uuid": PANEL_UUID, "vlessUuid": SAMPLE_UUID,
                     "shortUuid": "s", "subscriptionUrl": "u"},
        "body": None,
    }
    find_mock = AsyncMock(return_value=None)
    create_mock = AsyncMock(return_value=panel_response)
    p_cfg, p_find, p_create, _, _ = _patch_api(cfg, find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create:
        await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert create_mock.call_args.kwargs["external_squad_uuid"] is None


@pytest.mark.asyncio
async def test_preflight_adoption_patches_expire_at_and_squad_when_set():
    """Adopting an existing entity → PATCH expireAt + status + externalSquadUuid."""
    cfg = _cfg_stub(REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID=EXT_SQUAD_UUID)
    existing = {
        "uuid": PANEL_UUID,
        "vlessUuid": SAMPLE_UUID,
        "shortUuid": "rec",
        "username": "tg_42_premium",
        "telegramId": 42,
        "subscriptionUrl": "https://r/sub/from-recovery",
    }
    find_mock = AsyncMock(return_value=existing)
    create_mock = AsyncMock()
    update_mock = AsyncMock(return_value={"ok": True})
    p_cfg, p_find, p_create, _, _ = _patch_api(cfg, find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create, patch.object(
        remnawave_premium.remnawave_api, "update_user", update_mock,
    ):
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result.recovered is True
    create_mock.assert_not_called()
    update_mock.assert_awaited_once()
    sent = update_mock.call_args
    assert sent.args[0] == PANEL_UUID
    assert sent.kwargs["expireAt"].startswith("2030-01-01")
    assert sent.kwargs["status"] == "ACTIVE"
    assert sent.kwargs["externalSquadUuid"] == EXT_SQUAD_UUID


@pytest.mark.asyncio
async def test_preflight_adoption_always_patches_for_idempotency():
    """Adoption PATCHes expireAt unconditionally — the entity may have a
    stale expireAt that we want to overwrite with the caller's value
    (the whole point of this fix)."""
    cfg = _cfg_stub(REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID=EXT_SQUAD_UUID)
    existing = {
        "uuid": PANEL_UUID,
        "vlessUuid": SAMPLE_UUID,
        "shortUuid": "rec",
        "username": "tg_42_premium",
        "telegramId": 42,
        "subscriptionUrl": "https://r/sub/from-recovery",
        # already has the right ext_squad — PATCH should still happen
        # because expireAt may be stale.
        "externalSquadUuid": EXT_SQUAD_UUID,
    }
    find_mock = AsyncMock(return_value=existing)
    create_mock = AsyncMock()
    update_mock = AsyncMock(return_value={"ok": True})
    p_cfg, p_find, p_create, _, _ = _patch_api(cfg, find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create, patch.object(
        remnawave_premium.remnawave_api, "update_user", update_mock,
    ):
        await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    update_mock.assert_awaited_once()
    assert update_mock.call_args.kwargs["expireAt"].startswith("2030-01-01")


@pytest.mark.asyncio
async def test_preflight_adoption_patches_expire_at_only_when_squad_unset():
    """No config var → PATCH still carries expireAt + status, but omits
    externalSquadUuid."""
    cfg = _cfg_stub()
    existing = {
        "uuid": PANEL_UUID,
        "vlessUuid": SAMPLE_UUID,
        "shortUuid": "rec",
        "username": "tg_42_premium",
        "telegramId": 42,
        "subscriptionUrl": "u",
    }
    find_mock = AsyncMock(return_value=existing)
    create_mock = AsyncMock()
    update_mock = AsyncMock(return_value={"ok": True})
    p_cfg, p_find, p_create, _, _ = _patch_api(cfg, find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create, patch.object(
        remnawave_premium.remnawave_api, "update_user", update_mock,
    ):
        await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    update_mock.assert_awaited_once()
    kwargs = update_mock.call_args.kwargs
    assert kwargs["expireAt"].startswith("2030-01-01")
    assert kwargs["status"] == "ACTIVE"
    assert "externalSquadUuid" not in kwargs


@pytest.mark.asyncio
async def test_post409_adoption_patches_expire_at_and_squad():
    """409 recovery adoption path also extends expireAt and stamps squad."""
    cfg = _cfg_stub(REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID=EXT_SQUAD_UUID)
    first_call = {"ok": False, "status": 409, "body": "username taken", "response": None}
    existing_after_race = {
        "uuid": PANEL_UUID,
        "vlessUuid": SAMPLE_UUID,
        "shortUuid": "racesh",
        "username": "tg_42_premium",
        "telegramId": 42,
        "subscriptionUrl": "https://r/sub/race",
    }
    find_mock = AsyncMock(side_effect=[None, existing_after_race])
    create_mock = AsyncMock(return_value=first_call)
    update_mock = AsyncMock(return_value={"ok": True})
    p_cfg, p_find, p_create, _, _ = _patch_api(cfg, find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create, patch.object(
        remnawave_premium.remnawave_api, "update_user", update_mock,
    ):
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result.recovered is True
    update_mock.assert_awaited_once()
    kwargs = update_mock.call_args.kwargs
    assert kwargs["expireAt"].startswith("2030-01-01")
    assert kwargs["externalSquadUuid"] == EXT_SQUAD_UUID


@pytest.mark.asyncio
async def test_adoption_patch_failure_does_not_break_recovery():
    """If the PATCH itself raises, adoption still succeeds (next renewal retries)."""
    cfg = _cfg_stub(REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID=EXT_SQUAD_UUID)
    existing = {
        "uuid": PANEL_UUID,
        "vlessUuid": SAMPLE_UUID,
        "shortUuid": "rec",
        "username": "tg_42_premium",
        "telegramId": 42,
        "subscriptionUrl": "u",
    }
    find_mock = AsyncMock(return_value=existing)
    create_mock = AsyncMock()
    update_mock = AsyncMock(side_effect=RuntimeError("panel down"))
    p_cfg, p_find, p_create, _, _ = _patch_api(cfg, find=find_mock, create=create_mock)
    with p_cfg, p_find, p_create, patch.object(
        remnawave_premium.remnawave_api, "update_user", update_mock,
    ):
        result = await remnawave_premium.create_premium_user_entity(
            42,
            requested_uuid=SAMPLE_UUID,
            expire_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result.ok is True
    assert result.recovered is True


# ── renew_premium_user: externalSquadUuid safety net ──────────────────

@pytest.mark.asyncio
async def test_renew_premium_user_includes_external_squad_uuid_when_set():
    """Renewal PATCH carries externalSquadUuid alongside expireAt (idempotent stamp)."""
    cfg = _cfg_stub(REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID=EXT_SQUAD_UUID)
    update_mock = AsyncMock(return_value={"ok": True})
    db_mock = type("DB", (), {})()

    async def fake_get_uuid(tg):
        return PANEL_UUID
    db_mock.get_remnawave_premium_uuid = fake_get_uuid

    with patch.object(remnawave_premium, "config", cfg), \
         patch.object(remnawave_premium.remnawave_api, "update_user", update_mock), \
         patch.dict("sys.modules", {"database": db_mock}):
        ok = await remnawave_premium.renew_premium_user(
            42, datetime(2030, 6, 1, tzinfo=timezone.utc),
        )
    assert ok is True
    update_mock.assert_awaited_once()
    sent_uuid = update_mock.call_args.args[0]
    sent_fields = update_mock.call_args.kwargs
    assert sent_uuid == PANEL_UUID
    assert sent_fields["expireAt"].endswith("Z")
    assert sent_fields["status"] == "ACTIVE"
    assert sent_fields["externalSquadUuid"] == EXT_SQUAD_UUID


@pytest.mark.asyncio
async def test_renew_premium_user_omits_external_squad_uuid_when_unset():
    """Without the config var the PATCH carries only expireAt + status."""
    cfg = _cfg_stub()
    update_mock = AsyncMock(return_value={"ok": True})
    db_mock = type("DB", (), {})()

    async def fake_get_uuid(tg):
        return PANEL_UUID
    db_mock.get_remnawave_premium_uuid = fake_get_uuid

    with patch.object(remnawave_premium, "config", cfg), \
         patch.object(remnawave_premium.remnawave_api, "update_user", update_mock), \
         patch.dict("sys.modules", {"database": db_mock}):
        await remnawave_premium.renew_premium_user(
            42, datetime(2030, 6, 1, tzinfo=timezone.utc),
        )
    assert "externalSquadUuid" not in update_mock.call_args.kwargs


# ── renew_premium_user: retry logic (renewal-not-applied bug fix) ─────

@pytest.mark.asyncio
async def test_renew_premium_user_succeeds_on_second_attempt():
    """Transient panel failure on attempt 1, success on attempt 2 → True."""
    cfg = _cfg_stub()
    # First call returns None (HTTP error), second returns success
    update_mock = AsyncMock(side_effect=[None, {"ok": True}])
    sleep_mock = AsyncMock()
    db_mock = type("DB", (), {})()

    async def fake_get_uuid(tg):
        return PANEL_UUID
    db_mock.get_remnawave_premium_uuid = fake_get_uuid

    with patch.object(remnawave_premium, "config", cfg), \
         patch.object(remnawave_premium.remnawave_api, "update_user", update_mock), \
         patch.object(remnawave_premium.asyncio, "sleep", sleep_mock), \
         patch.dict("sys.modules", {"database": db_mock}):
        ok = await remnawave_premium.renew_premium_user(
            42, datetime(2030, 6, 1, tzinfo=timezone.utc),
        )
    assert ok is True
    assert update_mock.await_count == 2
    sleep_mock.assert_awaited_once()  # 1s backoff between attempts


@pytest.mark.asyncio
async def test_renew_premium_user_retries_3_times_then_gives_up():
    """All 3 attempts return None → renew returns False (caller falls back
    to create-then-adopt, which now PATCHes expireAt via
    _ensure_premium_entity_state)."""
    cfg = _cfg_stub()
    update_mock = AsyncMock(return_value=None)
    sleep_mock = AsyncMock()
    db_mock = type("DB", (), {})()

    async def fake_get_uuid(tg):
        return PANEL_UUID
    db_mock.get_remnawave_premium_uuid = fake_get_uuid

    with patch.object(remnawave_premium, "config", cfg), \
         patch.object(remnawave_premium.remnawave_api, "update_user", update_mock), \
         patch.object(remnawave_premium.asyncio, "sleep", sleep_mock), \
         patch.dict("sys.modules", {"database": db_mock}):
        ok = await remnawave_premium.renew_premium_user(
            42, datetime(2030, 6, 1, tzinfo=timezone.utc),
        )
    assert ok is False
    assert update_mock.await_count == 3
    assert sleep_mock.await_count == 2  # 2 backoffs between 3 attempts


@pytest.mark.asyncio
async def test_renew_premium_user_retries_on_exception_too():
    """An exception in update_user is treated like a None return →
    counts as a failed attempt, retry kicks in."""
    cfg = _cfg_stub()
    update_mock = AsyncMock(side_effect=[
        RuntimeError("panel boom"),
        {"ok": True},
    ])
    sleep_mock = AsyncMock()
    db_mock = type("DB", (), {})()

    async def fake_get_uuid(tg):
        return PANEL_UUID
    db_mock.get_remnawave_premium_uuid = fake_get_uuid

    with patch.object(remnawave_premium, "config", cfg), \
         patch.object(remnawave_premium.remnawave_api, "update_user", update_mock), \
         patch.object(remnawave_premium.asyncio, "sleep", sleep_mock), \
         patch.dict("sys.modules", {"database": db_mock}):
        ok = await remnawave_premium.renew_premium_user(
            42, datetime(2030, 6, 1, tzinfo=timezone.utc),
        )
    assert ok is True
    assert update_mock.await_count == 2
