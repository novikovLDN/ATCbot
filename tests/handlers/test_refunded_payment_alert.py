"""TG-RT-3 (port of refactor 54069e47): a Telegram refund (refunded_payment
service message, e.g. a Stars refund) must be logged to payment_errors and
raise a FORCED admin alert; access is not revoked automatically. Before the
fix no handler existed and the refund went unnoticed."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.handlers.common.states import TopUpStates
from tests.handlers import _tg_dispatch as td


@pytest.fixture
def dp():
    dispatcher = td.make_dispatcher()
    yield dispatcher
    td.detach()


@pytest.mark.parametrize("state", [None, TopUpStates.waiting_for_amount], ids=["default", "topup_amount"])
async def test_refunded_payment_logs_and_alerts_admin(dp, monkeypatch, state):
    import database
    from app.services import admin_alerts
    log_err = AsyncMock(return_value=1)
    alert = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "log_payment_error", log_err)
    monkeypatch.setattr(admin_alerts, "send_alert", alert)

    bot = td.FakeBot()
    if state is not None:
        await dp.fsm.get_context(bot=bot, chat_id=td.USER_ID, user_id=td.USER_ID).set_state(state)

    await dp.feed_update(bot, td.message_update(refunded_payment=td.REFUNDED))

    log_err.assert_awaited_once()
    assert log_err.await_args.kwargs["stage"] == "telegram_refund"
    assert log_err.await_args.kwargs["telegram_id"] == td.USER_ID
    alert.assert_awaited_once()
    assert alert.await_args.kwargs.get("force") is True
    text = alert.await_args.args[2]
    assert "REFUND" in text and str(td.USER_ID) in text and "100 XTR" in text


async def test_refund_handler_never_raises_when_logging_and_alert_fail(dp, monkeypatch):
    import database
    from app.services import admin_alerts
    monkeypatch.setattr(database, "log_payment_error", AsyncMock(side_effect=RuntimeError("db")))
    monkeypatch.setattr(admin_alerts, "send_alert", AsyncMock(side_effect=RuntimeError("tg")))
    await dp.feed_update(td.FakeBot(), td.message_update(refunded_payment=td.REFUNDED))
