"""P1: the "change tariff" screen crashed for every English-speaking user.

en `buy.button_price` / `buy.button_price_discount` need `{gb}` ("… · {gb} GB");
callback_switch_tariff built the no-badge period buttons (30 / 180 days)
without `gb` → KeyError('gb') inside get_text → the handler died after
callback.answer(), the screen never rendered. ru texts have no `{gb}`, so only
en users were hit.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.handlers.payments import callbacks as cb


@pytest.mark.parametrize("language", ["en", "ru"])
@pytest.mark.parametrize("discount", [0, 20])
async def test_switch_tariff_renders_for_every_language(monkeypatch, language, discount):
    monkeypatch.setattr(cb, "resolve_user_language", AsyncMock(return_value=language))
    monkeypatch.setattr(cb, "get_promo_session", AsyncMock(return_value=None))

    async def calculate_price(*, telegram_id, tariff, period_days, promo_code=None, **_kw):
        base = cb.config.TARIFFS[tariff][period_days]["price"] * 100
        final = base * (100 - discount) // 100
        return {"base_price_kopecks": base, "final_price_kopecks": final, "discount_percent": discount}

    monkeypatch.setattr(cb.subscription_service, "calculate_price", calculate_price)
    edit = AsyncMock()
    monkeypatch.setattr(cb, "safe_edit_text", edit)

    callback = MagicMock()
    callback.from_user.id = 1001
    callback.data = "switch_tariff:basic"
    callback.answer = AsyncMock()
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=None)

    await cb.callback_switch_tariff(callback, state)

    assert edit.await_count == 1, "the screen must render"
    kb = edit.await_args.kwargs["reply_markup"]
    period_buttons = [b for row in kb.inline_keyboard for b in row
                      if (b.callback_data or "").startswith("period:basic:")]
    assert len(period_buttons) == len(cb.config.TARIFFS["basic"])
