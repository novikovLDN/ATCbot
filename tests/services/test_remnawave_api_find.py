"""
Unit tests for remnawave_api.find_user_by_username — the preflight helper
that probes /api/users/by-username/{name} first and falls back to a
paginated list filter on 405 / 501.
"""
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from app.services import remnawave_api


@pytest.fixture(autouse=True)
def _reset_strategy_cache():
    remnawave_api._reset_find_strategy_for_tests()
    yield
    remnawave_api._reset_find_strategy_for_tests()


# ── _extract_list_items ────────────────────────────────────────────────

class TestExtractListItems:
    def test_flat_list(self):
        assert remnawave_api._extract_list_items([{"a": 1}]) == [{"a": 1}]

    def test_items_envelope(self):
        assert remnawave_api._extract_list_items({"items": [{"a": 1}]}) == [{"a": 1}]

    def test_users_envelope(self):
        assert remnawave_api._extract_list_items({"users": [{"a": 1}]}) == [{"a": 1}]

    def test_response_within_response(self):
        assert remnawave_api._extract_list_items({"response": {"users": [{"x": 1}]}}) == [{"x": 1}]

    def test_unknown_shape_returns_none(self):
        assert remnawave_api._extract_list_items({"foo": "bar"}) is None
        assert remnawave_api._extract_list_items(None) is None


# ── Helpers ───────────────────────────────────────────────────────────

def _raw(status: int, *, response: Any = None, body: Any = None, ok: bool = None):
    if ok is None:
        ok = status < 400
    return {"ok": ok, "status": status, "response": response, "body": body}


# ── find_user_by_username — dedicated endpoint ─────────────────────────

@pytest.mark.asyncio
async def test_find_user_uses_dedicated_endpoint_on_200():
    user = {"uuid": "u1", "username": "tg_42_premium", "telegramId": 42}
    raw_mock = AsyncMock(return_value=_raw(200, response=user))
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out == user
    # Only the dedicated endpoint was queried.
    raw_mock.assert_awaited_once()
    args, _kwargs = raw_mock.call_args
    assert args[0] == "GET"
    assert "by-username" in args[1]


@pytest.mark.asyncio
async def test_find_user_returns_none_on_404():
    raw_mock = AsyncMock(return_value=_raw(404, body="not found"))
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out is None
    # Cached "by_username" as winner — no fallback list calls expected.
    assert raw_mock.await_count == 1


@pytest.mark.asyncio
async def test_find_user_falls_back_on_405():
    by_name_resp = _raw(405, body="method not allowed")
    page_resp = _raw(200, response={
        "items": [
            {"username": "other_user"},
            {"username": "tg_42_premium", "uuid": "found-uuid"},
        ]
    })
    raw_mock = AsyncMock(side_effect=[by_name_resp, page_resp])
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out == {"username": "tg_42_premium", "uuid": "found-uuid"}
    assert raw_mock.await_count == 2
    # Second call must be /api/users (not /api/users/by-username/...)
    assert raw_mock.call_args_list[1].args[1] == "/api/users"
    params = raw_mock.call_args_list[1].kwargs["params"]
    # size+page is the first style probed
    assert "size" in params and "page" in params


@pytest.mark.asyncio
async def test_find_user_paginates_until_match_or_short_page():
    by_name_resp = _raw(405)
    # 3 pages: first two full of unrelated users, third has the match.
    page1 = _raw(200, response={"items": [{"username": f"u{i}"} for i in range(100)]})
    page2 = _raw(200, response={"items": [{"username": f"v{i}"} for i in range(100)]})
    page3 = _raw(200, response={"items": [
        {"username": "tg_42_premium", "uuid": "target"},
    ]})
    raw_mock = AsyncMock(side_effect=[by_name_resp, page1, page2, page3])
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out == {"username": "tg_42_premium", "uuid": "target"}
    # 1 dedicated probe + 3 list pages
    assert raw_mock.await_count == 4


@pytest.mark.asyncio
async def test_find_user_returns_none_when_not_on_any_page():
    by_name_resp = _raw(405)
    short_page = _raw(200, response={"items": [{"username": "someone-else"}]})  # less than page_size → last page
    raw_mock = AsyncMock(side_effect=[by_name_resp, short_page])
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        out = await remnawave_api.find_user_by_username("tg_42_premium")
    assert out is None
    assert raw_mock.await_count == 2


@pytest.mark.asyncio
async def test_find_user_strategy_is_sticky_after_first_call():
    """Once /by-username/ is confirmed working, subsequent calls don't probe again."""
    raw_mock = AsyncMock(side_effect=[
        _raw(404),                 # 1st call → username free, but strategy=by_username cached
        _raw(200, response={"uuid": "u2", "username": "tg_43_premium"}),  # 2nd call → goes straight to by-username
    ])
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        first = await remnawave_api.find_user_by_username("tg_42_premium")
        second = await remnawave_api.find_user_by_username("tg_43_premium")
    assert first is None
    assert second["uuid"] == "u2"
    # Both calls were to the dedicated endpoint — never to /api/users
    for call in raw_mock.call_args_list:
        assert "by-username" in call.args[1]


@pytest.mark.asyncio
async def test_find_user_empty_username_short_circuits():
    raw_mock = AsyncMock()
    with patch.object(remnawave_api, "_request_raw", raw_mock):
        out = await remnawave_api.find_user_by_username("")
    assert out is None
    raw_mock.assert_not_called()
