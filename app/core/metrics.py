"""
In-process metrics registry exposed to the admin Telegram dashboard.

Why in-process and not Prometheus:
- The bot is single-instance (advisory lock in main.py), so a shared registry
  is acceptable.
- The operator wants metrics inside the existing admin dashboard, not an
  external Grafana stack.
- Memory footprint is bounded (counters: ~24 bytes/key, histograms ~1 KB).

Concepts:
- ``Counter``       — monotonic; ``inc(label)``, ``rate(window_s)``.
- ``Gauge``         — current value; ``set(value)``.
- ``Histogram``     — sliding-window samples for latency / size; computes
                       p50 / p95 / p99 over the configured window (default 300 s).

Thread safety: not strictly required (asyncio is single-threaded), but the
registry is annotated as such so future moves to threadpool execution don't
silently corrupt counters.

Public API:
    metrics.counter(name, labels={}).inc()
    metrics.histogram(name, labels={}).observe(elapsed_ms)
    metrics.gauge(name).set(value)
    metrics.snapshot()  # returns dict-of-dict for the admin dashboard.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Tuple


_LabelKey = Tuple[Tuple[str, str], ...]


def _label_key(labels: Optional[Dict[str, str]]) -> _LabelKey:
    if not labels:
        return ()
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


@dataclass
class Counter:
    name: str
    description: str = ""
    # Per-label-set: monotonic total + per-second rate via sliding window.
    _totals: Dict[_LabelKey, int] = field(default_factory=lambda: defaultdict(int))
    _events: Dict[_LabelKey, Deque[float]] = field(
        default_factory=lambda: defaultdict(lambda: deque(maxlen=10_000))
    )

    def inc(self, n: int = 1, labels: Optional[Dict[str, str]] = None) -> None:
        key = _label_key(labels)
        self._totals[key] += n
        events = self._events[key]
        now = time.monotonic()
        # Append n separate timestamps if n > 1, but cap to avoid blow-up.
        events.append(now)
        if n > 1:
            # For rate, we care about events; record each so rate(N) is accurate.
            for _ in range(min(n - 1, 100)):
                events.append(now)

    def total(self, labels: Optional[Dict[str, str]] = None) -> int:
        return self._totals.get(_label_key(labels), 0)

    def rate(self, window_s: float = 60.0, labels: Optional[Dict[str, str]] = None) -> float:
        key = _label_key(labels)
        events = self._events.get(key)
        if not events:
            return 0.0
        cutoff = time.monotonic() - window_s
        # Pop stale events from the left in O(k) — k = stale count.
        while events and events[0] < cutoff:
            events.popleft()
        return len(events) / window_s if window_s > 0 else 0.0

    def label_keys(self) -> List[_LabelKey]:
        return list(self._totals.keys())


@dataclass
class Gauge:
    name: str
    description: str = ""
    _values: Dict[_LabelKey, float] = field(default_factory=dict)

    def set(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        self._values[_label_key(labels)] = float(value)

    def get(self, labels: Optional[Dict[str, str]] = None) -> Optional[float]:
        return self._values.get(_label_key(labels))

    def items(self) -> List[Tuple[_LabelKey, float]]:
        return list(self._values.items())


@dataclass
class Histogram:
    """Sliding-window histogram for latency observations (milliseconds).

    Stores raw samples within ``window_s``. Memory is bounded by ``max_samples``
    (default 5000 per label-set) — overflow is dropped oldest-first.
    """

    name: str
    description: str = ""
    window_s: float = 300.0
    max_samples: int = 5_000
    _samples: Dict[_LabelKey, Deque[Tuple[float, float]]] = field(
        default_factory=lambda: defaultdict(deque)
    )

    def observe(self, value_ms: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = _label_key(labels)
        samples = self._samples[key]
        samples.append((time.monotonic(), float(value_ms)))
        # Cap samples to bounded memory.
        while len(samples) > self.max_samples:
            samples.popleft()

    def quantiles(
        self,
        quantiles: Iterable[float] = (0.5, 0.95, 0.99),
        labels: Optional[Dict[str, str]] = None,
    ) -> Dict[float, Optional[float]]:
        key = _label_key(labels)
        samples = self._samples.get(key)
        if not samples:
            return {q: None for q in quantiles}
        cutoff = time.monotonic() - self.window_s
        # Drop stale.
        while samples and samples[0][0] < cutoff:
            samples.popleft()
        if not samples:
            return {q: None for q in quantiles}
        values = sorted(s[1] for s in samples)
        out: Dict[float, Optional[float]] = {}
        n = len(values)
        for q in quantiles:
            if not 0 <= q <= 1:
                out[q] = None
                continue
            idx = int(q * (n - 1))
            out[q] = values[idx]
        return out

    def count(self, labels: Optional[Dict[str, str]] = None) -> int:
        return len(self._samples.get(_label_key(labels), ()))

    def label_keys(self) -> List[_LabelKey]:
        return list(self._samples.keys())


class _Registry:
    """Singleton metric registry."""

    def __init__(self) -> None:
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}

    def counter(self, name: str, description: str = "") -> Counter:
        c = self._counters.get(name)
        if c is None:
            c = Counter(name=name, description=description)
            self._counters[name] = c
        return c

    def gauge(self, name: str, description: str = "") -> Gauge:
        g = self._gauges.get(name)
        if g is None:
            g = Gauge(name=name, description=description)
            self._gauges[name] = g
        return g

    def histogram(
        self,
        name: str,
        description: str = "",
        window_s: float = 300.0,
        max_samples: int = 5_000,
    ) -> Histogram:
        h = self._histograms.get(name)
        if h is None:
            h = Histogram(
                name=name,
                description=description,
                window_s=window_s,
                max_samples=max_samples,
            )
            self._histograms[name] = h
        return h

    def snapshot(self) -> Dict[str, Dict]:
        """Snapshot for the admin dashboard. Cheap enough to call per refresh."""
        out_counters: Dict[str, Dict] = {}
        for name, c in self._counters.items():
            entries = {}
            for lk in c.label_keys():
                labels = dict(lk)
                entries[" ".join(f"{k}={v}" for k, v in lk) or "_"] = {
                    "labels": labels,
                    "total": c.total(labels),
                    "rate_60s": round(c.rate(60.0, labels), 3),
                    "rate_5m":  round(c.rate(300.0, labels), 3),
                }
            out_counters[name] = entries

        out_gauges: Dict[str, Dict] = {}
        for name, g in self._gauges.items():
            out_gauges[name] = {
                " ".join(f"{k}={v}" for k, v in lk) or "_": value
                for lk, value in g.items()
            }

        out_histograms: Dict[str, Dict] = {}
        for name, h in self._histograms.items():
            entries = {}
            for lk in h.label_keys():
                labels = dict(lk)
                qs = h.quantiles((0.5, 0.95, 0.99), labels=labels)
                entries[" ".join(f"{k}={v}" for k, v in lk) or "_"] = {
                    "labels": labels,
                    "count": h.count(labels),
                    "p50_ms": qs[0.5],
                    "p95_ms": qs[0.95],
                    "p99_ms": qs[0.99],
                }
            out_histograms[name] = entries

        return {
            "counters": out_counters,
            "gauges": out_gauges,
            "histograms": out_histograms,
        }

    def reset(self) -> None:
        """Test-only. Wipe all metrics."""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()


_registry = _Registry()


def get_registry() -> _Registry:
    return _registry


# Module-level convenience helpers used by middleware / handlers.
def counter(name: str, description: str = "") -> Counter:
    return _registry.counter(name, description)


def gauge(name: str, description: str = "") -> Gauge:
    return _registry.gauge(name, description)


def histogram(
    name: str,
    description: str = "",
    window_s: float = 300.0,
    max_samples: int = 5_000,
) -> Histogram:
    return _registry.histogram(name, description, window_s, max_samples)


def snapshot() -> Dict[str, Dict]:
    return _registry.snapshot()


# Metric name conventions (single source of truth for instrumentation).
class M:
    HANDLER_TOTAL = "handler_total"                # counter labels: handler, outcome
    HANDLER_LATENCY_MS = "handler_latency_ms"      # histogram labels: handler
    WORKER_ITERATION_TOTAL = "worker_iteration_total"      # labels: worker, outcome
    WORKER_ITERATION_LATENCY_MS = "worker_iteration_latency_ms"  # labels: worker
    PAYMENT_INTENT_TOTAL = "payment_intent_total"  # labels: provider, outcome
    PAYMENT_PROVIDER_LATENCY_MS = "payment_provider_latency_ms"  # labels: provider
    SUBSCRIPTION_GRANT_TOTAL = "subscription_grant_total"  # labels: source, outcome
    TRIAL_ACTIVATED_TOTAL = "trial_activated_total"
    REFERRAL_REGISTERED_TOTAL = "referral_registered_total"
    REFERRAL_ACTIVATED_TOTAL = "referral_activated_total"
    RATE_LIMIT_HIT_TOTAL = "rate_limit_hit_total"  # labels: action
    DB_POOL_ACQUIRE_WAIT_MS = "db_pool_acquire_wait_ms"
    DB_POOL_SIZE = "db_pool_size"                  # gauge
    DB_POOL_FREE = "db_pool_free"                  # gauge
    WEBHOOK_DELIVERED_TOTAL = "webhook_delivered_total"  # labels: provider, outcome
