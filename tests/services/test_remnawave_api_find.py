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
    req_mock = AsyncMock(return_value=user)
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out == user
    method, path = req_mock.call_args.args[0], req_mock.call_args.args[1]
    assert method == "POST"
    assert path == "/api/users/resolve"
    kwargs = req_mock.call_args.kwargs
    assert kwargs["json"] == {"username": "tg_42_premium"}


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
    user = {"id": 382, "username": "tg_42_premium"}
    req_mock = AsyncMock(return_value={"user": user})
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out == user


# ── Hotfix (e835827f): 3.4.3 answers a taken username with 400 A019 ───

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
