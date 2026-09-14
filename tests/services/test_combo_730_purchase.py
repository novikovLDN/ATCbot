"""Combo 24 months (730 d) is shown on the combo screen, so it must be buyable
(HOW_IT_WORKS P1-3): create_subscription_purchase validates combo periods
against config.COMBO_TARIFFS, basic/plus against config.TARIFFS."""
from unittest.mock import AsyncMock

import pytest

import config
import database
from app.services.subscriptions import service as subscription_service
from app.services.subscriptions.exceptions import InvalidTariffError


@pytest.fixture
def created(monkeypatch):
    create = AsyncMock(return_value="pid-730")
    monkeypatch.setattr(database, "create_pending_purchase", create)
    monkeypatch.setattr(config, "VPN_ENABLED", True)

    async def combo_price(**kw):  # the combo price guard recomputes the price (no discounts here)
        rub = kw["base_price_override_rubles"]
        return {"final_price_kopecks": rub * 100, "base_price_kopecks": rub * 100, "discount_percent": 0}
    monkeypatch.setattr(subscription_service, "calculate_price", combo_price)
    return create


@pytest.mark.parametrize("tariff, key", [("basic", "combo_basic"), ("plus", "combo_plus")])
async def test_combo_730_creates_a_purchase(created, tariff, key):
    price = config.COMBO_TARIFFS[key][730]["price"] * 100
    pid = await subscription_service.create_subscription_purchase(
        telegram_id=1, tariff=tariff, period_days=730, price_kopecks=price, is_combo=True,
    )
    assert pid == "pid-730"
    kw = created.await_args.kwargs
    assert kw["period_days"] == 730 and kw["is_combo"] is True and kw["price_kopecks"] == price


async def test_basic_730_without_combo_is_still_rejected(created):
    with pytest.raises(InvalidTariffError):
        await subscription_service.create_subscription_purchase(
            telegram_id=1, tariff="basic", period_days=730, price_kopecks=699900, is_combo=False,
        )
    created.assert_not_awaited()


async def test_combo_unknown_period_is_rejected(created):
    with pytest.raises(InvalidTariffError):
        await subscription_service.create_subscription_purchase(
            telegram_id=1, tariff="basic", period_days=45, price_kopecks=10000, is_combo=True,
        )
    created.assert_not_awaited()


def test_every_combo_button_period_has_an_entitlement():
    """The combo screen shows every COMBO_TARIFFS period; each must map to
    premium days + combo GB (the provisioning side of the purchase)."""
    from app.services import tariffs
    for key, rows in config.COMBO_TARIFFS.items():
        for period, row in rows.items():
            ent = tariffs.for_purchase(key, period)
            assert ent.premium_days == period
            assert ent.bypass_bytes == row["gb"] * tariffs.GIB
