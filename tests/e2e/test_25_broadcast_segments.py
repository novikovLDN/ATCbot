"""Scenario 25 — broadcast segments with a period + the Overview quick action,
through the real dashboard API, the real sender and the bot's callbacks:

* a broadcast to `paid_ended:30d` reaches exactly the users whose paid
  subscription ended in the last 30 days — not the renewed one, not the one
  that ended a year ago, not an ex-trial, not the one who blocked the bot;
* «Предложить продление со скидкой» needs confirm, goes to paid subscriptions
  ending within 7 days without auto-renew, attaches the promo discount; the
  button applies it and a bigger personal discount is kept (the largest wins).
"""
from __future__ import annotations

from datetime import timedelta

import database
from app.services import admin_auth
from tests.e2e.world import new_user, utcnow

ORIGIN = {"Origin": "https://testserver"}
API = "/dashboard/api"
PW = "correct horse battery"


async def _login(e2e):
    assert await admin_auth.set_credentials("boss", PW)
    r = await e2e.http.post(f"{API}/auth/login", json={"username": "boss", "password": PW}, headers=ORIGIN)
    assert r.status_code == 200, r.text


def _naive(dt):
    return dt.replace(tzinfo=None)


async def _sub(e2e, u, *, days: float, source: str = "payment", auto: bool = False, action: str = "purchase"):
    """A subscription row ending `days` from now (negative = ended) + its history."""
    if not await e2e.val("SELECT 1 FROM users WHERE telegram_id=$1", u.id):
        await e2e.register(u)
    end = utcnow() + timedelta(days=days)
    start = end - timedelta(days=30)
    await e2e.pool.execute("DELETE FROM subscriptions WHERE telegram_id=$1", u.id)
    await e2e.pool.execute(
        "INSERT INTO subscriptions (telegram_id, expires_at, status, source, auto_renew) VALUES ($1, $2, $3, $4, $5)",
        u.id, _naive(end), "active" if days > 0 else "expired", source, auto)
    await e2e.pool.execute(
        "INSERT INTO subscription_history (telegram_id, vpn_key, start_date, end_date, action_type) "
        "VALUES ($1, 'k', $2, $3, $4)", u.id, _naive(start), _naive(end), action)
    if source == "payment":
        await e2e.pool.execute(
            "INSERT INTO payments (telegram_id, tariff, amount, status, created_at) "
            "VALUES ($1, 'basic_30', 19900, 'approved', $2)", u.id, _naive(start))


def _got(e2e, users, needle):
    return {u.id for u in users if any(needle in t for t in e2e.tg.texts(u.id))}


async def test_broadcast_to_a_period_segment_reaches_exactly_its_users(e2e):
    await _login(e2e)
    ended10, ended20, renewed, ended_year, ex_trial, blocked = (new_user() for _ in range(6))
    await _sub(e2e, ended10, days=-10)
    await _sub(e2e, ended20, days=-20)
    await _sub(e2e, renewed, days=-10)
    await _sub(e2e, renewed, days=+20, action="renewal")      # renewed → active again
    await _sub(e2e, ended_year, days=-365)
    await _sub(e2e, ex_trial, days=-5, source="trial", action="trial")
    await _sub(e2e, blocked, days=-5)
    await e2e.pool.execute("UPDATE users SET is_reachable = FALSE WHERE telegram_id=$1", blocked.id)
    everyone = (ended10, ended20, renewed, ended_year, ex_trial, blocked)

    r = await e2e.http.get(f"{API}/broadcasts/segments/count", params={"key": "paid_ended:30d"})
    assert r.status_code == 200, r.text
    assert r.json() == {"key": "paid_ended:30d", "count": 2,
                        "label": "Платная истекла, не продлил за последние 30 дней"}
    assert (await e2e.http.get(f"{API}/broadcasts/segments/count", params={"key": "paid_ended:0d"})).status_code == 400
    listed = {s["key"]: s for s in (await e2e.http.get(f"{API}/broadcasts/segments")).json()}
    assert listed["paid_ended"]["parametric"] is True and listed["paid_ended"]["count"] == 2

    bad = await e2e.http.post(f"{API}/broadcasts", headers=ORIGIN, json={
        "title": "bad", "message": "не уйдёт", "segment": "paid_ended:3651d", "buttons": []})
    assert bad.status_code == 400, bad.text

    r = await e2e.http.post(f"{API}/broadcasts", headers=ORIGIN, json={
        "title": "e2e period", "message": "Возвращайтесь — e2e period", "segment": "paid_ended:30d", "buttons": []})
    assert r.status_code == 200, r.text
    assert r.json()["audience"] == 2
    await e2e.settle()
    assert _got(e2e, everyone, "e2e period") == {ended10.id, ended20.id}
    assert _got(e2e, everyone, "не уйдёт") == set()

    bid = r.json()["broadcast_id"]
    assert await e2e.val("SELECT segment FROM broadcasts WHERE id=$1", bid) == "paid_ended:30d"
    recent = {b["id"]: b for b in (await e2e.http.get(f"{API}/broadcasts/recent")).json()}
    assert recent[bid]["segment_label"] == "Платная истекла, не продлил за последние 30 дней"

    # the whole history, «за всё время», takes the year-old one too
    r = await e2e.http.get(f"{API}/broadcasts/segments/count", params={"key": "paid_ended:any"})
    assert r.json()["count"] == 3 and r.json()["label"].endswith("за всё время")


