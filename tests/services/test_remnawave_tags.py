"""Remnawave user tags by tariff (owner 2026-09-14).

Premium entity = the current tariff (TRIAL / BASIC / PLUS / COMBO_BASIC /
COMBO_PLUS, legacy biz → PLUS), bypass entity = BYPASS. Contract: Remnawave
3.4.3 `tag` on POST/PATCH /api/users, ^[A-Z0-9_]+$ up to 16, nullable
(docs/providers/remnawave_3.4.3.md §3a).

  * mapping (tariffs.premium_panel_tag*)
  * client: tag sent / invalid never sent / a panel that rejects it still gets the write
  * outbox (flag on, FakePanel): creation, renewal same tier (no extra PATCH), tier
    change in the same PATCH, day grants keep the tag, rejected tags never fail
  * legacy path (flag off, FakeRemnawaveHTTP): the same rules through purchase_flow
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import database
from app.api import payment_webhook
from app.services import (
    admin_alerts, provisioning, purchase_flow, remnawave_api, remnawave_bypass, sub_aggregator, tariffs,
)
from app.services.tariffs import for_grant, for_purchase, for_trial
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeConn, FakeDB, FakeJobs
from tests.fakes.remnawave_http import FakeRemnawaveHTTP

GIB = 1024 ** 3
TG = 5151
NOW = datetime.now(timezone.utc).replace(microsecond=0)
UNTIL = NOW + timedelta(days=30)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── mapping ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tariff, kw, tag", [
    ("basic", {}, "BASIC"),
    ("plus", {}, "PLUS"),
    ("combo_basic", {}, "COMBO_BASIC"),
    ("combo_plus", {}, "COMBO_PLUS"),
    ("basic", {"is_combo": True}, "COMBO_BASIC"),
    ("plus", {"is_combo": True}, "COMBO_PLUS"),
    ("trial", {}, "TRIAL"),
    ("basic", {"is_trial": True}, "TRIAL"),
    ("biz_team", {}, "PLUS"),                 # legacy business tariff = Plus
    ("grant", {}, None),                      # day grant: tag untouched
    ("pack", {}, None),
    ("spotify_30", {}, None),
    (None, {}, None),
])
def test_premium_panel_tag(tariff, kw, tag):
    assert tariffs.premium_panel_tag(tariff, **kw) == tag


def test_every_tag_fits_the_3_4_3_contract():
    for tag in tariffs.PANEL_TAGS:
        assert remnawave_api.clean_tag(tag) == tag
    assert remnawave_api.clean_tag("plus") is None           # lower case
    assert remnawave_api.clean_tag("A" * 17) is None         # > 16 chars
    assert remnawave_api.clean_tag("COMBO-PLUS") is None     # dash


def test_tag_for_a_subscriptions_row():
    f = tariffs.premium_panel_tag_for_subscription
    assert f({"subscription_type": "basic", "source": "trial"}) == "TRIAL"
    assert f({"subscription_type": "plus", "is_combo": True, "source": "payment"}) == "COMBO_PLUS"
    assert f({"subscription_type": "basic", "source": "admin"}) == "BASIC"
    assert f({"subscription_type": "basic", "is_bypass_only": True}) is None
    assert f(None) is None


# ── client (HTTP fake, 3.4.3 validation) ───────────────────────────────

@pytest.fixture
def http(monkeypatch):
    return FakeRemnawaveHTTP().install(monkeypatch)


def _posts(http):
    return [r for r in http.requests if r[0] == "POST" and r[1] == "/api/users"]


def _patches(http):
    return [r for r in http.requests if r[0] == "PATCH" and r[1] == "/api/users"]


async def test_create_sends_the_tag(http):
    raw = await remnawave_api.create_user("tg_1_premium", "s1", 0, iso(UNTIL), squad_uuid="",
                                          tag="PLUS", raw_response=True)
    assert raw["ok"] and http.by_username("tg_1_premium")["tag"] == "PLUS"
    assert _posts(http)[0][2]["tag"] == "PLUS"


async def test_an_invalid_tag_is_never_sent(http):
    raw = await remnawave_api.create_user("u_bad", "s2", 0, iso(UNTIL), squad_uuid="",
                                          tag="plus-lower", raw_response=True)
    assert raw["ok"] and http.by_username("u_bad")["tag"] is None
    assert "tag" not in _posts(http)[0][2]


async def test_a_rejected_tag_on_patch_is_resent_without_it(http):
    ent = http.seed_premium(TG, NOW + timedelta(days=5))
    http.reject_tags = True
    res = await remnawave_api.update_user(ent["id"], expireAt=iso(UNTIL), status="ACTIVE", tag="PLUS")
    assert res is not None                                   # the write succeeded
    assert http.premium_expire(TG) == UNTIL
    assert http.premium(TG)["tag"] is None
    first, second = _patches(http)
    assert first[2]["tag"] == "PLUS" and "tag" not in second[2]
    assert second[2]["expireAt"] == iso(UNTIL)


async def test_another_400_is_not_resent(http):
    ent = http.seed_premium(TG, NOW + timedelta(days=5))
    res = await remnawave_api.update_user(ent["id"], expireAt=iso(NOW - timedelta(days=1)), tag="PLUS")
    assert res is None and len(_patches(http)) == 1          # a past expireAt: no tag fallback


async def test_a_rejected_tag_on_create_still_creates(http):
    http.reject_tags = True
    res = await remnawave_bypass.create_bypass_user_entity(TG, traffic_limit_bytes=10 * GIB)
    assert res.ok and not res.recovered
    assert http.bypass_limit(TG) == 10 * GIB and http.bypass(TG)["tag"] is None
    assert len(_posts(http)) == 2 and "tag" not in _posts(http)[1][2]


async def test_bypass_create_is_tagged_bypass(http):
    res = await remnawave_bypass.create_bypass_user_entity(TG, traffic_limit_bytes=GIB)
    assert res.ok and http.bypass(TG)["tag"] == "BYPASS"


async def test_set_user_tag_is_a_tag_only_patch(http):
    ent = http.seed_bypass(TG, GIB)
    env = await remnawave_api.set_user_tag(ent["id"], "BYPASS")
    assert env["ok"] and _patches(http) == [("PATCH", "/api/users", {"id": ent["id"], "tag": "BYPASS"})]
    bad = await remnawave_api.set_user_tag(ent["id"], "bad tag")
    assert bad["ok"] is False and len(_patches(http)) == 1


# ── outbox (flag on) ───────────────────────────────────────────────────

@pytest.fixture
def panel(monkeypatch):
    return FakePanel().install(monkeypatch)


@pytest.fixture
def jobs(monkeypatch, panel):
    return FakeJobs(panel).install(monkeypatch)


@pytest.fixture
def db(monkeypatch):
    return FakeDB().install(monkeypatch)


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.setattr(sub_aggregator, "invalidate_bg", lambda tg: None)
    monkeypatch.setattr(payment_webhook, "_bot", None)
    monkeypatch.setattr(admin_alerts, "send_alert", AsyncMock(return_value=True))


async def run_job(ent, key="purchase:1", until=UNTIL):
    job_id = await provisioning.enqueue(FakeConn(), key=key, telegram_id=TG, ent=ent,
                                        premium_until=until, source="test")
    assert await provisioning.run_now(job_id) is True


def premium_updates(panel):
    ids = {panel.premium[TG]["id"], panel.premium[TG]["vlessUuid"]} if TG in panel.premium else set()
    return [c for c in panel.calls if c[0] == "update_user" and c[1] in ids]


async def test_trial_creates_trial_and_bypass_tags(panel, jobs, db):
    await run_job(for_trial(3), key="trial:1", until=NOW + timedelta(days=3))
    assert panel.premium_tag(TG) == "TRIAL"
    assert panel.bypass_tag(TG) == "BYPASS"


@pytest.mark.parametrize("key, tag", [
    ("basic", "BASIC"), ("plus", "PLUS"), ("combo_basic", "COMBO_BASIC"), ("combo_plus", "COMBO_PLUS"),
])
async def test_purchase_creates_the_tariff_tag(panel, jobs, db, key, tag):
    await run_job(for_purchase(key, 30))
    assert panel.premium_tag(TG) == tag
    assert panel.bypass_tag(TG) == "BYPASS"


async def test_renewal_same_tier_sends_no_extra_patch(panel, jobs, db):
    panel.seed_premium(TG, NOW + timedelta(days=10), tag="PLUS")
    panel.seed_bypass(TG, 5 * GIB, tag="BYPASS")
    await run_job(for_purchase("plus", 30), until=NOW + timedelta(days=40))
    (_, _, fields), = premium_updates(panel)                 # ONE premium PATCH, as before
    assert "tag" not in fields
    assert panel.patch_count == 2                             # premium date + bypass GB
    assert panel.premium_tag(TG) == "PLUS"


async def test_tier_change_rides_in_the_same_patch(panel, jobs, db):
    panel.seed_premium(TG, NOW + timedelta(days=10), tag="BASIC")
    panel.seed_bypass(TG, 5 * GIB, tag="BYPASS")
    await run_job(for_purchase("plus", 30), until=NOW + timedelta(days=40))
    (_, _, fields), = premium_updates(panel)
    assert fields["tag"] == "PLUS" and "expireAt" in fields
    assert panel.premium_tag(TG) == "PLUS"


async def test_trial_to_paid_replaces_the_trial_tag(panel, jobs, db):
    panel.seed_premium(TG, NOW + timedelta(days=2), tag="TRIAL")
    panel.seed_bypass(TG, 500 * 1024 ** 2, tag="BYPASS")
    await run_job(for_purchase("combo_plus", 30))
    assert panel.premium_tag(TG) == "COMBO_PLUS"


async def test_tier_change_without_a_date_patch_sends_one_tag_patch(panel, jobs, db):
    panel.seed_premium(TG, NOW + timedelta(days=60), tag="BASIC")   # already beyond the target
    panel.seed_bypass(TG, GIB, tag="BYPASS")
    await run_job(for_purchase("plus", 30))
    (_, _, fields), = premium_updates(panel)
    assert fields == {"tag": "PLUS", "hwidDeviceLimit": 14}
    assert panel.premium_tag(TG) == "PLUS"


async def test_untagged_entity_without_a_date_patch_gets_no_extra_request(panel, jobs, db):
    panel.seed_premium(TG, NOW + timedelta(days=60))                # legacy, no tag
    panel.seed_bypass(TG, GIB, tag="BYPASS")
    await run_job(for_purchase("plus", 30))
    assert premium_updates(panel) == []                             # the backfill tags it, not us


async def test_day_grant_keeps_the_tag(panel, jobs, db):
    panel.seed_premium(TG, NOW + timedelta(days=10), tag="COMBO_PLUS")
    await run_job(for_grant("basic", 30), key="grant:1", until=NOW + timedelta(days=40))
    (_, _, fields), = premium_updates(panel)
    assert "tag" not in fields and "expireAt" in fields
    assert panel.premium_tag(TG) == "COMBO_PLUS"


async def test_day_grant_for_a_new_entity_tags_the_grant_tier(panel, jobs, db):
    await run_job(for_grant("plus", 7), key="grant:2", until=NOW + timedelta(days=7))
    assert panel.premium_tag(TG) == "PLUS"


async def test_day_grant_adopting_an_entity_keeps_its_tag(panel, jobs, db):
    panel.seed_premium(TG, NOW + timedelta(days=1), tag="TRIAL")
    panel.premium_invisible_reads = 1                               # read miss → create → adopt
    await run_job(for_grant("basic", 7), key="grant:3", until=NOW + timedelta(days=7))
    assert panel.premium_tag(TG) == "TRIAL"


async def test_a_panel_that_rejects_tags_still_delivers(panel, jobs, db):
    panel.reject_tags = True
    await run_job(for_purchase("combo_basic", 30))
    assert panel.premium_expire(TG) == UNTIL
    assert panel.bypass_limit(TG) == 75 * GIB
    assert panel.premium_tag(TG) is None and panel.bypass_tag(TG) is None
    assert panel.tag_rejections == 2


async def test_bypass_topup_tags_an_untagged_entity_in_the_same_patch(panel, jobs, db):
    panel.seed_bypass(TG, 3 * GIB)
    await run_job(for_purchase("basic", 30))
    patches = [c for c in panel.calls if c[0] == "update_user" and "trafficLimitBytes" in c[2]]
    assert len(patches) == 1 and patches[0][2]["tag"] == "BYPASS"
    assert panel.bypass_tag(TG) == "BYPASS"


# ── legacy path (flag off, HTTP fake) ──────────────────────────────────

@pytest.fixture
def legacy(monkeypatch, http):
    """purchase_flow + remnawave_* against the HTTP fake; the DB cache in a dict."""
    cache: dict = {}
    row: dict = {}

    async def get_sub(tg):
        return dict(row) or None

    async def premium_uuid(tg):
        return cache.get("premium_uuid")

    async def set_premium(tg, uuid, url, *, short_uuid=None, mark_migrated=True):
        cache.update(premium_uuid=uuid, premium_url=url)

    async def bypass_uuid(tg):
        return cache.get("bypass_uuid")

    async def set_bypass(tg, uuid, url, short):
        if uuid:
            cache["bypass_uuid"] = uuid
        if url:
            cache["bypass_url"] = url

    async def noop(*_a, **_k):
        return None

    async def bypass_cache(tg):
        return {"remnawave_bypass_sub_url": cache.get("bypass_url")}

    for name, fn in (
        ("get_subscription_any", get_sub), ("get_remnawave_premium_uuid", premium_uuid),
        ("set_remnawave_premium_uuid_and_url", set_premium), ("set_remnawave_premium_id", noop),
        ("get_remnawave_uuid", bypass_uuid), ("set_remnawave_bypass_cache", set_bypass),
        ("set_remnawave_id", noop), ("get_remnawave_bypass_cache", bypass_cache),
    ):
        monkeypatch.setattr(database, name, fn, raising=False)
    monkeypatch.setattr(purchase_flow, "_premium_url_for_existing", AsyncMock(return_value="https://p/sub/x"))

    async def cached_id(uuid):
        ent = http.premium(TG)
        return ent["id"] if ent and ent["vlessUuid"] == uuid else None

    monkeypatch.setattr(remnawave_api, "_lookup_cached_id_by_uuid", cached_id)
    return type("Legacy", (), {"cache": cache, "row": row})


@pytest.mark.parametrize("kw, tag", [
    ({"tariff": "basic"}, "BASIC"),
    ({"tariff": "plus", "is_combo": True}, "COMBO_PLUS"),
    ({"tariff": "basic", "is_trial": True}, "TRIAL"),
])
async def test_legacy_new_issuance_tags_both_entities(http, legacy, kw, tag):
    await purchase_flow.provision_subscription(TG, subscription_end=UNTIL, period_days=30, **kw)
    assert http.premium(TG)["tag"] == tag
    assert http.bypass(TG)["tag"] == "BYPASS"


def _seed_legacy_premium(http, legacy, tag):
    ent = http.seed_premium(TG, NOW + timedelta(days=10), tag=tag)
    legacy.cache["premium_uuid"] = ent["vlessUuid"]
    return ent


async def test_legacy_renewal_tier_change_in_the_same_patch(http, legacy):
    ent = _seed_legacy_premium(http, legacy, "BASIC")
    legacy.row.update(subscription_type="plus", source="payment")      # committed row
    await purchase_flow._sync_renewal_once({"telegram_id": TG, "subscription_end": UNTIL, "tariff": "plus"})
    (_, _, body), = _patches(http)
    assert body["id"] == ent["id"] and body["tag"] == "PLUS" and body["expireAt"] == iso(UNTIL)
    assert http.premium(TG)["tag"] == "PLUS"


async def test_legacy_renewal_same_tier_is_one_patch(http, legacy):
    _seed_legacy_premium(http, legacy, "PLUS")
    legacy.row.update(subscription_type="plus", source="payment")
    await purchase_flow._sync_renewal_once({"telegram_id": TG, "subscription_end": UNTIL, "tariff": "plus"})
    assert len(_patches(http)) == 1 and http.premium(TG)["tag"] == "PLUS"


async def test_legacy_combo_renewal_uses_the_purchase_tag(http, legacy):
    _seed_legacy_premium(http, legacy, "BASIC")
    legacy.row.update(subscription_type="basic", source="payment")     # combo flag not written yet
    await purchase_flow._sync_renewal_once({"telegram_id": TG, "subscription_end": UNTIL, "tariff": "basic",
                                            "panel_tag": "COMBO_BASIC"})
    assert http.premium(TG)["tag"] == "COMBO_BASIC"


async def test_legacy_day_grant_keeps_the_tag(http, legacy):
    _seed_legacy_premium(http, legacy, "COMBO_PLUS")
    await purchase_flow.provision_subscription(TG, tariff="basic", subscription_end=UNTIL, period_days=30,
                                               keep_panel_tag=True)
    assert "tag" not in _patches(http)[0][2]
    assert http.premium(TG)["tag"] == "COMBO_PLUS"


async def test_legacy_day_grant_renewal_keeps_the_row_tariff(http, legacy):
    _seed_legacy_premium(http, legacy, "TRIAL")
    legacy.row.update(subscription_type="basic", source="trial")       # the grant kept the trial row
    await purchase_flow._sync_renewal_once({"telegram_id": TG, "subscription_end": UNTIL, "tariff": "basic"})
    assert http.premium(TG)["tag"] == "TRIAL"


async def test_legacy_rejected_tag_never_fails_the_renewal(http, legacy):
    _seed_legacy_premium(http, legacy, "BASIC")
    legacy.row.update(subscription_type="plus", source="payment")
    http.reject_tags = True
    await purchase_flow._sync_renewal_once({"telegram_id": TG, "subscription_end": UNTIL, "tariff": "plus"})
    assert http.premium_expire(TG) == UNTIL
    assert http.premium(TG)["tag"] == "BASIC"
    assert [("tag" in r[2]) for r in _patches(http)] == [True, False]
