"""P0: a Combo flag left in FSM (abandoned Combo screen / broadcast Combo offer)
turned the next Basic/Plus purchase into Combo at the Basic price (75 GB for
199 ₽). Two layers: every FSM write of tariff_type also sets combo_bypass_gb,
and create_subscription_purchase refuses Combo below the Combo price."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import config
from app.services.subscriptions import service as svc

ROOT = Path(__file__).resolve().parents[2]


def _update_data_calls_setting_tariff():
    for path in (ROOT / "app" / "handlers").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "update_data"):
                kws = {k.arg for k in node.keywords if k.arg}
                if "tariff_type" in kws:
                    yield f"{path.relative_to(ROOT)}:{node.lineno}", kws


def test_every_tariff_write_also_sets_the_combo_flag():
    offenders = [loc for loc, kws in _update_data_calls_setting_tariff() if "combo_bypass_gb" not in kws]
    assert offenders == []


async def _create(monkeypatch, *, price_kopecks, combo_final):
    async def calc(**kw):
        return {"final_price_kopecks": combo_final, "base_price_kopecks": combo_final,
                "discount_percent": 0}

    async def create_pending(**kw):
        return "pid-combo"
    monkeypatch.setattr(svc, "calculate_price", calc)
    monkeypatch.setattr(svc.database, "create_pending_purchase", create_pending)
    monkeypatch.setattr(config, "VPN_ENABLED", True, raising=False)
    return await svc.create_subscription_purchase(
        telegram_id=1, tariff="basic", period_days=30, price_kopecks=price_kopecks, is_combo=True,
    )


async def test_combo_below_combo_price_is_refused(monkeypatch):
    combo = config.COMBO_TARIFFS["combo_basic"][30]["price"] * 100
    basic = config.TARIFFS["basic"][30]["price"] * 100
    assert basic < combo
    with pytest.raises(svc.InvalidTariffError):
        await _create(monkeypatch, price_kopecks=basic, combo_final=combo)


async def test_combo_at_combo_price_is_created(monkeypatch):
    combo = config.COMBO_TARIFFS["combo_basic"][30]["price"] * 100
    assert await _create(monkeypatch, price_kopecks=combo, combo_final=combo) == "pid-combo"
