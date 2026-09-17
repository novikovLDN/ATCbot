"""Что кнопка «Банковская карта» НЕ должна превращать в подписку.

Рекуррент списывает одну и ту же сумму каждый период. Значит покупка со
скидкой — промокод, персональная скидка, «Забрать подарок» из рассылки —
зафиксировала бы акционную цену навсегда: человек платил бы 99 ₽ вместо
199 ₽ каждый месяц, пока сам не попросит отменить. Такие покупки уходят
на прежнюю разовую оплату.

Период, которого нет в лестнице интервалов Platega, тоже уходит туда —
иначе покупатель упирается в тупик вместо оплаты.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import platega_service
from app.handlers.callbacks import payments_callbacks as pc
from app.handlers.common.states import PurchaseState


@pytest.fixture
def env(monkeypatch):
    """Хендлер подписки с замоканным всем, что ходит наружу."""
    monkeypatch.setattr(platega_service, "is_subscription_visible_to", lambda tg: True)
    create = AsyncMock(return_value={"subscription_id": "s-1", "redirect_url": "https://pay/1"})
    monkeypatch.setattr(platega_service, "create_subscription", create)
    fallback = AsyncMock()
    monkeypatch.setattr(pc, "callback_pay_card_pl", fallback)
    monkeypatch.setattr(pc, "check_rate_limit", lambda tg, key: (True, None))
    monkeypatch.setattr(pc, "resolve_user_language", AsyncMock(return_value="ru"))

    callback = MagicMock()
    callback.from_user.id = 777
    callback.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.bot.send_message = AsyncMock()
    return MagicMock(create=create, fallback=fallback, callback=callback)


def state_with(**data):
    state = MagicMock()
    state.get_state = AsyncMock(return_value=PurchaseState.choose_payment_method)
    state.set_state = AsyncMock()
    base = {"tariff_type": "basic", "period_days": 30, "final_price_kopecks": 19900}
    base.update(data)
    state.get_data = AsyncMock(return_value=base)
    return state


@pytest.mark.parametrize("discount", [
    {"discount_percent": 30},                 # персональная скидка
    {"promo_applied": True},                  # промокод
    {"offer_key": "gift1m"},                  # «Забрать подарок» из рассылки
])
async def test_a_discounted_purchase_never_becomes_a_subscription(env, discount):
    await pc.callback_pay_sbp_subscription(env.callback, state_with(**discount))

    assert not env.create.await_count, "акционная цена стала бы вечной"
    env.fallback.assert_awaited_once()


async def test_a_period_without_an_interval_falls_back(env):
    await pc.callback_pay_sbp_subscription(env.callback, state_with(period_days=45))

    env.create.assert_not_awaited()
    env.fallback.assert_awaited_once()


async def test_a_full_price_purchase_creates_the_subscription(env):
    await pc.callback_pay_sbp_subscription(env.callback, state_with(discount_percent=0))

    env.fallback.assert_not_awaited()
    env.create.assert_awaited_once()
    kwargs = env.create.await_args.kwargs
    assert (kwargs["interval"], kwargs["interval_count"]) == (3, 1)
    assert kwargs["period_days"] == 30
