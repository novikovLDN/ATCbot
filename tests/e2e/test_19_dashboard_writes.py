"""Scenario 17 — dashboard write endpoints with a real admin session on a
real DB and the panel fake (coverage 09: admin_revoke_access_atomic,
admin_grant_access_minutes_atomic and admin_delete_user_complete had never
run on Postgres; the delete test mocked the DB, so its SQL never executed).

Owner rules: an admin action changes access exactly as the button says, the
panel follows the DB, every action is in audit_log, CSRF (Origin) is enforced
on the real session.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

import database
from app.services import admin_auth
from tests.e2e import flows
from tests.e2e.world import ADMIN, new_user, utcnow
from tests.fakes import providers_http as prov

ORIGIN = {"Origin": "https://testserver"}
API = "/dashboard/api"
PW = "correct horse battery"


async def _admin(e2e):
    assert await admin_auth.set_credentials("boss", PW)
    r = await e2e.http.post(f"{API}/auth/login", json={"username": "boss", "password": PW}, headers=ORIGIN)
    assert r.status_code == 200, r.text


async def _audit(e2e, action, target):
    return await e2e.rows("SELECT * FROM audit_log WHERE action=$1 AND target_user=$2", action, target)


# ── revoke ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("seed_flag", [None, "on"], ids=["legacy-seed", "outbox-seed"])
async def test_revoke_cuts_vpn_in_the_panel_and_a_new_purchase_restores_it(e2e, seed_flag):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=20), flag=seed_flag)
    bypass_before = e2e.panel.bypass_limit(u.id)
    await _admin(e2e)

    r = await e2e.http.post(f"{API}/users/{u.id}/revoke", headers=ORIGIN)
    assert r.status_code == 200 and r.json() == {"ok": True}, r.text
    await e2e.settle()
    sub = await e2e.sub(u.id)
    assert sub["status"] == "expired" and sub["expires_at"] <= utcnow()
    assert e2e.panel.premium(u.id)["status"] == "DISABLED", \
        "revoked in the DB, but the VPN key keeps working in the panel"
    assert e2e.panel.bypass_limit(u.id) == bypass_before          # paid GB are not taken away
    audit = await _audit(e2e, "admin_revoke", u.id)
    assert len(audit) == 1 and audit[0]["telegram_id"] == ADMIN

    # the user pays again: premium is active again, panel = DB, +10 GB
    before = await flows.snapshot(e2e, u.id)
    p = await e2e.create_purchase(u, "basic", 30, provider="platega")
    assert (await flows.pay(e2e, u, p, "sbp")).body["status"] == "ok"
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert e2e.panel.premium(u.id)["status"] == "ACTIVE"


async def test_revoke_without_an_active_subscription_changes_nothing(e2e):
    u = new_user()
    await e2e.register(u)
    await _admin(e2e)
    writes = len(e2e.panel.writes())
    r = await e2e.http.post(f"{API}/users/{u.id}/revoke", headers=ORIGIN)
    assert r.status_code == 200 and r.json() == {"ok": False}
    assert len(e2e.panel.writes()) == writes
    assert await _audit(e2e, "admin_revoke", u.id) == []


async def test_revoke_without_origin_is_refused_and_changes_nothing(e2e):
    u = new_user()
    until = utcnow() + timedelta(days=20)
    await flows.seed_active(e2e, u, "basic", until, flag="on")
    await _admin(e2e)
    r = await e2e.http.post(f"{API}/users/{u.id}/revoke")                   # cookie, no Origin
    assert r.status_code == 403, r.text
    r = await e2e.http.post(f"{API}/users/{u.id}/revoke", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403, r.text
    sub = await e2e.sub(u.id)
    assert sub["status"] == "active" and abs((sub["expires_at"] - until).total_seconds()) < 2
    assert e2e.panel.premium(u.id)["status"] == "ACTIVE"


# ── grant minutes ────────────────────────────────────────────────────

@pytest.mark.parametrize("flag", ["off", "on"])
async def test_grant_minutes_extends_an_active_subscriber_in_db_and_panel(e2e, flag):
    u = new_user()
    until = utcnow() + timedelta(days=10)
    await flows.seed_active(e2e, u, "basic", until, flag=None if flag == "off" else "on")
    bypass_before = e2e.panel.bypass_limit(u.id)
    e2e.provisioning(flag)
    await _admin(e2e)

    r = await e2e.http.post(f"{API}/users/{u.id}/grant-minutes", json={"minutes": 90}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    if flag == "on":
        await e2e.provisioning_tick()
    await e2e.settle()
    sub = await e2e.sub(u.id)
    assert abs((sub["expires_at"] - (until + timedelta(minutes=90))).total_seconds()) <= 5, sub["expires_at"]
    assert abs((e2e.panel.premium_expire(u.id) - sub["expires_at"]).total_seconds()) <= 1.5
    assert e2e.panel.bypass_limit(u.id) == bypass_before           # a grant in minutes gives 0 GB
    assert sub["subscription_type"] == "basic"


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_grant_minutes_to_a_new_user_gives_premium_until_now_plus_minutes(e2e, flag):
    u = new_user()
    await e2e.register(u)
    e2e.provisioning(flag)
    await _admin(e2e)
    r = await e2e.http.post(f"{API}/users/{u.id}/grant-minutes", json={"minutes": 120}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    if flag == "on":
        await e2e.provisioning_tick()
    await e2e.settle()
    sub = await e2e.sub(u.id)
    assert sub["status"] == "active"
    assert abs((sub["expires_at"] - (utcnow() + timedelta(minutes=120))).total_seconds()) <= 10
    assert abs((e2e.panel.premium_expire(u.id) - sub["expires_at"]).total_seconds()) <= 1.5
    if flag == "on":
        assert (e2e.panel.bypass_limit(u.id) or 0) == 0            # T0-ADMIN-GB is fixed only by the flag


# ── delete user ──────────────────────────────────────────────────────

async def _top_up(e2e, u, rub=250):
    await e2e.tap(u, "topup_balance")
    await e2e.tap(u, f"topup_amount:{rub}")
    await e2e.tap(u, f"topup_sbp:{rub}")
    top = await flows.latest_pending(e2e, u.id)
    assert (await e2e.webhook(prov.platega_webhook(top["purchase_id"], float(rub)))).body["status"] == "ok"


async def test_delete_user_removes_his_rows_and_panel_entities(e2e):
    ref, u = new_user(), new_user()
    await e2e.register(ref)
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=20), flag="on")
    assert await database.register_referral(ref.id, u.id)
    await _top_up(e2e, u)
    assert e2e.panel.premium(u.id) is not None and e2e.panel.bypass(u.id) is not None
    await _admin(e2e)

    r = await e2e.http.delete(f"{API}/users/{u.id}", headers=ORIGIN)
    assert r.status_code == 200 and r.json().get("ok") is True, r.text
    await e2e.settle()
    for table, col in (("users", "telegram_id"), ("subscriptions", "telegram_id"),
                       ("payments", "telegram_id"), ("pending_purchases", "telegram_id"),
                       ("balance_transactions", "user_id"), ("referrals", "referred_user_id"),
                       ("subscription_history", "telegram_id")):
        assert await e2e.val(f"SELECT count(*) FROM {table} WHERE {col}=$1", u.id) == 0, table
    assert e2e.panel.premium(u.id) is None and e2e.panel.bypass(u.id) is None
    assert await e2e.val("SELECT count(*) FROM users WHERE telegram_id=$1", ref.id) == 1
    assert len(await _audit(e2e, "admin_delete_user", u.id)) == 1

    # the same Telegram account can start over
    await e2e.start_user(u)
    assert await e2e.val("SELECT count(*) FROM users WHERE telegram_id=$1", u.id) == 1


async def test_invitee_of_an_already_deleted_referrer_can_still_pay(e2e):
    """REF-DANGLING (fixed): a users.referrer_id pointing to a user that no longer
    exists is «no referrer» in process_referral_reward — no cashback, the link
    is cleared, the paid purchase goes through."""
    u = new_user()
    await e2e.register(u)
    await e2e.pool.execute("UPDATE users SET referrer_id=$2 WHERE telegram_id=$1", u.id, 9_999_001)
    before = await flows.snapshot(e2e, u.id)
    p = await e2e.create_purchase(u, "basic", 30, provider="platega")
    assert (await flows.pay(e2e, u, p, "sbp")).body["status"] == "ok"
    await flows.check_purchase(e2e, u.id, before, "basic", 30)


async def test_delete_referrer_keeps_the_referred_user_working(e2e):
    ref, u = new_user(), new_user()
    await e2e.register(ref)
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=20))
    assert await database.register_referral(ref.id, u.id)
    await _admin(e2e)
    r = await e2e.http.delete(f"{API}/users/{ref.id}", headers=ORIGIN)
    assert r.status_code == 200, r.text
    await e2e.settle()
    assert await e2e.val("SELECT count(*) FROM users WHERE telegram_id=$1", u.id) == 1
    # the referred user still buys normally (no cashback lookup crash on a deleted referrer)
    before = await flows.snapshot(e2e, u.id)
    p = await e2e.create_purchase(u, "basic", 30, provider="platega")
    assert (await flows.pay(e2e, u, p, "sbp")).body["status"] == "ok"
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
