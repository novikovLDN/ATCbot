"""TG-RT-9 (port of refactor 7559e320): a user who left the "enter top-up
amount" / "enter promo code" screen open and pays an invoice sent earlier —
the successful_payment must reach process_successful_payment, not the
state-only text handler (topup_fsm / promo_fsm are included before
payments_messages)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.handlers.common.states import PromoCodeInput, TopUpStates
from tests.handlers import _tg_dispatch as td


@pytest.fixture
def dp():
    dispatcher = td.make_dispatcher()
    yield dispatcher
    td.detach()


@pytest.mark.parametrize("state", [None, TopUpStates.waiting_for_amount, PromoCodeInput.waiting_for_promo],
                         ids=["default", "topup_amount", "promo_code"])
async def test_successful_payment_in_text_input_state_reaches_the_payment_handler(dp, monkeypatch, state):
    from app.handlers import router as root_router
    h = td.find_handler(root_router, "process_successful_payment")
    assert h is not None
    paid = AsyncMock(return_value=None)
    monkeypatch.setattr(h, "callback", paid)

    bot = td.FakeBot()
    if state is not None:
        await dp.fsm.get_context(bot=bot, chat_id=td.USER_ID, user_id=td.USER_ID).set_state(state)

    await dp.feed_update(bot, td.message_update(successful_payment=td.PAID))

    paid.assert_awaited_once()
