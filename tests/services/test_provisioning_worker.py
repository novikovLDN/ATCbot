"""T5 — app.workers.provisioning_worker against FakePanel + in-memory outbox.

Spec: docs/audit/02_payment_core_plan.md §A.4, §B.4-5, task T5.
The outbox is tests/fakes/provisioning.FakeJobs (same claim semantics as the
SQL: due by next_attempt_at, per-user FIFO, lease, expired lease reclaimable).
"Time passing" for the backoff is simulated with FakeJobs.make_due().
"""
from __future__ import annotations

import ast
import asyncio
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import database.provisioning_jobs as pj
from app.api import payment_webhook
from app.services import admin_alerts, provisioning, remnawave_api, sub_aggregator
from app.services.tariffs import for_purchase
from app.workers import provisioning_worker as worker
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeConn, FakeDB, FakeJobs, utcnow

GIB = 1024 ** 3
TG = 4242
TG2 = 5151
TG3 = 6363
BOT = object()
UNTIL = utcnow().replace(microsecond=0) + timedelta(days=30)
BASIC = for_purchase("basic", 30)
MAIN_PY = Path(__file__).resolve().parents[2] / "main.py"


# ── fixtures / helpers ─────────────────────────────────────────────────

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
    yield
    assert provisioning._USER_LOCKS == {}, "user locks must be released"


async def enqueue(key, ent=BASIC, tg=TG, until=UNTIL):
    return await provisioning.enqueue(
        FakeConn(), key=key, telegram_id=tg, ent=ent, premium_until=until, source="test",
    )


def forces(alerts):
    return [c.kwargs.get("force") for c in alerts.await_args_list]


def bypass_patches(panel):
    return [c for c in panel.calls if c[0] == "update_user" and "trafficLimitBytes" in c[2]]


