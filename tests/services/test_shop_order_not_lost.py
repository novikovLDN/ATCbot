"""
Owner decision «Заказ не теряется»: a paid shop order must never silently
disappear. When the admin order message (or the whole send_*_success) fails,
the admin gets a separate FORCED alert + a payment_errors row. The Spotify
password (pending.promo_code) and email (pending.country) never appear in
alerts, payment_errors or logs.

Shop texts, prices, delivery promises, admin buttons and manual fulfilment are
unchanged — only failure paths and one info line (purchase_id + provider) in
the admin order message.
"""
from __future__ import annotations

import importlib
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
from app.handlers.payments import spotify_purchase
from app.services import admin_alerts
from tests.services import payment_core_harness as h
from tests.services.payment_core_harness import TG, make_pending
from tests.services.test_payment_core_t8 import env  # noqa: F401 — pytest fixture
from tests.services.test_spotify_telegram_payment import (
    install_spies,
    pay_by_card,
    sent_to,
    spotify_pending,
)

EMAIL = "buyer@example.com"
PASSWORD = "s3cret-pass"
NOTICE = "Заказ оплачен, но уведомление админу не доставлено — выполните вручную"


# ── helpers ─────────────────────────────────────────────────────────────

@pytest.fixture
def sinks(monkeypatch, caplog):
    """Forced-alert spy + payment_errors spy + username lookup."""
    caplog.set_level(logging.DEBUG)
    s = MagicMock()
    s.alert = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", s.alert)
    s.payment_errors = []

    async def log_payment_error(**kw):
        s.payment_errors.append(kw)
        return 1
    monkeypatch.setattr(database, "log_payment_error", log_payment_error, raising=False)
    monkeypatch.setattr(database, "get_user", AsyncMock(return_value={"username": "buyer"}), raising=False)
    return s


def alert_texts(s) -> list[str]:
    return [c.args[2] for c in s.alert.await_args_list]


def assert_forced_payment_alert(s):
    assert s.alert.await_count == 1
    call = s.alert.await_args
    assert call.args[1] == "payment"
    assert call.kwargs.get("force") is True


def assert_no_secrets(s, caplog):
    blobs = alert_texts(s) + [repr(row) for row in s.payment_errors]
    blobs += [r.getMessage() for r in caplog.records]
    blobs += [str(r.exc_info[1]) for r in caplog.records if r.exc_info]
    for blob in blobs:
        assert PASSWORD not in blob, blob
        assert EMAIL not in blob, blob


def spotify_purchase_row(**over) -> dict:
    p = make_pending(purchase_id="spot-1", tariff="spotify_individual_3", period_days=90,
                     purchase_type="spotify", price_rub=1500)
    p.update(country=EMAIL, promo_code=PASSWORD)
    p.update(over)
    return p


def bot_where_admin_send_fails(exc: Exception | None = None):
    bot = MagicMock()

    async def send_message(chat_id, text, **kw):
        if chat_id == config.ADMIN_TELEGRAM_ID:
            # Telegram errors may echo the request — make sure we scrub it.
            raise exc or RuntimeError(f"Bad Request: can't parse entities in {text!r}")
        return MagicMock()
    bot.send_message = AsyncMock(side_effect=send_message)
    return bot


# ── 1. send_spotify_success: admin order message fails ──────────────────

async def test_admin_send_fails_forced_alert_and_payment_error_without_secrets(sinks, caplog):
    bot = bot_where_admin_send_fails()

    await spotify_purchase.send_spotify_success(
        bot, TG, "spot-1", spotify_purchase_row(), provider="wata",
    )

    assert_forced_payment_alert(sinks)
    text = alert_texts(sinks)[0]
    assert NOTICE in text
    assert "spot-1" in text and str(TG) in text and "@buyer" in text
    assert "wata" in text and "1500" in text
    assert "Spotify" in text and "3 мес" in text
    assert len(sinks.payment_errors) == 1
    row = sinks.payment_errors[0]
    assert row["stage"] == "shop_admin_notify_failed"
    assert row["purchase_id"] == "spot-1"
    assert row["telegram_id"] == TG
    assert row["payment_provider"] == "wata"
    assert_no_secrets(sinks, caplog)


