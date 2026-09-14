"""Devices on the premium panel entity (owner 2026-09-14; docs/audit/08_payments_ux.md
#7): Basic 10, Plus 14 — as the tariff texts say. Combo = its base tier, legacy
biz_* = Plus, the trial is a Basic premium. REMNAWAVE_PREMIUM_DEVICE_LIMIT (5 by
default) is only the fallback when the tier is unknown. Set on create, on
adoption, on renewal / tariff change — legacy path and provisioning outbox.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import config
from app.services import provisioning, purchase_flow, remnawave_api, remnawave_premium, tariffs

TG = 4455
FULL_UUID = "0d6b3c52-6c77-4e8e-9c53-0f5a6d6f2a10"
FUTURE = datetime.now(timezone.utc) + timedelta(days=30)


@pytest.mark.parametrize("tier, cap", [
    ("basic", 10), ("plus", 14), ("combo_basic", 10), ("combo_plus", 14),
    ("biz_team", 14), ("Plus", 14), ("trial", 10),
])
def test_cap_by_tariff(tier, cap):
    assert tariffs.premium_device_limit(tier) == cap


@pytest.mark.parametrize("tier", [None, "", "weird"])
def test_unknown_tier_uses_the_env_fallback(monkeypatch, tier):
    monkeypatch.setattr(config, "REMNAWAVE_PREMIUM_DEVICE_LIMIT", 7)
    assert tariffs.premium_device_limit(tier) == 7


@pytest.fixture
def panel_on(monkeypatch):
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
    monkeypatch.setattr(config, "REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID", None, raising=False)


async def test_create_sets_the_tariff_cap(monkeypatch, panel_on):
    monkeypatch.setattr(remnawave_api, "find_user_by_username", AsyncMock(return_value=None))
    create = AsyncMock(return_value={"ok": True, "status": 201, "response": {
        "uuid": FULL_UUID, "vlessUuid": FULL_UUID, "subscriptionUrl": "https://p/sub/x", "id": 1}})
    monkeypatch.setattr(remnawave_api, "create_user", create)
    res = await remnawave_premium.create_premium_user_entity(
        TG, requested_uuid=FULL_UUID, expire_at=FUTURE, tier="plus")
    assert res.ok and create.await_args.kwargs["device_limit"] == 14


async def test_create_without_tier_keeps_the_env_value(monkeypatch, panel_on):
    monkeypatch.setattr(config, "REMNAWAVE_PREMIUM_DEVICE_LIMIT", 5)
    monkeypatch.setattr(remnawave_api, "find_user_by_username", AsyncMock(return_value=None))
    create = AsyncMock(return_value={"ok": True, "status": 201, "response": {"uuid": FULL_UUID}})
    monkeypatch.setattr(remnawave_api, "create_user", create)
    await remnawave_premium.create_premium_user_entity(TG, requested_uuid=None, expire_at=FUTURE)
    assert create.await_args.kwargs["device_limit"] == 5


async def test_adoption_patches_the_tariff_cap(monkeypatch, panel_on):
    ours = {"id": 9, "uuid": FULL_UUID, "telegramId": TG, "username": "tg_x", "status": "DISABLED"}
    monkeypatch.setattr(remnawave_api, "find_user_by_username", AsyncMock(return_value=ours))
    update = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(remnawave_api, "update_user", update)
    await remnawave_premium.create_premium_user_entity(
        TG, requested_uuid=None, expire_at=FUTURE, tier="combo_basic")
    assert update.await_args.kwargs["hwidDeviceLimit"] == 10


@pytest.mark.parametrize("tier, expected", [("plus", 14), ("basic", 10), (None, None)])
async def test_renewal_sets_the_cap_only_with_a_tier(monkeypatch, panel_on, tier, expected):
    import database
    monkeypatch.setattr(database, "get_remnawave_premium_uuid", AsyncMock(return_value=FULL_UUID))
    update = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(remnawave_api, "update_user", update)
    assert await remnawave_premium.renew_premium_user(TG, FUTURE, tier=tier) is True
    assert update.await_args.kwargs.get("hwidDeviceLimit") == expected


async def test_legacy_renewal_sync_passes_the_tariff(monkeypatch):
    renew = AsyncMock(return_value=True)
    monkeypatch.setattr(remnawave_premium, "renew_premium_user", renew)
    await purchase_flow._sync_renewal_once({"telegram_id": TG, "subscription_end": FUTURE, "tariff": "plus"})
    assert renew.await_args.kwargs["tier"] == "plus"


# ── provisioning outbox (flag on) ──────────────────────────────────────


def _ent(expire: datetime, *, cap=None, status="ACTIVE"):
    return {"id": 77, "vlessUuid": FULL_UUID, "status": status, "hwidDeviceLimit": cap,
            "expireAt": expire.strftime("%Y-%m-%dT%H:%M:%S.000Z")}


@pytest.fixture
def patched(monkeypatch):
    update = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(remnawave_api, "update_user", update)
    monkeypatch.setattr(provisioning, "_invalidate_aggregator", lambda *_a: None)
    return update


async def test_outbox_extend_patch_carries_the_cap(patched):
    now = datetime.now(timezone.utc)
    await provisioning._ensure_premium_expire(TG, _ent(now + timedelta(days=1)), now + timedelta(days=31),
                                              tier="plus")
    assert patched.await_args.kwargs["hwidDeviceLimit"] == 14


@pytest.mark.parametrize("cap, status", [(10, "ACTIVE"), (None, "ACTIVE"), (10, "DISABLED")])
async def test_outbox_adds_no_write_when_the_date_needs_none(patched, cap, status):
    """The outbox stays minimal and idempotent (a retry never re-PATCHes): no
    cap-only PATCH — the entity gets its cap with its next extend."""
    now = datetime.now(timezone.utc)
    await provisioning._ensure_premium_expire(TG, _ent(now + timedelta(days=60), cap=cap, status=status),
                                              now + timedelta(days=31), tier="plus")
    patched.assert_not_awaited()
