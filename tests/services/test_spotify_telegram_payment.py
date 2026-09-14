"""
Shop bug fix: Spotify paid by Telegram card (spotify_purchase.cb_pay_card →
send_invoice payload "purchase:<id>", pending purchase_type="spotify",
tariff "spotify_<plan>_<months>") used to fall through
payments_messages.process_successful_payment into the VPN subscription path:
finalize_purchase → grant_access(tariff="spotify_<plan>_<months>",
period_days=months*30), a VPN key + "Подписка оформлена! Basic" screen for the
buyer, and no Spotify success message / no admin fulfilment notice.

Now a Spotify branch mirrors the webhook shop branch
(confirmation.process_confirmed_payment): mark_pending_purchase_paid first
(idempotent: False → no notification), then send_spotify_success, and return
before any VPN logic.

Fixtures `env` / `outbox` come from test_payment_core_t8 (flag OFF / flag ON
with every legacy panel writer failing loudly).
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
from app.handlers.payments import payments_messages as pm
from app.handlers.payments import spotify_purchase
from app.services.payments import service as payment_service
from tests.services import payment_core_harness as h
from tests.services.payment_core_harness import TG, make_pending
from tests.services.test_payment_core_t8 import (  # noqa: F401 — env / outbox are pytest fixtures
    _flag,
    assert_no_legacy_panel_writes,
    env,
    outbox,
)


# ── helpers ─────────────────────────────────────────────────────────────

def spotify_pending(purchase_id="spot-1", tariff="spotify_individual_1", months=1,
                    purchase_type="spotify") -> dict:
    """Same shape as spotify_purchase._create_pending: email in country, password in promo_code."""
    p = make_pending(purchase_id=purchase_id, tariff=tariff, period_days=months * 30,
                     purchase_type=purchase_type, price_rub=500)
    p.update(country="buyer@example.com", promo_code="s3cret-pass")
    return p


def world(request, monkeypatch, mode: str):
    """mode "off": flag OFF (legacy); mode "on": USE_NEW_PROVISIONING on for telegram."""
    if mode == "on":
        w = request.getfixturevalue("outbox")
        _flag(monkeypatch, "on", "telegram")
    else:
        w = request.getfixturevalue("env")
        _flag(monkeypatch, "off")
    return w


def install_spies(w, monkeypatch, *, mark_returns=(True,)):
    spies = SimpleNamespace()
    spies.mark_paid = AsyncMock(side_effect=list(mark_returns))
    monkeypatch.setattr(database, "mark_pending_purchase_paid", spies.mark_paid)
    spies.finalize_purchase = AsyncMock(side_effect=AssertionError("finalize_purchase must not run"))
    monkeypatch.setattr(database, "finalize_purchase", spies.finalize_purchase)
    spies.finalize_sub = AsyncMock(side_effect=AssertionError("VPN finalization must not run"))
    monkeypatch.setattr(payment_service, "finalize_subscription_payment", spies.finalize_sub)
    monkeypatch.setattr(database, "get_user", AsyncMock(return_value={"username": "buyer"}), raising=False)
    return spies


async def pay_by_card(w, monkeypatch, pending: dict):
    """Telegram-native successful_payment (RUB card) with a bot whose sends are recorded."""
    w.pending = pending

    async def get_pending(pid, tg, check_expiry=True):
        return dict(w.pending) if w.pending and w.pending["purchase_id"] == pid else None
    monkeypatch.setattr(database, "get_pending_purchase", get_pending)
    monkeypatch.setattr(pm, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pm, "i18n_get_text", w.recording_get_text)
    monkeypatch.setattr(pm, "clear_promo_session", AsyncMock())
    message = MagicMock()
    message.from_user.id = pending["telegram_id"]
    message.message_id = 1
    message.answer = AsyncMock()
    message.bot.send_message = AsyncMock()
    message.successful_payment = SimpleNamespace(
        currency="RUB", total_amount=pending["price_kopecks"],
        invoice_payload=f"purchase:{pending['purchase_id']}",
        telegram_payment_charge_id="tg-charge-1",
    )
    await pm.process_successful_payment(message, h._fsm(pending["telegram_id"]))
    await w.drain_background()
    return message


def sent_to(message) -> list:
    return [c.args[0] for c in message.bot.send_message.await_args_list]


def assert_no_vpn(w, spies, message):
    spies.finalize_purchase.assert_not_awaited()
    spies.finalize_sub.assert_not_awaited()
    assert w.grant_calls == []
    assert w.jobs.rows == {}
    assert not w.panel.touched
    if hasattr(w, "panel2"):
        assert w.panel2.calls == []
        assert_no_legacy_panel_writes(w)
    assert w.sub is None
    assert "purchase.success_first" not in w.i18n_keys
    message.answer.assert_not_awaited()


# ── Spotify via Telegram card ───────────────────────────────────────────

@pytest.mark.parametrize("mode", ["off", "on"])
async def test_spotify_card_payment_completes_as_spotify_not_vpn(request, monkeypatch, mode):
    w = world(request, monkeypatch, mode)
    spies = install_spies(w, monkeypatch)
    pending = spotify_pending()

    message = await pay_by_card(w, monkeypatch, pending)

    spies.mark_paid.assert_awaited_once_with("spot-1")
    # real send_spotify_success: one message to the buyer, one to the admin
    assert sent_to(message) == [TG, config.ADMIN_TELEGRAM_ID]
    user_text = message.bot.send_message.await_args_list[0].args[1]
    admin_text = message.bot.send_message.await_args_list[1].args[1]
    assert "Spotify Premium" in user_text and "buyer@example.com" in user_text
    assert "ПОКУПКА SPOTIFY PREMIUM" in admin_text and "s3cret-pass" in admin_text
    assert_no_vpn(w, spies, message)


@pytest.mark.parametrize("mode", ["off", "on"])
async def test_spotify_card_payment_calls_send_spotify_success_like_webhook(request, monkeypatch, mode):
    """Same call as confirmation.py's shop branch: (bot, telegram_id, purchase_id, pending)."""
    w = world(request, monkeypatch, mode)
    spies = install_spies(w, monkeypatch)
    send_success = AsyncMock()
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", send_success)
    order = []
    spies.mark_paid.side_effect = lambda pid: order.append("mark") or True
    send_success.side_effect = lambda *a, **k: order.append("send")
    pending = spotify_pending()

    message = await pay_by_card(w, monkeypatch, pending)

    send_success.assert_awaited_once()
    assert send_success.await_args.args == (message.bot, TG, "spot-1", pending)
    assert order == ["mark", "send"]
    assert_no_vpn(w, spies, message)


