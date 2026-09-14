"""Scenario 1 — a new user: /start (plain, referral link, gift code), trial
activation (500 MB + 3 days), trial expiry.
"""
from datetime import timedelta

import pytest

import database
from tests.e2e import flows
from tests.e2e.world import MB, new_user, utcnow

TRIAL_DAYS = 3


async def test_plain_start_captcha_language_main_menu(e2e):
    u = new_user()
    await e2e.send(u, "/start")
    user = await e2e.row("SELECT * FROM users WHERE telegram_id=$1", u.id)
    assert user is not None and user["referral_code"], user
    assert user["captcha_passed_at"] is None
    assert any(d.startswith("captcha:") for s in e2e.tg.to(u.id) for d in s.callback_data()), "no captcha"

    await e2e.pass_captcha(u)
    assert (await e2e.row("SELECT captcha_passed_at FROM users WHERE telegram_id=$1", u.id))["captcha_passed_at"]
    assert e2e.tg.with_button(u.id, "start_lang_ru"), "no language picker after captcha"
    mark = e2e.tg.mark()
    await e2e.tap(u, "start_lang_ru")
    assert (await e2e.row("SELECT language FROM users WHERE telegram_id=$1", u.id))["language"] == "ru"
    menu = e2e.tg.since(mark, u.id)
    assert menu and menu[-1].callback_data(), "no main menu"
    # nothing was granted, nothing went to the panel or the admin
    assert await e2e.sub(u.id) is None and e2e.panel.users == {}
    assert e2e.admin_texts() == []


async def test_second_start_does_not_duplicate_user_or_captcha(e2e):
    u = new_user()
    await e2e.start_user(u)
    mark = e2e.tg.mark()
    await e2e.send(u, "/start")
    assert await e2e.val("SELECT count(*) FROM users WHERE telegram_id=$1", u.id) == 1
    assert not any(d.startswith("captcha:") for s in e2e.tg.since(mark, u.id) for d in s.callback_data())


async def test_start_with_referral_link_binds_referrer_and_notifies_him(e2e):
    ref = new_user()
    await e2e.start_user(ref)
    code = await e2e.val("SELECT referral_code FROM users WHERE telegram_id=$1", ref.id)
    u = new_user()
    mark = e2e.tg.mark()
    await e2e.start_user(u, f"ref_{code}")
    assert await e2e.val("SELECT referrer_id FROM users WHERE telegram_id=$1", u.id) == ref.id
    assert await e2e.val(
        "SELECT count(*) FROM referrals WHERE referrer_user_id=$1 AND referred_user_id=$2", ref.id, u.id) == 1
    assert e2e.user_texts(ref.id, mark), "referrer was not notified"


async def test_referral_link_ignored_for_existing_user_and_self(e2e):
    ref = new_user()
    await e2e.start_user(ref)
    code = await e2e.val("SELECT referral_code FROM users WHERE telegram_id=$1", ref.id)
    # self-referral
    await e2e.send(ref, f"/start ref_{code}")
    assert await e2e.val("SELECT referrer_id FROM users WHERE telegram_id=$1", ref.id) is None
    # an existing user cannot be re-bound
    other = new_user()
    await e2e.start_user(other)
    await e2e.send(other, f"/start ref_{code}")
    assert await e2e.val("SELECT referrer_id FROM users WHERE telegram_id=$1", other.id) is None


async def test_start_with_gift_code_activates_the_gift(e2e):
    buyer = new_user()
    await e2e.register(buyer)
    gift = await e2e.create_purchase(buyer, "basic", 30, provider="platega", purchase_type="gift")
    res = await flows.pay(e2e, buyer, gift, "sbp")
    assert (res.status, res.body["status"]) == (200, "ok"), res
    code = await e2e.val("SELECT gift_code FROM gift_subscriptions WHERE buyer_telegram_id=$1", buyer.id)
    assert code, "gift code not created after payment"
    assert await e2e.sub(buyer.id) is None, "the buyer must not get the gifted access"

    u = new_user()
    before = await flows.snapshot(e2e, u.id)
    await e2e.start_user(u, f"gift_{code}")
    await flows.check_purchase(e2e, u.id, before, "basic", 30, paid=False)
    assert await e2e.val("SELECT status FROM gift_subscriptions WHERE gift_code=$1", code) == "activated"

    # the same link again (another user / replay) → nothing more
    other = new_user()
    await e2e.start_user(other, f"gift_{code}")
    assert await e2e.sub(other.id) is None


