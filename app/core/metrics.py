"""
In-memory metrics collector for bot observability.

Tracks:
- Counters: request counts, errors, rate limits, worker iterations
- Gauges: active connections, concurrent updates, memory usage
- Histograms: request latency distribution

Thread-safe, lock-free (uses atomics where possible).
No external dependencies. Designed for single-process use.

Metrics are reset on restart (by design — stateless deployment).
"""
import time
import threading
import os
import resource
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple


# ── Atomic counter (thread-safe via lock) ──────────────────────────

class _Counter:
    __slots__ = ("_value", "_lock")

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, n: int = 1) -> None:
        with self._lock:
            self._value += n

    @property
    def value(self) -> int:
        return self._value

    def reset(self) -> int:
        with self._lock:
            v = self._value
            self._value = 0
            return v


class _Gauge:
    __slots__ = ("_value", "_lock")

    def __init__(self, initial: float = 0.0) -> None:
        self._value = initial
        self._lock = threading.Lock()

    def set(self, v: float) -> None:
        with self._lock:
            self._value = v

    def inc(self, n: float = 1.0) -> None:
        with self._lock:
            self._value += n

    def dec(self, n: float = 1.0) -> None:
        with self._lock:
            self._value -= n

    @property
    def value(self) -> float:
        return self._value


class _Histogram:
    """Fixed-bucket histogram for latency tracking."""
    __slots__ = ("_buckets", "_boundaries", "_sum", "_count", "_lock")

    def __init__(self, boundaries: Tuple[float, ...] = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0)) -> None:
        self._boundaries = boundaries
        self._buckets = [0] * (len(boundaries) + 1)  # +1 for +Inf
        self._sum = 0.0
        self._count = 0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._count += 1
            for i, b in enumerate(self._boundaries):
                if value <= b:
                    self._buckets[i] += 1
                    return
            self._buckets[-1] += 1  # +Inf

    @property
    def count(self) -> int:
        return self._count

    @property
    def avg(self) -> float:
        c = self._count
        return self._sum / c if c > 0 else 0.0

    @property
    def total(self) -> float:
        return self._sum

    def percentile(self, p: float) -> float:
        """Approximate percentile from histogram buckets."""
        if self._count == 0:
            return 0.0
        target = self._count * p
        cumulative = 0
        for i, count in enumerate(self._buckets):
            cumulative += count
            if cumulative >= target:
                if i < len(self._boundaries):
                    return self._boundaries[i]
                return self._boundaries[-1] if self._boundaries else 0.0
        return self._boundaries[-1] if self._boundaries else 0.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "count": self._count,
                "sum": round(self._sum, 4),
                "avg": round(self.avg, 4),
                "p50": round(self.percentile(0.5), 4),
                "p95": round(self.percentile(0.95), 4),
                "p99": round(self.percentile(0.99), 4),
            }


# ── Sliding window rate calculator ────────────────────────────────

