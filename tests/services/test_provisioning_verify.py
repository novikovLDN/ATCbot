"""Post-apply delivery verification in the provisioning outbox.

Owner rule (docs/audit/02_payment_core_plan.md «Контроль доставки»): "bought a
month, the panel still shows 20 October" must be caught. After the premium /
bypass steps of a job succeed, apply() re-reads the panel; a panel that does
not show the job's target is a DELIVERY_MISMATCH: the job is NOT done, it
retries (the never-shorten / CAS logic makes a retry safe), the first mismatch
is a forced admin alert, a mismatch that persists goes dead (+ forced alert).
An unavailable panel during the re-read is an ordinary transient failure.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.api import payment_webhook
from app.services import admin_alerts, provisioning, remnawave_api, sub_aggregator
from app.services.provisioning import DeliveryMismatch, ProvisioningTransient
from app.services.tariffs import for_grant, for_pack, for_purchase
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeConn, FakeDB, FakeJobs

GIB = 1024 ** 3
TG = 5151
BOT = object()
NOW = datetime.now(timezone.utc).replace(microsecond=0)
OCT20 = NOW + timedelta(days=7)          # what the panel shows before the purchase
NOV20 = OCT20 + timedelta(days=30)       # what the user paid for
TAG = provisioning.MISMATCH_TAG

GRANT = for_grant("basic", 30)           # premium only, 0 GB
BASIC = for_purchase("basic", 30)        # premium + 10 GB
PACK = for_pack(15)                      # +15 GB, premium untouched


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


async def enqueue(key, ent, until=NOV20):
    return await provisioning.enqueue(
        FakeConn(), key=key, telegram_id=TG, ent=ent, premium_until=until, source="test",
    )


def texts(alerts):
    return [c.args[2] for c in alerts.await_args_list]


def forces(alerts):
    return [c.kwargs.get("force") for c in alerts.await_args_list]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── happy path ─────────────────────────────────────────────────────────

async def test_delivered_job_is_re_read_and_done_without_alert(panel, jobs, db, alerts):
    panel.seed_premium(TG, OCT20)
    job_id = await enqueue("buy:1", BASIC)

    assert await provisioning.run_now(job_id, bot=BOT) is True

    assert jobs.job(job_id)["status"] == "done"
    assert panel.premium_expire(TG) == NOV20 and panel.bypass_limit(TG) == 10 * GIB
    # the last panel calls are the verification re-reads, after every PATCH/create
    tail = [c[0] for c in panel.calls][-2:]
    assert tail == ["get_premium_state", "get_bypass_state"]
    alerts.assert_not_awaited()


# ── premium: the panel silently ignores the PATCH ──────────────────────

async def test_ignored_premium_patch_is_mismatch_retry_and_forced_alert(panel, jobs, db, alerts):
    panel.seed_premium(TG, OCT20)
    job_id = await enqueue("buy:oct20", GRANT)
    panel.mode = "ignore_patch"

    assert await provisioning.run_now(job_id, bot=BOT) is False

    row = jobs.job(job_id)
    assert row["status"] == "pending", "a mismatch is never marked done"
    assert TAG in row["last_error"]
    assert panel.premium_expire(TG) == OCT20
    assert forces(alerts) == [True]
    text = texts(alerts)[0]
    assert text.startswith(TAG)
    for part in (f"job_id: {job_id}", "key: buy:oct20", f"user: tg:{TG}",
                 f"expected expireAt >= {iso(NOV20)}", f"panel expireAt={iso(OCT20)}",
                 "status=ACTIVE"):
        assert part in text, part
    assert db.payment_errors[-1]["error_code"] == "retry"


async def test_second_mismatch_is_not_forced_then_recovery_applies_once(panel, jobs, db, alerts):
    panel.seed_premium(TG, OCT20)
    job_id = await enqueue("buy:2", GRANT)
    panel.mode = "ignore_patch"
    await provisioning.run_now(job_id, bot=BOT)
    jobs.make_due(job_id)
    await provisioning.run_now(job_id, bot=BOT)

    assert forces(alerts) == [True, False], "only the FIRST mismatch is forced"

    panel.mode = "ok"
    jobs.make_due(job_id)
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert jobs.job(job_id)["status"] == "done"
    assert panel.premium_expire(TG) == NOV20


async def test_mismatch_after_an_outage_is_forced_again(panel, jobs, db, alerts):
    """'First mismatch' = the previous failure of this job was not a mismatch."""
    panel.seed_premium(TG, OCT20)
    job_id = await enqueue("buy:3", GRANT)
    panel.mode = "down"
    await provisioning.run_now(job_id, bot=BOT)          # attempt 1: first failure (forced)
    panel.mode = "ignore_patch"
    jobs.make_due(job_id)
    await provisioning.run_now(job_id, bot=BOT)          # attempt 2: first mismatch (forced)

    assert forces(alerts) == [True, True]
    assert TAG not in texts(alerts)[0] and texts(alerts)[1].startswith(TAG)


async def test_disabled_premium_with_enough_days_is_terminal_not_a_mismatch(panel, jobs, db, alerts):
    """P1-5 (owner decision): never auto-enabled, no pointless mismatch retries —
    dead on the first attempt with one PREMIUM_DISABLED alert
    (tests/services/test_provisioning_premium_disabled.py)."""
    panel.seed_premium(TG, NOV20 + timedelta(days=100))
    panel.premium[TG]["status"] = "DISABLED"
    job_id = await enqueue("buy:4", GRANT)

    assert await provisioning.run_now(job_id, bot=BOT) is False
    row = jobs.job(job_id)
    assert row["status"] == "dead" and "DISABLED" in row["last_error"]
    assert texts(alerts)[0].startswith(provisioning.PREMIUM_DISABLED_TAG)
    assert panel.premium[TG]["status"] == "DISABLED"


async def test_absent_premium_on_re_read_is_a_mismatch(monkeypatch, panel, jobs, db, alerts):
    panel.seed_premium(TG, OCT20)
    job_id = await enqueue("buy:5", GRANT)
    real = panel.get_premium_state
    calls = {"n": 0}

    async def second_read_absent(tg):
        calls["n"] += 1
        return ("absent", None) if calls["n"] == 2 else await real(tg)
    monkeypatch.setattr(remnawave_api, "get_premium_state", second_read_absent)

    assert await provisioning.run_now(job_id, bot=BOT) is False
    assert "entity absent" in jobs.job(job_id)["last_error"]


# ── bypass: the panel silently ignores the PATCH ───────────────────────

async def test_ignored_bypass_patch_is_mismatch_and_retry_credits_exactly_once(panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    job_id = await enqueue("pack:1", PACK, until=None)
    panel.mode = "ignore_patch"

    assert await provisioning.run_now(job_id, bot=BOT) is False

    row = jobs.job(job_id)
    assert row["status"] == "pending" and TAG in row["last_error"]
    text = texts(alerts)[0]
    assert text.startswith(TAG) and forces(alerts) == [True]
    assert f"expected trafficLimitBytes={18 * GIB} (18 GB)" in text
    assert f"panel trafficLimitBytes={3 * GIB} (3 GB)" in text
    assert "plan 3 → 18 GB" in text

    panel.mode = "ok"
    jobs.make_due(job_id)
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert panel.bypass_limit(TG) == 18 * GIB, "CAS retry: +15 exactly once"


async def test_bypass_limit_above_target_is_a_mismatch(monkeypatch, panel, jobs, db, alerts):
    """trafficLimitBytes must equal the planned target (not merely >=)."""
    panel.seed_bypass(TG, 3 * GIB)
    job_id = await enqueue("pack:2", PACK, until=None)
    real_update = panel.update_user

    async def update_then_external_add(user_id, **fields):
        res = await real_update(user_id, **fields)
        panel.bypass[TG]["trafficLimitBytes"] += GIB    # someone else writes in between
        return res
    monkeypatch.setattr(remnawave_api, "update_user", update_then_external_add)

    assert await provisioning.run_now(job_id, bot=BOT) is False
    assert f"panel trafficLimitBytes={19 * GIB}" in jobs.job(job_id)["last_error"]


# ── unavailable panel during the re-read ───────────────────────────────

async def test_unavailable_panel_during_verification_is_transient_not_mismatch(monkeypatch, panel, jobs, db, alerts):
    panel.seed_premium(TG, OCT20)
    job_id = await enqueue("buy:6", GRANT)
    real = panel.get_premium_state
    calls = {"n": 0}

    async def second_read_down(tg):
        calls["n"] += 1
        return ("unavailable", None) if calls["n"] == 2 else await real(tg)
    monkeypatch.setattr(remnawave_api, "get_premium_state", second_read_down)

    assert await provisioning.run_now(job_id, bot=BOT) is False

    row = jobs.job(job_id)
    assert row["status"] == "pending" and TAG not in row["last_error"]
    assert "panel unavailable" in row["last_error"]
    assert forces(alerts) == [True] and TAG not in texts(alerts)[0]
    assert panel.premium_expire(TG) == NOV20      # the PATCH itself did apply


async def test_verify_raises_transient_subclass():
    assert issubclass(DeliveryMismatch, ProvisioningTransient)


# ── persisting mismatch → dead ─────────────────────────────────────────

async def test_mismatch_persisting_for_n_attempts_goes_dead_with_forced_alert(panel, jobs, db, alerts):
    panel.seed_premium(TG, OCT20)
    job_id = await enqueue("buy:7", GRANT)
    panel.mode = "ignore_patch"

    for _ in range(provisioning.MISMATCH_DEAD_AFTER_ATTEMPTS):
        jobs.make_due(job_id)
        await provisioning.run_now(job_id, bot=BOT)

    row = jobs.job(job_id)
    assert row["status"] == "dead" and row["attempts"] == provisioning.MISMATCH_DEAD_AFTER_ATTEMPTS
    assert forces(alerts)[0] is True and forces(alerts)[-1] is True
    assert all(f is False for f in forces(alerts)[1:-1])
    last = texts(alerts)[-1]
    assert "DEAD (delivery mismatch" in last and TAG in last
    assert db.payment_errors[-1]["error_code"] == "dead"


# ── flood: mismatches share the alert budget / digest ──────────────────

async def test_mismatch_flood_is_budgeted_and_digested(monkeypatch, panel, jobs, db, alerts):
    n = provisioning.ALERT_IMMEDIATE_PER_WINDOW + 3
    ids = []
    for i in range(n):
        tg = TG + 1 + i
        panel.seed_premium(tg, OCT20)
        ids.append(await provisioning.enqueue(
            FakeConn(), key=f"flood:{i}", telegram_id=tg, ent=GRANT, premium_until=NOV20, source="test",
        ))
    panel.mode = "ignore_patch"
    for job_id in ids:
        await provisioning.run_now(job_id, bot=BOT)

    assert forces(alerts) == [True] * provisioning.ALERT_IMMEDIATE_PER_WINDOW
    assert provisioning.pending_alert_counts() == {"mismatch": 3}

    assert await provisioning.flush_alert_digests(BOT) == 1
    digest = texts(alerts)[-1]
    assert digest.startswith("Provisioning DIGEST: DELIVERY_MISMATCH")
    assert "count: 3 job(s)" in digest
    assert "status IN ('pending')" in digest
