"""POST /traffic-audit/resync/{tg} reports the panel's used traffic from
userTraffic.usedTrafficBytes (Remnawave 3.4.3), the same source as
panel_traffic_audit.used_traffic_bytes — not the (absent) top-level field."""
from __future__ import annotations

from unittest.mock import AsyncMock

import database
from app.api.dashboard.routes import traffic_audit
from app.services import remnawave_api

GIB = 1024 ** 3
TG = 4242


async def _resync(monkeypatch, entity):
    monkeypatch.setattr(remnawave_api, "find_user_by_username", AsyncMock(return_value=entity))
    for name in ("set_remnawave_uuid", "set_remnawave_id", "set_remnawave_bypass_cache"):
        monkeypatch.setattr(database, name, AsyncMock(), raising=False)
    return await traffic_audit.resync_one(telegram_id=TG, admin={"sub": 1})


async def test_resync_reads_used_bytes_from_user_traffic(monkeypatch):
    entity = {
        "id": 7, "uuid": "u-1", "subscriptionUrl": "https://panel.test/sub/x",
        "trafficLimitBytes": 10 * GIB, "userTraffic": {"usedTrafficBytes": 3 * GIB},
    }
    out = await _resync(monkeypatch, entity)
    assert out["ok"] is True
    assert out["panel_used_bytes"] == 3 * GIB
    assert out["panel_limit_bytes"] == 10 * GIB


async def test_resync_falls_back_to_top_level_used_bytes(monkeypatch):
    entity = {"id": 7, "uuid": "u-1", "trafficLimitBytes": GIB, "usedTrafficBytes": 2 * GIB}
    out = await _resync(monkeypatch, entity)
    assert out["panel_used_bytes"] == 2 * GIB
