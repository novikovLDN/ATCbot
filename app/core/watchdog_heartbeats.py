"""
Watchdog multi-signal heartbeats for freeze detection.

Used to detect real event-loop freeze only when ALL signals are stale.
Silence of Telegram updates is NOT a freeze signal.
"""
import time

# Monotonic timestamps; updated by event_loop_heartbeat task, workers, and health endpoint.
# Initialize to now so we don't trigger on startup before first tick.
_now = time.monotonic()
last_event_loop_heartbeat: float = _now
last_worker_iteration_timestamp: float = _now
last_successful_healthcheck_timestamp: float = _now


def mark_event_loop_heartbeat() -> None:
    global last_event_loop_heartbeat
    last_event_loop_heartbeat = time.monotonic()


def mark_worker_iteration() -> None:
    global last_worker_iteration_timestamp
    last_worker_iteration_timestamp = time.monotonic()


def mark_healthcheck_success() -> None:
    global last_successful_healthcheck_timestamp
    last_successful_healthcheck_timestamp = time.monotonic()


def are_all_stale(
    event_loop_threshold_seconds: float = 60.0,
    worker_threshold_seconds: float = 90.0,
    healthcheck_threshold_seconds: float = 90.0,
) -> bool:
    """True only if event loop, worker, and healthcheck heartbeats are all stale."""
    now = time.monotonic()
    event_loop_stale = (now - last_event_loop_heartbeat) > event_loop_threshold_seconds
    worker_stale = (now - last_worker_iteration_timestamp) > worker_threshold_seconds
    healthcheck_stale = (now - last_successful_healthcheck_timestamp) > healthcheck_threshold_seconds
    return event_loop_stale and worker_stale and healthcheck_stale
