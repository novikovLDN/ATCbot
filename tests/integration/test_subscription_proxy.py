"""
Tests for app/api/subscription_proxy.py — legacy uuid → Remnawave redirect.

Uses FastAPI's TestClient so the routes are exercised end-to-end with
`remnawave_api.get_user` and `database` lookups mocked out.
"""
from typing import Optional  # noqa: F401  (used by _migrated_row default)
from unittest.mock import AsyncMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")  # not in dev requirements; skip if missing
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI  # noqa: E402

from app.api import subscription_proxy  # noqa: E402


def _app():
    app = FastAPI()
    app.include_router(subscription_proxy.router)
    return app


SAMOPIS_UUID = "11111111-2222-3333-4444-555555555555"
PANEL_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PANEL_SUB_URL = "https://rmnw.atlassecure.ru/api/sub/AbCdEf"


def _migrated_row(sub_url: Optional[str] = PANEL_SUB_URL):
    return {
        "telegram_id": 42,
        "remnawave_premium_uuid": PANEL_UUID,
        "remnawave_premium_sub_url": sub_url,
        "remnawave_uuid": None,
        "status": "active",
        "subscription_type": "basic",
        "expires_at": "2030-01-01",
        "samopis_migrated_at": "2026-05-12",
    }


def test_legacy_sub_uses_cached_sub_url_without_calling_panel():
    """Row has remnawave_premium_sub_url → router redirects without GET /api/users."""
    panel_mock = AsyncMock()  # must NOT be called
    with patch.object(subscription_proxy, "remnawave_api") as api_mock, \
         patch("database.get_subscription_by_premium_uuid",
               new=AsyncMock(return_value=_migrated_row())), \
         patch("database.get_subscription_by_samopis_uuid",
               new=AsyncMock(return_value=None)), \
         patch("database.set_remnawave_premium_sub_url",
               new=AsyncMock()):
        api_mock.get_user = panel_mock
        client = TestClient(_app())
        resp = client.get(f"/sub/{PANEL_UUID}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == PANEL_SUB_URL
    panel_mock.assert_not_called()


def test_legacy_sub_backfills_cache_on_miss():
    """Legacy row without cached sub_url → router calls panel + back-fills."""
    backfill_mock = AsyncMock()
    panel_mock = AsyncMock(return_value={"subscriptionUrl": PANEL_SUB_URL})
    with patch.object(subscription_proxy, "remnawave_api") as api_mock, \
         patch("database.get_subscription_by_premium_uuid",
               new=AsyncMock(return_value=_migrated_row(sub_url=None))), \
         patch("database.get_subscription_by_samopis_uuid",
               new=AsyncMock(return_value=None)), \
         patch("database.set_remnawave_premium_sub_url", new=backfill_mock):
        api_mock.get_user = panel_mock
        client = TestClient(_app())
        resp = client.get(f"/sub/{PANEL_UUID}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == PANEL_SUB_URL
    panel_mock.assert_called_once()
    backfill_mock.assert_awaited_once_with(42, PANEL_SUB_URL)


def test_legacy_sub_falls_back_to_samopis_for_unmigrated_user():
    unmigrated = {
        "telegram_id": 7,
        "uuid": SAMOPIS_UUID,
        "remnawave_premium_uuid": None,
        "remnawave_uuid": None,
        "status": "active",
        "subscription_type": "basic",
        "expires_at": "2030-01-01",
        "samopis_migrated_at": None,
    }
    with patch.object(subscription_proxy, "config") as cfg, \
         patch.object(subscription_proxy, "remnawave_api"), \
         patch("database.get_subscription_by_premium_uuid",
               new=AsyncMock(return_value=None)), \
         patch("database.get_subscription_by_samopis_uuid",
               new=AsyncMock(return_value=unmigrated)):
        cfg.LEGACY_SAMOPIS_SUB_BASE_URL = "https://api.mynewllcw.com"
        client = TestClient(_app())
        resp = client.get(f"/sub/{SAMOPIS_UUID}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == f"https://api.mynewllcw.com/sub/{SAMOPIS_UUID}"


def test_legacy_sub_returns_404_when_unknown_and_no_legacy_base():
    with patch.object(subscription_proxy, "config") as cfg, \
         patch("database.get_subscription_by_premium_uuid",
               new=AsyncMock(return_value=None)), \
         patch("database.get_subscription_by_samopis_uuid",
               new=AsyncMock(return_value=None)):
        cfg.LEGACY_SAMOPIS_SUB_BASE_URL = ""
        client = TestClient(_app())
        resp = client.get("/sub/abcd1234abcd1234", follow_redirects=False)

    assert resp.status_code == 404


def test_api_sub_route_redirects_migrated_user():
    """Cached sub_url takes the API path zero round-trips."""
    panel_mock = AsyncMock()
    with patch.object(subscription_proxy, "remnawave_api") as api_mock, \
         patch("database.get_subscription_by_premium_uuid",
               new=AsyncMock(return_value=_migrated_row())), \
         patch("database.get_subscription_by_samopis_uuid",
               new=AsyncMock(return_value=None)), \
         patch("database.set_remnawave_premium_sub_url", new=AsyncMock()):
        api_mock.get_user = panel_mock
        client = TestClient(_app())
        resp = client.get(f"/api/sub/{PANEL_UUID}?id=42", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == PANEL_SUB_URL
    panel_mock.assert_not_called()


def test_legacy_sub_returns_404_when_panel_lookup_returns_no_url():
    """Migrated row exists, cache empty, panel returns no URL → 404."""
    with patch.object(subscription_proxy, "remnawave_api") as api_mock, \
         patch.object(subscription_proxy, "config") as cfg, \
         patch("database.get_subscription_by_premium_uuid",
               new=AsyncMock(return_value=_migrated_row(sub_url=None))), \
         patch("database.get_subscription_by_samopis_uuid",
               new=AsyncMock(return_value=None)), \
         patch("database.set_remnawave_premium_sub_url", new=AsyncMock()):
        api_mock.get_user = AsyncMock(return_value=None)
        cfg.LEGACY_SAMOPIS_SUB_BASE_URL = ""
        client = TestClient(_app())
        resp = client.get(f"/sub/{PANEL_UUID}", follow_redirects=False)

    assert resp.status_code == 404


def test_legacy_sub_path_length_validation():
    """Routes reject obviously-bogus uuids early via FastAPI Path() limits."""
    client = TestClient(_app())
    # too short
    resp = client.get("/sub/abc", follow_redirects=False)
    assert resp.status_code in (404, 422)
