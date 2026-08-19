"""
Unit tests for remnawave_api.find_user_by_username on Remnawave v3.x.

Панель 3.x переехала `/api/users/by-username/{name}` → `/api/users/username/{name}`
(убрали `by-` префикс). Endpoint возвращает entity на 200 и errorCode "A063"
на 404 когда username свободен. Fallback list pagination path из старых
ревизий модуля выпилен; эти тесты пинят поведение.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import remnawave_api


def _raw(status: int, *, response=None, body=None, ok=None):
    if ok is None:
        ok = status < 400
    return {"ok": ok, "status": status, "response": response, "body": body}


@pytest.mark.asyncio
async def test_find_user_returns_entity_on_200():
    user = {
        "uuid": "panel-uuid",
        "vlessUuid": "vless-uuid",
        "shortUuid": "short123",
        "username": "tg_42_premium",
        "telegramId": 42,
        "subscriptionUrl": "https://rmnw.atlassecure.ru/api/sub/short123",
    }
    raw_mock = AsyncMock(return_value=_raw(200, response=user))
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out == user
    raw_mock.assert_awaited_once()
    method, path = raw_mock.call_args.args[0], raw_mock.call_args.args[1]
    assert method == "GET"
    assert path == "/api/users/username/tg_42_premium"


@pytest.mark.asyncio
async def test_find_user_returns_none_on_404():
    """404 with errorCode A063 → username is free."""
    body = {
        "timestamp": "2026-05-12T19:19:58.895Z",
        "path": "/api/users/username/test_nonexistent_xxx",
        "message": "User with specified params not found",
        "errorCode": "A063",
    }
    raw_mock = AsyncMock(return_value=_raw(404, body=body))
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out is None
    raw_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_user_returns_none_on_5xx_without_claiming_username_free():
    """Transient panel errors → return None and let the caller decide.

    The caller (create_premium_user_entity) treats None as "username
    might or might not be free, just try the POST" which is the safest
    behaviour for a hiccup-during-preflight.
    """
    raw_mock = AsyncMock(return_value=_raw(503, body="panel down"))
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out is None


@pytest.mark.asyncio
async def test_find_user_empty_username_short_circuits():
    raw_mock = AsyncMock()
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        out = await remnawave_api.find_user_by_username("")
    assert out is None
    raw_mock.assert_not_called()


@pytest.mark.asyncio
async def test_find_user_quotes_unsafe_username_chars():
    """Pathological usernames must not break the path."""
    raw_mock = AsyncMock(return_value=_raw(404))
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        await remnawave_api.find_user_by_username("tg/42 weird?name")
    path = raw_mock.call_args.args[1]
    assert "tg%2F42%20weird%3Fname" in path
