"""Security hotfix: /webhooks/lava must never finalize anything.

lava_service.process_webhook_data did not verify the signature and could
finalize any pending purchase (incl. balance top-ups with the amount taken
from the request body). The route now only logs and alerts.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import payment_webhook


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(payment_webhook.router)
    monkeypatch.setattr(payment_webhook, "_bot", object())
    return TestClient(app)


FORGED = {"order_id": "purchase_0123456789abcdef", "status": "success", "amount": 1000000}


def test_forged_lava_webhook_is_not_processed(client):
    process = AsyncMock(side_effect=AssertionError("must not be called"))
    finalize = AsyncMock(side_effect=AssertionError("must not be called"))
    with patch("lava_service.process_webhook_data", process), \
         patch("app.services.payments.confirmation.process_confirmed_payment", finalize), \
         patch.object(payment_webhook, "_log_pe", AsyncMock()) as log_pe, \
         patch("app.services.admin_alerts.send_alert", AsyncMock(return_value=True)) as alert:
        resp = client.post("/webhooks/lava", json=FORGED)

    assert resp.status_code == 200
    assert resp.json() == {"status": "disabled"}
    process.assert_not_called()
    finalize.assert_not_called()
    log_pe.assert_awaited_once()
    assert log_pe.await_args.args[:2] == ("lava_webhook_disabled", "lava")
    alert.assert_awaited_once()
    text = alert.await_args.args[2]
    assert "purchase_0123456789abcdef" in text
    assert "НИЧЕГО НЕ ЗАЧИСЛЕНО" in text


def test_invalid_json_still_rejected_without_processing(client):
    process = AsyncMock(side_effect=AssertionError("must not be called"))
    with patch("lava_service.process_webhook_data", process), \
         patch.object(payment_webhook, "_log_pe", AsyncMock()), \
         patch("app.services.admin_alerts.send_alert", AsyncMock(return_value=True)) as alert:
        resp = client.post("/webhooks/lava", content=b"not json",
                           headers={"content-type": "application/json"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "disabled"}
    process.assert_not_called()
    alert.assert_awaited_once()


def test_no_bot_does_not_crash(client, monkeypatch):
    monkeypatch.setattr(payment_webhook, "_bot", None)
    with patch.object(payment_webhook, "_log_pe", AsyncMock()), \
         patch("app.services.admin_alerts.send_alert", AsyncMock()) as alert:
        resp = client.post("/webhooks/lava", json=FORGED)

    assert resp.status_code == 200
    assert resp.json() == {"status": "disabled"}
    alert.assert_not_called()
