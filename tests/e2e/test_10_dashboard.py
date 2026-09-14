"""Scenario 10 — dashboard API with a real session: password login (cookie),
login rate limit / lockout, and money metrics consistent with purchases made
through the real payment paths (docs/dashboard/metrics.md: revenue is counted
when money enters — a top-up counts, spending the balance does not; shop,
proxy and game are separate lines).
"""
import database
from app.services import admin_auth
from tests.e2e import flows
from tests.e2e.world import new_user
from tests.fakes import providers_http as prov

ORIGIN = {"Origin": "https://testserver"}
API = "/dashboard/api"


async def _login(e2e, password="correct horse battery"):
    return await e2e.http.post(f"{API}/auth/login", json={"username": "boss", "password": password},
                               headers=ORIGIN)


async def test_login_sets_session_and_unauthenticated_is_401(e2e):
    assert await admin_auth.set_credentials("boss", "correct horse battery")
    assert (await e2e.http.get(f"{API}/metrics/money")).status_code == 401
    r = await _login(e2e)
    assert r.status_code == 200, r.text
    assert admin_auth.COOKIE_NAME in e2e.http.cookies
    me = await e2e.http.get(f"{API}/auth/me")
    assert me.status_code == 200 and me.json()["telegram_id"] == int(__import__("config").ADMIN_TELEGRAM_ID)


async def test_login_rate_limit_locks_after_repeated_failures(e2e):
    assert await admin_auth.set_credentials("boss", "correct horse battery")
    codes = [(await _login(e2e, "wrong")).status_code for _ in range(5)]
    assert codes == [401] * 5, codes
    locked = await _login(e2e)                      # even the right password while locked
    assert locked.status_code == 429, locked.text
    assert admin_auth.COOKIE_NAME not in e2e.http.cookies


async def test_money_metrics_match_the_seeded_purchases(e2e):
    assert await admin_auth.set_credentials("boss", "correct horse battery")
    u = new_user()
    await e2e.register(u)
    # 1) subscription via Platega: 199 ₽ of VPN revenue
    p = await e2e.create_purchase(u, "basic", 30, provider="platega")
    assert (await flows.pay(e2e, u, p, "sbp")).body["status"] == "ok"
    # 2) top-up 250 ₽ via Platega: revenue at top-up
    await e2e.tap(u, "topup_balance")
    await e2e.tap(u, "topup_amount:250")
    await e2e.tap(u, "topup_sbp:250")
    top = await flows.latest_pending(e2e, u.id)
    assert (await e2e.webhook(prov.platega_webhook(top["purchase_id"], 250.0))).body["status"] == "ok"
    # 3) GB pack 15 GB via Platega: VPN revenue (traffic)
    pack = await e2e.create_purchase(u, "traffic_15gb", 0, provider="platega", purchase_type="traffic_pack",
                                     price_rub=89)
    assert (await e2e.webhook(prov.platega_webhook(pack["purchase_id"], 89.0))).body["status"] == "ok"
    # 4) Plus from the balance... not enough (51 ₽ left after nothing) → buy basic 199 from 250
    await e2e.pool.execute("UPDATE payments SET created_at = created_at - interval '5 minutes' WHERE telegram_id=$1", u.id)
    await flows.buy(e2e, u, "basic", 30, "balance")
    assert await e2e.balance(u.id) == 51.0
    # 5) shop: Apple ID card 500 ₽ via Platega — a separate line
    shop_pid = await database.create_pending_purchase(
        telegram_id=u.id, tariff="apple_id_usa_500", period_days=0, price_kopecks=50_000, purchase_type="apple_id")
    await database.update_pending_purchase_invoice_id(shop_pid, "inv-shop", provider="platega")
    assert (await e2e.webhook(prov.platega_webhook(shop_pid, 500.0))).body["status"] == "ok"

    assert (await _login(e2e)).status_code == 200
    r = await e2e.http.get(f"{API}/metrics/money", params={"days": 30})
    assert r.status_code == 200, r.text
    cur = r.json()["current"]
    assert cur["vpn_kopecks"] == 19_900 + 25_000 + 8_900, cur        # balance spend NOT added
    assert cur["net_kopecks"] == cur["vpn_kopecks"]
    assert cur["shop_kopecks"] == 50_000
    assert cur["proxy_kopecks"] == 0 and cur["game_kopecks"] == 0
    assert cur["gross_kopecks"] == 19_900 + 25_000 + 8_900 + 50_000
    spend = r.json()["balance_spend"]
    assert 19_900 in (spend.get("kopecks"), spend.get("total_kopecks"), spend.get("spend_kopecks")), spend
