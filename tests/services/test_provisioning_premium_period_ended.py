"""Remnawave F7: PATCH /api/users with an expireAt in the past → HTTP 400
(update-user.command.ts:32-39).

A provisioning job processed after its paid period already ended (panel down
for days, a job re-planned by hand) used to PATCH a past expireAt, get 400 →
ProvisioningTransient → retry with backoff until the 24 h dead alert, all
pointless: there is nothing left to extend and access is not lost (the panel
entity is already expired, as it should be).

Owner decision: detect it, do not PATCH, do not retry; the job finishes (the
bypass GB of the job are still delivered) with ONE forced admin alert naming
the tg id, the purchase key and the paid-until date.
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
TG = 7373
BOT = object()
NOW = datetime.now(timezone.utc).replace(microsecond=0)
PANEL_EXPIRE = NOW - timedelta(days=10)
PAID_UNTIL = NOW - timedelta(days=1)       # the paid period is already over
BASIC = for_purchase("basic", 30)          # premium + 10 GB


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


async def test_late_job_finishes_without_patch_with_one_forced_alert(panel, jobs, db, alerts):
    panel.seed_premium(TG, PANEL_EXPIRE)
    job_id = await provisioning.enqueue(
        FakeConn(), key="purchase:pid-late", telegram_id=TG, ent=BASIC,
        premium_until=PAID_UNTIL, source="test",
    )

    assert await provisioning.run_now(job_id, bot=BOT) is True

    row = jobs.job(job_id)
    assert row["status"] == "done", "no pointless retries"
    assert row["attempts"] == 1
    assert panel.premium[TG]["expireAt"] == PANEL_EXPIRE, "no PATCH with a past expireAt"
    assert panel.bypass_limit(TG) == 10 * GIB, "the paid GB are still delivered"

    assert alerts.await_count == 1 and alerts.await_args.kwargs.get("force") is True
    text = alerts.await_args.args[2]
    assert f"tg:{TG}" in text and "purchase:pid-late" in text
    assert PAID_UNTIL.strftime("%Y-%m-%d") in text

    # terminal: later ticks do nothing and do not alert again
    assert await provisioning.run_now(job_id, bot=BOT) is False
    assert jobs.job(job_id)["attempts"] == 1 and alerts.await_count == 1


async def test_future_paid_until_still_patches(panel, jobs, db, alerts):
    """Control: an ordinary late-but-still-paid job keeps the old behaviour."""
    panel.seed_premium(TG, PANEL_EXPIRE)
    until = NOW + timedelta(days=5)
    job_id = await provisioning.enqueue(
        FakeConn(), key="purchase:pid-ok", telegram_id=TG, ent=BASIC,
        premium_until=until, source="test",
    )
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert panel.premium[TG]["expireAt"] >= until
    assert alerts.await_count == 0
