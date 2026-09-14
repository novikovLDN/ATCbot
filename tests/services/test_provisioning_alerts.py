"""Provisioning admin-alert aggregation: per-window budget + digest.

docs/audit/02_payment_core_plan.md "Статус выкатки" TODO P1; owner rule
(docs/audit/SCOPE.md): every failed transaction must reach the admin — the
digest carries exact counts, payment_errors keeps one row per failure.

Failures are fed through provisioning._record_failure (the real path of
run_now / the worker) with the outbox marks mocked, the clock is a fake
monotonic, admin_alerts.send_alert is mocked unless a test needs the real
header/length handling.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

import database.provisioning_jobs as pj
from app.api import payment_webhook
from app.services import admin_alerts, provisioning, sub_aggregator
from app.services.provisioning import ProvisioningPermanent, ProvisioningTransient
from app.services.tariffs import for_purchase
from app.workers import provisioning_worker as worker
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeConn, FakeDB, FakeJobs, utcnow

GIB = 1024 ** 3
TG0 = 700_000
BOT = object()
N = provisioning.ALERT_IMMEDIATE_PER_WINDOW
W = provisioning.ALERT_WINDOW_S
DIGEST = "Provisioning DIGEST"
PANEL_DOWN = "panel unavailable (bypass read)"


class Clock:
    def __init__(self):
        self.t = 10_000.0

    def __call__(self):
        return self.t


# ── fixtures / helpers ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.setattr(sub_aggregator, "invalidate_bg", lambda tg: None)
    monkeypatch.setattr(payment_webhook, "_bot", None)
    provisioning.reset_alert_state()
    yield
    provisioning.reset_alert_state()


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(provisioning, "_clock", c)
    return c


@pytest.fixture
def db(monkeypatch):
    return FakeDB().install(monkeypatch)


@pytest.fixture
def marks(monkeypatch):
    monkeypatch.setattr(pj, "mark_retry", AsyncMock(return_value=True))
    monkeypatch.setattr(pj, "mark_dead", AsyncMock(return_value=True))


@pytest.fixture
def alerts(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", mock)
    return mock


def job(i, *, attempts=1, age=timedelta(0), key=None):
    return {
        "id": i, "idempotency_key": key or f"bonus:900-77:{TG0 + i}", "telegram_id": TG0 + i,
        "source": "admin_bonus", "tariff_key": "basic", "premium_until": None,
        "bypass_add_bytes": 5 * GIB, "bypass_base_bytes": None, "bypass_target_bytes": None,
        "attempts": attempts, "created_at": utcnow() - age, "context": {},
    }


async def first_failure(i, reason=PANEL_DOWN, bot=BOT, **kw):
    await provisioning._record_failure(job(i, **kw), ProvisioningTransient(reason), permanent=False, bot=bot)


async def dead(i, bot=BOT):
    await provisioning._record_failure(
        job(i, attempts=5, age=timedelta(hours=25)), ProvisioningTransient(PANEL_DOWN),
        permanent=False, bot=bot,
    )


async def conflict(i, bot=BOT):
    err = ProvisioningPermanent(f"conflict: base={3 * GIB}, target={8 * GIB}, current={4 * GIB}")
    await provisioning._record_failure(job(i), err, permanent=True, bot=bot)


def texts(alerts):
    return [c.args[2] for c in alerts.await_args_list]


def digests(alerts):
    return [t for t in texts(alerts) if t.startswith(DIGEST)]


def per_job(alerts):
    return [c for c in alerts.await_args_list if not c.args[2].startswith(DIGEST)]


# ── low rate: unchanged per-job behaviour ──────────────────────────────

async def test_three_failures_in_a_window_are_three_immediate_alerts(clock, db, marks, alerts):
    for i in (1, 2, 3):
        await first_failure(i)
    await first_failure(1, attempts=2)             # a later retry: cooldown path, not budgeted

    assert [c.kwargs["force"] for c in alerts.await_args_list] == [True, True, True, False]
    assert all(c.args[0] is BOT and c.args[1] == "payment" for c in alerts.await_args_list)
    for i, text in zip((1, 2, 3), texts(alerts)):
        assert text.startswith("Provisioning job RETRY") and f"job_id: {i}" in text
    assert await provisioning.flush_alert_digests(BOT) == 0
    assert digests(alerts) == [] and provisioning.pending_alert_counts() == {}
    assert len(db.payment_errors) == 4


# ── flood ──────────────────────────────────────────────────────────────

async def test_flood_of_1000_failures_is_budget_plus_one_digest_with_exact_counts(clock, db, marks, alerts):
    for i in range(1, 1001):
        if i <= 600:
            reason = PANEL_DOWN
        elif i <= 900:
            reason = "timeout after 45.0s"
        else:
            reason = "bypass create failed: status=503 error=upstream"
        await first_failure(i, reason)

    immediate = per_job(alerts)
    assert len(immediate) == N and all(c.kwargs["force"] is True for c in immediate)
    assert provisioning.pending_alert_counts() == {"first_failure": 1000 - N}
    assert len(db.payment_errors) == 1000          # the DB is the complete record
    assert {e["error_code"] for e in db.payment_errors} == {"retry"}

    assert await provisioning.flush_alert_digests(BOT) == 1
    [text] = digests(alerts)
    last = alerts.await_args
    assert last.args[0] is BOT and last.args[1] == "payment" and last.kwargs["force"] is True
    assert f"count: {1000 - N} job(s)" in text
    assert "FIRST FAILURE" in text
    lines = text.splitlines()
    top = lines[lines.index("top reasons:") + 1: lines.index("top reasons:") + 4]
    assert top == [
        f"  {600 - N} × ProvisioningTransient: {PANEL_DOWN}",
        "  300 × ProvisioningTransient: timeout after 45.0s",
        "  100 × ProvisioningTransient: bypass create failed: status=503 error=upstream",
    ]
    assert f"jobs ({provisioning.ALERT_DIGEST_SAMPLE} of {1000 - N}):" in text
    keep = provisioning.ALERT_DIGEST_KEEP              # 995 > 500 → the latest 500 are kept
    first_kept = 1000 - keep + 1
    assert f"job {first_kept} tg:{TG0 + first_kept} key:bonus:900-77:{TG0 + first_kept}" in text
    assert f"only the latest {keep} kept in memory; all {1000 - N} are in payment_errors" in text
    # too many for an id list → retry by time range
    assert ("UPDATE provisioning_jobs SET status='pending', next_attempt_at=now() AT TIME ZONE 'UTC' "
            "WHERE status IN ('pending') AND updated_at >= '") in text
    assert "payment_errors (stage='provisioning'" in text
    assert len(text) <= provisioning.ALERT_DIGEST_MAX_CHARS

    assert provisioning.pending_alert_counts() == {}
    assert await provisioning.flush_alert_digests(BOT) == 0      # nothing left, one digest only
    assert len(alerts.await_args_list) == N + 1


async def test_dead_and_conflict_in_a_flood_get_their_own_digests(clock, db, marks, alerts):
    kinds = {}
    for i in range(1, 353):
        if i % 29 == 0 and i <= 348:               # 12 conflicts
            kinds[i] = "conflict"
            await conflict(i)
        elif i % 7 == 0 and len([k for k in kinds.values() if k == "dead"]) < 40:
            kinds[i] = "dead"
            await dead(i)
        else:
            kinds[i] = "first_failure"
            await first_failure(i)
    count = {k: list(kinds.values()).count(k) for k in ("conflict", "dead", "first_failure")}
    assert count == {"conflict": 12, "dead": 40, "first_failure": 300}

    assert len(per_job(alerts)) == 3 * N           # budget is per kind
    assert all(c.kwargs["force"] is True for c in alerts.await_args_list)
    assert sum("DEAD (24h" in t for t in texts(alerts)) == N
    assert sum("conflict:" in t for t in texts(alerts)) == N

    assert await provisioning.flush_alert_digests(BOT) == 3
    conf, dead_d, first = digests(alerts)          # money / GB at risk first
    assert conf.startswith(f"{DIGEST}: CONFLICT") and "count: 7 job(s)" in conf
    assert dead_d.startswith(f"{DIGEST}: DEAD") and "count: 35 job(s)" in dead_d
    assert first.startswith(f"{DIGEST}: FIRST FAILURE") and "count: 295 job(s)" in first
    assert "  7 × ProvisioningPermanent: conflict: base=N, target=N, current=N" in conf
    assert f"  35 × ProvisioningTransient: {PANEL_DOWN}" in dead_d

    conflict_ids = [i for i, k in kinds.items() if k == "conflict"][N:]
    dead_ids = [i for i, k in kinds.items() if k == "dead"][N:]
    assert (f"WHERE status IN ('dead') AND id = ANY(ARRAY[{','.join(map(str, conflict_ids))}]);"
            in conf)
    assert "bypass_base_bytes=NULL" in conf and "NOT credited" in conf
    assert f"WHERE status IN ('dead') AND id = ANY(ARRAY[{','.join(map(str, dead_ids))}]);" in dead_d

    codes = [e["error_code"] for e in db.payment_errors]
    assert (codes.count("retry"), codes.count("dead")) == (300, 52)


# ── delivery failures never lose anything ──────────────────────────────

async def test_digest_send_failure_is_retried_next_tick_nothing_lost(monkeypatch, clock, db, marks):
    state = {"digest": "fail"}
    sent = []

    async def send_alert(bot, category, text, *, force=False):
        if text.startswith(DIGEST):
            if state["digest"] == "fail":
                return False
            if state["digest"] == "raise":
                raise RuntimeError("telegram 502")
        sent.append(text)
        return True

    monkeypatch.setattr(admin_alerts, "send_alert", send_alert)
    for i in range(1, N + 11):
        await dead(i)
    assert len(sent) == N
    assert provisioning.pending_alert_counts() == {"dead": 10}

    assert await provisioning.flush_alert_digests(BOT) == 0
    assert provisioning.pending_alert_counts() == {"dead": 10}      # kept
    for i in range(100, 103):                                        # more arrive meanwhile
        await dead(i)
    state["digest"] = "raise"
    assert await provisioning.flush_alert_digests(BOT) == 0
    assert provisioning.pending_alert_counts() == {"dead": 13}

    state["digest"] = "ok"
    assert await provisioning.flush_alert_digests(BOT) == 1         # next tick, no interval wait
    assert "count: 13 job(s)" in sent[-1]
    ids = ",".join(map(str, [*range(N + 1, N + 11), 100, 101, 102]))
    assert f"id = ANY(ARRAY[{ids}])" in sent[-1]
    assert provisioning.pending_alert_counts() == {}
    assert len(db.payment_errors) == N + 13


async def test_failed_immediate_alert_goes_into_the_next_digest(clock, db, marks, alerts):
    alerts.return_value = False                    # Telegram down for the per-job alert
    await conflict(1)
    assert provisioning.pending_alert_counts() == {"conflict": 1}
    alerts.return_value = True
    assert await provisioning.flush_alert_digests(BOT) == 1
    assert "count: 1 job(s)" in digests(alerts)[0] and "ARRAY[1]" in digests(alerts)[0]


async def test_buffer_memory_is_capped_counts_stay_exact(monkeypatch, clock, db, marks, alerts):
    monkeypatch.setattr(provisioning, "ALERT_DIGEST_KEEP", 50)
    for i in range(1, 301):
        await dead(i)
    d = provisioning._pending["dead"]
    assert (d.total, len(d.entries), d.dropped) == (300 - N, 50, 300 - N - 50)
    assert d.entries[-1]["job_id"] == 300                        # the latest are kept
    await provisioning.flush_alert_digests(BOT)
    [text] = digests(alerts)
    assert f"count: {300 - N} job(s)" in text
    assert f"only the latest 50 kept in memory; all {300 - N} are in payment_errors" in text
    assert "updated_at BETWEEN '" in text and "last_error NOT LIKE '%conflict:%'" in text


# ── windows ────────────────────────────────────────────────────────────

async def test_window_rollover_resets_the_immediate_budget(clock, db, marks, alerts):
    t0 = clock.t
    for i in range(1, N + 3):
        await first_failure(i)
    assert len(per_job(alerts)) == N
    assert await provisioning.flush_alert_digests(BOT) == 1
    assert "count: 2 job(s)" in digests(alerts)[0]

    clock.t = t0 + 100                             # same window: budget still spent
    await first_failure(50)
    assert len(per_job(alerts)) == N
    assert await provisioning.flush_alert_digests(BOT) == 0     # one digest per window
    assert provisioning.pending_alert_counts() == {"first_failure": 1}

    clock.t = t0 + W + 1                           # window rolled over
    assert await provisioning.flush_alert_digests(BOT) == 1     # the worker tick
    assert "count: 1 job(s)" in digests(alerts)[1]
    for i in (60, 61, 62):
        await first_failure(i)
    assert len(per_job(alerts)) == N + 3           # immediate again
    assert all("Provisioning job RETRY" in c.args[2] for c in per_job(alerts)[N:])


# ── Telegram length ────────────────────────────────────────────────────

class CaptureBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append(text)


async def test_digest_fits_the_telegram_limit_with_exact_counts(clock, db, marks):
    bot = CaptureBot()                             # real send_alert: header + its own cut
    await provisioning.flush_alert_digests(bot)
    for i in range(1, 5001):
        # distinct within the 120-char reason key: > _REASON_KEYS_MAX kinds, 400+ chars each
        reason = f"variant {chr(65 + i % 26)}{i % 80} remote said {'x' * 400}"
        await first_failure(i, reason, bot=bot, key="k" * 190 + str(i))
    assert len(bot.messages) == N
    assert len(db.payment_errors) == 5000
    d = provisioning._pending["first_failure"]
    assert len(d.entries) == provisioning.ALERT_DIGEST_KEEP and d.total == 5000 - N

    assert await provisioning.flush_alert_digests(bot) == 1
    msg = bot.messages[-1]
    assert len(msg) <= 4096
    assert msg.startswith("PAYMENT ALERT\nProvisioning DIGEST")
    assert not msg.endswith("...")                 # not cut by send_alert: SQL intact
    assert f"count: {5000 - N} job(s)" in msg
    assert "UPDATE provisioning_jobs SET status='pending'" in msg
    assert "other reason(s)" in msg                # reasons beyond the top list are counted
    reason_lines = [ln for ln in msg.splitlines() if " × " in ln]
    assert sum(int(ln.split(" × ")[0]) for ln in reason_lines) == 5000 - N


# ── no bot / worker wiring ─────────────────────────────────────────────

async def test_payment_errors_for_every_job_and_no_bot_failures_reach_the_digest(clock, db, marks, alerts):
    for i in range(1, 51):
        await first_failure(i, bot=None)
    alerts.assert_not_awaited()
    assert len(db.payment_errors) == 50
    assert [e["purchase_id"] for e in db.payment_errors] == [f"bonus:900-77:{TG0 + i}" for i in range(1, 51)]
    assert provisioning.pending_alert_counts() == {"first_failure": 50}

    assert await provisioning.flush_alert_digests(None) == 0         # still no bot: kept
    assert await provisioning.flush_alert_digests(BOT) == 1
    [text] = digests(alerts)
    assert "count: 50 job(s)" in text
    assert f"WHERE status IN ('pending') AND id = ANY(ARRAY[{','.join(map(str, range(1, 51)))}]);" in text


@pytest.fixture
def panel(monkeypatch):
    return FakePanel().install(monkeypatch)


@pytest.fixture
def jobs(monkeypatch, panel):
    return FakeJobs(panel).install(monkeypatch)


async def test_worker_tick_flushes_the_digest_and_shutdown_flushes_the_rest(clock, panel, jobs, db, alerts):
    basic = for_purchase("basic", 30)
    panel.mode = "down"

    async def fail_via_run_now(tg):
        job_id = await provisioning.enqueue(
            FakeConn(), key=f"purchase:{tg}", telegram_id=tg, ent=basic,
            premium_until=utcnow() + timedelta(days=30), source="test",
        )
        assert await provisioning.run_now(job_id, bot=BOT) is False

    for tg in range(TG0, TG0 + N + 3):
        await fail_via_run_now(tg)
    assert len(per_job(alerts)) == N

    s = await worker.run_tick(BOT)
    assert s.processed == 0                        # backed off, not due
    [text] = digests(alerts)
    assert "count: 3 job(s)" in text

    for tg in (TG0 + 100, TG0 + 101):
        await fail_via_run_now(tg)
    await worker.run_tick(BOT)
    assert len(digests(alerts)) == 1               # interval not elapsed
    assert provisioning.pending_alert_counts() == {"first_failure": 2}

    task = asyncio.create_task(worker.provisioning_worker_task(BOT, interval=60))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert len(digests(alerts)) == 2 and "count: 2 job(s)" in digests(alerts)[1]
    assert provisioning.pending_alert_counts() == {}
    assert len(db.payment_errors) == N + 5
