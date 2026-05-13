"""
Unit tests for app.services.purchase_flow.provision_subscription.

Verifies the Task-2 cut-over orchestrator end-to-end with all DB +
service calls mocked.  The contract under test:

* Returns a dict shaped EXACTLY like the legacy
  `vpn_utils.add_vless_user` (uuid, vless_url, vless_url_plus,
  subscription_type) — so the surrounding grant_access /
  finalize_purchase code consumes it unchanged.
* Creates premium + bypass entities for paid tariffs, only bypass-
  with-1GB-trial entity for is_trial=True.
* Reuses existing samopis uuid as forced vlessUuid for un-migrated
  users (backward compat for legacy VLESS links).
* On renewal (existing premium uuid in DB) PATCHes expireAt rather
  than re-creating.
* Bypass top-up accumulates traffic (never resets).
* Bypass-side failure does NOT block premium provisioning.
"""
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _cfg(**overrides):
    cfg = type("Cfg", (), {})()
    cfg.REMNAWAVE_ENABLED = True
    cfg.TRIAL_BYPASS_GB = 1
    cfg.COMBO_TARIFFS = {
        "combo_basic": {
            30: {"price": 269, "gb": 75, "base_tariff": "basic"},
            90: {"price": 719, "gb": 200, "base_tariff": "basic"},
        },
        "combo_plus": {
            30: {"price": 399, "gb": 75, "base_tariff": "plus"},
        },
    }
    cfg.TRAFFIC_LIMITS = {
        "basic": {30: 10 * 1024**3, 90: 10 * 1024**3, 180: 10 * 1024**3, 365: 10 * 1024**3},
        "plus":  {30: 10 * 1024**3, 90: 10 * 1024**3, 180: 10 * 1024**3, 365: 10 * 1024**3},
    }
    cfg.DEVICE_LIMITS = {"basic": 5, "plus": 7}
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _fake_db(monkeypatch, *, existing_premium_uuid=None, existing_bypass_uuid=None,
             existing_subscription=None, cached_premium_url=None, cached_bypass_cache=None):
    """Build a `database` module stub and inject into sys.modules."""
    pool = SimpleNamespace()

    async def fake_set_remnawave_premium_uuid_and_url(*a, **kw): return None
    async def fake_set_remnawave_premium_sub_url(*a, **kw): return None
    async def fake_set_remnawave_bypass_cache(*a, **kw): return None

    db = SimpleNamespace(
        get_pool=AsyncMock(return_value=None),  # _premium_url_for_existing returns None
        get_subscription_any=AsyncMock(return_value=existing_subscription),
        get_remnawave_premium_uuid=AsyncMock(return_value=existing_premium_uuid),
        get_remnawave_uuid=AsyncMock(return_value=existing_bypass_uuid),
        get_remnawave_bypass_cache=AsyncMock(return_value=cached_bypass_cache),
        set_remnawave_premium_uuid_and_url=AsyncMock(side_effect=fake_set_remnawave_premium_uuid_and_url),
        set_remnawave_premium_sub_url=AsyncMock(side_effect=fake_set_remnawave_premium_sub_url),
        set_remnawave_bypass_cache=AsyncMock(side_effect=fake_set_remnawave_bypass_cache),
    )
    monkeypatch.setitem(sys.modules, "database", db)
    return db


# ── _bypass_bytes_for ──────────────────────────────────────────────────

class TestBypassBytesFor:
    def test_trial_returns_one_gb(self, monkeypatch):
        from app.services import purchase_flow
        with patch.object(purchase_flow, "config", _cfg()):
            assert purchase_flow._bypass_bytes_for("basic", 30, is_trial=True) == 1 * 1024**3

    def test_combo_uses_per_period_gb(self, monkeypatch):
        from app.services import purchase_flow
        with patch.object(purchase_flow, "config", _cfg()):
            assert purchase_flow._bypass_bytes_for("combo_basic", 30, False) == 75 * 1024**3
            assert purchase_flow._bypass_bytes_for("combo_basic", 90, False) == 200 * 1024**3

    def test_basic_returns_table_value(self, monkeypatch):
        from app.services import purchase_flow
        with patch.object(purchase_flow, "config", _cfg()):
            assert purchase_flow._bypass_bytes_for("basic", 30, False) == 10 * 1024**3
            assert purchase_flow._bypass_bytes_for("plus", 365, False) == 10 * 1024**3

    def test_unknown_tariff_falls_back_to_10gb(self, monkeypatch):
        from app.services import purchase_flow
        with patch.object(purchase_flow, "config", _cfg()):
            assert purchase_flow._bypass_bytes_for("zzz_unknown", 30, False) == 10 * 1024**3


