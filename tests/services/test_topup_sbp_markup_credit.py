"""Owner decision 2026-09-14 (HOW_IT_WORKS P2 «SBP markup goes to the balance»,
payments_callbacks.py topup_sbp, database/subscriptions.py top-up crediting).

Top-up via SBP with SBP_MARKUP_PERCENT > 0: the user pays amount + markup, and
ONLY the amount they asked for is credited. The pending row keeps the charged
price (revenue = money paid) and carries credit_kopecks = the base amount.
The crediting itself is tested on Postgres (tests/db/test_topup_credit.py).
"""
from unittest.mock import AsyncMock, MagicMock

import config
import database


async def test_sbp_topup_with_markup_asks_to_credit_only_the_base(monkeypatch):
    from app.handlers.callbacks import payments_callbacks as pc
    import platega_service
    from app.services import sbp_router

    monkeypatch.setattr(config, "SBP_MARKUP_PERCENT", 11)
    monkeypatch.setattr(pc, "ensure_db_ready_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(pc, "check_rate_limit", lambda *a, **k: (True, None))
    monkeypatch.setattr(pc, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(sbp_router, "resolve_provider", AsyncMock(return_value="platega"))
    monkeypatch.setattr(platega_service, "is_enabled", lambda: True)
    create_tx = AsyncMock(return_value={"transaction_id": "t1", "redirect_url": "https://pay"})
    monkeypatch.setattr(platega_service, "create_transaction", create_tx)
    monkeypatch.setattr(database, "update_pending_purchase_invoice_id", AsyncMock())
    create = AsyncMock(return_value="pid-top")
    monkeypatch.setattr(pc.subscription_service, "create_balance_topup_purchase", create)

    callback = MagicMock()
    callback.from_user.id = 7
    callback.data = "topup_sbp:100"
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    await pc.callback_topup_sbp(callback)

    charged = platega_service.apply_sbp_markup(10000)      # 100 ₽ + 11 % (existing rounding)
    assert charged > 10000
    kw = create.await_args.kwargs
    assert kw["amount_kopecks"] == charged          # charged: amount + markup (revenue)
    assert kw["credit_kopecks"] == 10000            # credited: the 100 ₽ asked for
    assert create_tx.await_args.kwargs["amount_rubles"] == charged / 100


async def test_sbp_topup_without_markup_credits_the_amount(monkeypatch):
    from app.services.subscriptions import service as sub_service
    create = AsyncMock(return_value="pid")
    monkeypatch.setattr(database, "create_pending_balance_topup_purchase", create)
    await sub_service.create_balance_topup_purchase(telegram_id=7, amount_kopecks=10000, credit_kopecks=10000)
    assert create.await_args.kwargs["credit_kopecks"] == 10000
    await sub_service.create_balance_topup_purchase(telegram_id=7, amount_kopecks=10000)
    assert create.await_args.kwargs["credit_kopecks"] is None