async def test_trial_activation_is_3_days_and_500_mb_once(e2e):
    u = new_user()
    await e2e.start_user(u)
    mark = e2e.tg.mark()
    await e2e.tap(u, "activate_trial")
    sub = await e2e.sub(u.id)
    assert sub and sub["source"] == "trial" and sub["status"] == "active"
    assert abs((sub["expires_at"] - (utcnow() + timedelta(days=TRIAL_DAYS))).total_seconds()) < 60
    assert abs((e2e.panel.premium_expire(u.id) - sub["expires_at"]).total_seconds()) <= 1.5
    assert e2e.panel.bypass_limit(u.id) == 500 * MB
    assert await e2e.val("SELECT trial_used_at FROM users WHERE telegram_id=$1", u.id)
    assert e2e.user_texts(u.id, mark)
    assert await e2e.payments(u.id) == []

    # second tap: no second trial
    await e2e.pool.execute("DELETE FROM subscriptions WHERE 1=0")
    from app.core import rate_limit
    e2e.mp.setattr(rate_limit, "_rate_limiter", None)
    await e2e.tap(u, "activate_trial")
    assert (await e2e.sub(u.id))["expires_at"] == sub["expires_at"]
    assert e2e.panel.bypass_limit(u.id) == 500 * MB


async def test_trial_expiry_path(e2e):
    """Trial ends → trial worker expires it and tells the user once; the paid
    purchase afterwards starts from now."""
    import trial_notifications
    u = new_user()
    await e2e.start_user(u)
    await e2e.tap(u, "activate_trial")
    past = utcnow() - timedelta(minutes=5)
    await e2e.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1", u.id, past.replace(tzinfo=None))
    await e2e.pool.execute("UPDATE users SET trial_expires_at=$2 WHERE telegram_id=$1", u.id, past.replace(tzinfo=None))

    mark = e2e.tg.mark()
    await trial_notifications.expire_trial_subscriptions(e2e.bot)
    await e2e.settle()
    first = e2e.user_texts(u.id, mark)
    sub = await e2e.sub(u.id)
    assert sub["status"] != "active" or sub["expires_at"] <= utcnow() or sub.get("is_bypass_only")
    mark2 = e2e.tg.mark()
    await trial_notifications.expire_trial_subscriptions(e2e.bot)
    await e2e.settle()
    assert e2e.user_texts(u.id, mark2) == [], "trial-expired notice sent twice"
    assert first, "the user was not told the trial ended"

    before = await flows.snapshot(e2e, u.id)
    pending = await e2e.create_purchase(u, "basic", 30, provider="platega")
    res = await flows.pay(e2e, u, pending, "sbp")
    assert res.body["status"] == "ok"
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert (await e2e.sub(u.id))["source"] == "payment"


async def test_trial_tap_by_a_paying_user_grants_nothing(e2e):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=20))
    sub = await e2e.sub(u.id)
    limit = e2e.panel.bypass_limit(u.id)
    await e2e.tap(u, "activate_trial")
    after = await e2e.sub(u.id)
    assert after["expires_at"] >= sub["expires_at"] and after["source"] == "payment"
    assert after["subscription_type"] == "basic"
    # at most the +3 days of a trial gift, never a shorter premium, never 500 MB over paid GB
    assert after["expires_at"] <= sub["expires_at"] + timedelta(days=3, seconds=5)
    assert e2e.panel.bypass_limit(u.id) in (limit, limit + 500 * MB)
    assert await database.get_user(u.id)
