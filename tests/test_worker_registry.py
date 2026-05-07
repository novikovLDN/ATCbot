"""Unit tests for app.core.worker_registry."""
from __future__ import annotations

import time

from app.core import worker_registry


def test_register_then_heartbeat_is_healthy(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    worker_registry.register("w1", expected_interval_s=60)
    worker_registry.mark_iteration_start("w1")
    clock[0] += 1.0
    worker_registry.mark_iteration_end("w1", outcome="success", items=5)
    snap = worker_registry.snapshot()
    assert len(snap) == 1
    assert snap[0]["name"] == "w1"
    assert snap[0]["health"] == "healthy"
    assert snap[0]["iteration_count"] == 1
    assert snap[0]["last_outcome"] == "success"
    assert snap[0]["last_items"] == 5


def test_starting_then_never_ran(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    worker_registry.register("starter", expected_interval_s=60)
    snap = worker_registry.snapshot()
    assert snap[0]["health"] == "starting"
    # Past 2× expected interval — must transition to never_ran.
    clock[0] += 200.0
    snap = worker_registry.snapshot()
    assert snap[0]["health"] == "never_ran"


def test_stale_after_two_intervals(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    worker_registry.register("stale_one", expected_interval_s=60)
    worker_registry.mark_iteration_start("stale_one")
    worker_registry.mark_iteration_end("stale_one", outcome="success")
    clock[0] += 200.0  # > 2*60
    snap = worker_registry.snapshot()
    assert snap[0]["health"] == "stale"


def test_consecutive_failures_mark_failing(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    worker_registry.register("flaky", expected_interval_s=60)
    for _ in range(3):
        worker_registry.mark_iteration_start("flaky")
        worker_registry.mark_iteration_end("flaky", outcome="failed", error="X")
    snap = worker_registry.snapshot()
    assert snap[0]["health"] == "failing"
    assert snap[0]["consecutive_failures"] == 3


def test_success_resets_consecutive_failures(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    worker_registry.register("recovers", expected_interval_s=60)
    worker_registry.mark_iteration_start("recovers")
    worker_registry.mark_iteration_end("recovers", outcome="failed", error="x")
    worker_registry.mark_iteration_start("recovers")
    worker_registry.mark_iteration_end("recovers", outcome="success")
    snap = worker_registry.snapshot()
    assert snap[0]["consecutive_failures"] == 0
    assert snap[0]["failure_count"] == 1
