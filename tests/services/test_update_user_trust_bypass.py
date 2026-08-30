"""update_user premium-guard vs _trust_bypass.

Regression: a bypass traffic top-up (add_bypass_traffic) resolves the bypass
entity by username=str(tg) via get_bypass_entity_safe and PATCHes its own
numeric id. The premium SAFETY-GUARD (_is_premium_entity) is a GLOBAL check
`WHERE remnawave_premium_id = id` — a contaminated column can make it a false
positive for a legit bypass id, silently dropping the PATCH → "paid but GB
never arrived". `_trust_bypass=True` skips that guard for the trusted caller.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import remnawave_api as api


@pytest.mark.asyncio
async def test_traffic_patch_dropped_without_trust_when_flagged_premium(monkeypatch):
    # _is_premium_entity FALSE-POSITIVE (contaminated column) → guard drops.
    with patch.object(api, "_resolve_to_int_id", AsyncMock(return_value=555)), \
         patch.object(api, "_is_premium_entity", AsyncMock(return_value=True)), \
         patch.object(api, "_request", AsyncMock(return_value={"ok": True})) as req:
        out = await api.update_user(555, trafficLimitBytes=42, status="ACTIVE")
    assert out is None            # SAFETY-DROP → None
    req.assert_not_awaited()      # PATCH never sent


@pytest.mark.asyncio
async def test_traffic_patch_sent_with_trust_bypass(monkeypatch):
    # Trusted caller (add_bypass_traffic, entity username-verified) → skip guard.
    with patch.object(api, "_resolve_to_int_id", AsyncMock(return_value=555)), \
         patch.object(api, "_is_premium_entity", AsyncMock(return_value=True)) as isprem, \
         patch.object(api, "_request", AsyncMock(return_value={"ok": True})) as req:
        out = await api.update_user(555, trafficLimitBytes=42, status="ACTIVE", _trust_bypass=True)
    assert out == {"ok": True}    # PATCH sent
    req.assert_awaited_once()
    # guard short-circuited before the DB check
    isprem.assert_not_awaited()
    # _trust_bypass must NOT leak into the PATCH body
    _, kwargs = req.await_args
    assert "_trust_bypass" not in kwargs.get("json", {})
    assert kwargs["json"]["trafficLimitBytes"] == 42


@pytest.mark.asyncio
async def test_normal_patch_still_guarded_for_real_premium(monkeypatch):
    # No _trust_bypass → genuine premium entity still protected (unlimited).
    with patch.object(api, "_resolve_to_int_id", AsyncMock(return_value=777)), \
         patch.object(api, "_is_premium_entity", AsyncMock(return_value=True)), \
         patch.object(api, "_request", AsyncMock(return_value={"ok": True})) as req:
        out = await api.update_user(777, trafficLimitBytes=999)
    assert out is None
    req.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_traffic_patch_unaffected(monkeypatch):
    # PATCH without trafficLimitBytes never hits the guard.
    with patch.object(api, "_resolve_to_int_id", AsyncMock(return_value=1)), \
         patch.object(api, "_is_premium_entity", AsyncMock(return_value=True)) as isprem, \
         patch.object(api, "_request", AsyncMock(return_value={"ok": True})) as req:
        out = await api.update_user(1, status="ACTIVE")
    assert out == {"ok": True}
    isprem.assert_not_awaited()
    req.assert_awaited_once()
