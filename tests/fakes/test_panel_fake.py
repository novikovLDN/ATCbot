"""T3 — FakePanel: in-memory Remnawave mirroring the real return contracts of
the functions the provisioning core calls."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import remnawave_api, remnawave_bypass, remnawave_premium
from app.services.remnawave_bypass import BypassCreateResult
from app.services.remnawave_premium import PremiumCreateResult
from tests.fakes.panel import FakePanel

GIB = 1024 ** 3
TG = 42
T0 = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def panel(monkeypatch):
    return FakePanel().install(monkeypatch)


# ── install ────────────────────────────────────────────────────────────

def test_install_patches_module_attributes(panel):
    assert remnawave_premium.create_premium_user_entity == panel.create_premium_user_entity
    assert remnawave_premium.renew_premium_user == panel.renew_premium_user
    assert remnawave_bypass.create_bypass_user_entity == panel.create_bypass_user_entity
    assert remnawave_api.get_bypass_entity_safe == panel.get_bypass_entity_safe
    assert remnawave_api.get_bypass_state == panel.get_bypass_state
    assert remnawave_api.get_premium_state == panel.get_premium_state
    assert remnawave_api.update_user == panel.update_user


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        FakePanel(mode="flaky")
    p = FakePanel()
    with pytest.raises(ValueError):
        p.mode = "flaky"


# ── ok mode ────────────────────────────────────────────────────────────

async def test_premium_create_then_renew(panel):
    res = await remnawave_premium.create_premium_user_entity(
        TG, requested_uuid="req-uuid", expire_at=T0,
    )
    assert isinstance(res, PremiumCreateResult)
    assert res.ok and not res.recovered and res.forced_uuid_accepted
    assert res.panel_uuid and res.subscription_url and res.error is None
    assert panel.premium_expire(TG) == T0

    later = T0 + timedelta(days=30)
    assert await remnawave_premium.renew_premium_user(TG, later) is True
    assert panel.premium_expire(TG) == later


async def test_premium_create_adopts_existing(panel):
    panel.seed_premium(TG, T0)
    res = await remnawave_premium.create_premium_user_entity(
        TG, requested_uuid=None, expire_at=T0 + timedelta(days=3),
    )
    assert res.ok and res.recovered
    assert res.panel_uuid == panel.premium[TG]["vlessUuid"]
    assert panel.premium_expire(TG) == T0 + timedelta(days=3)
    # Real _ensure_premium_entity_state PATCHes expireAt to exactly the
    # requested value — adoption CAN shorten premium. The core must guard.
    res2 = await remnawave_premium.create_premium_user_entity(TG, requested_uuid=None, expire_at=T0)
    assert res2.recovered and panel.premium_expire(TG) == T0


async def test_renew_without_entity_returns_false(panel):
    assert await remnawave_premium.renew_premium_user(TG, T0) is False


async def test_bypass_create_read_patch(panel):
    res = await remnawave_bypass.create_bypass_user_entity(TG, traffic_limit_bytes=75 * GIB)
    assert isinstance(res, BypassCreateResult)
    assert res.ok and not res.recovered and res.panel_id is not None
    ent = await remnawave_api.get_bypass_entity_safe(TG)
    assert ent["username"] == str(TG)
    assert ent["trafficLimitBytes"] == 75 * GIB
    assert ent["vlessUuid"] == res.panel_uuid and ent["subscriptionUrl"] == res.subscription_url

    out = await remnawave_api.update_user(ent["id"], trafficLimitBytes=85 * GIB, _trust_bypass=True)
    assert out["trafficLimitBytes"] == 85 * GIB
    assert panel.bypass_limit(TG) == 85 * GIB
    assert panel.patch_count == 1


async def test_get_bypass_returns_copy(panel):
    panel.seed_bypass(TG, 3 * GIB)
    ent = await remnawave_api.get_bypass_entity_safe(TG)
    ent["trafficLimitBytes"] = 0
    assert panel.bypass_limit(TG) == 3 * GIB


async def test_bypass_create_adopts_without_changing_limit(panel):
    panel.seed_bypass(TG, 3 * GIB)
    res = await remnawave_bypass.create_bypass_user_entity(TG, traffic_limit_bytes=75 * GIB)
    assert res.ok and res.recovered
    assert panel.bypass_limit(TG) == 3 * GIB  # real adopt path does not PATCH the limit


async def test_bypass_create_rejects_non_positive(panel):
    res = await remnawave_bypass.create_bypass_user_entity(TG, traffic_limit_bytes=0)
    assert not res.ok and res.error == "non_positive_traffic_limit"
    assert panel.bypass_limit(TG) is None


async def test_get_bypass_missing_is_none(panel):
    assert await remnawave_api.get_bypass_entity_safe(TG) is None


async def test_update_user_by_uuid_and_expire_iso(panel):
    ent = panel.seed_premium(TG, T0)
    out = await remnawave_api.update_user(ent["vlessUuid"], expireAt="2026-12-01T00:00:00.000Z")
    assert out["expireAt"] == "2026-12-01T00:00:00.000Z"
    assert panel.premium_expire(TG) == datetime(2026, 12, 1, tzinfo=timezone.utc)


async def test_update_user_unknown_id_is_none(panel):
    assert await remnawave_api.update_user(999, trafficLimitBytes=1) is None


async def test_entity_shape_matches_3_4_3(panel):
    """ExtendedUsersSchema: numeric id, vlessUuid, NO `uuid`, traffic nested."""
    ent = panel.seed_bypass(TG, 3 * GIB)
    assert "uuid" not in ent
    assert isinstance(ent["id"], int) and ent["vlessUuid"]
    assert ent["userTraffic"]["usedTrafficBytes"] == 0
    assert "usedTrafficBytes" not in ent


@pytest.mark.parametrize("fields", [
    {"status": "LIMITED"},                             # PATCH status: ACTIVE | DISABLED only
    {"status": "EXPIRED"},
    {"expireAt": "2020-01-01T00:00:00.000Z"},          # "Expiration date cannot be in the past"
    {"trafficLimitBytes": -1},                         # min(0)
])
async def test_update_user_rejects_what_3_4_3_rejects(panel, fields):
    ent = panel.seed_bypass(TG, 3 * GIB)
    assert await remnawave_api.update_user(ent["id"], _trust_bypass=True, **fields) is None
    assert panel.patch_count == 0


async def test_update_user_premium_safety_drop(panel):
    ent = panel.seed_premium(TG, T0)
    assert await remnawave_api.update_user(ent["id"], trafficLimitBytes=GIB) is None
    assert panel.premium[TG]["trafficLimitBytes"] == 0
    assert panel.patch_count == 0


# ── down mode ──────────────────────────────────────────────────────────

async def test_down_mode_never_raises_and_changes_nothing(panel):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, T0)
    panel.mode = "down"
    p = await remnawave_premium.create_premium_user_entity(TG + 1, requested_uuid=None, expire_at=T0)
    assert not p.ok and p.status == 0 and p.error
    assert await remnawave_premium.renew_premium_user(TG, T0 + timedelta(days=1)) is False
    b = await remnawave_bypass.create_bypass_user_entity(TG + 1, traffic_limit_bytes=GIB)
    assert not b.ok and b.status == 0 and b.error
    # real get_bypass_entity_safe swallows transport errors → None
    assert await remnawave_api.get_bypass_entity_safe(TG) is None
    assert await remnawave_api.update_user(panel.bypass[TG]["id"], trafficLimitBytes=GIB) is None
    assert panel.bypass_limit(TG) == 3 * GIB
    assert panel.premium_expire(TG) == T0
    assert panel.patch_count == 0
    assert TG + 1 not in panel.premium and TG + 1 not in panel.bypass


# ── crash_after_patch mode ─────────────────────────────────────────────

async def test_crash_after_patch_applies_then_raises(panel):
    ent = panel.seed_bypass(TG, 3 * GIB)
    panel.mode = "crash_after_patch"
    with pytest.raises(ConnectionError):
        await remnawave_api.update_user(ent["id"], trafficLimitBytes=13 * GIB, _trust_bypass=True)
    assert panel.bypass_limit(TG) == 13 * GIB
    assert panel.patch_count == 1

    panel.mode = "ok"
    assert (await remnawave_api.get_bypass_entity_safe(TG))["trafficLimitBytes"] == 13 * GIB


async def test_crash_after_patch_renew_applies_but_reports_false(panel):
    panel.seed_premium(TG, T0)
    panel.mode = "crash_after_patch"
    # real renew_premium_user catches the exception and returns False
    assert await remnawave_premium.renew_premium_user(TG, T0 + timedelta(days=30)) is False
    assert panel.premium_expire(TG) == T0 + timedelta(days=30)


# ── conflict mode ──────────────────────────────────────────────────────

async def test_conflict_changes_limit_between_reads(panel):
    panel.seed_bypass(TG, 3 * GIB)
    panel.mode = "conflict"
    first = await remnawave_api.get_bypass_entity_safe(TG)
    second = await remnawave_api.get_bypass_entity_safe(TG)
    third = await remnawave_api.get_bypass_entity_safe(TG)
    assert first["trafficLimitBytes"] == 3 * GIB
    assert second["trafficLimitBytes"] == 3 * GIB + panel.conflict_delta
    assert third["trafficLimitBytes"] == second["trafficLimitBytes"]  # one external change
    assert panel.patch_count == 0


async def test_conflict_delta_configurable(monkeypatch):
    panel = FakePanel(mode="conflict", conflict_delta=5 * GIB).install(monkeypatch)
    panel.seed_bypass(TG, GIB)
    await remnawave_api.get_bypass_entity_safe(TG)
    assert (await remnawave_api.get_bypass_entity_safe(TG))["trafficLimitBytes"] == 6 * GIB


# ── precise state readers (T4) ─────────────────────────────────────────

async def test_state_readers_present_absent_unavailable(panel):
    assert await remnawave_api.get_bypass_state(TG) == ("absent", None)
    assert await remnawave_api.get_premium_state(TG) == ("absent", None)
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, T0)
    kind, ent = await remnawave_api.get_bypass_state(TG)
    assert kind == "present" and ent["trafficLimitBytes"] == 3 * GIB
    kind, ent = await remnawave_api.get_premium_state(TG)
    assert kind == "present" and ent["expireAt"] == "2026-09-13T12:00:00.000Z"
    panel.mode = "down"
    assert await remnawave_api.get_bypass_state(TG) == ("unavailable", None)
    assert await remnawave_api.get_premium_state(TG) == ("unavailable", None)


async def test_bypass_state_shares_conflict_counter(panel):
    panel.seed_bypass(TG, 3 * GIB)
    panel.mode = "conflict"
    assert (await remnawave_api.get_bypass_state(TG))[1]["trafficLimitBytes"] == 3 * GIB
    assert (await remnawave_api.get_bypass_state(TG))[1]["trafficLimitBytes"] == 4 * GIB


async def test_invisible_reads_then_adoption(panel):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, T0)
    panel.bypass_invisible_reads = 1
    panel.premium_invisible_reads = 1
    assert await remnawave_api.get_bypass_state(TG) == ("absent", None)
    assert await remnawave_api.get_premium_state(TG) == ("absent", None)
    assert (await remnawave_api.get_bypass_state(TG))[0] == "present"
    assert (await remnawave_api.get_premium_state(TG))[0] == "present"


async def test_premium_adopt_without_patch_keeps_stale_expire(panel):
    panel.seed_premium(TG, T0)
    panel.premium_adopt_patch_ok = False
    res = await remnawave_premium.create_premium_user_entity(
        TG, requested_uuid=None, expire_at=T0 + timedelta(days=30),
    )
    assert res.ok and res.recovered
    assert panel.premium_expire(TG) == T0
    assert panel.patch_count == 0


# ── call log ───────────────────────────────────────────────────────────

async def test_calls_are_recorded(panel):
    await remnawave_api.get_bypass_entity_safe(TG)
    await remnawave_bypass.create_bypass_user_entity(TG, traffic_limit_bytes=GIB)
    assert [c[0] for c in panel.calls] == ["get_bypass_entity_safe", "create_bypass_user_entity"]


# ── ignore_patch mode ──────────────────────────────────────────────────

async def test_ignore_patch_answers_ok_but_changes_nothing(panel):
    panel.seed_premium(TG, T0)
    ent = panel.seed_bypass(TG, 3 * GIB)
    panel.mode = "ignore_patch"
    later = T0 + timedelta(days=30)
    prem_id = panel.premium[TG]["id"]

    assert await remnawave_api.update_user(prem_id, expireAt=later, status="ACTIVE") is not None
    assert await remnawave_api.update_user(ent["id"], trafficLimitBytes=13 * GIB,
                                           _trust_bypass=True) is not None

    assert panel.premium_expire(TG) == T0
    assert panel.bypass_limit(TG) == 3 * GIB
    assert panel.patch_count == 0
