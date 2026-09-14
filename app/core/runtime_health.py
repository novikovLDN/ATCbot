"""In-process liveness registry for background workers + admin alert counter.

The bot is one process: every worker is an asyncio task started from
main.py. Nothing recorded when a worker last completed an iteration, so a
hung or silently failing loop was invisible until users complained. Each
worker loop now calls:

    register("reminders", interval_s=45 * 60, initial_delay_s=60)  # once, at task start
    record("reminders", outcome, error)                            # after every iteration

(`outcome` is the worker's own iteration outcome: success / failed /
timeout / degraded / skipped / cancelled). The dashboard reads snapshot().

Memory only — it describes THIS process since it started, which is
exactly the question "is the worker alive right now?". No DB writes, no
migration, no locks (one event loop; dict updates are atomic there).
Bookkeeping never raises into a worker.

Alerts: every admin alert that actually went out (Telegram DM or push)
calls record_alert(category); the dashboard shows the volume for the last
24 h (or since start, when the process is younger).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional, Tuple

# Stale = no iteration finished (ok, failed or skipped) for
# interval × STALE_FACTOR + START_GRACE_S.
STALE_FACTOR = 2.5
START_GRACE_S = 180.0
ALERT_WINDOW_S = 24 * 3600
_MAX_ALERTS_KEPT = 5000

_OK = ("success",)
_FAIL = ("failed", "timeout", "degraded", "error")
_SKIP = ("skipped",)


@dataclass
class _Worker:
    name: str
    interval_s: float
    initial_delay_s: float
    registered_at: float
    registered_wall: datetime
    last_ok: Optional[float] = None
    last_ok_wall: Optional[datetime] = None
    last_fail: Optional[float] = None
    last_fail_wall: Optional[datetime] = None
    last_skip: Optional[float] = None
    # Kind of the most recent finished iteration: "ok" | "fail" | "skip".
    last_kind: Optional[str] = None
    last_error: Optional[str] = None
    ok_count: int = 0
    fail_count: int = 0
    skip_count: int = 0
    consecutive_fails: int = 0


_workers: Dict[str, _Worker] = {}
_alerts: Deque[Tuple[float, str]] = deque(maxlen=_MAX_ALERTS_KEPT)
_started_mono = time.monotonic()


def _wall() -> datetime:
    return datetime.now(timezone.utc)


def register(name: str, *, interval_s: float, initial_delay_s: float = 0.0) -> None:
    """Declare a running worker and how often it completes a loop
    (sleep + typical run time). Re-registering (a task restarted after DB
    recovery) resets its counters."""
    try:
        _workers[name] = _Worker(
            name=name,
            interval_s=float(interval_s),
            initial_delay_s=float(initial_delay_s),
            registered_at=time.monotonic(),
            registered_wall=_wall(),
        )
    except Exception:
        pass


def beat(name: str) -> None:
    record(name, "success")


def fail(name: str, err: Any = None) -> None:
    record(name, "failed", err)


def record(name: str, outcome: Optional[str], err: Any = None) -> None:
    """Record one finished iteration. Only the exception TYPE (or a short
    error class like 'timeout') is kept: messages can carry user data."""
    try:
        w = _workers.get(name)
        if w is None or not outcome:
            return
        now = time.monotonic()
        if outcome == "cancelled":
            return
        if outcome in _FAIL:
            w.last_fail, w.last_fail_wall = now, _wall()
            w.fail_count += 1
            w.consecutive_fails += 1
            w.last_kind = "fail"
            if isinstance(err, BaseException):
                w.last_error = type(err).__name__
            else:
                w.last_error = (str(err) if err else outcome)[:40]
        elif outcome in _SKIP:
            w.last_skip = now
            w.skip_count += 1
            w.last_kind = "skip"
        else:
            # "success" and any worker-specific completion label (e.g. the
            # activation worker's "no_work"): the loop finished normally.
            w.last_ok, w.last_ok_wall = now, _wall()
            w.ok_count += 1
            w.consecutive_fails = 0
            w.last_kind = "ok"
    except Exception:
        pass


def worker_state(w: _Worker, now_mono: float) -> str:
    """ok | failing | paused | stale | starting. Pure given record + clock.

    failing: the last two or more iterations raised / timed out.
    paused:  the last iteration was skipped (feature flag off, DB not ready).
    stale:   nothing finished for interval × STALE_FACTOR + grace.
    """
    last_any = max((t for t in (w.last_ok, w.last_fail, w.last_skip) if t is not None), default=None)
    if last_any is None:
        expected_first = w.registered_at + w.initial_delay_s + w.interval_s + START_GRACE_S
        return "starting" if now_mono <= expected_first else "stale"
    if now_mono - last_any > w.interval_s * STALE_FACTOR + START_GRACE_S:
        return "stale"
    if w.last_kind == "fail" and w.consecutive_fails >= 2:
        return "failing"
    if w.last_kind == "skip":
        return "paused"
    return "ok"


def snapshot() -> list[dict[str, Any]]:
    now = time.monotonic()
    out = []
    for w in sorted(_workers.values(), key=lambda x: x.name):
        out.append({
            "name": w.name,
            "state": worker_state(w, now),
            "interval_s": w.interval_s,
            "registered_at": w.registered_wall.isoformat(),
            "last_ok_at": w.last_ok_wall.isoformat() if w.last_ok_wall else None,
            "last_ok_age_s": round(now - w.last_ok, 1) if w.last_ok is not None else None,
            "last_fail_at": w.last_fail_wall.isoformat() if w.last_fail_wall else None,
            "last_error": w.last_error,
            "ok_count": w.ok_count,
            "fail_count": w.fail_count,
            "skip_count": w.skip_count,
            "consecutive_fails": w.consecutive_fails,
        })
    return out


def record_alert(category: str) -> None:
    try:
        _alerts.append((time.monotonic(), str(category or "other")[:40]))
    except Exception:
        pass


def alerts_summary(window_s: float = ALERT_WINDOW_S) -> dict[str, Any]:
    now = time.monotonic()
    since = now - window_s
    by_cat: Dict[str, int] = {}
    for ts, cat in _alerts:
        if ts >= since:
            by_cat[cat] = by_cat.get(cat, 0) + 1
    return {
        "total": sum(by_cat.values()),
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
        "window_s": window_s,
        # Shorter than window_s when the process restarted recently.
        "covered_s": round(min(window_s, now - _started_mono), 1),
    }


def reset() -> None:
    """Tests only."""
    _workers.clear()
    _alerts.clear()
