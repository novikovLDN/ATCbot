"""«Купить трафик» → «Резерв» (Platega): the payment is created WITHOUT a fixed
method (POST /v2/transaction/process, the payer picks card / SBP / … on
Platega's page; the link comes back in `url`). Owner request 2026-09-15: the
reserve button was Platega SBP only. Every other Platega button keeps its
fixed method on /transaction/process.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
import platega_service
from app.handlers import traffic
from app.i18n import get_text
from tests.fakes.providers_http import FakeProviders


def _platega_calls(fake):
    return [(url, body) for method, url, body in fake.requests if "platega" in url and method == "POST"]


async def test_any_method_goes_to_v2_without_payment_method(monkeypatch):
    fake = FakeProviders().install(monkeypatch)

    tx = await platega_service.create_transaction(
        amount_rubles=99.9, description="t", purchase_id="p-1", telegram_id=7,
        method=platega_service.PAYMENT_METHOD_ANY,
    )

    [(url, body)] = _platega_calls(fake)
    assert url.endswith("/v2/transaction/process")
    assert "paymentMethod" not in body
    assert body["paymentDetails"] == {"amount": 99.9, "currency": "RUB"}
    assert body["metadata"] == {"userId": "7"}
    assert tx["redirect_url"].startswith("https://pay.platega.test/")   # read from `url`


async def test_default_method_is_unchanged(monkeypatch):
    fake = FakeProviders().install(monkeypatch)

    tx = await platega_service.create_transaction(amount_rubles=10, description="t", purchase_id="p-2")

    [(url, body)] = _platega_calls(fake)
    assert url.endswith("/transaction/process") and "/v2/" not in url
    assert body["paymentMethod"] == platega_service.PAYMENT_METHOD_SBP
    assert tx["redirect_url"].startswith("https://pay.platega.test/")


@pytest.mark.parametrize("handler, data", [
    ("callback_traffic_pay_sbp", "traffic_pay_sbp:15"),
    ("callback_bypass_pay_sbp", "bypass_pay_sbp:15"),
])
async def test_reserve_buttons_create_a_payer_choice_payment(monkeypatch, handler, data):
    create_tx = AsyncMock(return_value={"transaction_id": "tx-1", "redirect_url": "https://pay.platega.test/tx-1"})
    monkeypatch.setattr(platega_service, "create_transaction", create_tx)
    monkeypatch.setattr(platega_service, "is_enabled", lambda: True)
    monkeypatch.setattr(traffic, "ensure_db_ready_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(traffic, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(traffic, "_bypass_price", AsyncMock(return_value=(100, {})), raising=False)
    monkeypatch.setattr(traffic, "_auto_delete_invoice_msg", AsyncMock())
    monkeypatch.setattr(database, "get_user_traffic_discount", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(database, "create_pending_purchase", AsyncMock(return_value="p-9"), raising=False)
    monkeypatch.setattr(database, "update_pending_purchase_invoice_id", AsyncMock(), raising=False)
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = 5
    cb.answer = AsyncMock()
    cb.message.delete = AsyncMock()
    cb.message.answer = AsyncMock(return_value=MagicMock(message_id=11))

    await getattr(traffic, handler)(cb)

    assert create_tx.await_args.kwargs["method"] is platega_service.PAYMENT_METHOD_ANY
    text = cb.message.answer.await_args.args[0]
    kb = cb.message.answer.await_args.kwargs["reply_markup"]
    assert text.startswith(get_text("ru", "payment.platega_any_waiting").split("{", 1)[0])
    assert kb.inline_keyboard[0][0].url == "https://pay.platega.test/tx-1"
    assert "СБП" not in text and "СБП" not in kb.inline_keyboard[0][0].text