class _RateWindow:
    """Tracks event count over a sliding window for rate calculation."""
    __slots__ = ("_window_seconds", "_events", "_lock")

    def __init__(self, window_seconds: int = 60) -> None:
        self._window_seconds = window_seconds
        self._events: List[float] = []
        self._lock = threading.Lock()

    def record(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._events.append(now)
            self._cleanup(now)

    def _cleanup(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._events and self._events[0] < cutoff:
            self._events.pop(0)

    @property
    def rate(self) -> float:
        """Events per second over the window."""
        now = time.monotonic()
        with self._lock:
            self._cleanup(now)
            count = len(self._events)
        return count / self._window_seconds if self._window_seconds > 0 else 0.0

    @property
    def count(self) -> int:
        now = time.monotonic()
        with self._lock:
            self._cleanup(now)
            return len(self._events)


# ── Error tracker with recent samples ─────────────────────────────

@dataclass
class ErrorSample:
    timestamp: float
    error_type: str
    message: str
    component: str


class _ErrorTracker:
    """Keeps recent error samples for admin visibility."""

    def __init__(self, max_samples: int = 50) -> None:
        self._max_samples = max_samples
        self._samples: List[ErrorSample] = []
        self._lock = threading.Lock()
        self._total = 0

    def record(self, error_type: str, message: str, component: str = "unknown") -> None:
        sample = ErrorSample(
            timestamp=time.time(),
            error_type=error_type,
            message=message[:200],
            component=component,
        )
        with self._lock:
            self._total += 1
            self._samples.append(sample)
            if len(self._samples) > self._max_samples:
                self._samples = self._samples[-self._max_samples:]

    @property
    def total(self) -> int:
        return self._total

    def recent(self, n: int = 10) -> List[ErrorSample]:
        with self._lock:
            return list(self._samples[-n:])


# ── Global metrics registry ───────────────────────────────────────

class MetricsCollector:
    """Central metrics registry. Singleton pattern."""

    def __init__(self) -> None:
        self._start_time = time.monotonic()

        # Request counters
        self.requests_total = _Counter()
        self.requests_success = _Counter()
        self.requests_error = _Counter()
        self.requests_rate_limited = _Counter()
        self.requests_timeout = _Counter()

        # Request rate (sliding window)
        self.request_rate = _RateWindow(60)
        self.error_rate = _RateWindow(60)

        # Request latency
        self.request_latency = _Histogram()

        # Webhook-specific
        self.webhook_requests = _Counter()
        self.webhook_errors = _Counter()
        self.webhook_latency = _Histogram()

        # Concurrent updates gauge
        self.concurrent_updates = _Gauge()
        self.peak_concurrent_updates = _Gauge()

        # DB pool
        self.db_pool_acquired = _Counter()
        self.db_pool_released = _Counter()
        self.db_pool_timeouts = _Counter()
        self.db_query_count = _Counter()
        self.db_query_errors = _Counter()
        self.db_query_latency = _Histogram(
            boundaries=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
        )

        # Worker metrics (per worker name)
        self.worker_iterations: Dict[str, _Counter] = defaultdict(_Counter)
        self.worker_errors: Dict[str, _Counter] = defaultdict(_Counter)
        self.worker_last_success: Dict[str, float] = {}
        self.worker_last_error: Dict[str, float] = {}
        self.worker_last_error_msg: Dict[str, str] = {}
        self.worker_iteration_latency: Dict[str, _Histogram] = defaultdict(
            lambda: _Histogram(boundaries=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0))
        )

        # Rate limiting stats
        self.rate_limit_hits = _Counter()
        self.flood_bans = _Counter()

        # Admin alerts
        self.alerts_sent = _Counter()
        self.alerts_failed = _Counter()

        # Payment metrics
        self.payments_initiated = _Counter()
        self.payments_success = _Counter()
        self.payments_failed = _Counter()
        self.payments_revenue_kopecks = _Counter()

        # Error tracker
        self.errors = _ErrorTracker(max_samples=100)

    def get_process_metrics(self) -> dict:
        """Collect process-level metrics (memory, uptime, etc.)."""
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            rss_mb = usage.ru_maxrss / 1024  # Linux: KB -> MB
        except Exception:
            rss_mb = 0.0

        try:
            with open("/proc/self/status") as f:
                status = f.read()
            vm_rss_line = [l for l in status.split("\n") if l.startswith("VmRSS:")]
            if vm_rss_line:
                rss_mb = int(vm_rss_line[0].split()[1]) / 1024  # kB -> MB
        except Exception:
            pass

        uptime = time.monotonic() - self._start_time

        return {
            "uptime_seconds": int(uptime),
            "memory_rss_mb": round(rss_mb, 1),
            "pid": os.getpid(),
        }

    def get_worker_status(self) -> Dict[str, dict]:
        """Get status of all tracked workers."""
        now = time.monotonic()
        result = {}
        all_workers = set(self.worker_iterations.keys()) | set(self.worker_errors.keys())
        for name in all_workers:
            last_ok = self.worker_last_success.get(name)
            last_err = self.worker_last_error.get(name)
            iterations = self.worker_iterations[name].value
            errors = self.worker_errors[name].value

            if last_ok:
                since_last_ok = now - last_ok
            else:
                since_last_ok = None

            status = "unknown"
            if iterations == 0:
                status = "starting"
            elif last_ok and (since_last_ok is not None and since_last_ok < 1800):
                status = "healthy"
            elif last_err and (not last_ok or last_err > (last_ok or 0)):
                status = "failing"
            else:
                status = "stale"

            result[name] = {
                "status": status,
                "iterations": iterations,
                "errors": errors,
                "error_rate": f"{(errors / iterations * 100):.1f}%" if iterations > 0 else "0%",
                "since_last_ok_s": int(since_last_ok) if since_last_ok is not None else None,
                "last_error": self.worker_last_error_msg.get(name),
                "latency": self.worker_iteration_latency[name].snapshot() if name in self.worker_iteration_latency else None,
            }
        return result

    def snapshot(self) -> dict:
        """Full metrics snapshot for admin dashboard / API."""
        process = self.get_process_metrics()
        workers = self.get_worker_status()

        return {
            "process": process,
            "requests": {
                "total": self.requests_total.value,
                "success": self.requests_success.value,
                "errors": self.requests_error.value,
                "rate_limited": self.requests_rate_limited.value,
                "timeouts": self.requests_timeout.value,
                "rate_per_sec": round(self.request_rate.rate, 2),
                "error_rate_per_sec": round(self.error_rate.rate, 2),
                "latency": self.request_latency.snapshot(),
            },
            "webhooks": {
                "total": self.webhook_requests.value,
                "errors": self.webhook_errors.value,
                "latency": self.webhook_latency.snapshot(),
            },
            "concurrency": {
                "current": int(self.concurrent_updates.value),
                "peak": int(self.peak_concurrent_updates.value),
            },
            "database": {
                "queries": self.db_query_count.value,
                "errors": self.db_query_errors.value,
                "pool_timeouts": self.db_pool_timeouts.value,
                "query_latency": self.db_query_latency.snapshot(),
            },
            "workers": workers,
            "rate_limiting": {
                "hits": self.rate_limit_hits.value,
                "flood_bans": self.flood_bans.value,
            },
            "payments": {
                "initiated": self.payments_initiated.value,
                "success": self.payments_success.value,
                "failed": self.payments_failed.value,
                "revenue_rub": round(self.payments_revenue_kopecks.value / 100, 2),
            },
            "alerts": {
                "sent": self.alerts_sent.value,
                "failed": self.alerts_failed.value,
            },
            "errors": {
                "total": self.errors.total,
                "recent": [
                    {
                        "time": e.timestamp,
                        "type": e.error_type,
                        "msg": e.message,
                        "component": e.component,
                    }
                    for e in self.errors.recent(10)
                ],
            },
        }


# ── Singleton ─────────────────────────────────────────────────────

_instance: Optional[MetricsCollector] = None
_instance_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    """Get or create the global metrics collector."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = MetricsCollector()
    return _instance
