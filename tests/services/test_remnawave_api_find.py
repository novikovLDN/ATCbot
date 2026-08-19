"""
Unit tests for remnawave_api.find_user_by_username on Remnawave Panel 3.x.

В 3.x dedicated `/by-username/{name}` эндпоинт удалён общей политикой
уборки `/by-*/`. Замена — курсорный `GET /api/users/stream?username=…`,
где возвращается коллекция пользователей; при уникальном имени берём
первый элемент.

Смотри docs/REMNAWAVE_3_MIGRATION.md → §2.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import remnawave_api


def _envelope(users):
    """Стандартный envelope 3.x /users/stream ответа."""
    return {"users": list(users), "total": len(users), "nextCursor": None}


@pytest.mark.asyncio
async def test_find_user_returns_entity_on_hit():
    user = {
        "id": 382,
        "shortUuid": "short123",
        "username": "tg_42_premium",
        "telegramId": 42,
        "subscriptionUrl": "https://rmnw.atlassecure.ru/api/sub/short123",
    }
    req_mock = AsyncMock(return_value=_envelope([user]))
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out == user
    req_mock.assert_awaited_once()
    method, path = req_mock.call_args.args[0], req_mock.call_args.args[1]
    assert method == "GET"
    assert path == "/api/users/stream?username=tg_42_premium"


@pytest.mark.asyncio
async def test_find_user_returns_none_on_empty_stream():
    """Пустой users-array → username свободен."""
    req_mock = AsyncMock(return_value=_envelope([]))
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out is None


@pytest.mark.asyncio
async def test_find_user_returns_none_when_request_fails():
    """Транзитная ошибка _request (None) → пробрасываем None caller'у.

    caller (create_*_user_entity) трактует None как «попробуй POST» — это
    самое безопасное на flaky preflight.
    """
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
async def test_find_user_quotes_unsafe_username_chars():
    """Патологические usernames не должны сломать URL."""
    req_mock = AsyncMock(return_value=_envelope([]))
    with patch.object(remnawave_api, "_request", req_mock):
        await remnawave_api.find_user_by_username("tg/42 weird?name")
    path = req_mock.call_args.args[1]
    # slash / space / ? — все должны быть percent-encoded.
    assert "tg%2F42%20weird%3Fname" in path


@pytest.mark.asyncio
async def test_find_user_by_telegram_id_uses_stream():
    """find_user_by_telegram_id: 3.x replacement for /by-telegram-id/."""
    user = {"id": 382, "username": "681274560", "telegramId": 681274560}
    req_mock = AsyncMock(return_value=_envelope([user]))
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.find_user_by_telegram_id(681274560)
    assert out == user
    path = req_mock.call_args.args[1]
    assert path == "/api/users/stream?telegramId=681274560"
