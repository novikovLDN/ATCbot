"""Remnawave 3.4.3 proxy-check middleware (src/common/middlewares/
proxy-check.middleware.ts): outside dev the panel closes the socket on any
request without X-Forwarded-For AND X-Forwarded-Proto: https. The bot must
send both itself, so a direct backend REMNAWAVE_API_URL works too (behind the
HTTPS proxy they are harmless: the proxy overwrites/extends them).
docs/providers/remnawave_3.4.3.md F12.
"""
import httpx
import pytest

from app.services import remnawave_api


@pytest.fixture
def captured(monkeypatch):
    seen = []

    async def fake_request(self, method, url, headers=None, **kwargs):
        seen.append(dict(headers or {}))
        return httpx.Response(200, json={"response": {"id": 1}}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    return seen


def _assert_forwarded(headers):
    assert headers.get("X-Forwarded-Proto") == "https"
    assert headers.get("X-Forwarded-For") == "127.0.0.1"
    assert headers.get("Authorization", "").startswith("Bearer ")


async def test_request_sends_forwarded_headers(captured):
    assert await remnawave_api._request("GET", "/api/users/1") == {"id": 1}
    _assert_forwarded(captured[0])


async def test_request_raw_sends_forwarded_headers(captured):
    out = await remnawave_api._request_raw("GET", "/api/users/1")
    assert out["ok"] is True
    _assert_forwarded(captured[0])
