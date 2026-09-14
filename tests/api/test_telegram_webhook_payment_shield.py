"""HOW_IT_WORKS P1-6: a Telegram update carrying successful_payment (card or
Stars) must not be cancelled by the webhook's 25 s wait_for.

Telegram already charged the user and never resends successful_payment. Before
the fix, wait_for cancelled the handler half-way (CancelledError bypasses the
handler's `except Exception` → no forced alert). Now:
- the payment update runs as a shielded task: the timeout stops only the wait,
  the handler finishes in the background;
- the timeout sends a budgeted admin alert (processing is still running);
- if the background processing fails or is cancelled → admin alert.
Other updates keep the old behaviour (cancelled on timeout).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import config
from app.api import telegram_webhook
from app.services import admin_alerts, provisioning


def _update(*, payment: bool) -> dict:
    msg = {
        "message_id": 1, "date": 0,
        "chat": {"id": 7, "type": "private"},
        "from": {"id": 7, "is_bot": False, "first_name": "u"},
    }
    if payment:
        msg["successful_payment"] = {
            "currency": "XTR", "total_amount": 185, "invoice_payload": "purchase:p1",
            "telegram_payment_charge_id": "c1", "provider_payment_charge_id": "p1",
        }
    else:
        msg["text"] = "hi"
    return {"update_id": 42, "message": msg}


class _Req:
    def __init__(self, body):
        self._body = body
        self.headers = {}
        self.client = SimpleNamespace(host="t")

    async def json(self):
        return self._body


@pytest.fixture()
def alerts(monkeypatch):
    provisioning.reset_alert_state()
    sent = []

    async def send_alert(bot, category, message, *, force=False):
        sent.append({"text": message, "force": force})
        return True
    monkeypatch.setattr(admin_alerts, "send_alert", send_alert)
    monkeypatch.setattr(telegram_webhook, "_HANDLER_TIMEOUT", 0.05)
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "s")
    yield sent
    provisioning.reset_alert_state()


def _setup(monkeypatch, handler):
    dp = SimpleNamespace(feed_webhook_update=handler)
    monkeypatch.setattr(telegram_webhook, "_dp", dp)
    monkeypatch.setattr(telegram_webhook, "_bot", object())


async def _call(body):
    return await telegram_webhook.telegram_webhook(_Req(body), x_telegram_bot_api_secret_token="s")


async def _drain():
    for _ in range(50):
        await asyncio.sleep(0.02)
        if not telegram_webhook._payment_tasks:
            return


async def test_slow_payment_update_is_not_cancelled(monkeypatch, alerts):
    finished = asyncio.Event()

    async def handler(bot, update):
        await asyncio.sleep(0.3)          # longer than the (patched) timeout
        finished.set()
    _setup(monkeypatch, handler)

    resp = await _call(_update(payment=True))
    assert resp.status_code == 200
    await asyncio.wait_for(finished.wait(), 2)     # completed in the background
    await _drain()
    forced = [a for a in alerts if a["force"]]
    assert len(forced) == 1 and "timeout" in forced[0]["text"] and "successful_payment" in forced[0]["text"]


async def test_payment_update_failing_in_background_alerts(monkeypatch, alerts):
    async def handler(bot, update):
        await asyncio.sleep(0.2)
        raise RuntimeError("finalize blew up")
    _setup(monkeypatch, handler)

    await _call(_update(payment=True))
    await asyncio.sleep(0.4)
    await _drain()
    texts = [a["text"] for a in alerts if a["force"]]
    assert any("RuntimeError" in t and "finalize blew up" in t for t in texts), texts


async def test_payment_update_cancelled_in_background_alerts(monkeypatch, alerts):
    async def handler(bot, update):
        await asyncio.sleep(10)
    _setup(monkeypatch, handler)

    await _call(_update(payment=True))
    for task in list(telegram_webhook._payment_tasks):
        task.cancel()
    await _drain()
    texts = [a["text"] for a in alerts if a["force"]]
    assert any("cancelled" in t for t in texts), texts


async def test_fast_payment_update_sends_no_alert(monkeypatch, alerts):
    async def handler(bot, update):
        return None
    _setup(monkeypatch, handler)
    resp = await _call(_update(payment=True))
    assert resp.status_code == 200
    await _drain()
    assert not alerts


async def test_other_updates_keep_the_timeout(monkeypatch, alerts):
    cancelled = asyncio.Event()

    async def handler(bot, update):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.set()
            raise
    _setup(monkeypatch, handler)
    resp = await _call(_update(payment=False))
    assert resp.status_code == 200
    assert cancelled.is_set()
    assert not alerts
