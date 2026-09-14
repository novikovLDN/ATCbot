"""HOW_IT_WORKS P2: PREMIUM_PERIOD_ENDED alert could be lost, and the
provisioning alert digest was lost on every deploy.

- provisioning._alert_period_ended only logged a failed send (no digest, no
  payment_errors). Now: payment_errors row (stage premium_period_ended) + the
  shared budget/digest (kind "period_ended"): a failed send or a missing bot
  keeps it for the next digest.
- The digests were flushed only when the worker task was cancelled, and a
  Railway SIGTERM never got there. The FastAPI app now flushes the provisioning
  digests and the held admin_alerts on shutdown (uvicorn runs shutdown
  handlers on SIGTERM).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
from app.services import admin_alerts, provisioning


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    provisioning.reset_alert_state()
    admin_alerts.reset_state()
    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", 1)
    monkeypatch.setattr(admin_alerts, "_RETRY_DELAY", 0)
    yield
    provisioning.reset_alert_state()
    admin_alerts.reset_state()


def _job():
    return {
        "id": 9, "telegram_id": 42, "idempotency_key": "purchase:p9", "source": "webhook",
        "tariff_key": "basic", "bypass_add_bytes": 10 * 1024 ** 3,
        provisioning.PERIOD_ENDED_KEY: datetime.now(timezone.utc) - timedelta(hours=1),
    }


async def test_period_ended_writes_payment_error_and_survives_a_failed_send(monkeypatch):
    log_pe = AsyncMock()
    monkeypatch.setattr(database, "log_payment_error", log_pe)
    monkeypatch.setattr(provisioning.admin_alerts, "send_alert", AsyncMock(return_value=False))
    await provisioning._alert_period_ended(_job(), MagicMock())
    assert log_pe.await_args.kwargs["stage"] == "premium_period_ended"
    assert provisioning.pending_alert_counts().get("period_ended") == 1


async def test_period_ended_without_a_bot_is_kept_for_the_digest(monkeypatch):
    monkeypatch.setattr(database, "log_payment_error", AsyncMock())
    from app.api import payment_webhook
    monkeypatch.setattr(payment_webhook, "_bot", None)
    await provisioning._alert_period_ended(_job(), None)
    assert provisioning.pending_alert_counts().get("period_ended") == 1


async def test_shutdown_flushes_provisioning_and_admin_alert_digests(monkeypatch):
    import app.api as api
    from app.api import payment_webhook

    sent = []

    class Bot:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)
    bot = Bot()
    monkeypatch.setattr(payment_webhook, "_bot", bot)

    # a provisioning digest waiting for the next worker tick
    provisioning._buffer("dead", {"id": 1, "telegram_id": 5, "idempotency_key": "purchase:x"}, "panel down")
    # an admin alert held by the category cooldown
    monkeypatch.setitem(admin_alerts._ALERT_COOLDOWNS, "worker", 3600)
    await admin_alerts.send_alert(bot, "worker", "first")
    await admin_alerts.send_alert(bot, "worker", "held: worker X crashed")
    assert provisioning.pending_alert_counts() and admin_alerts.pending_digest_counts()

    assert api.flush_alerts_on_shutdown in api.app.router.on_shutdown
    await api.flush_alerts_on_shutdown()

    assert provisioning.pending_alert_counts() == {}
    assert admin_alerts.pending_digest_counts() == {}
    assert any("panel down" in t for t in sent)
    assert any("worker X crashed" in t for t in sent)