# ── provision_subscription: new user, basic, paid ──────────────────────

@pytest.mark.asyncio
async def test_provision_new_basic_creates_both_entities(monkeypatch):
    from app.services import purchase_flow, remnawave_premium, remnawave_bypass

    _fake_db(monkeypatch)

    premium_result = remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid="prem-uuid",
        forced_uuid_accepted=False, subscription_url="https://rmnw/sub/prem",
        status=201, error=None, recovered=False, short_uuid="prem_s",
    )
    bypass_result = remnawave_bypass.BypassCreateResult(
        ok=True, panel_uuid="byp-uuid",
        subscription_url="https://rmnw/sub/byp", short_uuid="byp_s",
        status=201, error=None, recovered=False,
    )
    create_premium_mock = AsyncMock(return_value=premium_result)
    create_bypass_mock = AsyncMock(return_value=bypass_result)
    with patch.object(purchase_flow, "config", _cfg()), \
         patch.object(purchase_flow.remnawave_premium, "create_premium_user_entity", create_premium_mock), \
         patch.object(purchase_flow.remnawave_bypass, "create_bypass_user_entity", create_bypass_mock):
        out = await purchase_flow.provision_subscription(
            42,
            tariff="basic",
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            period_days=30,
            is_trial=False,
        )

    # Premium: created with expireAt=subscription_end, requested_uuid is generated
    create_premium_mock.assert_awaited_once()
    pkwargs = create_premium_mock.call_args.kwargs
    assert pkwargs["expire_at"] == datetime(2030, 1, 1, tzinfo=timezone.utc)

    # Bypass: created with 10 GB
    create_bypass_mock.assert_awaited_once()
    bkwargs = create_bypass_mock.call_args.kwargs
    assert bkwargs["traffic_limit_bytes"] == 10 * 1024**3

    # Return shape mirrors vpn_utils.add_vless_user
    assert set(out.keys()) >= {"uuid", "vless_url", "vless_url_plus", "subscription_type"}
    assert out["vless_url"] == "https://rmnw/sub/prem"     # premium → vless_url
    assert out["vless_url_plus"] == "https://rmnw/sub/byp" # bypass → vless_url_plus
    assert out["subscription_type"] == "basic"


# ── Trial flow ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provision_trial_creates_both_with_1gb_bypass(monkeypatch):
    from app.services import purchase_flow, remnawave_premium, remnawave_bypass

    _fake_db(monkeypatch)

    pres = remnawave_premium.PremiumCreateResult(
        True, "p", False, "https://rmnw/sub/p", 201, None, False, "ps",
    )
    bres = remnawave_bypass.BypassCreateResult(
        True, "b", "https://rmnw/sub/b", "bs", 201, None, False,
    )
    cpm = AsyncMock(return_value=pres)
    cbm = AsyncMock(return_value=bres)
    with patch.object(purchase_flow, "config", _cfg()), \
         patch.object(purchase_flow.remnawave_premium, "create_premium_user_entity", cpm), \
         patch.object(purchase_flow.remnawave_bypass, "create_bypass_user_entity", cbm):
        await purchase_flow.provision_subscription(
            7,
            tariff="basic",  # tariff is meaningless for trial but the API still wants it
            subscription_end=datetime(2026, 5, 16, tzinfo=timezone.utc),
            period_days=3,
            is_trial=True,
        )

    assert cbm.call_args.kwargs["traffic_limit_bytes"] == 1 * 1024**3  # Trial=1GB


