"""
T0 — characterization of every payment entry point (docs/audit/02_payment_core_plan.md).

Two kinds of tests:

* MUST-KEEP — plain tests pinning behaviour the payment-core rewrite has to
  preserve (shop branch, i18n keys, payments.tariff format, referral / promo
  calls, pending activation, GB amounts that are already right).
* KNOWN BUGS — ``xfail(strict=True, raises=AssertionError)`` tests asserting the
  owner's TARGET rules (SCOPE.md, plan §G0). The measured CURRENT value is in
  each docstring. When a fix lands the test XPASSes, strict mode fails it, and
  the fixer removes the marker. ``raises=AssertionError`` keeps harness breakage
  from hiding behind the xfail.

Owner rules: basic/plus +10 GB once; combo_basic/combo_plus = combo GB only
(30d → 75 GB); a renewal adds again; traffic pack +N; day grants 0 GB; auto-renewal
bills and provisions the subscription's actual tariff.

Out of scope here (task T6 is changing them): finalize_purchase row locking and
concurrency, webhook HTTP status codes, not_found alerts, provider mismatch,
balance top-up credited amount.

Harness and fakes: tests/services/payment_core_harness.py.
"""
import functools
from unittest.mock import AsyncMock

import pytest

from tests.services import payment_core_harness as h
from tests.services.payment_core_harness import TG, make_pending, text_prefix

payment_core_bug = functools.partial(pytest.mark.xfail, strict=True, raises=AssertionError)


@pytest.fixture
def w(monkeypatch):
    return h.install(monkeypatch)


def _sent_text(mock) -> str:
    """First positional text of the last call to a send/answer mock."""
    args = mock.call_args.args
    return args[1] if len(args) > 1 and isinstance(args[1], str) else args[0]


# ═══════════════════════════════════════════════════════════════════════
# MUST-KEEP: shop branch of process_confirmed_payment (SCOPE.md: do not touch)
# ═══════════════════════════════════════════════════════════════════════

SHOP_CASES = [
    ("telegram_stars", "stars_100", "app.handlers.payments.telegram_stars_purchase", "send_stars_success"),
    ("telegram_premium", "premium_3m", "app.handlers.payments.telegram_premium", "send_premium_success"),
    ("steam", "steam_500", "app.handlers.payments.steam_purchase", "send_steam_success"),
    ("spotify", "spotify_1m", "app.handlers.payments.spotify_purchase", "send_spotify_success"),
    ("apple_id", "apple_id_usa_25", "app.handlers.callbacks.navigation", "send_apple_id_success"),
]


@pytest.mark.parametrize("purchase_type,tariff,module_path,sender", SHOP_CASES,
                         ids=[c[0] for c in SHOP_CASES])
async def test_shop_purchase_marks_paid_and_notifies_once_without_provisioning(
        w, monkeypatch, purchase_type, tariff, module_path, sender):
    import importlib
    import database

    # Arrange
    send_success = AsyncMock()
    monkeypatch.setattr(importlib.import_module(module_path), sender, send_success)
    mark_paid = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "mark_pending_purchase_paid", mark_paid)
    finalize = AsyncMock()
    monkeypatch.setattr(database, "finalize_purchase", finalize)
    pending = make_pending(purchase_id="shop-1", tariff=tariff, period_days=0,
                           purchase_type=purchase_type, price_rub=500)

    # Act
    out = await h.run_webhook(w, monkeypatch, pending)

    # Assert
    assert out["result"]["status"] == "ok"
    mark_paid.assert_awaited_once_with("shop-1")
    send_success.assert_awaited_once()
    if purchase_type == "apple_id":
        assert send_success.await_args.args == (out["bot"], TG, "usa", 25, 500.0)
    else:
        assert send_success.await_args.args == (out["bot"], TG, "shop-1", pending)
    finalize.assert_not_awaited()
    assert w.grant_calls == []
    assert not w.panel.touched


