"""Prices of the broadcast gift offers («−30% на 1 месяц», «−30% на 3 месяца»,
«−40% на 1 год» — app/handlers/payments/broadcast_offers.py): one source for
the offer screens and for the Combo price guard in subscriptions.service.

The offer screens put the offer price into the purchase FSM themselves. The
Combo guard (2026-09-14) recomputed the Combo price without the offer's
discount and refused every paid Combo offer (prod 2026-09-15:
COMBO_PRICE_BELOW_COMBO user=502708 gift1y40 combo_plus/365 2399 < 3999 ₽).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import config

# offer key → discount percent and the periods it applies to (others: list price)
OFFERS = {
    "gift1m": {"percent": 30, "periods": (30,)},
    "gift3m": {"percent": 30, "periods": (90,)},
    "gift1y40": {"percent": 40, "periods": (365,)},
}


def list_price_rubles(tariff: str, period_days: int) -> Optional[int]:
    """List price of basic / plus / combo_basic / combo_plus for the period."""
    if tariff in ("basic", "plus"):
        return config.TARIFFS.get(tariff, {}).get(period_days, {}).get("price")
    if tariff in ("combo_basic", "combo_plus"):
        return config.COMBO_TARIFFS.get(tariff, {}).get(period_days, {}).get("price")
    return None


def offer_price_rubles(offer_key: str, tariff: str, period_days: int) -> Optional[int]:
    """Price of `tariff` for `period_days` inside the offer; None when unknown."""
    offer = OFFERS.get(offer_key)
    base = list_price_rubles(tariff, period_days)
    if offer is None or not base:
        return None
    if period_days in offer["periods"]:
        return round(base * (100 - offer["percent"]) / 100)
    return base


def fsm_offer_key(fsm_data: Mapping[str, Any]) -> Optional[str]:
    """The broadcast offer this purchase FSM still belongs to, or None.

    Valid only when the FSM price is exactly that offer's price for the FSM
    tariff / period / Combo flag: a regular flow that changed any of them drops
    the offer, so a stale offer key never lowers another purchase's Combo floor
    (plus/365 = 2599 ₽ is above combo_plus/365 at −40 % = 2399 ₽)."""
    key = fsm_data.get("offer_key")
    tariff = fsm_data.get("tariff_type")
    period = fsm_data.get("period_days")
    if not key or not tariff or not period:
        return None
    combo = (fsm_data.get("combo_bypass_gb") or 0) > 0
    try:
        price = offer_price_rubles(str(key), f"combo_{tariff}" if combo else str(tariff), int(period))
        if price and int(fsm_data.get("final_price_kopecks") or 0) == price * 100:
            return str(key)
    except (TypeError, ValueError):
        pass
    return None
