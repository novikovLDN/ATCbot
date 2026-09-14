"""P1-5: premium entity DISABLED in the panel, with a correct expireAt.

Before: _ensure_premium_expire saw expireAt >= target → no PATCH; the delivery
check wants ACTIVE → DeliveryMismatch; every retry did the same → 6 attempts,
then dead with a misleading «paid, access not delivered» alert.

Owner decision: never auto-enable (an admin may have disabled the user on
purpose). The job detects it on the FIRST attempt, still delivers the bypass GB,
goes to a terminal state without burning retries and sends ONE forced alert
«paid, but premium entity is DISABLED in the panel — enable manually if not
banned» with the tg id and the purchase key.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.api import payment_webhook
from app.services import admin_alerts, provisioning, sub_aggregator
from app.services.tariffs import for_purchase
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeConn, FakeDB, FakeJobs

GIB = 1024 ** 3
TG = 5252
BOT = object()
NOW = datetime.now(timezone.utc).replace(microsecond=0)
PAID_UNTIL = NOW + timedelta(days=30)
BASIC = for_purchase("basic", 30)        # premium + 10 GB


@pytest.fixture
def panel(monkeypatch):
    return FakePanel().install(monkeypatch)


@pytest.fixture
def jobs(monkeypatch, panel):
    return FakeJobs(panel).install(monkeypatch)


@pytest.fixture
def db(monkeypatch):
    return FakeDB().install(monkeypatch)


@pytest.fixture
def alerts(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", mock)
    return mock


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.setattr(sub_aggregator, "invalidate_bg", lambda tg: None)
    monkeypatch.setattr(payment_webhook, "_bot", None)
    provisioning.reset_alert_state()
    yield
    provisioning.reset_alert_state()


async def test_disabled_premium_with_correct_date_is_terminal_on_first_attempt(panel, jobs, db, alerts):
    panel.seed_premium(TG, PAID_UNTIL + timedelta(days=1))   # date already covers the purchase
    panel.premium[TG]["status"] = "DISABLED"                  # disabled by an admin
    job_id = await provisioning.enqueue(
        FakeConn(), key="purchase:pid-disabled", telegram_id=TG, ent=BASIC,
        premium_until=PAID_UNTIL, source="test",
    )

    assert await provisioning.run_now(job_id, bot=BOT) is False

    row = jobs.job(job_id)
    assert row["status"] == "dead", "no pointless retries"
    assert row["attempts"] == 1
    assert panel.premium[TG]["status"] == "DISABLED", "never auto-enabled"
    assert panel.bypass_limit(TG) == 10 * GIB, "the paid GB are still delivered"

    assert alerts.await_count == 1 and alerts.await_args.kwargs.get("force") is True
    text = alerts.await_args.args[2]
    assert "DISABLED" in text and "enable" in text.lower()
    assert f"tg:{TG}" in text and "purchase:pid-disabled" in text

    # nothing more happens on later ticks: the job is terminal
    assert await provisioning.run_now(job_id, bot=BOT) is False
    assert jobs.job(job_id)["attempts"] == 1 and alerts.await_count == 1