async def test_shop_purchase_already_marked_paid_sends_no_second_notification(w, monkeypatch):
    import database
    from app.handlers.payments import telegram_premium

    send_success = AsyncMock()
    monkeypatch.setattr(telegram_premium, "send_premium_success", send_success)
    monkeypatch.setattr(database, "mark_pending_purchase_paid", AsyncMock(return_value=False))
    pending = make_pending(purchase_id="shop-2", tariff="premium_3m", period_days=0,
                           purchase_type="telegram_premium", price_rub=500)

    out = await h.run_webhook(w, monkeypatch, pending)

    assert out["result"]["status"] == "already_processed"
    send_success.assert_not_awaited()
    assert not w.panel.touched


# ═══════════════════════════════════════════════════════════════════════
# MUST-KEEP: external webhook → finalize_purchase (correct today)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tariff,period_days", [("basic", 30), ("plus", 365)])
async def test_webhook_new_subscription_adds_10gb_once(w, monkeypatch, tariff, period_days):
    await h.run_webhook(w, monkeypatch, make_pending(tariff=tariff, period_days=period_days))

    assert w.panel.added_gb(TG) == 10


async def test_webhook_renewal_adds_10gb_again(w, monkeypatch):
    w.seed_active_subscription(bypass_gb=3)

    await h.run_webhook(w, monkeypatch, make_pending())

    assert w.panel.added_gb(TG) == 10
    assert w.panel.bypass_bytes(TG) == 13 * h.GB


async def test_webhook_combo_basic_30d_new_adds_combo_gb_only(w, monkeypatch):
    await h.run_webhook(w, monkeypatch, make_pending(is_combo=True))

    assert w.panel.added_gb(TG) == 75


async def test_webhook_combo_basic_30d_renewal_adds_combo_gb_again(w, monkeypatch):
    w.seed_active_subscription()

    await h.run_webhook(w, monkeypatch, make_pending(is_combo=True))

    assert w.panel.added_gb(TG) == 75


async def test_webhook_traffic_pack_adds_exactly_pack_gb_with_traffic_success_text(w, monkeypatch):
    w.seed_active_subscription()
    premium_before = w.panel.premium_expire[TG]
    pending = make_pending(tariff="traffic_15gb", period_days=0,
                           purchase_type="traffic_pack", price_rub=89)

    out = await h.run_webhook(w, monkeypatch, pending)

    assert w.panel.added_gb(TG) == 15
    assert w.panel.premium_expire[TG] == premium_before  # pack never touches premium
    assert "traffic.purchase_success" in w.i18n_keys
    assert _sent_text(out["bot"].send_message).startswith(text_prefix(w, "traffic.purchase_success"))


async def test_webhook_subscription_success_uses_unified_success_text(w, monkeypatch):
    """08_payments_ux #3: the webhook sends the same success message as
    balance / Telegram (tariff, period, date, GB) — not a bare «processed»."""
    out = await h.run_webhook(w, monkeypatch, make_pending())

    assert "purchase.success_first" in w.i18n_keys
    text = _sent_text(out["bot"].send_message)
    assert text.startswith(text_prefix(w, "purchase.success_first"))
    assert "+10 ГБ" in text and "1 месяц" in text


@pytest.mark.parametrize("tariff,period_days", [("basic", 30), ("plus", 90)])
async def test_finalize_purchase_writes_payments_tariff_as_tariff_underscore_period(
        w, monkeypatch, tariff, period_days):
    await h.run_webhook(w, monkeypatch, make_pending(tariff=tariff, period_days=period_days))

    inserts = w.conn.payment_inserts()
    assert len(inserts) == 1
    assert inserts[0][1] == f"{tariff}_{period_days}"


async def test_finalize_purchase_awards_referral_once_in_the_payment_connection(w, monkeypatch):
    await h.run_webhook(w, monkeypatch, make_pending(purchase_id="pid-ref"))

    w.db.process_referral_reward.assert_awaited_once_with(
        buyer_id=TG, purchase_id="pid-ref", amount_rubles=199.0, conn=w.conn,
    )