@pytest.mark.parametrize("mode", ["off", "on"])
async def test_spotify_detected_by_tariff_prefix_like_webhook(request, monkeypatch, mode):
    w = world(request, monkeypatch, mode)
    spies = install_spies(w, monkeypatch)
    send_success = AsyncMock()
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", send_success)
    pending = spotify_pending(tariff="spotify_family_3", months=3, purchase_type="subscription")

    message = await pay_by_card(w, monkeypatch, pending)

    spies.mark_paid.assert_awaited_once_with("spot-1")
    send_success.assert_awaited_once()
    assert_no_vpn(w, spies, message)


@pytest.mark.parametrize("mode", ["off", "on"])
async def test_spotify_duplicate_delivery_notifies_once(request, monkeypatch, mode):
    """Second successful_payment for the same purchase (race past the status='pending'
    lookup): mark_pending_purchase_paid returns False → no second user/admin message."""
    w = world(request, monkeypatch, mode)
    spies = install_spies(w, monkeypatch, mark_returns=(True, False))
    pending = spotify_pending()

    first = await pay_by_card(w, monkeypatch, pending)
    second = await pay_by_card(w, monkeypatch, pending)

    assert spies.mark_paid.await_count == 2
    assert sent_to(first) == [TG, config.ADMIN_TELEGRAM_ID]
    assert sent_to(second) == []
    assert_no_vpn(w, spies, first)
    assert_no_vpn(w, spies, second)


async def test_spotify_mark_paid_db_error_shows_error_and_no_notification(env, monkeypatch):
    w = env
    spies = install_spies(w, monkeypatch)
    spies.mark_paid.side_effect = RuntimeError("db down")
    send_success = AsyncMock()
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", send_success)

    message = await pay_by_card(w, monkeypatch, spotify_pending())

    send_success.assert_not_awaited()
    message.answer.assert_awaited_once()
    assert w.i18n_keys[-1] == "errors.payment_processing"
    spies.finalize_purchase.assert_not_awaited()
    spies.finalize_sub.assert_not_awaited()
    assert w.grant_calls == []


# ── other shop items / VPN purchases unchanged ──────────────────────────

SHOP_TG_CASES = [
    ("telegram_premium", "premium_3m", "app.handlers.payments.telegram_premium", "send_premium_success"),
    ("telegram_stars", "stars_100", "app.handlers.payments.telegram_stars_purchase", "send_stars_success"),
    ("steam", "steam_500", "app.handlers.payments.steam_purchase", "send_steam_success"),
    ("apple_id", "apple_id_usa_25", "app.handlers.callbacks.navigation", "send_apple_id_success"),
]


@pytest.mark.parametrize("mode", ["off", "on"])
@pytest.mark.parametrize("purchase_type,tariff,module_path,sender", SHOP_TG_CASES,
                         ids=[c[0] for c in SHOP_TG_CASES])
async def test_other_shop_items_keep_their_branch(request, monkeypatch, mode, purchase_type, tariff,
                                                  module_path, sender):
    w = world(request, monkeypatch, mode)
    spies = install_spies(w, monkeypatch)
    own_sender = AsyncMock()
    monkeypatch.setattr(importlib.import_module(module_path), sender, own_sender)
    spotify_sender = AsyncMock()
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", spotify_sender)
    pending = make_pending(purchase_id="shop-1", tariff=tariff, period_days=0,
                           purchase_type=purchase_type, price_rub=500)

    message = await pay_by_card(w, monkeypatch, pending)

    own_sender.assert_awaited_once()
    spotify_sender.assert_not_awaited()
    spies.mark_paid.assert_awaited_once_with("shop-1")
    assert_no_vpn(w, spies, message)


@pytest.mark.parametrize("mode", ["off", "on"])
async def test_vpn_purchase_still_goes_through_vpn_path(request, monkeypatch, mode):
    w = world(request, monkeypatch, mode)
    mark_paid = AsyncMock(side_effect=AssertionError("VPN purchase must not use mark_pending_purchase_paid"))
    monkeypatch.setattr(database, "mark_pending_purchase_paid", mark_paid)
    spotify_sender = AsyncMock()
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", spotify_sender)

    message = await pay_by_card(w, monkeypatch, make_pending())

    spotify_sender.assert_not_awaited()
    mark_paid.assert_not_awaited()
    assert len(w.grant_calls) == 1 and w.grant_calls[0]["tariff"] == "basic"
    assert "purchase.success_first" in w.i18n_keys
    assert (len(w.jobs.rows) == 1) is (mode == "on")
