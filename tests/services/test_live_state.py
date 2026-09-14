"""live_state.read_bypass: the bypass GB a user has left now (panel, short
timeout, "unavailable" instead of a guess) for the notices that name them."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import remnawave_api
from app.services.subscriptions import live_state as ls

GB = 1024 ** 3


def _bypass_ent(limit, used, status="ACTIVE"):
    return {"id": 1, "username": "1", "trafficLimitBytes": limit, "status": status,
            "userTraffic": {"usedTrafficBytes": used}}


def _panel(monkeypatch, bypass, delay=0.0):
    async def get_bypass_state(tg):
        await asyncio.sleep(delay)
        return bypass
    monkeypatch.setattr(remnawave_api, "get_bypass_state", get_bypass_state)


@pytest.mark.parametrize("state,ent,works,remaining", [
    ("present", _bypass_ent(10 * GB, 3 * GB), True, 7 * GB),
    ("present", _bypass_ent(10 * GB, 11 * GB), False, 0),
    ("present", _bypass_ent(0, 50 * GB), True, 0),                 # unlimited
    ("present", _bypass_ent(10 * GB, 0, status="DISABLED"), False, 0),
    ("absent", None, False, 0),
    ("unavailable", None, None, 0),
])
async def test_read_bypass(monkeypatch, state, ent, works, remaining):
    _panel(monkeypatch, (state, ent))
    b = await ls.read_bypass(1)
    assert (b.works, b.remaining) == (works, remaining)


async def test_a_slow_panel_is_unavailable_not_a_guess(monkeypatch):
    _panel(monkeypatch, ("present", _bypass_ent(10 * GB, 0)), delay=5)
    b = await ls.read_bypass(1, timeout=0.05)
    assert b.state == "unavailable" and b.works is None


async def test_a_panel_error_is_unavailable(monkeypatch):
    monkeypatch.setattr(remnawave_api, "get_bypass_state", AsyncMock(side_effect=RuntimeError("boom")))
    assert (await ls.read_bypass(1)).state == "unavailable"


@pytest.mark.parametrize("lang,b,text", [
    ("ru", int(7.5 * GB), "7.5 ГБ"), ("en", 12 * GB, "12 GB"), ("ru", 640 * 1024 ** 2, "640 МБ"),
    ("ru", 0, "0 ГБ"), ("en", 3 * GB, "3 GB"), ("ru", int(10.5 * GB), "10.5 ГБ"), ("ru", 150 * GB, "150 ГБ"),
])
def test_format_bytes(lang, b, text):
    assert ls.format_bytes(lang, b) == text
