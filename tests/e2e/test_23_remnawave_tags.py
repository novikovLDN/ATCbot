"""Remnawave user tags by tariff on the real stack — real Postgres, handlers,
webhooks, provisioning; the Remnawave 3.4.3 REST API faked with its `tag`
validation (tests/fakes/remnawave_http). Provisioning flag off and on.

Premium entity = the tariff (BASIC / PLUS / COMBO_BASIC / COMBO_PLUS / TRIAL),
bypass entity = BYPASS; a renewal of the same tier adds no request, a tier change
rides in the same PATCH, a day grant keeps the tag, a panel that rejects tags
still delivers the purchase.
"""
from datetime import timedelta

import pytest

from app.services import admin_auth
from tests.e2e import flows
from tests.e2e.world import new_user, utcnow

ORIGIN = {"Origin": "https://testserver"}
FLAGS = ["off", "on"]


async def buy_sbp(e2e, u, tariff: str, period: int = 30) -> None:
    pending = await e2e.create_purchase(u, tariff, period, provider="platega")
    res = await flows.pay(e2e, u, pending, "sbp")
    assert res.status == 200 and res.body["status"] == "ok", res
    await e2e.settle()
    await e2e.provisioning_tick()


def premium_date_patches(e2e, tg: int, since: int):
    pid = e2e.panel.premium(tg)["id"]
    return [r for r in e2e.panel.requests[since:]
            if r[0] == "PATCH" and r[1] == "/api/users" and isinstance(r[2], dict)
            and r[2].get("id") == pid and "expireAt" in r[2]]


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("tariff, tag", [
    ("basic", "BASIC"), ("plus", "PLUS"), ("combo_basic", "COMBO_BASIC"), ("combo_plus", "COMBO_PLUS"),
])
async def test_new_purchase_tags_both_entities(e2e, flag, tariff, tag):
    u = new_user()
    await e2e.register(u)
    e2e.provisioning(flag)
    await buy_sbp(e2e, u, tariff)
    assert e2e.panel.premium(u.id)["tag"] == tag
    assert e2e.panel.bypass(u.id)["tag"] == "BYPASS"


@pytest.mark.parametrize("flag", FLAGS)
async def test_trial_is_tagged_trial(e2e, flag):
    u = new_user()
    await e2e.start_user(u)
    e2e.provisioning(flag, "trial" if flag == "on" else None)
    await e2e.tap(u, "activate_trial")
    await e2e.provisioning_tick()
    assert e2e.panel.premium(u.id)["tag"] == "TRIAL"
    assert e2e.panel.bypass(u.id)["tag"] == "BYPASS"


@pytest.mark.parametrize("flag", FLAGS)
async def test_tier_change_rides_in_the_renewal_patch(e2e, flag):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=10))
    assert e2e.panel.premium(u.id)["tag"] == "BASIC"
    e2e.provisioning(flag)
    since = len(e2e.panel.requests)
    await buy_sbp(e2e, u, "plus")
    (_, _, body), = premium_date_patches(e2e, u.id, since)
    assert body["tag"] == "PLUS"
    assert e2e.panel.premium(u.id)["tag"] == "PLUS"


@pytest.mark.parametrize("flag", FLAGS)
async def test_renewal_of_the_same_tier_adds_no_request(e2e, flag):
    u = new_user()
    await flows.seed_active(e2e, u, "plus", utcnow() + timedelta(days=10))
    e2e.provisioning(flag)
    since = len(e2e.panel.requests)
    await buy_sbp(e2e, u, "plus")
    assert len(premium_date_patches(e2e, u.id, since)) == 1
    tag_only = [r for r in e2e.panel.requests[since:] if r[0] == "PATCH" and set(r[2]) == {"id", "tag"}]
    assert tag_only == []
    assert e2e.panel.premium(u.id)["tag"] == "PLUS"


@pytest.mark.parametrize("flag", FLAGS)
async def test_admin_day_grant_keeps_the_tag(e2e, flag):
    u = new_user()
    await flows.seed_active(e2e, u, "combo_plus", utcnow() + timedelta(days=10))
    assert e2e.panel.premium(u.id)["tag"] == "COMBO_PLUS"
    assert await admin_auth.set_credentials("boss", "correct horse battery")
    r = await e2e.http.post("/dashboard/api/auth/login", headers=ORIGIN,
                            json={"username": "boss", "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    e2e.provisioning(flag, "admin" if flag == "on" else None)
    r = await e2e.http.post(f"/dashboard/api/users/{u.id}/grant", headers=ORIGIN,
                            json={"days": 7, "tariff": "basic"})
    assert r.status_code == 200, r.text
    await e2e.settle()
    await e2e.provisioning_tick()
    assert e2e.panel.premium(u.id)["tag"] == "COMBO_PLUS"


@pytest.mark.parametrize("flag", FLAGS)
async def test_a_panel_that_rejects_tags_still_delivers_the_purchase(e2e, flag):
    u = new_user()
    await e2e.register(u)
    e2e.provisioning(flag)
    e2e.panel.reject_tags = True
    before = await flows.snapshot(e2e, u.id)
    mark = e2e.tg.mark()
    await buy_sbp(e2e, u, "combo_basic")
    await flows.check_purchase(e2e, u.id, before, "combo_basic", 30)
    assert e2e.panel.premium(u.id)["tag"] is None and e2e.panel.bypass(u.id)["tag"] is None
    assert e2e.admin_texts(mark) == [], e2e.admin_texts(mark)