async def test_finalize_purchase_consumes_promo_once_in_the_payment_connection(w, monkeypatch):
    await h.run_webhook(w, monkeypatch, make_pending(purchase_id="pid-promo", promo_code="SPRING"))

    w.db.consume_promo.assert_awaited_once_with(w.conn, "SPRING", TG, "pid-promo")


# ═══════════════════════════════════════════════════════════════════════
# MUST-KEEP: balance purchase (payments_callbacks.callback_pay_balance)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tariff,key", [("basic", "purchase.success_first"),
                                        ("plus", "purchase.success_first")])
async def test_balance_new_subscription_uses_welcome_text(w, monkeypatch, tariff, key):
    callback = await h.run_balance_purchase(w, monkeypatch, tariff=tariff)

    assert key in w.i18n_keys
    assert _sent_text(callback.message.answer).startswith(text_prefix(w, key))


async def test_balance_renewal_uses_unified_renewal_text(w, monkeypatch):
    w.seed_active_subscription()

    callback = await h.run_balance_purchase(w, monkeypatch)

    key = "purchase.success_renewal"
    assert key in w.i18n_keys
    assert _sent_text(callback.message.answer).startswith(text_prefix(w, key))


async def test_balance_renewal_basic_adds_10gb_again(w, monkeypatch):
    w.seed_active_subscription()

    await h.run_balance_purchase(w, monkeypatch)

    assert w.panel.added_gb(TG) == 10


@payment_core_bug(reason="payment-core: balance pending_activation branch is unreachable (T7/T10)")
async def test_balance_pending_activation_uses_pending_activation_text(w, monkeypatch):
    """Current: grant_access returns action='pending_activation' without vpn_key, so
    finalize_balance_purchase raises 'vpn_key is missing' (admin.py:2808), the tx rolls
    back and the user sees errors.payment_processing. The handler branch that sends
    payment.pending_activation (payments_callbacks.py:645) is never reached. Target
    (plan §A, T7): deferred new issuance lands there and shows payment.pending_activation.
    """
    import config
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", False)
    monkeypatch.setattr(config, "VPN_ENABLED", False)

    callback = await h.run_balance_purchase(w, monkeypatch)

    assert "payment.pending_activation" in w.i18n_keys
    assert _sent_text(callback.message.answer).startswith(
        text_prefix(w, "payment.pending_activation"))
    assert not w.panel.touched


async def test_balance_purchase_writes_payments_tariff_as_tariff_underscore_period(w, monkeypatch):
    await h.run_balance_purchase(w, monkeypatch, tariff="plus", period_days=90)

    inserts = w.conn.payment_inserts()
    assert len(inserts) == 1
    assert inserts[0][1] == "plus_90"


async def test_balance_purchase_awards_referral_once_in_the_payment_connection(w, monkeypatch):
    await h.run_balance_purchase(w, monkeypatch)

    w.db.process_referral_reward.assert_awaited_once()
    kwargs = w.db.process_referral_reward.await_args.kwargs
    assert kwargs["buyer_id"] == TG
    assert kwargs["purchase_id"].startswith("balance_purchase_")
    assert kwargs["amount_rubles"] == 199.0
    assert kwargs["conn"] is w.conn


async def test_balance_purchase_consumes_promo_once_in_the_payment_connection(w, monkeypatch):
    await h.run_balance_purchase(w, monkeypatch, promo_code="SPRING")

    w.db.consume_promo.assert_awaited_once_with(w.conn, "SPRING", TG, None)


# ═══════════════════════════════════════════════════════════════════════
# MUST-KEEP: Telegram-native payment (payments_messages.process_successful_payment)
# ═══════════════════════════════════════════════════════════════════════

