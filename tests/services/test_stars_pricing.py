"""Telegram Stars: price, accounting and promo (HOW_IT_WORKS P1-2 / P2 «Stars as rubles»).

- Combo bought with Stars is priced from the COMBO RUB price with the same
  RUB→Stars rule the bot uses for top-ups and gifts (ceil(rub × 1.7 / 1.85)),
  not from the basic/plus Stars table.
- pending_purchases.price_kopecks of a Stars purchase holds the RUB list price
  (not stars × 100); successful_payment converts the paid stars to that RUB
  amount, so payments.amount, revenue and cashback are rubles.
- Legacy rows (price_kopecks = stars × 100) keep the old behaviour.
- A promo code is not attached to a Stars purchase (Stars price is fixed, so
  the code must not be consumed).
"""
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
from app.services import tariffs


def _rule(rub: int) -> int:
    return math.ceil(rub * 1.7 / 1.85)


# ── tariffs.stars_price ───────────────────────────────────────────────


@pytest.mark.parametrize("key", ["combo_basic", "combo_plus"])
@pytest.mark.parametrize("period", [30, 90, 180, 365, 730])
def test_combo_stars_price_is_derived_from_combo_rub_price(key, period):
    rub = config.COMBO_TARIFFS[key][period]["price"]
    assert tariffs.stars_price(key, period) == _rule(rub)


@pytest.mark.parametrize("key", ["basic", "plus"])
@pytest.mark.parametrize("period", [30, 90, 180, 365])
def test_base_stars_price_is_the_stars_table(key, period):
    assert tariffs.stars_price(key, period) == config.TARIFFS_STARS[key][period]["price"]


def test_stars_price_unknown_period_raises():
    with pytest.raises(tariffs.TariffConfigError):
        tariffs.stars_price("basic", 730)


# ── callback_pay_stars ────────────────────────────────────────────────


async def _pay_stars(monkeypatch, *, tariff_type, period_days, combo_gb=0, final_price_kopecks=None,
                     promo_code=None):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from app.handlers.callbacks import payments_callbacks as pc
    from app.handlers.common.states import PurchaseState

    tg = 777
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=tg, user_id=tg))
    await state.set_state(PurchaseState.choose_payment_method)
    if final_price_kopecks is None:   # no discount: the screen showed the list price
        key = tariffs.tariff_key(tariff_type, combo_gb > 0)
        final_price_kopecks = tariffs.renewal_price_rub(key, period_days) * 100
    await state.update_data(tariff_type=tariff_type, period_days=period_days,
                            final_price_kopecks=final_price_kopecks, combo_bypass_gb=combo_gb)

    monkeypatch.setattr(pc, "check_rate_limit", lambda *a, **k: (True, None))
    monkeypatch.setattr(pc, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pc, "get_promo_session",
                        AsyncMock(return_value={"promo_code": promo_code} if promo_code else None))
    monkeypatch.setattr(pc, "get_applied_promo_code", AsyncMock(return_value=promo_code))
    monkeypatch.setattr(pc, "_schedule_invoice_deletion", AsyncMock())
    create = AsyncMock(return_value="pid-stars")
    monkeypatch.setattr(pc.subscription_service, "create_subscription_purchase", create)

    callback = MagicMock()
    callback.from_user.id = tg
    callback.answer = AsyncMock()
    callback.message.delete = AsyncMock()
    callback.bot.send_invoice = AsyncMock(return_value=SimpleNamespace(message_id=5))
    callback.bot.send_message = AsyncMock()
    await pc.callback_pay_stars(callback, state)
    return callback, create


async def test_pay_stars_combo_is_priced_as_combo(monkeypatch):
    callback, create = await _pay_stars(monkeypatch, tariff_type="basic", period_days=30, combo_gb=75)
    invoice = callback.bot.send_invoice.await_args.kwargs
    assert invoice["currency"] == "XTR"
    assert invoice["prices"][0].amount == _rule(329)          # combo basic 30 d = 329 ₽
    assert invoice["prices"][0].amount != config.TARIFFS_STARS["basic"][30]["price"]
    kw = create.await_args.kwargs
    assert kw["is_combo"] is True
    assert kw["price_kopecks"] == 329 * 100                   # RUB list price, not stars × 100


async def test_pay_stars_combo_730_creates_an_invoice(monkeypatch):
    callback, create = await _pay_stars(monkeypatch, tariff_type="plus", period_days=730, combo_gb=1500)
    assert callback.bot.send_invoice.await_count == 1
    assert callback.bot.send_invoice.await_args.kwargs["prices"][0].amount == _rule(7999)
    assert create.await_args.kwargs["price_kopecks"] == 7999 * 100


async def test_pay_stars_basic_records_rubles_and_takes_no_promo(monkeypatch):
    callback, create = await _pay_stars(monkeypatch, tariff_type="basic", period_days=30,
                                        promo_code="SALE5")
    assert callback.bot.send_invoice.await_args.kwargs["prices"][0].amount == 185
    kw = create.await_args.kwargs
    assert kw["price_kopecks"] == 199 * 100
    assert kw["is_combo"] is False
    assert kw["promo_code"] is None     # Stars price is fixed → the code is not consumed


