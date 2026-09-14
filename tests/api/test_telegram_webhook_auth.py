"""Telegram webhook authentication (app/api/telegram_webhook.py).

Risk closed: a forged update must never reach the dispatcher. A fake
`successful_payment` posted to /telegram/webhook would otherwise finalize a
pending purchase — free access without money. The secret check is the only
thing between the internet and the payment handlers; until now only the happy
path (correct secret) was exercised (coverage 09: lines 170-205 unrun).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import config
from app.api import telegram_webhook as tw

SECRET = "right-secret"


class _Req:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = headers or {}
        self.client = SimpleNamespace(host="203.0.113.7")

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _successful_payment_update() -> dict:
    return {
        "update_id": 7001,
        "message": {
            "message_id": 1, "date": 0,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "x"},
            "successful_payment": {
                "currency": "RUB", "total_amount": 19900, "invoice_payload": "purchase:p_forged",
                "telegram_payment_charge_id": "c1", "provider_payment_charge_id": "p1",
            },
        },
    }


def _text_update() -> dict:
    return {
        "update_id": 7002,
        "message": {
            "message_id": 2, "date": 0,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "x"},
            "text": "/start",
        },
    }


@pytest.fixture
def fed(monkeypatch):
    """Every update that reaches the dispatcher."""
    calls = []

    async def feed(bot, update):
        calls.append(update)

    monkeypatch.setattr(tw, "_dp", SimpleNamespace(feed_webhook_update=feed))
    monkeypatch.setattr(tw, "_bot", object())
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    return calls


async def _drain():
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not tw._payment_tasks:
            return


async def _post(body, token, headers=None):
    return await tw.telegram_webhook(_Req(body, headers), x_telegram_bot_api_secret_token=token)


async def test_no_secret_configured_refuses_everything(fed, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    r = await _post(_successful_payment_update(), "")
    assert r.status_code == 503
    await _drain()
    assert fed == []


@pytest.mark.parametrize("token", [None, "", "wrong", SECRET + " ", SECRET.upper(), SECRET[:-1]])
async def test_forged_payment_with_bad_secret_never_reaches_handlers(fed, token):
    before = tw.last_webhook_update_at
    r = await _post(_successful_payment_update(), token)
    assert r.status_code == 403
    await _drain()
    assert fed == [], "a forged successful_payment was dispatched"
    assert tw.last_webhook_update_at == before, "an unauthenticated request counted as liveness"


async def test_oversized_body_is_refused_before_parsing(fed):
    r = await _post(_text_update(), SECRET, headers={"content-length": str(2 * 1024 * 1024)})
    assert r.status_code == 413
    assert fed == []


async def test_invalid_content_length_is_refused(fed):
    r = await _post(_text_update(), SECRET, headers={"content-length": "abc"})
    assert r.status_code == 400
    assert fed == []


async def test_correct_secret_dispatches_once(fed):
    r = await _post(_text_update(), SECRET)
    assert r.status_code == 200
    assert len(fed) == 1 and fed[0].update_id == 7002


async def test_correct_secret_payment_update_is_dispatched(fed):
    r = await _post(_successful_payment_update(), SECRET)
    assert r.status_code == 200
    await _drain()
    assert len(fed) == 1 and fed[0].message.successful_payment is not None


async def test_garbage_body_with_correct_secret_is_acknowledged_not_dispatched(fed):
    # 200 so Telegram does not retry a malformed update forever
    r = await _post(ValueError("not json"), SECRET)
    assert r.status_code == 200
    assert fed == []
