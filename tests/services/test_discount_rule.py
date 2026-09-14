"""Owner decision 2026-09-14 (docs/audit/SCOPE.md «VIP»): VIP is removed completely.
Discounts that remain: promo codes and personal discounts (−15 % offers, the −15 %
special offer when a paid subscription ends, trial −30 %, admin-set). Several apply
→ the LARGEST single discount wins, no stacking. One pricing function:
database.subscriptions.calculate_final_price (+ pick_largest_discount, also used by
auto-renewal).
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

import config
import database
import database.admin as db_admin
import database.subscriptions as db_subs

TG = 4242
BASE = round(config.TARIFFS["basic"][30]["price"] * 100)


def _world(monkeypatch, *, promo=None, special=None, personal=None):
    monkeypatch.setattr(db_subs, "check_promo_code_valid", AsyncMock(
        return_value={"discount_percent": promo} if promo is not None else None))
    monkeypatch.setattr(db_subs, "get_special_offer_info", AsyncMock(
        return_value={"discount_percent": special} if special is not None else None))
    monkeypatch.setattr(db_admin, "get_user_discount", AsyncMock(
        return_value={"discount_percent": personal} if personal is not None else None))


@pytest.mark.parametrize("promo,special,personal,kind,pct", [
    (None, None, None, None, 0),
    (10, None, None, "promo", 10),
    (None, 15, None, "special_offer", 15),
    (None, None, 30, "personal", 30),
    (10, None, 30, "personal", 30),       # a bigger personal discount beats a small promo
    (50, None, 30, "promo", 50),
    (20, None, 20, "promo", 20),          # tie → the code the user typed
    (None, 15, 30, "personal", 30),       # the special offer no longer hides a bigger personal one
    (None, 15, 10, "special_offer", 15),
    (5, 15, 10, "special_offer", 15),
    (40, 15, 30, "promo", 40),
    (150, None, None, "promo", 100),      # clamped, the price never goes below 0
])
async def test_the_largest_single_discount_wins(monkeypatch, promo, special, personal, kind, pct):
    _world(monkeypatch, promo=promo, special=special, personal=personal)
    code = "code" if promo is not None else None

    price = await database.calculate_final_price(TG, "basic", 30, promo_code=code)

    assert price["discount_type"] == kind
    assert price["discount_percent"] == pct
    assert price["discount_amount_kopecks"] == int(BASE * pct / 100)      # no stacking
    assert price["final_price_kopecks"] == BASE - int(BASE * pct / 100)
    # the promo code is reported as applied only when it is the discount that won
    assert price["promo_code"] == ("CODE" if kind == "promo" else None)


async def test_the_rule_applies_to_a_combo_base_price(monkeypatch):
    _world(monkeypatch, promo=10, personal=25)
    price = await database.calculate_final_price(TG, "basic", 30, promo_code="c",
                                                 base_price_override_rubles=1000)
    assert (price["discount_type"], price["final_price_kopecks"]) == ("personal", 75_000)


@pytest.mark.parametrize("candidates,expected", [
    ([], (None, 0)),
    ([("promo", 0), ("personal", None)], (None, 0)),
    ([("promo", 10), ("personal", 30)], ("personal", 30)),
    ([("promo", 30), ("personal", 30)], ("promo", 30)),
    ([("personal", 120)], ("personal", 100)),
    ([("personal", -5)], (None, 0)),
    ([("personal", "x"), ("special_offer", 15)], ("special_offer", 15)),
])
def test_pick_largest_discount(candidates, expected):
    assert db_subs.pick_largest_discount(candidates) == expected


def test_vip_functions_are_gone():
    for name in ("is_vip_user", "grant_vip_status", "revoke_vip_status"):
        assert not hasattr(database, name), name
        assert not hasattr(db_admin, name), name


def test_pricing_never_reads_vip():
    src = inspect.getsource(db_subs.calculate_final_price).lower()
    assert "is_vip" not in src and "vip_users" not in src and '"vip"' not in src


def test_dashboard_has_no_vip_endpoints_or_filter():
    from app.api.dashboard.routes import users as routes
    paths = {getattr(r, "path", "") for r in routes.router.routes}
    assert not any(p.endswith("/vip") for p in paths), paths
    assert "is_vip" not in inspect.signature(routes.users_list).parameters
    assert "is_vip" not in inspect.signature(database.list_users_dashboard).parameters


async def test_a_promo_code_that_lost_is_not_stored_or_consumed():
    """A promo code that lost to a bigger discount gave nothing: the purchase must
    not store / consume it (get_applied_promo_code reads FSM promo_applied)."""
    import time
    from app.handlers.common.utils import get_applied_promo_code
    from tests.services.payment_core_harness import _fsm

    state = _fsm(1)
    await state.update_data(promo_session={"promo_code": "CODE", "discount_percent": 10,
                                           "expires_at": time.time() + 300})
    assert await get_applied_promo_code(state) == "CODE"      # no flag (older flows) → as before
    await state.update_data(promo_applied=False)
    assert await get_applied_promo_code(state) is None
    await state.update_data(promo_applied=True)
    assert await get_applied_promo_code(state) == "CODE"


def test_purchase_sites_use_the_applied_promo_code_and_price_screens_set_the_flag():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    pay = (root / "app/handlers/callbacks/payments_callbacks.py").read_text()
    assert 'promo_session.get("promo_code")' not in pay
    assert pay.count("await get_applied_promo_code(state)") >= 6
    periods = (root / "app/handlers/payments/callbacks.py").read_text()
    assert periods.count('promo_applied=bool(price_info.get("promo_code"))') == 2
    assert "promo_applied=promo_applied" in (root / "app/handlers/callbacks/navigation.py").read_text()


def test_no_vip_broadcast_segment():
    from app.api.dashboard.routes import broadcasts
    assert "vip_active" not in inspect.getsource(broadcasts)
    assert "vip_active" not in inspect.getsource(db_admin.get_users_by_segment)