# ── discounts (docs/audit/08_payments_ux.md P1 #2) ────────────────────


async def test_pay_stars_uses_the_discounted_price_the_screen_showed(monkeypatch):
    """Offer «Basic 1 month for 139 ₽»: before, 185 ⭐ (≈199 ₽) were charged."""
    callback, create = await _pay_stars(monkeypatch, tariff_type="basic", period_days=30,
                                        final_price_kopecks=13900)
    assert callback.bot.send_invoice.await_args.kwargs["prices"][0].amount == _rule(139)
    assert create.await_args.kwargs["price_kopecks"] == 13900


async def test_pay_stars_discounted_combo_offer(monkeypatch):
    callback, create = await _pay_stars(monkeypatch, tariff_type="basic", period_days=30,
                                        combo_gb=75, final_price_kopecks=23000)   # 329 × 0.7
    assert callback.bot.send_invoice.await_args.kwargs["prices"][0].amount == _rule(230)
    assert create.await_args.kwargs["price_kopecks"] == 23000
    assert create.await_args.kwargs["is_combo"] is True


async def test_pay_stars_promo_that_gave_the_price_is_stored(monkeypatch):
    callback, create = await _pay_stars(monkeypatch, tariff_type="plus", period_days=90,
                                        final_price_kopecks=80900, promo_code="SALE10")
    assert create.await_args.kwargs["promo_code"] == "SALE10"
    assert callback.bot.send_invoice.await_args.kwargs["prices"][0].amount == _rule(809)


@pytest.mark.parametrize("price_kopecks, stars", [
    (19900, 185),            # list price → the Stars table
    (30000, 185),            # dashboard price above the catalog → never more than the table
    (16900, _rule(169)),     # −15 %
    (100, 1),                # tiny price → at least 1 star
])
def test_stars_for_purchase(price_kopecks, stars):
    assert tariffs.stars_for_purchase("basic", 30, price_kopecks) == stars


def test_discounted_stars_row_is_converted_back_to_its_price():
    from app.handlers.payments import payments_messages as pm
    row = _pending(price_kopecks=13900)
    assert pm._expected_stars(row) == _rule(139)
    assert pm._stars_paid_to_rubles(row, _rule(139)) == pytest.approx(139.0)


# ── successful_payment: stars → rubles ───────────────────────────────


def _pending(**kw):
    base = {"purchase_id": "p1", "telegram_id": 777, "purchase_type": "subscription",
            "tariff": "basic", "period_days": 30, "is_combo": False, "status": "pending"}
    base.update(kw)
    return base


@pytest.mark.parametrize("pending, paid, rub", [
    (_pending(price_kopecks=19900), 185, 199.0),                                  # basic, new row
    (_pending(is_combo=True, price_kopecks=32900), _rule(329), 329.0),            # combo, new row
    (_pending(purchase_type="gift", price_kopecks=19900), 185, 199.0),            # gift (always RUB)
    (_pending(price_kopecks=18500), 185, 185.0),                                  # legacy row: stars × 100
])
def test_stars_paid_is_converted_to_rubles(pending, paid, rub):
    from app.handlers.payments import payments_messages as pm
    assert pm._stars_paid_to_rubles(pending, paid) == pytest.approx(rub)


def test_stars_underpayment_stays_an_underpayment():
    from app.handlers.payments import payments_messages as pm
    # combo row paid with the old basic price → far below 329 ₽ → rejected by the amount check
    assert pm._stars_paid_to_rubles(_pending(is_combo=True, price_kopecks=32900), 185) < 329 - 100


async def test_gift_paid_with_stars_is_finalized_in_rubles(monkeypatch):
    """Before: 185 stars were passed as 185 ₽ against a 199 ₽ gift row → amount
    mismatch → money taken, no gift."""
    from app.handlers.payments import payments_messages as pm
    from app.handlers.callbacks import gift as gift_mod

    pending = _pending(purchase_type="gift", price_kopecks=19900)
    monkeypatch.setattr(database, "DB_READY", True)
    monkeypatch.setattr(pm, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pm.payment_service, "verify_payment_payload", AsyncMock(
        return_value=SimpleNamespace(payload_type="purchase", purchase_id="p1")))
    monkeypatch.setattr(database, "get_pending_purchase", AsyncMock(return_value=dict(pending)))
    monkeypatch.setattr(database, "_log_audit_event_atomic_standalone", AsyncMock())
    finalize = AsyncMock(return_value={"is_gift": True, "gift_code": "G1", "gift_tariff": "basic",
                                       "gift_period_days": 30})
    monkeypatch.setattr(database, "finalize_purchase", finalize)
    monkeypatch.setattr(gift_mod, "_send_gift_success", AsyncMock())

    message = MagicMock()
    message.from_user.id = 777
    message.message_id = 1
    message.answer = AsyncMock()
    message.successful_payment = SimpleNamespace(
        currency="XTR", total_amount=185, invoice_payload="purchase:p1",
        telegram_payment_charge_id="c1",
    )
    state = MagicMock()
    state.clear = AsyncMock()
    await pm.process_successful_payment(message, state)
    assert finalize.await_args.kwargs["amount_rubles"] == pytest.approx(199.0)
    assert finalize.await_args.kwargs["payment_provider"] == "telegram_stars"
