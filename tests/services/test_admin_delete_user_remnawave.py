"""Full admin user delete (dashboard DELETE /users/{id} → admin_delete_user_complete):
the Remnawave cleanup goes through remnawave_api.delete_user (3.x DELETE /api/users/{id}).

Regression: the raw call used DELETE /api/users/delete/{id}, a path that does not
exist in Remnawave 3.x (docs/providers/remnawave_3.4.3.md), so panel entities of
deleted users were never removed.
"""
from unittest.mock import AsyncMock

import pytest

from app.services import remnawave_api
from database import admin as db_admin


@pytest.fixture
def api(monkeypatch):
    delete = AsyncMock(return_value={})
    find = AsyncMock(return_value=None)
    monkeypatch.setattr(remnawave_api, "delete_user", delete)
    monkeypatch.setattr(remnawave_api, "find_user_by_username", find)
    monkeypatch.setattr(remnawave_api, "_request",
                        AsyncMock(side_effect=AssertionError("no raw panel request here")))
    return delete, find


async def test_numeric_id_is_deleted_through_delete_user(api):
    delete, find = api
    await db_admin._delete_remnawave_entity(5, numeric_id=42, uuid="u-1", username_hint="5")
    delete.assert_awaited_once_with(42)
    find.assert_not_awaited()


async def test_without_numeric_id_resolves_by_our_username(api):
    delete, find = api
    find.return_value = {"id": "77", "uuid": "u-2"}
    await db_admin._delete_remnawave_entity(5, numeric_id=None, uuid="u-2", username_hint="tg_5_premium")
    find.assert_awaited_once_with("tg_5_premium")
    delete.assert_awaited_once_with(77)


async def test_unresolvable_entity_is_skipped(api):
    delete, find = api
    await db_admin._delete_remnawave_entity(5, numeric_id=None, uuid="u-3", username_hint="5")
    find.assert_awaited_once_with("5")
    delete.assert_not_awaited()


async def test_no_panel_entity_makes_no_call(api):
    delete, find = api
    await db_admin._delete_remnawave_entity(5, numeric_id=None, uuid=None, username_hint="5")
    delete.assert_not_awaited()
    find.assert_not_awaited()


async def test_panel_error_is_logged_not_raised(api, caplog):
    delete, _ = api
    delete.side_effect = RuntimeError("panel down")
    await db_admin._delete_remnawave_entity(5, numeric_id=42, uuid="u-4", username_hint="5")
    assert "REMNAWAVE_ADMIN_DELETE_ENTITY_FAIL" in caplog.text


async def test_delete_user_uses_the_3x_path(monkeypatch):
    request = AsyncMock(return_value={})
    monkeypatch.setattr(remnawave_api, "_request", request)
    await remnawave_api.delete_user(42)
    request.assert_awaited_once_with("DELETE", "/api/users/42")
