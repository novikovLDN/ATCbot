"""HOW_IT_WORKS P2 «gift paid from balance is two operations».

callback_gift_pay_balance debits the balance, then creates the gift code on a
different connection. If creating the code failed, the money stayed debited:
no refund, no alert. Now the debit is refunded and the admin gets a forced
alert + payment_errors. A failure AFTER the code exists (e.g. sending the
success message) must not refund — the buyer has the gift.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

import database


async def _pay(monkeypatch, *, create_gift, send_success=None, refund_ok=True):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from app.handlers.callbacks import gift
    from app.handlers.common.states import GiftState

    tg = 555
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=tg, user_id=tg))
    await state.set_state(GiftState.choose_payment_method)
    await state.update_data(gift_tariff="basic", gift_period_days=30, gift_price_kopecks=19900)

    monkeypatch.setattr(gift, "check_rate_limit", lambda *a, **k: (True, None))
    monkeypatch.setattr(gift, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(database, "get_user_balance", AsyncMock(return_value=500.0))
    decrease = AsyncMock(return_value=True)
    increase = AsyncMock(return_value=refund_ok)
    monkeypatch.setattr(database, "decrease_balance", decrease)
    monkeypatch.setattr(database, "increase_balance", increase)
    monkeypatch.setattr(database, "create_gift_subscription", create_gift)
    log_pe = AsyncMock()
    monkeypatch.setattr(database, "log_payment_error", log_pe)
    monkeypatch.setattr(gift, "_send_gift_success", send_success or AsyncMock())

    alerts = []

    async def send_alert(bot, category, message, *, force=False):
        alerts.append({"text": message, "force": force})
        return True
    import app.services.admin_alerts as admin_alerts
    monkeypatch.setattr(admin_alerts, "send_alert", send_alert)

    callback = MagicMock()
    callback.from_user.id = tg
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    await gift.callback_gift_pay_balance(callback, state)
    return decrease, increase, log_pe, alerts


async def test_gift_code_not_created_refunds_and_alerts(monkeypatch):
    decrease, increase, log_pe, alerts = await _pay(
        monkeypatch, create_gift=AsyncMock(side_effect=RuntimeError("db down")))
    decrease.assert_awaited_once()
    increase.assert_awaited_once()
    assert increase.await_args.kwargs["amount"] == pytest.approx(199.0)
    assert increase.await_args.kwargs["telegram_id"] == 555
    assert [a for a in alerts if a["force"]], alerts
    assert "refunded" in alerts[0]["text"].lower()
    log_pe.assert_awaited()


async def test_refund_failure_is_alerted_as_manual(monkeypatch):
    _, increase, _, alerts = await _pay(
        monkeypatch, create_gift=AsyncMock(side_effect=RuntimeError("db down")), refund_ok=False)
    increase.assert_awaited_once()
    forced = [a for a in alerts if a["force"]]
    assert forced and "NOT refunded" in forced[0]["text"]


async def test_failure_after_gift_created_does_not_refund(monkeypatch):
    _, increase, _, alerts = await _pay(
        monkeypatch, create_gift=AsyncMock(return_value={"gift_code": "G1", "id": 1}),
        send_success=AsyncMock(side_effect=RuntimeError("telegram down")))
    increase.assert_not_awaited()


async def test_happy_path_no_refund_no_alert(monkeypatch):
    _, increase, log_pe, alerts = await _pay(
        monkeypatch, create_gift=AsyncMock(return_value={"gift_code": "G1", "id": 1}))
    increase.assert_not_awaited()
    assert not alerts
    log_pe.assert_not_awaited()
