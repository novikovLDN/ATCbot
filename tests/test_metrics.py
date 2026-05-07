"""Unit tests for app.core.metrics."""
from __future__ import annotations

import time

import pytest

from app.core import metrics as m


class TestCounter:
    def test_inc_and_total(self):
        c = m.counter("t_counter", "")
        c.inc()
        c.inc(2, labels={"k": "v"})
        assert c.total() == 1
        assert c.total(labels={"k": "v"}) == 2
        assert c.total(labels={"k": "z"}) == 0

    def test_rate_window_decays(self, monkeypatch):
        c = m.counter("t_rate", "")
        # Fix monotonic clock to control sliding window.
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        for _ in range(10):
            c.inc()
        assert c.rate(window_s=60.0) == pytest.approx(10 / 60, rel=1e-6)
        # Advance past window.
        clock[0] = 1100.0
        assert c.rate(window_s=60.0) == 0.0


class TestGauge:
    def test_set_get(self):
        g = m.gauge("t_gauge", "")
        g.set(42.0)
        g.set(99.0, labels={"node": "a"})
        assert g.get() == 42.0
        assert g.get(labels={"node": "a"}) == 99.0
        assert g.get(labels={"node": "missing"}) is None


class TestHistogram:
    def test_observe_and_quantiles(self, monkeypatch):
        h = m.histogram("t_hist", "", window_s=60.0)
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        for v in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
            h.observe(v)
        qs = h.quantiles((0.5, 0.95, 0.99))
        assert qs[0.5] in (50, 60)  # discrete index, n=10
        assert qs[0.95] >= 90
        assert qs[0.99] >= 90

    def test_window_drops_stale_samples(self, monkeypatch):
        h = m.histogram("t_hist_window", "", window_s=10.0)
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        h.observe(100)
        clock[0] += 100  # past window
        qs = h.quantiles((0.5,))
        assert qs[0.5] is None
        assert h.count() == 0


class TestSnapshot:
    def test_snapshot_shape(self):
        m.counter("snap_c").inc(labels={"x": "1"})
        m.gauge("snap_g").set(7)
        m.histogram("snap_h").observe(123.0, labels={"y": "z"})
        snap = m.snapshot()
        assert "counters" in snap and "gauges" in snap and "histograms" in snap
        assert "snap_c" in snap["counters"]
        assert "snap_g" in snap["gauges"]
        assert "snap_h" in snap["histograms"]
