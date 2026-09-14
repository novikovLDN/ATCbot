"""Background worker loops survive a failing iteration and stop on cancel.

Risk closed: every worker runs as one asyncio task for the life of the
process (main.py). If an exception escapes the loop, the task dies silently
and nothing restarts it: auto-renewal stops charging and renewing, reminders
and trial notices stop, the WATA reconciler stops recovering lost webhooks,
pending activations are never retried. The loops themselves were never run by
any test (coverage 09: auto_renewal_task 70/71 lines unrun, reminders_task
39/40, run_trial_scheduler 70/71, wata_reconciler_task 19/20,
activation_worker_task 0%); tests only called the inner functions.

Each test replaces the iteration body with a scripted fake: ok → exception →
ok → cancel. The module's `asyncio.sleep` never waits (delays are recorded).
"""
from __future__ import annotations

import asyncio

import pytest

import database
from app.services import admin_alerts


class _AsyncioProxy:
    """Stand-in for `module.asyncio`: everything real except sleep(), which
    records the delay and returns at once."""

    def __init__(self):
        self.delays: list[float] = []

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay=0, result=None):
        self.delays.append(delay)
        await asyncio.sleep(0)
        return result


class _Script:
    """Scripted iteration body: 'ok', 'boom' (RuntimeError), 'hang' (blocks),
    'stop' (CancelledError, i.e. the task is cancelled while inside the body)."""

    def __init__(self, *steps, result=None):
        self.steps = list(steps)
        self.calls = 0
        self.result = result

    async def __call__(self, *_a, **_kw):
        self.calls += 1
        step = self.steps.pop(0) if self.steps else "stop"
        if step == "boom":
            raise RuntimeError("iteration failed")
        if step == "hang":
            await asyncio.sleep(30)
        if step == "stop":
            raise asyncio.CancelledError()
        return self.result


@pytest.fixture
def alerts(monkeypatch):
    sent = []

    async def alert_worker_failure(bot, worker_name, error, **kw):
        sent.append(worker_name)
    monkeypatch.setattr(admin_alerts, "alert_worker_failure", alert_worker_failure)
    monkeypatch.setattr(database, "DB_READY", True)
    return sent


async def _run(module, task_fn, monkeypatch) -> _AsyncioProxy:
    proxy = _AsyncioProxy()
    monkeypatch.setattr(module, "asyncio", proxy)
    task = asyncio.ensure_future(task_fn(object()))
    try:
        await asyncio.wait_for(task, timeout=5)
    except asyncio.CancelledError:
        pass
    assert task.done(), "worker loop did not stop"
    return proxy


async def test_auto_renewal_loop_survives_a_failed_iteration(monkeypatch, alerts):
    import auto_renewal
    body = _Script("ok", "ok", "boom", "ok", "stop")   # initial check, then the loop
    monkeypatch.setattr(auto_renewal, "process_auto_renewals", body)
    proxy = await _run(auto_renewal, auto_renewal.auto_renewal_task, monkeypatch)
    assert body.calls == 5, "the loop stopped after the failing iteration"
    assert alerts == ["auto_renewal"]
    # after a failure the loop backs off instead of spinning
    assert auto_renewal.MINIMUM_SAFE_SLEEP_ON_FAILURE in proxy.delays


async def test_auto_renewal_initial_check_failure_does_not_prevent_the_loop(monkeypatch, alerts):
    import auto_renewal
    body = _Script("boom", "ok", "stop")
    monkeypatch.setattr(auto_renewal, "process_auto_renewals", body)
    await _run(auto_renewal, auto_renewal.auto_renewal_task, monkeypatch)
    assert body.calls == 3


async def test_auto_renewal_hung_iteration_is_cut_and_the_lock_released(monkeypatch, alerts):
    """A hung run is cancelled by the hard timeout; the worker lock must be
    released, otherwise every later iteration would wait on it forever."""
    import auto_renewal
    monkeypatch.setattr(auto_renewal, "ITERATION_HARD_TIMEOUT_SECONDS", 0.05)
    body = _Script("ok", "hang", "ok", "stop")
    monkeypatch.setattr(auto_renewal, "process_auto_renewals", body)
    await _run(auto_renewal, auto_renewal.auto_renewal_task, monkeypatch)
    assert body.calls == 4
    assert not auto_renewal._worker_lock.locked()


async def test_auto_renewal_skips_iterations_while_db_is_down(monkeypatch, alerts):
    import auto_renewal
    body = _Script("ok", "stop")
    monkeypatch.setattr(auto_renewal, "process_auto_renewals", body)
    monkeypatch.setattr(database, "DB_READY", False)
    proxy = _AsyncioProxy()
    monkeypatch.setattr(auto_renewal, "asyncio", proxy)

    # DB never comes back within the test: the loop must keep sleeping, not crash
    orig_sleep = proxy.sleep

    async def sleep(delay=0, result=None):
        if len(proxy.delays) >= 5:
            raise asyncio.CancelledError()
        return await orig_sleep(delay, result)
    proxy.sleep = sleep
    task = asyncio.ensure_future(auto_renewal.auto_renewal_task(object()))
    try:
        await asyncio.wait_for(task, timeout=5)     # cancel inside the loop → clean exit
    except asyncio.CancelledError:
        pass
    assert task.done()
    assert body.calls == 1          # only the initial check ran; the loop skipped
    assert alerts == []


async def test_reminders_loop_survives_a_failed_iteration(monkeypatch, alerts):
    import reminders
    body = _Script("boom", "ok", "stop")
    monkeypatch.setattr(reminders, "send_smart_reminders", body)
    await _run(reminders, reminders.reminders_task, monkeypatch)
    assert body.calls == 3
    assert alerts == ["reminders"]


async def test_trial_scheduler_survives_a_failed_iteration(monkeypatch, alerts):
    import trial_notifications as tn
    monkeypatch.setattr(tn, "_TRIAL_SCHEDULER_STARTED", False)
    notify = _Script("ok", "boom", "ok", "stop")
    expire = _Script("ok", "ok", "ok", "ok")
    monkeypatch.setattr(tn, "process_trial_notifications", notify)
    monkeypatch.setattr(tn, "expire_trial_subscriptions", expire)
    proxy = await _run(tn, tn.run_trial_scheduler, monkeypatch)
    assert notify.calls == 4
    assert expire.calls == 2        # the failed iteration skipped expiry; the next ones ran it
    assert alerts == ["trial_notifications"]
    assert tn.MINIMUM_SAFE_SLEEP_ON_FAILURE in proxy.delays


async def test_wata_reconciler_loop_survives_a_failed_iteration(monkeypatch, alerts):
    import wata_service
    from app.workers import wata_reconciler
    monkeypatch.setattr(wata_service, "is_enabled", lambda: True)
    body = _Script("boom", "ok", "stop")
    monkeypatch.setattr(wata_reconciler, "_reconcile_iteration", body)
    await _run(wata_reconciler, wata_reconciler.wata_reconciler_task, monkeypatch)
    assert body.calls == 3


async def test_activation_worker_loop_survives_a_failed_iteration(monkeypatch, alerts):
    import activation_worker
    body = _Script("ok", "boom", "ok", "stop", result=(0, "success"))
    monkeypatch.setattr(activation_worker, "process_pending_activations", body)
    proxy = await _run(activation_worker, activation_worker.activation_worker_task, monkeypatch)
    assert body.calls == 4
    assert 10 in proxy.delays       # MINIMUM_SAFE_SLEEP_ON_FAILURE after the failure