# ── Combo flow ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provision_combo_basic_uses_75gb_bypass(monkeypatch):
    from app.services import purchase_flow, remnawave_premium, remnawave_bypass

    _fake_db(monkeypatch)

    pres = remnawave_premium.PremiumCreateResult(
        True, "p", False, "https://rmnw/sub/p", 201, None, False, "ps",
    )
    bres = remnawave_bypass.BypassCreateResult(
        True, "b", "https://rmnw/sub/b", "bs", 201, None, False,
    )
    cpm = AsyncMock(return_value=pres)
    cbm = AsyncMock(return_value=bres)
    with patch.object(purchase_flow, "config", _cfg()), \
         patch.object(purchase_flow.remnawave_premium, "create_premium_user_entity", cpm), \
         patch.object(purchase_flow.remnawave_bypass, "create_bypass_user_entity", cbm):
        await purchase_flow.provision_subscription(
            42,
            tariff="combo_basic",
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            period_days=30,
            is_trial=False,
        )
    assert cbm.call_args.kwargs["traffic_limit_bytes"] == 75 * 1024**3


# ── Renewal path: existing premium uuid in DB ──────────────────────────

@pytest.mark.asyncio
async def test_provision_renewal_patches_premium_expireat(monkeypatch):
    """User already has remnawave_premium_uuid → renew (PATCH expireAt)
    rather than create-new-premium."""
    from app.services import purchase_flow, remnawave_premium, remnawave_bypass

    _fake_db(monkeypatch, existing_premium_uuid="existing-prem-uuid",
             cached_premium_url="https://rmnw/sub/cached_prem")

    bres = remnawave_bypass.BypassCreateResult(
        True, "b", "https://rmnw/sub/b", "bs", 201, None, False,
    )
    renew_mock = AsyncMock(return_value=True)
    cpm = AsyncMock()  # MUST NOT be called
    cbm = AsyncMock(return_value=bres)
    with patch.object(purchase_flow, "config", _cfg()), \
         patch.object(purchase_flow.remnawave_premium, "renew_premium_user", renew_mock), \
         patch.object(purchase_flow.remnawave_premium, "create_premium_user_entity", cpm), \
         patch.object(purchase_flow.remnawave_bypass, "create_bypass_user_entity", cbm), \
         patch.object(purchase_flow, "_premium_url_for_existing",
                      AsyncMock(return_value="https://rmnw/sub/cached_prem")):
        out = await purchase_flow.provision_subscription(
            42,
            tariff="basic",
            subscription_end=datetime(2030, 6, 1, tzinfo=timezone.utc),
            period_days=30,
            is_trial=False,
        )

    renew_mock.assert_awaited_once_with(42, datetime(2030, 6, 1, tzinfo=timezone.utc))
    cpm.assert_not_called()
    assert out["vless_url"] == "https://rmnw/sub/cached_prem"


# ── Bypass top-up on renewal ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_provision_renewal_accumulates_bypass_traffic(monkeypatch):
    """User already has a bypass entity → add_bypass_traffic, not re-create."""
    from app.services import purchase_flow, remnawave_premium, remnawave_bypass

    _fake_db(monkeypatch,
             existing_premium_uuid="prem",
             existing_bypass_uuid="byp",
             cached_premium_url="https://rmnw/sub/prem",
             cached_bypass_cache={
                 "remnawave_uuid": "byp",
                 "remnawave_bypass_sub_url": "https://rmnw/sub/byp_cached",
                 "remnawave_bypass_short_uuid": "byp_s",
             })

    renew_mock = AsyncMock(return_value=True)
    add_traffic_mock = AsyncMock(return_value=True)
    cbm = AsyncMock()  # MUST NOT be called
    with patch.object(purchase_flow, "config", _cfg()), \
         patch.object(purchase_flow.remnawave_premium, "renew_premium_user", renew_mock), \
         patch.object(purchase_flow.remnawave_bypass, "add_bypass_traffic", add_traffic_mock), \
         patch.object(purchase_flow.remnawave_bypass, "create_bypass_user_entity", cbm), \
         patch.object(purchase_flow, "_premium_url_for_existing",
                      AsyncMock(return_value="https://rmnw/sub/prem")):
        out = await purchase_flow.provision_subscription(
            42,
            tariff="basic",
            subscription_end=datetime(2030, 6, 1, tzinfo=timezone.utc),
            period_days=30,
            is_trial=False,
        )

    add_traffic_mock.assert_awaited_once()
    # +10 GB accumulated (default basic 30-day)
    assert add_traffic_mock.call_args.kwargs["extra_bytes"] == 10 * 1024**3
    cbm.assert_not_called()
    assert out["vless_url_plus"] == "https://rmnw/sub/byp_cached"