async def test_admin_send_fails_and_alert_fails_logs_critical_no_exception(sinks, caplog):
    bot = bot_where_admin_send_fails()
    sinks.alert.return_value = False

    await spotify_purchase.send_spotify_success(
        bot, TG, "spot-1", spotify_purchase_row(), provider="platega",
    )

    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert crit and "spot-1" in crit[0].getMessage()
    assert_no_secrets(sinks, caplog)


async def test_alert_raising_is_contained_and_logged_critical(sinks, caplog, monkeypatch):
    bot = bot_where_admin_send_fails()
    sinks.alert.side_effect = RuntimeError("telegram down")

    async def broken_log(**kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(database, "log_payment_error", broken_log, raising=False)

    await spotify_purchase.send_spotify_success(
        bot, TG, "spot-1", spotify_purchase_row(), provider="platega",
    )

    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    assert_no_secrets(sinks, caplog)


async def test_admin_message_contains_purchase_id_and_provider(sinks):
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await spotify_purchase.send_spotify_success(
        bot, TG, "spot-1", spotify_purchase_row(), provider="cryptobot",
    )

    admin_calls = [c for c in bot.send_message.await_args_list if c.args[0] == config.ADMIN_TELEGRAM_ID]
    assert len(admin_calls) == 1
    admin_text = admin_calls[0].args[1]
    assert "spot-1" in admin_text and "cryptobot" in admin_text
    # unchanged content: header, credentials for manual fulfilment, button
    assert "ПОКУПКА SPOTIFY PREMIUM" in admin_text and PASSWORD in admin_text
    assert admin_calls[0].kwargs["reply_markup"] is not None
    sinks.alert.assert_not_awaited()
    assert sinks.payment_errors == []


async def test_provider_kwarg_is_optional(sinks):
    """Backward compatible: old 4-arg call still works (provider shown as «—»)."""
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await spotify_purchase.send_spotify_success(bot, TG, "spot-1", spotify_purchase_row())

    admin_text = bot.send_message.await_args_list[-1].args[1]
    assert "spot-1" in admin_text
    sinks.alert.assert_not_awaited()


async def test_user_text_unchanged_on_admin_failure(sinks):
    bot = bot_where_admin_send_fails()

    await spotify_purchase.send_spotify_success(bot, TG, "spot-1", spotify_purchase_row(), provider="wata")

    user_text = bot.send_message.await_args_list[0].args[1]
    assert bot.send_message.await_args_list[0].args[0] == TG
    assert "spot-1" not in user_text and "wata" not in user_text


# ── 2. confirmation.py shop branch: send_*_success raises ───────────────

CONF_CASES = [
    ("spotify", "spotify_individual_3", "app.handlers.payments.spotify_purchase", "send_spotify_success"),
    ("steam", "steam_500", "app.handlers.payments.steam_purchase", "send_steam_success"),
    ("telegram_premium", "premium_3m", "app.handlers.payments.telegram_premium", "send_premium_success"),
    ("telegram_stars", "stars_100", "app.handlers.payments.telegram_stars_purchase", "send_stars_success"),
    ("apple_id", "apple_id_usa_25", "app.handlers.callbacks.navigation", "send_apple_id_success"),
]


@pytest.mark.parametrize("purchase_type,tariff,module_path,sender", CONF_CASES,
                         ids=[c[0] for c in CONF_CASES])
async def test_confirmation_shop_notification_exception_sends_forced_alert(
        monkeypatch, sinks, caplog, purchase_type, tariff, module_path, sender):
    w = h.install(monkeypatch)
    boom = AsyncMock(side_effect=RuntimeError(f"boom {PASSWORD} {EMAIL}"))
    monkeypatch.setattr(importlib.import_module(module_path), sender, boom)
    mark_paid = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "mark_pending_purchase_paid", mark_paid)
    finalize = AsyncMock()
    monkeypatch.setattr(database, "finalize_purchase", finalize)
    pending = make_pending(purchase_id="shop-9", tariff=tariff, period_days=0,
                           purchase_type=purchase_type, price_rub=700)
    pending.update(country=EMAIL, promo_code=PASSWORD)

    out = await h.run_webhook(w, monkeypatch, pending, provider="wata")

    assert out["result"]["status"] == "ok"  # shop behaviour unchanged
    mark_paid.assert_awaited_once_with("shop-9")
    finalize.assert_not_awaited()
    assert_forced_payment_alert(sinks)
    text = alert_texts(sinks)[0]
    assert NOTICE in text and "shop-9" in text and str(TG) in text and "wata" in text
    assert len(sinks.payment_errors) == 1
    assert sinks.payment_errors[0]["stage"] == "shop_admin_notify_failed"
    assert sinks.payment_errors[0]["purchase_id"] == "shop-9"
    assert_no_secrets(sinks, caplog)


