"""
Tests for the Task-2 cut-over no-op guards in vpn_utils.

When config.PURCHASE_FLOW_REMNAWAVE is True (the production default
after the cut-over) the legacy samopis xray entry points must NOT
hit the network — they return a stub success instead.  This keeps
any residual recovery / admin-reissue caller working without dialling
a decommissioned service.
"""
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def vpn_utils_module(monkeypatch):
    """Import vpn_utils with stubbed asyncpg / httpx / aiogram for unit-test
    isolation.  Real CI installs these, but the tests don't need them."""
    # vpn_utils itself only needs config + httpx + logging at import time.
    # asyncpg-using imports are lazy.
    import vpn_utils as mod
    return mod


def _patch_config(remnawave_on=True):
    cfg = SimpleNamespace(
        PURCHASE_FLOW_REMNAWAVE=remnawave_on,
        VPN_PROVISIONING_ENABLED=True,
        VPN_ENABLED=True,
        XRAY_API_URL="https://xray.example",
        XRAY_API_KEY="x",
    )
    import vpn_utils
    return patch.object(vpn_utils, "config", cfg)


# ── add_vless_user ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_vless_user_noop_when_remnawave_on(vpn_utils_module):
    with _patch_config(remnawave_on=True):
        result = await vpn_utils_module.add_vless_user(
            telegram_id=42,
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            uuid="11111111-2222-3333-4444-555555555555",
            tariff="basic",
        )
    # No-op path returns stub success: uuid passes through, urls empty.
    assert result["uuid"] == "11111111-2222-3333-4444-555555555555"
    assert result["vless_url"] == ""
    assert result["vless_url_plus"] is None
    assert result["subscription_type"] == "basic"


@pytest.mark.asyncio
async def test_add_vless_user_generates_uuid_when_none_supplied(vpn_utils_module):
    """Stub must produce SOMETHING usable as uuid for legacy callers
    that expected the API to assign one."""
    with _patch_config(remnawave_on=True):
        result = await vpn_utils_module.add_vless_user(
            telegram_id=7,
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            uuid=None,
            tariff="plus",
        )
    assert result["uuid"]
    assert len(result["uuid"]) == 36  # standard uuid4 length
    assert result["subscription_type"] == "plus"


# ── update_vless_user / remove_vless_user ─────────────────────────────

@pytest.mark.asyncio
async def test_update_vless_user_noop_when_remnawave_on(vpn_utils_module):
    with _patch_config(remnawave_on=True):
        result = await vpn_utils_module.update_vless_user(
            uuid="abcd1234-...",
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    assert result is None


@pytest.mark.asyncio
async def test_remove_vless_user_noop_when_remnawave_on(vpn_utils_module):
    with _patch_config(remnawave_on=True):
        result = await vpn_utils_module.remove_vless_user(uuid="abcd1234-...")
    assert result is None


# ── Flag default is True ──────────────────────────────────────────────

def test_purchase_flow_remnawave_default_is_true():
    """After the Task-2 cut-over the bot must default to Remnawave-only.

    The flag still exists for emergency rollback; this test pins the
    default so an accidental refactor that flips it back to False is
    caught immediately.
    """
    # Import fresh to get the actual default — patch env to clear any override
    import os
    # When env vars are unset, _envbool falls back to its default arg.
    saved = {k: os.environ.pop(k, None) for k in (
        "PURCHASE_FLOW_REMNAWAVE",
        "STAGE_PURCHASE_FLOW_REMNAWAVE",
        "PROD_PURCHASE_FLOW_REMNAWAVE",
        "LOCAL_PURCHASE_FLOW_REMNAWAVE",
    )}
    try:
        import importlib
        import config as cfg
        importlib.reload(cfg)
        assert cfg.PURCHASE_FLOW_REMNAWAVE is True
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        import importlib
        import config as cfg
        importlib.reload(cfg)
