"""
Worker liveness registry.

Each background task reports a heartbeat at the start and end of every
iteration. The admin dashboard reads the registry to detect stuck or dead
workers (no heartbeat for > 2× expected interval).

Usage from a worker:

    from app.core.worker_registry import register, mark_iteration_start, mark_iteration_end

    register("auto_renewal", expected_interval_s=600)
    ...
    mark_iteration_start("auto_renewal")
    try:
        ... do work ...
        mark_iteration_end("auto_renewal", outcome="success", items=processed_count)
    except Exception as e:
        mark_iteration_end("auto_renewal", outcome="failed", error=type(e).__name__)
        raise

The registry is safe to read at any time. ``snapshot()`` returns a list of
worker entries with health status: ``healthy`` / ``stale`` / ``never_ran``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class WorkerHeartbeat:
    name: str
    expected_interval_s: float
    registered_at_monotonic: float
    last_iteration_start_at: Optional[float] = None
    last_iteration_end_at: Optional[float] = None
    last_iteration_outcome: Optional[str] = None
    last_iteration_error: Optional[str] = None
    last_iteration_items: int = 0
    iteration_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    # Wall-clock for human-readable reporting.
    last_iteration_started_wall: Optional[datetime] = None
    last_iteration_ended_wall: Optional[datetime] = None


_workers: Dict[str, WorkerHeartbeat] = {}


def register(name: str, expected_interval_s: float) -> None:
    """Idempotent registration. Re-registration just updates the interval."""
    existing = _workers.get(name)
    now = time.monotonic()
    if existing is None:
        _workers[name] = WorkerHeartbeat(
            name=name,
            expected_interval_s=expected_interval_s,
            registered_at_monotonic=now,
        )
    else:
        existing.expected_interval_s = expected_interval_s


def unregister(name: str) -> None:
    _workers.pop(name, None)


def mark_iteration_start(name: str) -> None:
    hb = _workers.get(name)
    if hb is None:
        # Auto-register with a sane default if the worker forgot to register.
        register(name, expected_interval_s=300.0)
        hb = _workers[name]
    hb.last_iteration_start_at = time.monotonic()
    hb.last_iteration_started_wall = datetime.now(timezone.utc)


def mark_iteration_end(
    name: str,
    outcome: str = "success",
    items: int = 0,
    error: Optional[str] = None,
) -> None:
    hb = _workers.get(name)
    if hb is None:
        return
    hb.last_iteration_end_at = time.monotonic()
    hb.last_iteration_ended_wall = datetime.now(timezone.utc)
    hb.last_iteration_outcome = outcome
    hb.last_iteration_error = error
    hb.last_iteration_items = items
    hb.iteration_count += 1
    if outcome in ("success", "skipped"):
        hb.consecutive_failures = 0
    else:
        hb.failure_count += 1
        hb.consecutive_failures += 1


def health_for(hb: WorkerHeartbeat) -> str:
    """Return one of: ``healthy``, ``stale``, ``never_ran``, ``failing``."""
    if hb.last_iteration_end_at is None:
        # Allow 2× interval grace for first run.
        age = time.monotonic() - hb.registered_at_monotonic
        if age > hb.expected_interval_s * 2:
            return "never_ran"
        return "starting"
    age = time.monotonic() - hb.last_iteration_end_at
    if age > hb.expected_interval_s * 2:
        return "stale"
    if hb.consecutive_failures >= 3:
        return "failing"
    return "healthy"


def snapshot() -> List[Dict]:
    """List of worker status dicts for the admin dashboard."""
    out: List[Dict] = []
    now = time.monotonic()
    for hb in _workers.values():
        last_end_age_s: Optional[float] = None
        if hb.last_iteration_end_at is not None:
            last_end_age_s = round(now - hb.last_iteration_end_at, 1)
        last_dur_ms: Optional[float] = None
        if (
            hb.last_iteration_start_at is not None
            and hb.last_iteration_end_at is not None
            and hb.last_iteration_end_at >= hb.last_iteration_start_at
        ):
            last_dur_ms = round(
                (hb.last_iteration_end_at - hb.last_iteration_start_at) * 1000.0, 1
            )
        out.append({
            "name": hb.name,
            "health": health_for(hb),
            "expected_interval_s": hb.expected_interval_s,
            "iteration_count": hb.iteration_count,
            "failure_count": hb.failure_count,
            "consecutive_failures": hb.consecutive_failures,
            "last_outcome": hb.last_iteration_outcome,
            "last_error": hb.last_iteration_error,
            "last_items": hb.last_iteration_items,
            "last_end_age_s": last_end_age_s,
            "last_duration_ms": last_dur_ms,
        })
    out.sort(key=lambda d: d["name"])
    return out


def reset() -> None:
    """Test-only."""
    _workers.clear()