async def test_confirmation_passes_provider_to_spotify(monkeypatch, sinks):
    w = h.install(monkeypatch)
    send_success = AsyncMock()
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", send_success)
    monkeypatch.setattr(database, "mark_pending_purchase_paid", AsyncMock(return_value=True))
    pending = make_pending(purchase_id="shop-3", tariff="spotify_family_1", period_days=0,
                           purchase_type="spotify", price_rub=500)

    out = await h.run_webhook(w, monkeypatch, pending, provider="platega")

    assert send_success.await_args.args == (out["bot"], TG, "shop-3", pending)
    assert send_success.await_args.kwargs == {"provider": "platega"}
    sinks.alert.assert_not_awaited()


async def test_confirmation_shop_success_sends_no_alert(monkeypatch, sinks):
    w = h.install(monkeypatch)
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", AsyncMock())
    monkeypatch.setattr(database, "mark_pending_purchase_paid", AsyncMock(return_value=True))
    pending = make_pending(purchase_id="shop-4", tariff="spotify_duo_1", period_days=0,
                           purchase_type="spotify", price_rub=500)

    await h.run_webhook(w, monkeypatch, pending)

    sinks.alert.assert_not_awaited()
    assert sinks.payment_errors == []


# ── 3. Telegram card (payments_messages) Spotify edge cases ─────────────

async def test_card_mark_paid_db_error_alerts_admin(env, monkeypatch, sinks, caplog):  # noqa: F811
    w = env
    spies = install_spies(w, monkeypatch)
    spies.mark_paid.side_effect = RuntimeError("db down")
    send_success = AsyncMock()
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", send_success)
    pending = spotify_pending()

    message = await pay_by_card(w, monkeypatch, pending)

    send_success.assert_not_awaited()
    message.answer.assert_awaited_once()
    assert w.i18n_keys[-1] == "errors.payment_processing"  # user text unchanged
    assert_forced_payment_alert(sinks)
    text = alert_texts(sinks)[0]
    assert NOTICE in text and "spot-1" in text and "telegram_card" in text and str(TG) in text
    assert len(sinks.payment_errors) == 1
    assert sinks.payment_errors[0]["stage"] == "shop_mark_paid_failed"
    assert sinks.payment_errors[0]["payment_provider"] == "telegram_card"
    assert_no_secrets(sinks, caplog)


async def test_card_send_success_exception_alerts_admin(env, monkeypatch, sinks, caplog):  # noqa: F811
    w = env
    install_spies(w, monkeypatch)
    monkeypatch.setattr(spotify_purchase, "send_spotify_success",
                        AsyncMock(side_effect=RuntimeError(f"x {PASSWORD}")))

    await pay_by_card(w, monkeypatch, spotify_pending())

    assert_forced_payment_alert(sinks)
    assert sinks.payment_errors[0]["stage"] == "shop_admin_notify_failed"
    assert_no_secrets(sinks, caplog)


async def test_card_passes_telegram_card_provider(env, monkeypatch, sinks):  # noqa: F811
    w = env
    install_spies(w, monkeypatch)
    send_success = AsyncMock()
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", send_success)
    pending = spotify_pending()

    message = await pay_by_card(w, monkeypatch, pending)

    assert send_success.await_args.args == (message.bot, TG, "spot-1", pending)
    assert send_success.await_args.kwargs == {"provider": "telegram_card"}


async def test_card_admin_message_shows_purchase_and_provider(env, monkeypatch, sinks):  # noqa: F811
    w = env
    install_spies(w, monkeypatch)

    message = await pay_by_card(w, monkeypatch, spotify_pending())

    assert sent_to(message) == [TG, config.ADMIN_TELEGRAM_ID]
    admin_text = message.bot.send_message.await_args_list[1].args[1]
    assert "spot-1" in admin_text and "telegram_card" in admin_text


def _expired_lookup(w, monkeypatch, row):
    """Row is 'expired' (user opened another payment method → create_pending_purchase
    expired it): the status='pending' lookup misses, the by-id lookup finds it."""
    async def by_id(pid, check_expiry=False):
        return dict(row) if row and row["purchase_id"] == pid else None
    monkeypatch.setattr(database, "get_pending_purchase_by_id", by_id)


