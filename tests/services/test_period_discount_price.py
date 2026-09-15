"""calculate_final_price × the broadcast period gift (migration 095, owner
2026-09-15): «−40% на 1 год» discounts ONLY the 365-day period; the largest
single discount wins (a general personal discount stays on the other periods).
"""
from unittest.mock import AsyncMock

import pytest

import config
from database import admin as db_admin
from database import subscriptions as db_subs


def _world(monkeypatch, *, general=None, period=None):
    """general: the user_discounts %; period: {period_days: %} of user_period_discounts."""
    monkeypatch.setattr(db_subs, "get_special_offer_info", AsyncMock(return_value=None))
    monkeypatch.setattr(db_admin, "get_user_discount", AsyncMock(
        return_value={"discount_percent": general} if general else None))
    period = period or {}
    monkeypatch.setattr(db_admin, "get_period_discount", AsyncMock(
        side_effect=lambda _tg, p: {"discount_percent": period[p]} if p in period else None))


def _off(kopecks: int, pct: int) -> int:
    return kopecks - int(kopecks * pct / 100)


@pytest.mark.parametrize("tariff", ["basic", "plus"])
async def test_year_gift_discounts_only_the_year(monkeypatch, tariff):
    _world(monkeypatch, period={365: 40})
    year = await db_subs.calculate_final_price(1, tariff, 365)
    month = await db_subs.calculate_final_price(1, tariff, 30)
    assert year["final_price_kopecks"] == _off(config.TARIFFS[tariff][365]["price"] * 100, 40)
    assert year["discount_percent"] == 40
    assert month["final_price_kopecks"] == config.TARIFFS[tariff][30]["price"] * 100
    assert month["discount_percent"] == 0


async def test_combo_year_gets_the_year_gift(monkeypatch):
    _world(monkeypatch, period={365: 40})
    combo = config.COMBO_TARIFFS["combo_plus"][365]["price"]
    res = await db_subs.calculate_final_price(1, "plus", 365, base_price_override_rubles=combo)
    assert res["final_price_kopecks"] == _off(combo * 100, 40)


async def test_a_general_discount_stays_on_the_other_periods(monkeypatch):
    _world(monkeypatch, general=15, period={365: 40})
    assert (await db_subs.calculate_final_price(1, "basic", 30))["discount_percent"] == 15
    assert (await db_subs.calculate_final_price(1, "basic", 365))["discount_percent"] == 40


async def test_a_bigger_general_discount_wins_on_the_gift_period(monkeypatch):
    _world(monkeypatch, general=50, period={365: 40})
    assert (await db_subs.calculate_final_price(1, "basic", 365))["discount_percent"] == 50
