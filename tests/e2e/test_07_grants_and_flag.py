"""Scenario 7 — the provisioning flag on the non-purchase entry points, and the
owner rule «grants without a purchase = premium days, 0 GB».

Purchases (webhook / telegram / balance) × flag off/on are in
test_02_purchases.py, auto-renewal × flag in test_05_autorenew.py; here: admin
grant through the dashboard API (entrypoint "admin"), gift activation ("gift"),
trial ("trial").
"""
from datetime import timedelta

import pytest

from app.services import admin_auth
from tests.e2e import flows
from tests.e2e.world import MB, new_user, utcnow

ORIGIN = {"Origin": "https://testserver"}


async def _dashboard_session(e2e):
    assert await admin_auth.set_credentials("boss", "correct horse battery")
    r = await e2e.http.post("/dashboard/api/auth/login", headers=ORIGIN,
                            json={"username": "boss", "password": "correct horse battery"})
    assert r.status_code == 200, r.text


def _grant_params():
    out = []
    for flag in ("off", "on"):
        for state in ("new", "active"):
            marks = []
            if flag == "off" and state == "new":
                marks = [pytest.mark.xfail(strict=True, reason="legacy flag-off bug T0-ADMIN-GB "
                                                               "(day grant to a new user creates 10 GB)")]
            out.append(pytest.param(flag, state, marks=marks, id=f"{flag}-{state}"))
    return out


@pytest.mark.parametrize("flag,state", _grant_params())
async def test_admin_grant_days_via_dashboard_is_premium_only_0_gb(e2e, flag, state):
    u = new_user()
    if state == "new":
        await e2e.register(u)
    else:
        await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=10))
    await _dashboard_session(e2e)
    e2e.provisioning(flag, "admin" if flag == "on" else None)
    before = await flows.snapshot(e2e, u.id)

    r = await e2e.http.post(f"/dashboard/api/users/{u.id}/grant", headers=ORIGIN,
                            json={"days": 7, "tariff": "basic"})
    assert r.status_code == 200, r.text
    await e2e.settle()
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "basic", 7, paid=False, bypass_gain=0)
    assert len(await e2e.payments(u.id)) == before.payments       # a grant is not a payment


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_gift_activation_under_both_flags(e2e, flag):
    buyer, u = new_user(), new_user()
    await e2e.register(buyer)
    gift = await e2e.create_purchase(buyer, "basic", 90, provider="platega", purchase_type="gift")
    assert (await flows.pay(e2e, buyer, gift, "sbp")).body["status"] == "ok"
    code = await e2e.val("SELECT gift_code FROM gift_subscriptions WHERE buyer_telegram_id=$1", buyer.id)
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=5))
    e2e.provisioning(flag, "gift" if flag == "on" else None)
    before = await flows.snapshot(e2e, u.id)
    await e2e.send(u, f"/start gift_{code}")
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "basic", 90, paid=False)


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_trial_under_both_flags(e2e, flag):
    u = new_user()
    await e2e.start_user(u)
    e2e.provisioning(flag, "trial" if flag == "on" else None)
    await e2e.tap(u, "activate_trial")
    await e2e.provisioning_tick()
    sub = await e2e.sub(u.id)
    assert sub and abs((sub["expires_at"] - (utcnow() + timedelta(days=3))).total_seconds()) < 60
    assert abs((e2e.panel.premium_expire(u.id) - sub["expires_at"]).total_seconds()) <= 1.5
    assert e2e.panel.bypass_limit(u.id) == 500 * MB
