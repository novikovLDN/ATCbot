"""panel_traffic_audit — recon §3 P0-F: used traffic is read from
userTraffic.usedTrafficBytes (Remnawave 3.4.3 GET /api/users/{id}), not the
top level; apply_fix must never set a limit that under-credits paid GB.

P1-8: the bypass limit is LIFETIME (NO_RESET) and purchases add to it, so the
expected limit is the sum of all GB granted (what the audit computes). The fix
sets limit = max(current, expected) — never expected + used (that credited the
used GB a second time), never lower than the panel already has.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import panel_traffic_audit as pta
from app.services import remnawave_api

GIB = 1024 ** 3
TG = 9090


def row(**kw) -> pta.UserRow:
    base = dict(telegram_id=TG, subscription_type="basic", period_days=30, is_bypass_only=True,
                remnawave_uuid=None, remnawave_id=77, traffic_purchases_gb=20)
    base.update(kw)
    return pta.UserRow(**base)


def entity(limit, used, *, nested=True, status="ACTIVE"):
    ent = {"id": 77, "username": str(TG), "trafficLimitBytes": limit, "status": status,
           "subscriptionUrl": "https://panel.test/sub/x", "telegramId": TG}
    if nested:
        ent["userTraffic"] = {"usedTrafficBytes": used}
    else:
        ent["usedTrafficBytes"] = used
    return ent


@pytest.fixture
def panel(monkeypatch):
    state = {"entity": None}

    async def get_user(_probe):
        return state["entity"]
    update = AsyncMock(side_effect=lambda probe, **f: {"id": probe, **f})
    monkeypatch.setattr(remnawave_api, "get_user", get_user)
    monkeypatch.setattr(remnawave_api, "update_user", update)
    monkeypatch.setattr(pta, "fetch_candidates", AsyncMock(return_value=[row()]))
    state["update"] = update
    return state


def test_used_bytes_reads_user_traffic_with_top_level_fallback():
    assert pta.used_traffic_bytes(entity(5 * GIB, 4 * GIB)) == 4 * GIB
    assert pta.used_traffic_bytes(entity(5 * GIB, 4 * GIB, nested=False)) == 4 * GIB
    assert pta.used_traffic_bytes({"userTraffic": {"usedTrafficBytes": str(3 * GIB)}}) == 3 * GIB
    assert pta.used_traffic_bytes({"trafficLimitBytes": 1}) == 0


async def test_audit_one_reads_used_from_user_traffic(panel):
    panel["entity"] = entity(5 * GIB, 4 * GIB)

    res = await pta.audit_one(row())

    assert res.kind == "mismatch"
    assert res.expected_bytes == 20 * GIB and res.actual_bytes == 5 * GIB
    assert res.used_bytes == 4 * GIB, "3.4.3: usedTrafficBytes is inside userTraffic"
    assert res.panel_by_our_ref.used_traffic_bytes == 4 * GIB


async def test_apply_fix_sets_the_lifetime_limit_to_the_granted_total(panel):
    """P1-8: 20 GB granted in total, panel limit 5 GB, 6 GB already used →
    limit 20 GB (the used GB were part of the 20), not 26 GB."""
    panel["entity"] = entity(5 * GIB, 4 * GIB)
    res = await pta.audit_one(row())
    panel["entity"] = entity(5 * GIB, 6 * GIB)          # the user kept browsing since the audit

    out = await pta.apply_fix(res)

    assert out["ok"] is True
    new_limit = panel["update"].await_args.kwargs["trafficLimitBytes"]
    assert new_limit == res.expected_bytes == 20 * GIB, "never expected + used"
    assert out["after_bytes"] == new_limit and out["before_bytes"] == 5 * GIB
    assert out["used_bytes"] == 6 * GIB                  # reported, not added


@pytest.mark.parametrize("current_gb", [20, 25])
async def test_apply_fix_keeps_a_limit_that_already_covers_the_granted_total(panel, current_gb):
    """max(current, expected): a limit already >= expected is never lowered, and
    used GB never raise it (the report's example: 50 must stay 50, not become 68)."""
    panel["entity"] = entity(5 * GIB, 0)
    res = await pta.audit_one(row())
    panel["entity"] = entity(current_gb * GIB, 18 * GIB)

    out = await pta.apply_fix(res)

    assert out["ok"] is False and out["reason"] == "no_shortfall_now"
    panel["update"].assert_not_awaited()


async def test_apply_fix_never_lowers_the_limit(panel):
    panel["entity"] = entity(5 * GIB, 0)
    res = await pta.audit_one(row())
    panel["entity"] = entity(30 * GIB, 2 * GIB)          # fixed/credited in the meantime

    out = await pta.apply_fix(res)

    assert out == {"ok": False, "reason": "no_shortfall_now", "before_bytes": 30 * GIB,
                   "used_bytes": 2 * GIB, "expected_bytes": 20 * GIB}
    panel["update"].assert_not_awaited()


async def test_apply_fix_without_a_fresh_panel_read_does_nothing(panel):
    panel["entity"] = entity(5 * GIB, 4 * GIB)
    res = await pta.audit_one(row())
    panel["entity"] = None

    out = await pta.apply_fix(res)

    assert out == {"ok": False, "reason": "panel_read_failed"}
    panel["update"].assert_not_awaited()
