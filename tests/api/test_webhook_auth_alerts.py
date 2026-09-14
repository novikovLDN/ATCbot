"""HOW_IT_WORKS P1-4 + P2 «callback without purchase_id».

Wrong / empty PLATEGA_MERCHANT_ID / PLATEGA_SECRET (or a failed CryptoBot HMAC)
used to answer every PAID callback with 200 {"status": "unauthorized"}: no
alert, no payment_errors, the provider stops — payments silently lost.

Now:
- `unauthorized` → payment_errors (stage webhook_unauthorized) + forced admin
  alert within the shared "webhook" budget (flood → one digest) + HTTP 500:
  Platega retries a non-200 callback up to 3 times, 5 min apart
  (docs/providers/platega_api.md §4), so fixing the keys in time recovers it.
- `invalid` (paid callback without purchase_id / orderId) → payment_errors +
  alert, still 200 (a retry carries the same body).
- Internal details (`_detail`) never go back to the caller.
"""
from __future__ import annotations

import importlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import payment_webhook
from app.services import admin_alerts, provisioning

ROUTES = {
    "platega": ("/webhooks/platega", "platega_service", "process_webhook_data"),
    "platega_subscription": ("/webhooks/platega-subscription", "platega_service",
                             "process_subscription_webhook_data"),
    "cryptobot": ("/webhooks/cryptobot", "cryptobot_service", "process_webhook_data"),
    "wata": ("/webhooks/wata", "wata_service", "process_webhook_data"),
}


@pytest.fixture()
def alerts(monkeypatch):
    provisioning.reset_alert_state()
    sent = []

    async def send_alert(bot, category, message, *, force=False):
        sent.append({"category": category, "text": message, "force": force})
        return True
    monkeypatch.setattr(admin_alerts, "send_alert", send_alert)
    pe = AsyncMock()
    monkeypatch.setattr(payment_webhook, "_log_pe", pe)
    yield sent, pe
    provisioning.reset_alert_state()


def _client(monkeypatch, route, result):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    path, module_name, func = ROUTES[route]
    mod = importlib.import_module(module_name)

    async def impl(*args, **kwargs):
        return dict(result)
    monkeypatch.setattr(mod, "is_enabled", lambda: True)
    monkeypatch.setattr(mod, func, impl)
    monkeypatch.setattr(payment_webhook, "_bot", MagicMock(name="bot"))
    app = FastAPI()
    app.include_router(payment_webhook.router)
    return TestClient(app), path


@pytest.mark.parametrize("route", ["platega", "platega_subscription", "cryptobot"])
def test_unauthorized_is_500_with_alert_and_payment_error(monkeypatch, alerts, route):
    sent, pe = alerts
    client, path = _client(monkeypatch, route, {"status": "unauthorized", "_detail": "secret mismatch, tx=T1"})
    resp = client.post(path, json={"x": 1})

    assert resp.status_code == 500
    assert resp.json() == {"status": "unauthorized"}          # no internal detail leaked
    forced = [a for a in sent if a["force"]]
    assert len(forced) == 1
    assert "unauthorized" in forced[0]["text"] and "tx=T1" in forced[0]["text"]
    stages = [c.args[0] for c in pe.await_args_list]
    assert "webhook_unauthorized" in stages


def test_unauthorized_flood_is_budgeted_into_a_digest(monkeypatch, alerts):
    sent, _ = alerts
    client, path = _client(monkeypatch, "platega", {"status": "unauthorized", "_detail": "x"})
    for _ in range(20):
        assert client.post(path, json={"x": 1}).status_code == 500
    assert len([a for a in sent if a["force"]]) == provisioning.ALERT_IMMEDIATE_PER_WINDOW
    assert provisioning.pending_alert_counts().get("webhook") == 20 - provisioning.ALERT_IMMEDIATE_PER_WINDOW


@pytest.mark.parametrize("route", ["platega", "cryptobot", "wata"])
def test_invalid_paid_callback_alerts_but_stays_200(monkeypatch, alerts, route):
    sent, pe = alerts
    client, path = _client(monkeypatch, route, {"status": "invalid", "_detail": "no purchase_id, tx=T2"})
    resp = client.post(path, json={"x": 1})

    assert resp.status_code == 200
    assert resp.json() == {"status": "invalid"}
    forced = [a for a in sent if a["force"]]
    assert len(forced) == 1 and "tx=T2" in forced[0]["text"]
    assert "webhook_invalid" in [c.args[0] for c in pe.await_args_list]


def test_ok_result_sends_no_alert(monkeypatch, alerts):
    sent, pe = alerts
    client, path = _client(monkeypatch, "platega", {"status": "ok"})
    assert client.post(path, json={"x": 1}).status_code == 200
    assert not sent and not pe.await_args_list


# ── the services say why ─────────────────────────────────────────────


def _platega(monkeypatch, *, merchant="m-1", secret="s-1"):
    import platega_service as svc
    db = MagicMock()
    db.DB_READY = True
    monkeypatch.setattr(svc, "database", db)
    monkeypatch.setattr(svc, "PLATEGA_MERCHANT_ID", merchant)
    monkeypatch.setattr(svc, "PLATEGA_SECRET", secret)
    return svc


async def test_platega_unconfigured_credentials_are_named(monkeypatch):
    svc = _platega(monkeypatch, secret="")
    out = await svc.process_webhook_data({"x-merchantid": "m-1", "x-secret": ""},
                                         {"id": "tx-9", "status": "CONFIRMED"}, MagicMock())
    assert out["status"] == "unauthorized"
    assert "not configured" in out["_detail"] and "tx-9" in out["_detail"]


async def test_platega_wrong_secret_detail_has_the_transaction(monkeypatch):
    svc = _platega(monkeypatch)
    out = await svc.process_webhook_data({"x-merchantid": "m-1", "x-secret": "bad"},
                                         {"id": "tx-8", "status": "CONFIRMED"}, MagicMock())
    assert out["status"] == "unauthorized" and "tx-8" in out["_detail"]


async def test_platega_paid_callback_without_purchase_id_is_invalid_with_detail(monkeypatch):
    svc = _platega(monkeypatch)
    out = await svc.process_webhook_data({"x-merchantid": "m-1", "x-secret": "s-1"},
                                         {"id": "tx-7", "status": "CONFIRMED", "payload": "{}"}, MagicMock())
    assert out["status"] == "invalid" and "tx-7" in out["_detail"]


async def test_cryptobot_bad_signature_detail(monkeypatch):
    import cryptobot_service as svc
    db = MagicMock()
    db.DB_READY = True
    monkeypatch.setattr(svc, "database", db)
    monkeypatch.setattr(svc, "CRYPTOBOT_API_TOKEN", "tok")
    body = {"update_type": "invoice_paid", "payload": {"invoice_id": 55, "status": "paid"}}
    out = await svc.process_webhook_data({"crypto-pay-api-signature": "bad"}, json.dumps(body).encode(),
                                         body, MagicMock())
    assert out["status"] == "unauthorized" and "55" in out["_detail"]
    out = await svc.process_webhook_data({}, b"{}", body, MagicMock())
    assert out["status"] == "unauthorized" and "missing" in out["_detail"]
