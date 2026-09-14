"""P1-1: every non-200 branch of payment_webhook._run_webhook reaches the admin.

Timeout, unhandled exception, service/bot missing and a transient error that was
not alerted upstream used to write only payment_errors and answer 500 — no
Telegram alert. Now each one sends a forced alert within the shared per-window
budget (provisioning alert aggregation, kind "webhook"); a retry storm beyond the
budget goes into ONE digest instead of flooding or being dropped.

Plus: the webhook's wait_for timeout must not cancel confirmation half-way —
the post-commit delivery keeps running (asyncio.shield).
"""
from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import payment_webhook
from app.services import admin_alerts, provisioning
from app.services.payments import confirmation
from app.services.payments.confirmation import TransientPaymentError

PATH, MODULE, FUNC = "/webhooks/platega", "platega_service", "process_webhook_data"


@pytest.fixture()
def alerts(monkeypatch):
    provisioning.reset_alert_state()
    sent = []

    async def send_alert(bot, category, message, *, force=False):
        sent.append({"bot": bot, "category": category, "text": message, "force": force})
        return True
    monkeypatch.setattr(admin_alerts, "send_alert", send_alert)
    monkeypatch.setattr(payment_webhook, "_log_pe", AsyncMock())
    yield sent
    provisioning.reset_alert_state()


def _client(monkeypatch, impl, *, bot=True):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    mod = importlib.import_module(MODULE)
    monkeypatch.setattr(mod, "is_enabled", lambda: True)
    monkeypatch.setattr(mod, FUNC, impl)
    monkeypatch.setattr(payment_webhook, "_bot", MagicMock(name="bot") if bot else None)
    app = FastAPI()
    app.include_router(payment_webhook.router)
    return TestClient(app)


def _raising(exc):
    async def impl(*args, **kwargs):
        raise exc
    return impl


def _alerted_transient():
    e = TransientPaymentError("already alerted upstream")
    e.alerted = True
    return e


@pytest.mark.parametrize("exc,code,branch", [
    (asyncio.TimeoutError(), 500, "timeout"),
    (RuntimeError("boom"), 500, "unhandled_exception"),
    (TransientPaymentError("wata key unavailable"), 500, "transient"),
    (ImportError("no module"), 500, "service_missing"),
    (ValueError("could not convert string to float: 'abc'"), 200, "value_error"),
])
def test_every_failure_branch_sends_a_forced_alert(monkeypatch, alerts, exc, code, branch):
    resp = _client(monkeypatch, _raising(exc)).post(PATH, json={"x": 1})

    assert resp.status_code == code
    forced = [a for a in alerts if a["force"]]
    assert len(forced) == 1, alerts
    assert "platega" in forced[0]["text"] and branch in forced[0]["text"]


def test_transient_already_alerted_upstream_is_not_alerted_twice(monkeypatch, alerts):
    resp = _client(monkeypatch, _raising(_alerted_transient())).post(PATH, json={"x": 1})

    assert resp.status_code == 500
    assert alerts == []


def test_setup_missing_alerts_through_the_telegram_bot(monkeypatch, alerts):
    from app.api import telegram_webhook
    tg_bot = MagicMock(name="tg_bot")
    monkeypatch.setattr(telegram_webhook, "_bot", tg_bot, raising=False)

    resp = _client(monkeypatch, _raising(RuntimeError("unused")), bot=False).post(PATH, json={"x": 1})

    assert resp.status_code == 500
    assert [a["bot"] for a in alerts if a["force"]] == [tg_bot]
    assert "setup_missing" in alerts[0]["text"]


async def test_retry_storm_is_budgeted_then_digested_never_dropped(monkeypatch, alerts):
    monkeypatch.setattr(payment_webhook, "_bot", MagicMock(name="bot"))

    async def timeout():
        raise asyncio.TimeoutError()
    for _ in range(20):
        resp = await payment_webhook._run_webhook("wata", timeout)
        assert resp.status_code == 500

    immediate = [a for a in alerts if a["force"]]
    assert len(immediate) == provisioning.ALERT_IMMEDIATE_PER_WINDOW
    assert provisioning.pending_alert_counts() == {"webhook": 20 - provisioning.ALERT_IMMEDIATE_PER_WINDOW}

    assert await provisioning.flush_alert_digests(MagicMock(), final=True) == 1
    digest = alerts[-1]["text"]
    assert "15" in digest and "wata" in digest and "provisioning_jobs" not in digest


async def test_webhook_timeout_does_not_cancel_post_commit_delivery(monkeypatch):
    """wait_for(25 s) cancelled confirmation mid-way: after the finalize commit
    the delivery (_send_confirmation: GB, user message) was cancelled and a
    provider retry stops at already_processed. The delivery must complete."""
    released = asyncio.Event()
    delivered = []
    db = confirmation.database
    monkeypatch.setattr(db, "get_pending_purchase_by_id", AsyncMock(return_value={
        "telegram_id": 111, "purchase_type": "subscription", "tariff": "basic", "period_days": 30}))
    monkeypatch.setattr(db, "finalize_purchase", AsyncMock(return_value={
        "success": True, "payment_id": 5, "expires_at": None}))

    async def slow_send(**kw):
        await released.wait()
        delivered.append(kw["purchase_id"])
    monkeypatch.setattr(confirmation, "_send_confirmation", slow_send)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(confirmation.process_confirmed_payment(
            provider="platega", purchase_id="p-1", amount_rubles=199.0,
            invoice_id="inv", telegram_id=111, bot=MagicMock()), timeout=0.05)
    released.set()
    for _ in range(20):
        await asyncio.sleep(0)

    assert delivered == ["p-1"]