async def test_telegram_new_subscription_uses_purchase_success_first_text(w, monkeypatch):
    message = await h.run_telegram_payment(w, monkeypatch, make_pending())

    assert "purchase.success_first" in w.i18n_keys
    assert _sent_text(message.answer).startswith(text_prefix(w, "purchase.success_first"))


async def test_telegram_renewal_uses_purchase_success_renewal_text(w, monkeypatch):
    w.seed_active_subscription()

    message = await h.run_telegram_payment(w, monkeypatch, make_pending())

    assert "purchase.success_renewal" in w.i18n_keys
    assert _sent_text(message.answer).startswith(text_prefix(w, "purchase.success_renewal"))


async def test_telegram_renewal_basic_adds_10gb_again(w, monkeypatch):
    w.seed_active_subscription()

    await h.run_telegram_payment(w, monkeypatch, make_pending())

    assert w.panel.added_gb(TG) == 10


async def test_telegram_combo_renewal_adds_combo_gb_again(w, monkeypatch):
    w.seed_active_subscription()

    await h.run_telegram_payment(w, monkeypatch, make_pending(is_combo=True), fsm_combo_gb=75)

    assert w.panel.added_gb(TG) == 75


async def test_telegram_pending_activation_uses_pending_activation_text(w, monkeypatch):
    import config
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", False)
    monkeypatch.setattr(config, "VPN_ENABLED", False)

    message = await h.run_telegram_payment(w, monkeypatch, make_pending())

    assert "payment.pending_activation" in w.i18n_keys
    assert _sent_text(message.answer).startswith(text_prefix(w, "payment.pending_activation"))
    assert not w.panel.touched


# ═══════════════════════════════════════════════════════════════════════
# MUST-KEEP: auto-renewal of a basic subscription
# ═══════════════════════════════════════════════════════════════════════

async def test_auto_renewal_basic_charges_basic_price_and_adds_10gb(w, monkeypatch):
    import config
    w.seed_active_subscription(days_left=0)
    w.sub["expires_at"] = h.naive(h.utcnow() + h.timedelta(hours=2))

    out = await h.run_auto_renewal(w, monkeypatch, last_payment_tariff="basic_30")

    assert out["decrease_balance"].await_args.kwargs["amount"] == config.TARIFFS["basic"][30]["price"]
    assert w.panel.added_gb(TG) == 10
    assert "purchase.auto_renewal_success" in w.i18n_keys
    assert _sent_text(out["safe_send_message"]).startswith(
        text_prefix(w, "purchase.auto_renewal_success"))


# ═══════════════════════════════════════════════════════════════════════
# KNOWN BUGS (xfail strict): total bypass GB added to the panel per flow
# ═══════════════════════════════════════════════════════════════════════

@payment_core_bug(reason="payment-core: balance basic new = provision 10 + renew_remnawave_user_bg 10 (T10)")
async def test_balance_new_basic_30d_adds_10gb_once(w, monkeypatch):
    """Current: 20 GB (fresh bypass with 10 GB + handler renew_remnawave_user_bg +10)."""
    await h.run_balance_purchase(w, monkeypatch)

    assert w.panel.added_gb(TG) == 10


@payment_core_bug(reason="payment-core: balance combo new = 10 + renew_bg 10 + add_bypass_traffic 75 (T10)")
async def test_balance_combo_basic_30d_new_adds_combo_gb_only(w, monkeypatch):
    """Current: 95 GB (basic fresh 10 + renew_bg 10 + combo 75)."""
    await h.run_balance_purchase(w, monkeypatch, combo_gb=75)

    assert w.panel.added_gb(TG) == 75


async def test_balance_combo_basic_30d_renewal_adds_combo_gb_only(w, monkeypatch):
    """Fixed (T0-BAL-COMBO-RENEW): was 85 GB — renew_bg +10 raced combo +75 (both
    read-modify-write on the bypass limit). Combo now gets only its table GB."""
    w.seed_active_subscription()

    await h.run_balance_purchase(w, monkeypatch, combo_gb=75)

    assert w.panel.added_gb(TG) == 75


