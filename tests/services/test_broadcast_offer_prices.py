"""Broadcast gift offers × the Combo price guard.

Prod 2026-09-15: COMBO_PRICE_BELOW_COMBO user=502708 — «−40% на 1 год» sold
Combo Plus / 365 at 2399 ₽; the guard (2026-09-14) recomputed 3999 ₽ without the
offer's discount and refused the card payment. Every paid Combo offer was
refused. The guard now accepts the offer price — only while the FSM still
holds exactly that offer (a stale offer key never lowers another Combo floor).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
from app.handlers.payments import broadcast_offers as bo
from app.services import broadcast_offer_prices as offers
from app.services.subscriptions import service
from app.services.subscriptions.exceptions import InvalidTariffError

TARIFFS = ("basic", "plus", "combo_basic", "combo_plus")
OFFER_PRICE = round(config.COMBO_TARIFFS["combo_plus"][365]["price"] * 60 / 100)   # 2399
COMBO_PRICE = config.COMBO_TARIFFS["combo_plus"][365]["price"]                      # 3999
PLUS_PRICE = config.TARIFFS["plus"][365]["price"]                                   # 2599


def _fsm(**kw):
    fsm = {"offer_key": "gift1y40", "tariff_type": "plus", "period_days": 365,
           "combo_bypass_gb": 800, "final_price_kopecks": OFFER_PRICE * 100}
    fsm.update(kw)
    return fsm


# ── one formula for the offer screens and the guard ────────────────────

def test_offer_screens_use_the_guard_formula():
    for t in TARIFFS:
        assert bo._gift1m_price_rubles(t) == offers.offer_price_rubles("gift1m", t, 30)
        assert bo._gift3m_price_rubles(t) == offers.offer_price_rubles("gift3m", t, 90)
        for p in (30, 90, 180, 365):
            assert bo._gift1y40_final_price(t, p) == offers.offer_price_rubles("gift1y40", t, p)
    assert offers.OFFERS["gift1m"]["percent"] == bo._GIFT1M_DISCOUNT_PERCENT
    assert offers.OFFERS["gift3m"]["percent"] == bo._GIFT3M_DISCOUNT_PERCENT
    assert offers.OFFERS["gift1y40"]["percent"] == bo._GIFT1Y40_DISCOUNT_PERCENT
    assert offers.offer_price_rubles("gift1y40", "combo_plus", 365) == OFFER_PRICE
    assert offers.offer_price_rubles("gift1y40", "combo_plus", 90) == config.COMBO_TARIFFS["combo_plus"][90]["price"]


# ── fsm_offer_key: the offer holds only while the FSM is that offer ────

def test_fsm_offer_key_accepts_the_offer_purchase():
    assert offers.fsm_offer_key(_fsm()) == "gift1y40"


@pytest.mark.parametrize("change", [
    {"final_price_kopecks": PLUS_PRICE * 100},   # regular Plus price after the offer
    {"period_days": 180},                        # another period
    {"combo_bypass_gb": 0},                      # not Combo any more
    {"offer_key": None},
    {"offer_key": "unknown"},
    {"period_days": "x"},
])
def test_fsm_offer_key_drops_a_stale_or_foreign_offer(change):
    assert offers.fsm_offer_key(_fsm(**change)) is None


# ── the guard ──────────────────────────────────────────────────────────

@pytest.fixture
def world(monkeypatch):
    calc = AsyncMock(return_value={"final_price_kopecks": COMBO_PRICE * 100})
    monkeypatch.setattr(service, "calculate_price", calc)
    monkeypatch.setattr(database, "create_pending_purchase", AsyncMock(return_value="pid-1"), raising=False)
    monkeypatch.setattr(config, "VPN_ENABLED", True, raising=False)
    return calc


async def test_paid_combo_offer_passes_the_guard(world):
    pid = await service.create_subscription_purchase(
        502708, "plus", 365, OFFER_PRICE * 100, is_combo=True,
        combo_offer_key=offers.fsm_offer_key(_fsm()))
    assert pid == "pid-1"
    world.assert_not_awaited()                    # the offer price is the floor


async def test_card_markup_on_top_of_the_offer_passes(world):
    assert await service.create_subscription_purchase(
        502708, "plus", 365, OFFER_PRICE * 100 + 1000, is_combo=True, combo_offer_key="gift1y40") == "pid-1"


async def test_combo_below_its_price_without_an_offer_is_still_refused(world):
    with pytest.raises(InvalidTariffError):
        await service.create_subscription_purchase(502708, "plus", 365, OFFER_PRICE * 100, is_combo=True)


async def test_stale_offer_key_with_a_regular_plus_price_is_refused(world):
    """P0 kept: a stale Combo flag + the Plus price (2599 ₽ > the offer's 2399 ₽)
    must not buy Combo — the stale offer key is dropped by fsm_offer_key."""
    stale = _fsm(final_price_kopecks=PLUS_PRICE * 100)
    with pytest.raises(InvalidTariffError):
        await service.create_subscription_purchase(
            502708, "plus", 365, PLUS_PRICE * 100, is_combo=True, combo_offer_key=offers.fsm_offer_key(stale))


async def test_balance_guard_accepts_the_offer_and_refuses_without_it(world):
    await service.ensure_combo_price_not_below(502708, "plus", 365, OFFER_PRICE * 100, combo_offer_key="gift1y40")
    with pytest.raises(InvalidTariffError):
        await service.ensure_combo_price_not_below(502708, "plus", 365, OFFER_PRICE * 100)


# ── the offer screen writes the key the guard reads ────────────────────

@pytest.mark.parametrize("data,handler,key", [
    ("bcg1y40:buy:combo_plus:365", "callback_broadcast_gift_1y_40_buy", "gift1y40"),
    ("bcg1m:buy:combo_basic", "callback_broadcast_gift_1m_buy", "gift1m"),
    ("bcg3m:buy:combo_plus", "callback_broadcast_gift_3m_buy", "gift3m"),
])
async def test_offer_buy_screen_stores_a_valid_offer_key(monkeypatch, data, handler, key):
    import app.handlers.payments.payment_method_selection as pms
    monkeypatch.setattr(pms, "show_payment_method_selection", AsyncMock())
    callback = MagicMock()
    callback.data = data
    callback.from_user.id = 502708
    callback.answer = AsyncMock()
    state = MagicMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    await getattr(bo, handler)(callback, state)

    fsm = state.update_data.await_args.kwargs
    assert fsm["offer_key"] == key and fsm["combo_bypass_gb"] > 0
    assert offers.fsm_offer_key(fsm) == key        # the guard will accept this purchase


# ── «Забрать подарок» (broadcast_gift_combo): the broadcast's own percent ─

GIFT_BASE = config.COMBO_TARIFFS["combo_basic"][30]["price"]     # 329


def test_gift_combo_price_is_the_screen_price_for_combo_basic_only():
    assert offers.offer_price_rubles(offers.gift_combo_key(30), "combo_basic", 30) == round(GIFT_BASE * 0.7)
    assert offers.offer_price_rubles(offers.gift_combo_key(30), "combo_plus", 30) is None
    assert offers.offer_price_rubles(offers.gift_combo_key(30), "combo_basic", 90) == \
        config.COMBO_TARIFFS["combo_basic"][90]["price"]   # outside the gift: list price
    for bad in ("gift_combo:0", "gift_combo:100", "gift_combo:x", "gift_combo"):
        assert offers.offer_price_rubles(bad, "combo_basic", 30) is None


def test_gift_combo_fsm_key_holds_only_for_the_gift_price():
    fsm = {"offer_key": "gift_combo:30", "tariff_type": "basic", "period_days": 30,
           "combo_bypass_gb": 75, "final_price_kopecks": round(GIFT_BASE * 0.7) * 100}
    assert offers.fsm_offer_key(fsm) == "gift_combo:30"
    assert offers.fsm_offer_key({**fsm, "final_price_kopecks": 19_900}) is None   # stale flag + Basic price
    assert offers.fsm_offer_key({**fsm, "combo_bypass_gb": 0}) is None


async def test_gift_combo_rounded_down_price_passes_the_guard(monkeypatch):
    """d = 30: the screen's 230 ₽ is 30 kopecks under the guard's exact 230.30 ₽."""
    monkeypatch.setattr(service, "calculate_price",
                        AsyncMock(return_value={"final_price_kopecks": 32_900 - int(32_900 * 30 / 100)}))
    monkeypatch.setattr(database, "create_pending_purchase", AsyncMock(return_value="pid-g"), raising=False)
    monkeypatch.setattr(config, "VPN_ENABLED", True, raising=False)
    price = round(GIFT_BASE * 0.7) * 100
    with pytest.raises(InvalidTariffError):                    # without the key: refused, as before
        await service.create_subscription_purchase(1, "basic", 30, price, is_combo=True)
    assert await service.create_subscription_purchase(
        1, "basic", 30, price, is_combo=True, combo_offer_key="gift_combo:30") == "pid-g"
