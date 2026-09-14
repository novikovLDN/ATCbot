"""Scenario 9 — the admin in the bot (docs/audit/SCOPE.md, 2026-09-14): /admin
shows ONLY the dashboard link, «Написать пользователю» and «Сбросить пароль»;
admin → user chat works; a non-admin gets nothing; the 🔒 shop order-delivery
buttons on admin alerts still work (shop code exercised, not modified).
"""
from tests.e2e import flows
from tests.e2e.world import ADMIN, new_user
from tests.fakes import providers_http as prov
from tests.fakes.telegram import TgUser

ADMIN_USER = TgUser(id=ADMIN, first_name="Admin", username="boss")


async def _admin(e2e):
    await e2e.register(ADMIN_USER)


async def test_admin_menu_has_exactly_three_entries(e2e):
    await _admin(e2e)
    mark = e2e.tg.mark()
    await e2e.send(ADMIN_USER, "/admin")
    screens = e2e.tg.since(mark, ADMIN)
    assert screens, "/admin answered nothing"
    buttons = screens[-1].buttons()
    datas = [t for _, t in buttons]
    assert "admin:chat" in datas and "admin:reset_password" in datas
    urls = [t for t in datas if str(t).startswith("http")]
    assert len(urls) == 1 and "/dashboard" in urls[0], buttons
    assert len(buttons) == 3, buttons


async def test_non_admin_admin_command_is_ignored(e2e):
    u = new_user()
    await e2e.register(u)
    mark = e2e.tg.mark()
    await e2e.send(u, "/admin")
    assert not any("admin:" in d for s in e2e.tg.since(mark, u.id) for d in s.callback_data())


async def test_admin_writes_to_a_user(e2e):
    await _admin(e2e)
    u = new_user()
    await e2e.register(u)
    await e2e.tap(ADMIN_USER, "admin:chat")
    await e2e.send(ADMIN_USER, str(u.id))
    mark = e2e.tg.mark()
    await e2e.send(ADMIN_USER, "Здравствуйте! Проверка связи")
    assert any("Проверка связи" in t for t in e2e.user_texts(u.id, mark)), e2e.user_texts(u.id, mark)


async def test_reset_password_flow_asks_confirmation_and_clears_credentials(e2e):
    from app.services import admin_auth
    await _admin(e2e)
    assert await admin_auth.set_credentials("boss", "correct horse battery")
    await e2e.tap(ADMIN_USER, "admin:reset_password")
    assert e2e.tg.with_button(ADMIN, "admin:reset_password_confirm")
    assert await admin_auth.credentials_exist(), "reset before confirmation"
    await e2e.tap(ADMIN_USER, "admin:reset_password_confirm")
    assert not await admin_auth.credentials_exist()


async def test_apple_id_order_delivery_buttons(e2e):
    """🔒 shop: paid Apple ID order → admin alert with «send key» → admin sends the
    key → confirms → the buyer receives it."""
    await _admin(e2e)
    buyer = new_user()
    await e2e.register(buyer)
    pid = await __import__("database").create_pending_purchase(
        telegram_id=buyer.id, tariff="apple_id_usa_500", period_days=0, price_kopecks=50_000,
        purchase_type="apple_id")
    await __import__("database").update_pending_purchase_invoice_id(pid, "inv-apple", provider="platega")
    mark = e2e.tg.mark()
    res = await e2e.webhook(prov.platega_webhook(pid, 500.0))
    assert (res.status, res.body["status"]) == (200, "ok"), res
    alert = e2e.tg.with_button(ADMIN, "admin:chat")
    assert alert is not None and e2e.user_texts(buyer.id, mark)
    send_key = [d for d in alert.callback_data() if d.startswith("apple_send_key:")]
    assert send_key, alert.buttons()

    await e2e.tap(ADMIN_USER, send_key[0])
    await e2e.send(ADMIN_USER, "AAAA-BBBB-CCCC-DDDD")
    confirm = e2e.tg.with_button(ADMIN, f"apple_key_confirm:{buyer.id}")
    assert confirm is not None, "no confirm button after the key was typed"
    mark2 = e2e.tg.mark()
    await e2e.tap(ADMIN_USER, f"apple_key_confirm:{buyer.id}")
    assert any("AAAA-BBBB-CCCC-DDDD" in t for t in e2e.user_texts(buyer.id, mark2)), e2e.user_texts(buyer.id, mark2)
    # a shop order grants no VPN access
    assert await e2e.sub(buyer.id) is None and e2e.panel.users == {}
    assert flows is not None


async def test_spotify_order_delivery_button(e2e):
    """🔒 shop: paid Spotify order → admin alert with «✅ Выполнено» → the buyer
    is told the order is done; only the admin may press it."""
    import database
    await _admin(e2e)
    buyer = new_user()
    await e2e.register(buyer)
    pid = await database.create_pending_purchase(
        telegram_id=buyer.id, tariff="spotify_duo_1", period_days=30, price_kopecks=29_900,
        purchase_type="spotify")
    await database.update_pending_purchase_invoice_id(pid, "inv-spotify", provider="platega")
    mark = e2e.tg.mark()
    res = await e2e.webhook(prov.platega_webhook(pid, 299.0))
    assert (res.status, res.body["status"]) == (200, "ok"), res
    assert e2e.user_texts(buyer.id, mark), "buyer not told about the paid order"
    alert = e2e.tg.with_button(ADMIN, f"spotify_done:{buyer.id}")
    assert alert is not None and "admin:chat" in alert.callback_data(), e2e.admin_texts(mark)

    # a non-admin pressing the button does nothing
    stranger = new_user()
    await e2e.register(stranger)
    m1 = e2e.tg.mark()
    await e2e.tap(stranger, f"spotify_done:{buyer.id}")
    assert e2e.user_texts(buyer.id, m1) == []

    m2 = e2e.tg.mark()
    await e2e.tap(ADMIN_USER, f"spotify_done:{buyer.id}")
    assert e2e.user_texts(buyer.id, m2), "buyer not notified when the admin marked the order done"
    assert await e2e.sub(buyer.id) is None and e2e.panel.users == {}
