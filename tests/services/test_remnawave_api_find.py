"""Unit tests for remnawave_api.find_* on Remnawave Panel 3.x.

В 3.x поиск по username — не stream-фильтр (username там не работает),
а POST /api/users/resolve body {username|shortUuid|id|email|tag}.
Поиск по telegram_id — через /api/users/stream?telegramId=X (это
единственный из наших полей, который в списке stream-фильтров ТЗ).
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import remnawave_api


def _stream(users):
    return {"users": list(users), "total": len(users), "nextCursor": None}


@pytest.mark.asyncio
async def test_find_user_by_username_uses_resolve():
    user = {
        "id": 382,
        "shortUuid": "short123",
        "username": "tg_42_premium",
        "telegramId": 42,
    }
    # /resolve returns a trimmed user without subscriptionUrl → since
    # dc5b8d50 the full entity is fetched via GET /api/users/{id} and merged.
    full = {**user, "subscriptionUrl": "https://rmnw/sub/short123"}
    req_mock = AsyncMock(side_effect=[user, full])
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out == full
    assert req_mock.await_count == 2
    first, second = req_mock.call_args_list
    assert first.args[:2] == ("POST", "/api/users/resolve")
    assert first.kwargs["json"] == {"username": "tg_42_premium"}
    assert second.args[:2] == ("GET", "/api/users/382")


@pytest.mark.asyncio
async def test_find_user_by_username_skips_full_fetch_when_url_present():
    user = {
        "id": 382,
        "username": "tg_42_premium",
        "trafficLimitBytes": 0,
        "expireAt": "2030-01-01T00:00:00.000Z",
        "subscriptionUrl": "https://rmnw/sub/short123",
    }
    req_mock = AsyncMock(return_value=user)
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out == user
    req_mock.assert_awaited_once()
    assert req_mock.call_args.args[:2] == ("POST", "/api/users/resolve")


@pytest.mark.asyncio
async def test_find_user_by_username_returns_none_on_404():
    req_mock = AsyncMock(return_value=None)
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out is None


@pytest.mark.asyncio
async def test_find_user_empty_username_short_circuits():
    req_mock = AsyncMock()
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("")
    assert out is None
    req_mock.assert_not_called()


@pytest.mark.asyncio
async def test_find_user_by_telegram_id_uses_stream():
    """find_user_by_telegram_id: /api/users/stream?telegramId=X (единственный
    из наших поисков, у которого stream-filter существует по ТЗ)."""
    user = {"id": 382, "username": "681274560", "telegramId": 681274560}
    req_mock = AsyncMock(return_value=_stream([user]))
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_telegram_id(681274560)
    assert out == user
    path = req_mock.call_args.args[1]
    assert path == "/api/users/stream?telegramId=681274560"


@pytest.mark.asyncio
async def test_find_user_by_short_uuid_uses_resolve():
    user = {"id": 382, "shortUuid": "short123"}
    req_mock = AsyncMock(return_value=user)
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_short_uuid("short123")
    assert out == user
    path = req_mock.call_args.args[1]
    assert path == "/api/users/resolve"
    assert req_mock.call_args.kwargs["json"] == {"shortUuid": "short123"}


@pytest.mark.asyncio
async def test_find_user_unwraps_response_user_key():
    """Панель может обернуть в {user: {...}} — расспаковываем."""
    user = {"id": 382, "username": "tg_42_premium", "trafficLimitBytes": 0,
            "expireAt": "2030-01-01T00:00:00.000Z", "subscriptionUrl": "https://rmnw/sub/x"}
    req_mock = AsyncMock(return_value={"user": user})
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out == user
    req_mock.assert_awaited_once()  # URL present → no GET enrichment


# libs/contract/commands/users/create-user.command.ts:18-120 (3.4.3)
CREATE_USER_KEYS = {
    "username", "status", "shortUuid", "trojanPassword", "vlessUuid", "ssPassword",
    "trafficLimitBytes", "trafficLimitStrategy", "expireAt", "createdAt",
    "lastTrafficResetAt", "description", "tag", "telegramId", "email",
    "hwidDeviceLimit", "activeInternalSquads", "externalSquadUuid",
}


@pytest.mark.asyncio
async def test_create_user_body_only_uses_3_4_3_fields():
    raw = AsyncMock(return_value={"ok": True, "status": 201, "body": {}, "response": {}})
    with patch.object(remnawave_api, "_request_raw", raw):
        await remnawave_api.create_user(
            "42", "abc-def", 10 * 1024 ** 3, "2099-12-31T23:59:59Z", 5,
            uuid="bbbbbbbb-0000-4000-8000-000000000001", squad_uuid="sq-uuid",
            description="bypass via bot", telegram_id=42,
            external_squad_uuid="cccccccc-0000-4000-8000-000000000002", raw_response=True,
        )
    assert raw.call_args.args == ("POST", "/api/users")
    body = raw.call_args.kwargs["json"]
    assert set(body) <= CREATE_USER_KEYS, set(body) - CREATE_USER_KEYS
    assert body["hwidDeviceLimit"] == 5
    assert body["trafficLimitStrategy"] == "NO_RESET" and body["status"] == "ACTIVE"
    assert body["activeInternalSquads"] == ["sq-uuid"]


BYPASS_VLESS = "bbbbbbbb-0000-4000-8000-000000000001"
PREMIUM_VLESS = "cccccccc-0000-4000-8000-000000000002"
BOTH = _stream([
    {"id": 22, "username": "tg_42_premium", "telegramId": 42, "vlessUuid": PREMIUM_VLESS},
    {"id": 12, "username": "42", "telegramId": 42, "vlessUuid": BYPASS_VLESS},
])


@pytest.fixture
def uncached(monkeypatch):
    monkeypatch.setattr(remnawave_api, "_lookup_cached_id_by_uuid", AsyncMock(return_value=None))
    monkeypatch.setattr(remnawave_api, "_lookup_telegram_id_by_uuid", AsyncMock(return_value=42))


@pytest.mark.asyncio
@pytest.mark.parametrize("value,expected", [(BYPASS_VLESS, 12), (PREMIUM_VLESS, 22)])
async def test_resolve_uuid_picks_entity_by_vless_uuid(uncached, value, expected):
    """stream?telegramId returns BOTH entities (premium first); the uuid must
    resolve to its own entity, not to the first one."""
    with patch.object(remnawave_api, "_request", AsyncMock(return_value=BOTH)):
        assert await remnawave_api._resolve_to_int_id(value) == expected


@pytest.mark.asyncio
async def test_resolve_unknown_uuid_with_two_entities_is_none(uncached):
    with patch.object(remnawave_api, "_request", AsyncMock(return_value=BOTH)):
        assert await remnawave_api._resolve_to_int_id("dddddddd-0000-4000-8000-000000000003") is None


@pytest.mark.asyncio
async def test_resolve_lone_entity_still_accepted(uncached):
    one = _stream([{"id": 12, "username": "42", "telegramId": 42, "vlessUuid": BYPASS_VLESS}])
    with patch.object(remnawave_api, "_request", AsyncMock(return_value=one)):
        assert await remnawave_api._resolve_to_int_id("eeeeeeee-0000-4000-8000-000000000004") == 12


# 3.4.3 ResolveUserCommand.ResponseSchema: {id, username, shortUuid} only.
TRIMMED = {"id": 382, "username": "42", "shortUuid": "short123"}


@pytest.mark.asyncio
async def test_find_user_by_username_failed_completion_is_none():
    """GET /api/users/{id} after /resolve fails → None, never the trimmed
    entity (no trafficLimitBytes → add_bypass_traffic wiped accumulated GB)."""
    req_mock = AsyncMock(side_effect=[TRIMMED, None])
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("42")
    assert out is None
    assert req_mock.await_count == 2


@pytest.mark.asyncio
async def test_find_user_by_username_completion_exception_is_none():
    req_mock = AsyncMock(side_effect=[TRIMMED, RuntimeError("boom")])
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("42")
    assert out is None


@pytest.mark.asyncio
async def test_bypass_entity_safe_does_not_return_trimmed_entity(monkeypatch):
    """get_bypass_entity_safe → username fallback with a failed completion
    must be 'no entity', so add_bypass_traffic cannot compute 0 + N."""
    import database
    monkeypatch.setattr(database, "get_remnawave_id", AsyncMock(return_value=None))
    set_id = AsyncMock()
    monkeypatch.setattr(database, "set_remnawave_id", set_id)
    req_mock = AsyncMock(side_effect=[TRIMMED, None])
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.get_bypass_entity_safe(42)
    assert out is None
    set_id.assert_not_awaited()


@pytest.mark.parametrize("raw,expected", [
    ({"status": 400, "body": {"message": "User username already exists", "errorCode": "A019"}}, True),
    ({"status": 400, "body": {"message": "whatever", "errorCode": "A019"}}, True),
    ({"status": 400, "body": "User username already exists"}, True),
    ({"status": 409, "body": "conflict"}, True),
    ({"status": 400, "body": {"message": "User short UUID already exists", "errorCode": "A020"}}, False),
    ({"status": 400, "body": {"message": "Validation failed", "errors": []}}, False),
    ({"status": 500, "body": {"message": "User username already exists"}}, False),
    ({"status": 0, "body": None}, False),
    (None, False),
])
def test_is_username_conflict_matches_3_4_3_a019(raw, expected):
    assert remnawave_api.is_username_conflict(raw) is expected