@payment_core_bug(reason="payment-core: Telegram basic new = provision 10 + renew_remnawave_user_bg 10 (T9)")
async def test_telegram_new_basic_30d_adds_10gb_once(w, monkeypatch):
    """Current: 20 GB (fresh bypass 10 GB + handler renew_remnawave_user_bg +10)."""
    await h.run_telegram_payment(w, monkeypatch, make_pending())

    assert w.panel.added_gb(TG) == 10


@payment_core_bug(reason="payment-core: Telegram combo new = fresh 75 + add_bypass_traffic 75 (T9)")
@pytest.mark.parametrize("fsm_combo_gb", [75, 0], ids=["fsm_kept", "fsm_lost_fallback"])
async def test_telegram_combo_basic_30d_new_adds_combo_gb_only(w, monkeypatch, fsm_combo_gb):
    """Current: 150 GB both with FSM data and via the COMBO_BYPASS_FSM_FALLBACK."""
    await h.run_telegram_payment(w, monkeypatch, make_pending(is_combo=True),
                                 fsm_combo_gb=fsm_combo_gb)

    assert w.panel.added_gb(TG) == 75


@payment_core_bug(reason="payment-core: admin day grant must give premium only, 0 GB (T13, §G0)")
async def test_admin_grant_days_new_user_adds_no_gb(w, monkeypatch):
    """Current: 20 GB (provision fresh 10 + renew_remnawave_user_bg 10)."""
    resp = await h.run_admin_grant_days(w, monkeypatch, days=7)

    assert resp["ok"] is True
    assert h.TG in w.panel.premium_expire
    assert w.panel.added_gb(TG) == 0


async def test_admin_grant_days_renewal_adds_no_gb(w, monkeypatch):
    """Dashboard grant (the only admin entry since the bot panel was removed): 0 GB on renewal.
    The 10 GB came from the bot handler's extra renew_remnawave_user_bg call."""
    w.seed_active_subscription()

    await h.run_admin_grant_days(w, monkeypatch, days=7)

    assert w.panel.added_gb(TG) == 0


@payment_core_bug(reason="payment-core: gift = provision 10 + start.py renew_remnawave_user_bg 10 (T14)")
async def test_gift_activation_basic_new_recipient_adds_10gb_once(w, monkeypatch):
    """Current: 20 GB (fresh bypass 10 GB + /start renew_remnawave_user_bg +10)."""
    message = await h.run_gift_activation(w, monkeypatch)

    assert "gift.activated" in w.i18n_keys
    assert _sent_text(message.answer).startswith(text_prefix(w, "gift.activated"))
    assert w.panel.added_gb(TG) == 10


@payment_core_bug(reason="payment-core: auto-renewal bills combo users the basic price (T12, §G0)")
async def test_auto_renewal_combo_charges_combo_price(w, monkeypatch):
    """Current: 199 ₽ (basic price) — payments.tariff of a combo purchase is 'basic_30'."""
    import config
    w.seed_active_subscription(is_combo=True)
    w.sub["expires_at"] = h.naive(h.utcnow() + h.timedelta(hours=2))

    out = await h.run_auto_renewal(w, monkeypatch, last_payment_tariff="basic_30")

    expected = config.COMBO_TARIFFS["combo_basic"][30]["price"]
    assert out["decrease_balance"].await_args.kwargs["amount"] == expected


@payment_core_bug(reason="payment-core: auto-renewal gives combo users 10 GB instead of combo GB (T12, §G0)")
async def test_auto_renewal_combo_adds_combo_gb(w, monkeypatch):
    """Current: 10 GB (renew_remnawave_user_bg with tariff 'basic')."""
    w.seed_active_subscription(is_combo=True)
    w.sub["expires_at"] = h.naive(h.utcnow() + h.timedelta(hours=2))

    await h.run_auto_renewal(w, monkeypatch, last_payment_tariff="basic_30")

    assert w.panel.added_gb(TG) == 75
