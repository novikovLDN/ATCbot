"""Dashboard v4 definitions that need no database: pure functions,
worker liveness, overall system status, and the new routes (mocked)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core import runtime_health as rh
from app.services import system_health as sh
from database import metrics as mx
from database import revenue as rev

UTC = timezone.utc
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


class TestProviderRow:
    def test_abandoned_counts_as_unpaid(self):
        r = rev.provider_row({"provider": "telegram", "created": 10, "paid": 6, "expired": 1,
                              "abandoned": 2, "pending": 1, "invoiced_users": 8, "paid_users": 6})
        assert r["provider"] == "telegram_payment"
        assert r["expired"] == 3 and r["abandoned"] == 2 and r["expired_marked"] == 1
        assert r["success_rate"] == 66.7  # 6 / (6 + 3)
        assert r["conversion"] == 60.0    # 6 / 10
        assert r["user_conversion"] == 75.0

    def test_nothing_created(self):
        r = rev.provider_row({"provider": None})
        assert r["provider"] == "unknown" and r["conversion"] is None and r["success_rate"] is None


class TestTrials:
    def test_rates_use_matured_cohorts(self):
        t = mx.summarize_trials({"trials": 10, "matured_7d": 4, "matured_30d": 0, "paid_7d": 5,
                                 "paid_7d_matured": 2, "paid_30d": 6, "paid_30d_matured": 0, "paid_any": 6})
        assert t["rate_7d"] == 50.0 and t["rate_30d"] is None and t["rate_any"] == 60.0


class TestMotionAndGrowth:
    def test_motion(self):
        m = mx.summarize_motion({"new_n": 3, "new_k": 60_000, "new_users": 3, "renew_n": 1, "renew_k": 20_000},
                                {"count": 2, "kopecks": 40_000})
        assert m["new_share"] == 75.0 and m["auto_renew_balance"] == {"count": 2, "kopecks": 40_000}

    def test_growth(self):
        g = mx.growth({"new": {"users": 5}}, {"churned": 7, "open": 2})
        assert g == {"new_paying": 5, "churned": 7, "net": -2, "undecided": 2}


class TestRenewalPrice:
    @pytest.mark.parametrize("last,sub,combo,flag,price", [
        ("basic_30", "basic", False, True, 199),
        ("plus_90", "plus", False, True, 899),
        ("3", "basic", False, True, 499),          # legacy months
        ("traffic_15gb", "basic", False, True, 199),  # not a subscription → basic 30
        (None, "plus", False, True, 199),          # no approved payment → basic 30
        ("basic_30", "basic", True, True, 329),    # combo price with the outbox on
        ("basic_30", "basic", True, False, 199),   # legacy path bills the plain tariff
        ("plus_90", "plus", True, True, 1299),
        ("biz_team_30", "biz_team", False, True, 349),  # legacy biz renews at the Plus price
    ])
    def test_mirrors_auto_renewal(self, last, sub, combo, flag, price):
        assert mx.renewal_list_price_rub(last, sub, combo, flag) == price


class TestPipeline:
    def test_summary(self):
        subs = [
            {"telegram_id": 1, "expires_at": NOW + timedelta(days=1), "source": "payment", "auto_renew": True,
             "has_uuid": True, "subscription_type": "basic", "is_combo": False, "balance": 50_000},
            {"telegram_id": 2, "expires_at": NOW + timedelta(days=2), "source": "payment", "auto_renew": True,
             "has_uuid": True, "subscription_type": "plus", "is_combo": False, "balance": 0},
            {"telegram_id": 3, "expires_at": NOW + timedelta(days=3), "source": "trial", "auto_renew": False,
             "has_uuid": True, "subscription_type": "basic", "is_combo": False, "balance": 0},
        ]
        p = mx.summarize_pipeline(subs, {1: "basic_30", 2: "plus_30"}, NOW)
        assert p["expiring"] == 3 and p["auto_renew"] == 2 and p["manual"] == 1
        assert p["expected_list_kopecks"] == 19_900 + 34_900
        assert p["covered"] == 1 and p["shortfall_kopecks"] == 34_900
        assert len(p["by_day"]) == 8 and p["by_day"][0]["date"] == "2026-09-13"


class TestSilence:
    def test_rare_provider_not_judged(self):
        assert mx.provider_silence(None, 3, NOW)["state"] == "rare"

    def test_silent_and_ok(self):
        # 42 paid / week → expected gap 4 h → threshold 16 h.
        assert mx.provider_silence(NOW - timedelta(hours=20), 42, NOW)["state"] == "silent"
        assert mx.provider_silence(NOW - timedelta(hours=10), 42, NOW) == {
            "state": "ok", "age_h": 10.0, "threshold_h": 16.0}

    def test_minimum_threshold_and_disabled(self):
        assert mx.provider_silence(NOW - timedelta(hours=2), 500, NOW)["threshold_h"] == 3.0
        assert mx.provider_silence(None, 100, NOW, enabled=False)["state"] == "disabled"


class TestVerdicts:
    def test_payments(self):
        v = mx.payments_verdict({
            "errors_24h": {"total": 12, "by_stage": [{"stage": "amount_mismatch", "count": 12}]},
            "providers": [{"provider": "wata", "state": "silent", "age_h": 30.0, "threshold_h": 12.0}],
            "providers_7d": [{"provider": "cryptobot", "paid": 5, "expired": 20, "success_rate": 20.0}],
        })
        assert v["status"] == "critical"
        assert [r["key"] for r in v["reasons"]] == ["payment_errors", "silent_wata", "low_sr_cryptobot"]

    def test_payments_ok(self):
        assert mx.payments_verdict({"errors_24h": {"total": 0}})["status"] == "ok"

    def test_delivery(self):
        v = mx.delivery_verdict({"queue": {"available": True, "dead": 0, "retrying": 0, "oldest_open_age_s": 2000},
                                 "activations": {"pending": 0}, "errors_24h": {}})
        assert v["status"] == "warning" and v["reasons"][0]["key"] == "queue_wait"


class TestErrorsSummary:
    def test_summary(self):
        s = mx.summarize_errors([
            {"stage": "amount_mismatch", "provider": "wata", "n": 2, "last_at": NOW},
            {"stage": "telegram_payment_rejected", "provider": "telegram_payment", "n": 1, "last_at": None},
            {"stage": "amount_mismatch", "provider": "platega", "n": 1, "last_at": NOW},
        ])
        assert s["total"] == 4 and s["telegram_money"] == 1
        assert s["by_stage"][0] == {"stage": "amount_mismatch", "count": 3}


class TestWorkerLiveness:
    def setup_method(self):
        rh.reset()

    def test_states(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(rh.time, "monotonic", lambda: clock[0])
        rh.register("w", interval_s=60, initial_delay_s=10)
        w = rh._workers["w"]
        assert rh.worker_state(w, clock[0]) == "starting"
        assert rh.worker_state(w, clock[0] + 10 + 60 + rh.START_GRACE_S + 1) == "stale"
        rh.record("w", "success")
        assert rh.worker_state(w, clock[0] + 30) == "ok"
        clock[0] += 60
        rh.record("w", "failed", ValueError("user 123 secret"))
        assert rh.worker_state(w, clock[0]) == "ok"  # one failure is not "failing"
        clock[0] += 60
        rh.record("w", "timeout")
        assert rh.worker_state(w, clock[0]) == "failing"
        assert w.last_error == "timeout"
        clock[0] += 60
        rh.record("w", "skipped")
        assert rh.worker_state(w, clock[0]) == "paused"
        rh.record("w", "no_work")  # worker-specific completion label = finished normally
        assert rh.worker_state(w, clock[0]) == "ok"
        assert rh.worker_state(w, clock[0] + 60 * rh.STALE_FACTOR + rh.START_GRACE_S + 1) == "stale"

    def test_error_text_is_not_kept(self):
        rh.register("x", interval_s=60)
        rh.record("x", "failed", RuntimeError("telegram_id=42 card=4111"))
        assert rh.snapshot()[0]["last_error"] == "RuntimeError"

    def test_unregistered_and_cancelled_are_ignored(self):
        rh.record("nope", "success")
        rh.register("y", interval_s=60)
        rh.record("y", "cancelled")
        assert rh.snapshot()[0]["ok_count"] == 0

    def test_alerts(self):
        rh.record_alert("payment")
        rh.record_alert("payment")
        rh.record_alert("worker")
        s = rh.alerts_summary()
        assert s["total"] == 3 and s["by_category"] == {"payment": 2, "worker": 1}


class TestOverallStatus:
    def base(self, **kw):
        s = {"db": {"ready": True, "ok": True, "pool": {"in_use": 2, "max": 50}},
             "webhook": {"ok": True, "url_set": True, "pending_update_count": 0, "last_error_age_s": None},
             "remnawave": {"enabled": True, "ok": True, "latency_ms": 120},
             "redis": {"configured": False}, "workers": [], "flags": {}}
        s.update(kw)
        return s

    def test_ok(self):
        assert sh.overall_status(self.base()) == {"status": "ok", "reasons": []}

    def test_db_down(self):
        o = sh.overall_status(self.base(db={"ready": False}))
        assert o["status"] == "down" and o["reasons"][0]["key"] == "db_not_ready"

    def test_webhook_backlog_and_recent_error(self):
        o = sh.overall_status(self.base(webhook={"ok": True, "url_set": True, "pending_update_count": 80,
                                                 "last_error_age_s": 120, "last_error_message": "timeout"}))
        assert o["status"] == "degraded"
        assert {r["key"] for r in o["reasons"]} == {"webhook_backlog", "webhook_error"}

    def test_webhook_unset_is_down(self):
        o = sh.overall_status(self.base(webhook={"ok": True, "url_set": False, "pending_update_count": 0}))
        assert o["status"] == "down"

    def test_panel_pool_worker(self):
        o = sh.overall_status(self.base(
            db={"ready": True, "ok": True, "pool": {"in_use": 45, "max": 50}},
            remnawave={"enabled": True, "ok": False, "error": "timeout"},
            workers=[{"name": "auto_renewal", "state": "stale", "consecutive_fails": 0},
                     {"name": "reminders", "state": "paused", "consecutive_fails": 0}],
        ))
        keys = [r["key"] for r in o["reasons"]]
        assert o["status"] == "degraded"
        assert {"db_pool", "remnawave_down", "worker_auto_renewal"} <= set(keys)
        assert o["reasons"][-1]["level"] == "info"  # paused worker is information only


# ── Routes (mocked) ───────────────────────────────────────────────────

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI  # noqa: E402

from app.api.dashboard.deps import require_admin  # noqa: E402
from app.api.dashboard.routes import metrics as routes  # noqa: E402
from app.services import dashboard_cache  # noqa: E402


def _client(authed=True):
    app = FastAPI()
    app.include_router(routes.router, prefix="/metrics")
    if authed:
        app.dependency_overrides[require_admin] = lambda: {"sub": 1, "role": "admin", "auth": "session"}
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_cache():
    dashboard_cache.clear()
    yield
    dashboard_cache.clear()


@pytest.mark.parametrize("path", ["/metrics/health", "/metrics/payments-health", "/metrics/delivery",
                                  "/metrics/engagement", "/metrics/pipeline", "/metrics/overview"])
def test_new_routes_require_admin(path):
    assert _client(authed=False).get(path).status_code == 401


def test_payments_health_route_adds_silence(monkeypatch):
    raw = {"now": NOW.isoformat(), "providers_24h": [], "providers_7d": [],
           "errors_24h": {"total": 0, "by_stage": [], "by_provider": [], "matrix": [], "telegram_money": 0},
           "stuck": [], "time_to_pay": [],
           "last_paid": {"platega": (NOW - timedelta(hours=30)).isoformat()},
           "paid_7d": {"platega": 70}}
    monkeypatch.setattr(mx, "payments_health", AsyncMock(return_value=raw))
    monkeypatch.setattr(routes, "provider_enabled", lambda: {"platega": True, "wata": False})
    body = _client().get("/metrics/payments-health").json()
    pl = next(p for p in body["providers"] if p["provider"] == "platega")
    assert pl["state"] == "silent" and pl["threshold_h"] == 9.6
    assert next(p for p in body["providers"] if p["provider"] == "wata")["state"] == "disabled"
    assert body["verdict"]["status"] == "warning"


def test_engagement_route_window(monkeypatch):
    eng = AsyncMock(return_value={"reminders": {"users": 1}})
    monkeypatch.setattr(mx, "engagement", eng)
    body = _client().get("/metrics/engagement?days=30").json()
    assert body == {"window_days": 30, "reminders": {"users": 1}}
    since, until = eng.await_args.args
    assert (until - since) > timedelta(days=29)


def test_health_route_uses_collect(monkeypatch):
    monkeypatch.setattr(sh, "collect", AsyncMock(return_value={"overall": {"status": "ok", "reasons": []}}))
    assert _client().get("/metrics/health").json()["overall"]["status"] == "ok"


def test_overview_survives_a_failing_section(monkeypatch):
    totals = rev.empty_totals()
    monkeypatch.setattr(rev, "totals", AsyncMock(return_value=totals))
    monkeypatch.setattr(rev, "payers", AsyncMock(return_value={"payers": 0, "new": 0, "returning": 0}))
    monkeypatch.setattr(rev, "series", AsyncMock(return_value=[]))
    monkeypatch.setattr(mx, "new_users", AsyncMock(return_value=0))
    monkeypatch.setattr(mx, "active_subscriptions", AsyncMock(return_value=mx.summarize_active([])))
    monkeypatch.setattr(mx, "renewals", AsyncMock(return_value=mx.summarize_renewals({}, 3)))
    monkeypatch.setattr(mx, "renewal_pipeline", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(mx, "payments_health", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(mx, "delivery_health", AsyncMock(return_value={
        "queue": {"available": False}, "activations": {"pending": 0}, "errors_24h": {}, "dead_jobs": []}))
    monkeypatch.setattr(sh, "collect", AsyncMock(return_value={"overall": {"status": "ok", "reasons": []}}))
    monkeypatch.setattr(routes, "_panel_quick", AsyncMock(return_value=(None, None)))
    body = _client().get("/metrics/overview?days=1").json()
    assert [k["key"] for k in body["kpis"]] == ["revenue", "payments", "payers", "new_payers", "new_users", "arppu"]
    assert body["payments"]["status"] == "unknown"
    assert body["subscribers"]["pipeline"] is None
    assert body["health"]["status"] == "ok"
