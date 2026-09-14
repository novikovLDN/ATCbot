"""P2: the admin-set global discount reason (dashboard, free text up to 200
chars) was inserted unescaped into the HTML period-selection screen. A reason
like "Скидка <3" or "R&D" made Telegram reject the edit ("can't parse
entities") — the tariff period screen broke for EVERY user until the discount
was removed. Now it is HTML-escaped.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.handlers.payments import callbacks as cb
from app.services import pricing


async def test_discount_reason_is_escaped(monkeypatch):
    monkeypatch.setattr(cb, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(cb, "get_promo_session", AsyncMock(return_value=None))
    monkeypatch.setattr(cb, "validate_callback_data", lambda data: True)

    async def calculate_price(*, telegram_id, tariff, period_days, promo_code=None, **_kw):
        base = cb.config.TARIFFS[tariff][period_days]["price"] * 100
        return {"base_price_kopecks": base, "final_price_kopecks": base, "discount_percent": 0}

    monkeypatch.setattr(cb.subscription_service, "calculate_price", calculate_price)
    monkeypatch.setattr(pricing, "get_global_discount", AsyncMock(return_value={
        "global_discount_percent": 10, "discount_reason": "Скидка <3 & R&D", "discount_until_at": None,
    }))
    edit = AsyncMock()
    monkeypatch.setattr(cb, "safe_edit_text", edit)

    callback = MagicMock()
    callback.from_user.id = 1001
    callback.data = "tariff:basic"
    callback.answer = AsyncMock()
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=None)
    state.get_data = AsyncMock(return_value={})

    await cb.callback_tariff_type(callback, state)

    assert edit.await_count == 1
    text = edit.await_args.args[1]
    assert "Скидка &lt;3 &amp; R&amp;D" in text
    assert "<3" not in text