async def test_card_expired_spotify_row_alerts_admin_behaviour_unchanged(env, monkeypatch, sinks, caplog):  # noqa: F811
    """Choice: alert-only. Pre-checkout already rejects non-pending rows; the gap is
    a row expired between pre-checkout and successful_payment. Accepting it would
    need service.verify_payment_payload changes — so the buyer still gets the
    same error, and the admin gets a forced alert with the order to fulfil/refund."""
    w = env
    spies = install_spies(w, monkeypatch)
    expired = dict(spotify_pending(), status="expired")
    _expired_lookup(w, monkeypatch, expired)
    send_success = AsyncMock()
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", send_success)

    # the status='pending' lookup misses the expired row
    async def get_pending(pid, tg, check_expiry=True):
        return None
    message = await _pay_with_lookup(w, monkeypatch, expired, get_pending)

    spies.mark_paid.assert_not_awaited()
    send_success.assert_not_awaited()
    spies.finalize_purchase.assert_not_awaited()
    spies.finalize_sub.assert_not_awaited()
    assert w.grant_calls == []
    message.answer.assert_awaited_once()
    assert w.i18n_keys[-1] == "errors.payment_processing"  # buyer text unchanged
    assert_forced_payment_alert(sinks)
    text = alert_texts(sinks)[0]
    assert NOTICE in text and "spot-1" in text and "expired" in text and "telegram_card" in text
    assert "5.0" in text or "500" in text
    assert sinks.payment_errors[0]["stage"] == "shop_order_not_pending"
    assert_no_secrets(sinks, caplog)


async def test_card_expired_row_of_other_user_no_shop_alert(env, monkeypatch, sinks):  # noqa: F811
    """Another user's row: no shop alert (no order to fulfil), but the buyer was
    charged and nothing was granted -> exactly one generic money-taken alert."""
    w = env
    spies = install_spies(w, monkeypatch)
    expired = dict(spotify_pending(), status="expired", telegram_id=TG + 1)
    _expired_lookup(w, monkeypatch, expired)
    send_success = AsyncMock()
    monkeypatch.setattr(spotify_purchase, "send_spotify_success", send_success)

    async def get_pending(pid, tg, check_expiry=True):
        return None
    message = await _pay_with_lookup(w, monkeypatch, dict(expired, telegram_id=TG), get_pending)

    send_success.assert_not_awaited()
    spies.mark_paid.assert_not_awaited()
    assert w.i18n_keys[-1] == "errors.payment_processing"
    message.answer.assert_awaited_once()
    assert sinks.alert.await_count == 1
    assert all(e["stage"] != "shop_order_not_pending" for e in sinks.payment_errors)


async def test_card_expired_vpn_row_unchanged_no_shop_alert(env, monkeypatch, sinks):  # noqa: F811
    """Only shop rows get the shop alert; a VPN row gets exactly one generic
    money-taken alert (docs/audit/03_payment_matrix.md, alert coverage)."""
    w = env
    spies = install_spies(w, monkeypatch)
    expired = dict(make_pending(purchase_id="vpn-1"), status="expired")
    _expired_lookup(w, monkeypatch, expired)

    async def get_pending(pid, tg, check_expiry=True):
        return None
    message = await _pay_with_lookup(w, monkeypatch, expired, get_pending)

    spies.mark_paid.assert_not_awaited()
    spies.finalize_sub.assert_not_awaited()
    assert w.grant_calls == []
    assert w.i18n_keys[-1] == "errors.payment_processing"
    message.answer.assert_awaited_once()
    assert sinks.alert.await_count == 1
    assert all(e["stage"] != "shop_order_not_pending" for e in sinks.payment_errors)


async def _pay_with_lookup(w, monkeypatch, pending, get_pending):
    """pay_by_card, but with a custom status='pending' lookup (installed after
    pay_by_card's own, which it would otherwise overwrite)."""
    real_setattr = monkeypatch.setattr

    def setattr_keep_lookup(target, name, value, *a, **k):
        if target is database and name == "get_pending_purchase":
            value = get_pending
        return real_setattr(target, name, value, *a, **k)
    monkeypatch.setattr = setattr_keep_lookup
    try:
        return await pay_by_card(w, monkeypatch, pending)
    finally:
        monkeypatch.setattr = real_setattr
