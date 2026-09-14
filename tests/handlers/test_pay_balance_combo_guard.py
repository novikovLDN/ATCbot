"""Hotfix of b179203f: P0 Combo guard on the balance path.

create_subscription_purchase refuses a Combo purchase below the Combo price
(23b2388d), but pay:balance never goes through it: a stale / forged FSM
(combo_bypass_gb + a Basic/Plus price) was debited at the Basic price and
then granted the combo GB + the combo flag. pay:balance must refuse it
before any debit.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
from app.handlers.callbacks import payments_callbacks as mod
from app.handlers.common.states import PurchaseState
from app.services.subscriptions import service as svc

COMBO_KOP = config.COMBO_TARIFFS["combo_basic"][30]["price"] * 100
BASIC_KOP = config.TARIFFS["basic"][30]["price"] * 100


def _callback(tg=42):
    cb = MagicMock()
    cb.data = "pay:balance"
    cb.from_user = SimpleNamespace(id=tg)
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.delete = AsyncMock()
    return cb


def _state(data):
    st = MagicMock()
    st.get_state = AsyncMock(return_value=PurchaseState.choose_payment_method.state)
    st.get_data = AsyncMock(return_value=dict(data))
    st.set_state = AsyncMock()
    st.update_data = AsyncMock()
    st.clear = AsyncMock()
    return st


@pytest.fixture
def finalize(monkeypatch):
    """Everything around the handler mocked; returns finalize_balance_purchase
    (the single place that debits the balance). It answers success=False so
    the handler stops right after the debit attempt."""
    fin = AsyncMock(return_value={"success": False})
    monkeypatch.setattr(mod, "check_rate_limit", MagicMock(return_value=(True, None)))
    monkeypatch.setattr(mod, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(mod, "get_promo_session", AsyncMock(return_value=None))
    monkeypatch.setattr(database, "get_user_balance", AsyncMock(return_value=10_000.0), raising=False)
    monkeypatch.setattr(database, "get_subscription", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(database, "finalize_balance_purchase", fin, raising=False)

    async def calc(**kw):
        base = kw.get("base_price_override_rubles")
        kop = int(base * 100) if base is not None else BASIC_KOP
        return {"final_price_kopecks": kop, "base_price_kopecks": kop, "discount_percent": 0}
    monkeypatch.setattr(svc, "calculate_price", calc)
    return fin


def _fsm(price, combo_gb):
    return {"tariff_type": "basic", "period_days": 30,
            "final_price_kopecks": price, "combo_bypass_gb": combo_gb}


async def test_forged_combo_flag_with_basic_price_is_refused_before_debit(finalize):
    assert BASIC_KOP < COMBO_KOP
    cb, st = _callback(), _state(_fsm(BASIC_KOP, 75))
    await mod.callback_pay_balance(cb, st)
    finalize.assert_not_awaited()  # no money taken, no combo GB granted
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    st.set_state.assert_any_await(None)


async def test_combo_at_combo_price_is_paid(finalize):
    await mod.callback_pay_balance(_callback(), _state(_fsm(COMBO_KOP, 75)))
    finalize.assert_awaited_once()


async def test_plain_basic_is_paid(finalize):
    await mod.callback_pay_balance(_callback(), _state(_fsm(BASIC_KOP, 0)))
    finalize.assert_awaited_once()