async def test_renewal_offer_quick_action_end_to_end(e2e):
    await _login(e2e)
    manual, has_bigger, auto, later, gift = (new_user() for _ in range(5))
    await _sub(e2e, manual, days=3)
    await _sub(e2e, has_bigger, days=5)
    await _sub(e2e, auto, days=4, auto=True)
    await _sub(e2e, later, days=20)
    await _sub(e2e, gift, days=2, source="gift", action="gift")
    await database.create_user_discount(has_bigger.id, 20, utcnow() + timedelta(days=2), 0)
    everyone = (manual, has_bigger, auto, later, gift)

    info = (await e2e.http.get(f"{API}/broadcasts/renewal-offer")).json()
    assert info["audience"] == {"all": 3, "manual": 2}
    assert [t["title"] for t in info["templates"]] == ["Продлите заранее", "Выгоднее на длинный срок",
                                                        "Не потеряйте доступ"]
    body = {"message": info["templates"][0]["text"], "discount_percent": 15, "discount_hours": 72,
            "exclude_auto_renew": True}

    r = await e2e.http.post(f"{API}/broadcasts/renewal-offer", headers=ORIGIN, json=body)
    assert r.status_code == 400 and "confirm_required" in r.text
    assert await e2e.val("SELECT count(*) FROM broadcasts") == 0

    r = await e2e.http.post(f"{API}/broadcasts/renewal-offer", headers=ORIGIN, json={**body, "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["audience"] == 2
    await e2e.settle()
    assert _got(e2e, everyone, "Скидка действует 72 ч.") == {manual.id, has_bigger.id}
    assert not any("{discount}" in t or "{hours}" in t for u in everyone for t in e2e.tg.texts(u.id))

    bid = r.json()["broadcast_id"]
    row = await e2e.row("SELECT segment, buttons, tag FROM broadcasts WHERE id=$1", bid)
    assert row["segment"] == "paid_expiring_manual:7d" and row["tag"] == "продление"
    disc = await database.get_broadcast_discount(bid)
    assert (disc["discount_percent"], disc["discount_hours"]) == (15, 72)

    async def tap_offer(u):
        mid = await e2e.val("SELECT message_id FROM broadcast_log WHERE broadcast_id=$1 AND telegram_id=$2",
                            bid, u.id)
        await e2e.tap(u, f"broadcast_promo_buy:{bid}", message_id=mid)

    # the button applies the offer…
    await tap_offer(manual)
    assert (await database.get_user_discount(manual.id))["discount_percent"] == 15
    from database.subscriptions import calculate_final_price
    assert (await calculate_final_price(manual.id, "basic", 30))["discount_percent"] == 15
    # …and never lowers a bigger one: the single largest discount wins
    await tap_offer(has_bigger)
    assert (await database.get_user_discount(has_bigger.id))["discount_percent"] == 20
    assert (await calculate_final_price(has_bigger.id, "basic", 30))["discount_percent"] == 20

    # without the toggle the auto-renew one is included
    r = await e2e.http.post(f"{API}/broadcasts/renewal-offer", headers=ORIGIN,
                            json={**body, "exclude_auto_renew": False, "confirm": True,
                                  "message": "Продлите со скидкой {discount}% — e2e all"})
    assert r.status_code == 200 and r.json()["audience"] == 3
    await e2e.settle()
    assert _got(e2e, everyone, "e2e all") == {manual.id, has_bigger.id, auto.id}