# ── Backward compat: forced UUID for un-migrated legacy user ───────────

@pytest.mark.asyncio
async def test_provision_reuses_legacy_samopis_uuid_when_present(monkeypatch):
    """Legacy user with a samopis uuid but no premium entity → forced vlessUuid."""
    from app.services import purchase_flow, remnawave_premium, remnawave_bypass

    legacy = "11111111-2222-3333-4444-555555555555"
    _fake_db(
        monkeypatch,
        existing_subscription={"telegram_id": 42, "uuid": legacy},
        existing_premium_uuid=None,
        existing_bypass_uuid=None,
    )

    pres = remnawave_premium.PremiumCreateResult(
        True, "panel-uuid", True, "https://rmnw/sub/p", 201, None, False, "ps",
    )
    bres = remnawave_bypass.BypassCreateResult(
        True, "byp", "https://rmnw/sub/b", "bs", 201, None, False,
    )
    cpm = AsyncMock(return_value=pres)
    cbm = AsyncMock(return_value=bres)
    with patch.object(purchase_flow, "config", _cfg()), \
         patch.object(purchase_flow.remnawave_premium, "create_premium_user_entity", cpm), \
         patch.object(purchase_flow.remnawave_bypass, "create_bypass_user_entity", cbm):
        out = await purchase_flow.provision_subscription(
            42,
            tariff="basic",
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            period_days=30,
            is_trial=False,
        )

    assert cpm.call_args.kwargs["requested_uuid"] == legacy
    assert out["uuid"] == legacy


# ── Bypass failure does not block premium ─────────────────────────────

@pytest.mark.asyncio
async def test_provision_bypass_failure_does_not_break_premium(monkeypatch):
    from app.services import purchase_flow, remnawave_premium, remnawave_bypass

    _fake_db(monkeypatch)

    pres = remnawave_premium.PremiumCreateResult(
        True, "panel-uuid", False, "https://rmnw/sub/p", 201, None, False, "ps",
    )
    bres = remnawave_bypass.BypassCreateResult(
        False, None, None, None, 500, "panel_down", False,
    )
    cpm = AsyncMock(return_value=pres)
    cbm = AsyncMock(return_value=bres)
    with patch.object(purchase_flow, "config", _cfg()), \
         patch.object(purchase_flow.remnawave_premium, "create_premium_user_entity", cpm), \
         patch.object(purchase_flow.remnawave_bypass, "create_bypass_user_entity", cbm):
        out = await purchase_flow.provision_subscription(
            42,
            tariff="basic",
            subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
            period_days=30,
            is_trial=False,
        )

    assert out["vless_url"] == "https://rmnw/sub/p"  # premium ok
    assert out["vless_url_plus"] is None             # bypass missing — still succeed overall


# ── Premium failure must raise ────────────────────────────────────────

@pytest.mark.asyncio
async def test_provision_premium_failure_raises(monkeypatch):
    """Premium failure should propagate so grant_access retry-loop fires."""
    from app.services import purchase_flow, remnawave_premium

    _fake_db(monkeypatch)

    pres = remnawave_premium.PremiumCreateResult(
        False, None, False, None, 500, "panel_down", False,
    )
    cpm = AsyncMock(return_value=pres)
    with patch.object(purchase_flow, "config", _cfg()), \
         patch.object(purchase_flow.remnawave_premium, "create_premium_user_entity", cpm):
        with pytest.raises(RuntimeError, match="premium provision failed"):
            await purchase_flow.provision_subscription(
                42,
                tariff="basic",
                subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
                period_days=30,
                is_trial=False,
            )


# ── Disabled → refuses ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provision_refuses_when_remnawave_disabled(monkeypatch):
    from app.services import purchase_flow
    with patch.object(purchase_flow, "config", _cfg(REMNAWAVE_ENABLED=False)):
        with pytest.raises(RuntimeError, match="REMNAWAVE_API_URL"):
            await purchase_flow.provision_subscription(
                42,
                tariff="basic",
                subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
                period_days=30,
                is_trial=False,
            )