async def wait_until(pred, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


# ── retries, backoff, alerts ───────────────────────────────────────────

async def test_panel_down_backoff_one_forced_alert_then_recovers_gb_once(panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, utcnow())
    job_id = await enqueue("purchase:1")
    row = jobs.job(job_id)
    panel.mode = "down"

    s = await worker.run_tick(BOT)
    assert (s.processed, s.done, s.retried, s.dead, s.errors) == (1, 0, 1, 0, 0)
    assert (row["status"], row["attempts"]) == ("pending", 1)
    assert row["next_attempt_at"] > utcnow()
    assert forces(alerts) == [True]

    # backoff not elapsed → not claimed this tick
    s = await worker.run_tick(BOT)
    assert s.processed == 0
    assert row["attempts"] == 1

    jobs.make_due(job_id)
    s = await worker.run_tick(BOT)
    assert (s.processed, s.retried) == (1, 1)
    assert row["attempts"] == 2
    assert forces(alerts) == [True, False]          # only the first failure is forced
    assert panel.bypass_limit(TG) == 3 * GIB

    panel.mode = "ok"
    jobs.make_due(job_id)
    s = await worker.run_tick(BOT)
    assert (s.processed, s.done) == (1, 1)
    assert (row["status"], row["attempts"]) == ("done", 3)
    assert panel.bypass_limit(TG) == 13 * GIB
    assert panel.premium_expire(TG) == UNTIL
    assert len(bypass_patches(panel)) == 1

    s = await worker.run_tick(BOT)                 # nothing left, GB applied once
    assert s.processed == 0
    assert panel.bypass_limit(TG) == 13 * GIB
    assert len(alerts.await_args_list) == 2


async def test_after_24h_job_is_dead_with_forced_alert(panel, jobs, db, alerts):
    job_id = await enqueue("purchase:1")
    jobs.job(job_id)["created_at"] = utcnow() - timedelta(hours=25)
    jobs.job(job_id)["attempts"] = 5
    panel.mode = "down"

    s = await worker.run_tick(BOT)
    assert (s.processed, s.dead, s.retried) == (1, 1, 0)
    assert jobs.job(job_id)["status"] == "dead"
    assert forces(alerts) == [True]
    assert "DEAD" in alerts.await_args.args[2]
    assert db.payment_errors[-1]["error_code"] == "dead"

    s = await worker.run_tick(BOT)                 # dead jobs are never claimed again
    assert s.processed == 0


# ── ordering / fairness ────────────────────────────────────────────────

async def test_same_user_jobs_in_id_order_second_waits_for_first(monkeypatch, panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, UNTIL + timedelta(days=60))
    j1 = await enqueue("purchase:1")
    j2 = await enqueue("purchase:2")
    order = []
    real = provisioning.process_claimed

    async def spy(job, **kw):
        order.append(job["id"])
        return await real(job, **kw)

    monkeypatch.setattr(provisioning, "process_claimed", spy)

    panel.mode = "down"
    s = await worker.run_tick(BOT)
    assert s.processed == 1                        # j2 is due, but j1 is still open
    assert (jobs.job(j2)["status"], jobs.job(j2)["attempts"]) == ("pending", 0)

    panel.mode = "ok"
    jobs.make_due(j1)
    s = await worker.run_tick(BOT)
    assert (s.processed, s.done) == (2, 2)
    assert order == [j1, j1, j2]
    assert panel.bypass_limit(TG) == 23 * GIB


async def test_different_users_are_all_processed_in_one_tick(panel, jobs, db, alerts):
    ids = [await enqueue(f"purchase:{tg}", tg=tg) for tg in (TG, TG2, TG3)]
    s = await worker.run_tick(BOT)
    assert (s.processed, s.done) == (3, 3)
    assert all(jobs.job(i)["status"] == "done" for i in ids)


async def test_per_tick_cap(panel, jobs, db, alerts):
    for tg in (TG, TG2, TG3):
        await enqueue(f"purchase:{tg}", tg=tg)
    s = await worker.run_tick(BOT, max_jobs=2)
    assert s.processed == 2
    s = await worker.run_tick(BOT, max_jobs=2)
    assert s.processed == 1


async def test_expired_running_lease_is_reclaimed(panel, jobs, db, alerts):
    job_id = await enqueue("purchase:1")
    row = jobs.job(job_id)
    row.update(status="running", attempts=1, lease_until=utcnow() + timedelta(seconds=60))
    s = await worker.run_tick(BOT)
    assert s.processed == 0                        # lease still held (another run in flight)

    row["lease_until"] = utcnow() - timedelta(seconds=1)   # crashed worker
    s = await worker.run_tick(BOT)
    assert (s.processed, s.done) == (1, 1)
    assert (row["status"], row["attempts"]) == ("done", 2)


# ── isolation of failures ──────────────────────────────────────────────

async def test_exception_in_one_job_does_not_stop_others(monkeypatch, panel, jobs, db, alerts):
    j1 = await enqueue("purchase:1", tg=TG)
    j2 = await enqueue("purchase:2", tg=TG2)
    real = provisioning.process_claimed

    async def flaky(job, **kw):
        if job["id"] == j1:
            raise RuntimeError("bug in process_claimed")
        return await real(job, **kw)

    monkeypatch.setattr(provisioning, "process_claimed", flaky)
    s = await worker.run_tick(BOT)
    assert (s.processed, s.done, s.errors) == (2, 1, 1)
    assert jobs.job(j2)["status"] == "done"
    assert jobs.job(j1)["status"] == "running"     # lease expires → reclaimed later


async def test_hung_job_is_cut_by_the_guard_and_others_run(monkeypatch, panel, jobs, db, alerts):
    j1 = await enqueue("purchase:1", tg=TG)
    j2 = await enqueue("purchase:2", tg=TG2)
    real = provisioning.process_claimed

    async def hang(job, **kw):
        if job["id"] == j1:
            await asyncio.sleep(10)
        return await real(job, **kw)

    monkeypatch.setattr(provisioning, "process_claimed", hang)
    monkeypatch.setattr(worker, "JOB_GUARD_S", 0.05)
    s = await worker.run_tick(BOT, job_timeout=0.05)
    assert (s.processed, s.done, s.errors) == (2, 1, 1)
    assert jobs.job(j2)["status"] == "done"


async def test_hung_panel_call_becomes_a_retry(monkeypatch, panel, jobs, db, alerts):
    async def hang(tg):
        await asyncio.sleep(10)

    monkeypatch.setattr(remnawave_api, "get_premium_state", hang)
    job_id = await enqueue("purchase:1")
    s = await worker.run_tick(BOT, job_timeout=0.05)
    assert (s.processed, s.retried) == (1, 1)
    assert "timeout" in jobs.job(job_id)["last_error"].lower()


async def test_worker_job_timeout_fits_inside_the_lease():
    assert worker.JOB_TIMEOUT_S + worker.JOB_GUARD_S < worker.LEASE_S


async def test_claim_failure_ends_the_tick_without_raising(monkeypatch, panel, jobs, db, alerts):
    monkeypatch.setattr(pj, "claim", AsyncMock(side_effect=RuntimeError("db down")))
    s = await worker.run_tick(BOT)
    assert s.processed == 0 and s.claim_failed


# ── per-user lock ──────────────────────────────────────────────────────

async def test_user_lock_serializes_same_user_not_different_users(monkeypatch, panel, jobs, db, alerts):
    j1 = await enqueue("purchase:1", tg=TG)
    j2 = await enqueue("purchase:2", tg=TG)
    j3 = await enqueue("purchase:3", tg=TG2)
    active = defaultdict(int)
    peak = defaultdict(int)
    gate = asyncio.Event()

    async def slow_apply(job):
        tg = job["telegram_id"]
        active[tg] += 1
        peak[tg] = max(peak[tg], active[tg])
        await gate.wait()
        active[tg] -= 1

    monkeypatch.setattr(provisioning, "apply", slow_apply)
    tasks = [asyncio.create_task(provisioning.process_claimed(dict(jobs.job(j)), bot=BOT))
             for j in (j1, j2, j3)]
    await wait_until(lambda: active[TG] == 1 and active[TG2] == 1)
    await asyncio.sleep(0.02)
    assert active[TG] == 1                         # second job of TG waits for the lock
    assert active[TG2] == 1                        # another user is not blocked
    gate.set()
    assert await asyncio.gather(*tasks) == [True, True, True]
    assert peak[TG] == 1 and peak[TG2] == 1


async def test_worker_waits_on_the_lock_shared_with_run_now(panel, jobs, db, alerts):
    job_id = await enqueue("purchase:1")
    async with provisioning.user_lock(TG):         # e.g. a run_now of this user in flight
        tick = asyncio.create_task(worker.run_tick(BOT))
        await asyncio.sleep(0.05)
        assert not tick.done()
        assert panel.calls == []                   # claimed, but not applied under the lock
    s = await tick
    assert (s.processed, s.done) == (1, 1)
    assert jobs.job(job_id)["status"] == "done"


# ── the loop ───────────────────────────────────────────────────────────

async def test_loop_survives_a_failing_tick(monkeypatch):
    calls = []

    async def fake_tick(bot, **kw):
        calls.append(bot)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return worker.TickStats()

    monkeypatch.setattr(worker, "run_tick", fake_tick)
    task = asyncio.create_task(worker.provisioning_worker_task(BOT, interval=0.001))
    await wait_until(lambda: len(calls) >= 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls[0] is BOT


async def test_loop_processes_jobs_and_cancellation_stops_it(panel, jobs, db, alerts):
    job_id = await enqueue("purchase:1")
    task = asyncio.create_task(worker.provisioning_worker_task(BOT, interval=0.001))
    await wait_until(lambda: jobs.job(job_id)["status"] == "done")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_cancellation_during_a_job_propagates(monkeypatch, panel, jobs, db, alerts):
    await enqueue("purchase:1")
    entered = asyncio.Event()

    async def hang(job, **kw):
        entered.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(provisioning, "process_claimed", hang)
    task = asyncio.create_task(worker.provisioning_worker_task(BOT, interval=0.001))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


# ── main.py wiring ─────────────────────────────────────────────────────

def _refs(node, name):
    return any(
        (isinstance(n, ast.Name) and n.id == name) or (isinstance(n, ast.Attribute) and n.attr == name)
        for n in ast.walk(node)
    )


def test_main_starts_worker_when_db_ready_and_on_db_recovery():
    """N5: both paths go through ONE starter, start_db_services, which starts the
    provisioning worker with no feature flag (tests/test_main_db_workers.py checks
    the full worker set and that the advisory lock comes first)."""
    src = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = {n.name: n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}
    starter, main_fn, retry_fn = fns["start_db_services"], fns["main"], fns["retry_db_init"]

    # the starter starts it unconditionally (not under any `if`)
    assert _refs(starter, "provisioning_worker_task")
    assert not any(isinstance(n, ast.If) and _refs(n, "provisioning_worker_task") for n in ast.walk(starter))

    # startup: a top-level `if database.DB_READY:` block in main() calls the starter
    start_blocks = [
        n for n in main_fn.body
        if isinstance(n, ast.If)
        and ast.get_source_segment(src, n.test) == "database.DB_READY"
        and _refs(n, "start_db_services")
    ]
    assert len(start_blocks) == 1

    # DB recovery path calls the same starter
    assert _refs(retry_fn, "start_db_services")
