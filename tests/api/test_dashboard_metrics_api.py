"""Dashboard v3 metric endpoints: thin routes over the canonical
definitions, panel reads cached and read-only, legacy shapes aligned."""
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI  # noqa: E402

from app.api.dashboard.deps import require_admin  # noqa: E402
from app.api.dashboard.routes import metrics as metrics_routes  # noqa: E402
from app.api.dashboard.routes import panel as panel_routes  # noqa: E402
from app.api.dashboard.routes import payments as payments_routes  # noqa: E402
from app.api.dashboard.routes import stats as stats_routes  # noqa: E402
from app.services import dashboard_cache, panel_stats, remnawave_api  # noqa: E402
from database import metrics as mx  # noqa: E402
from database import revenue as rev  # noqa: E402


def _client(authed: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(metrics_routes.router, prefix="/metrics")
    app.include_router(panel_routes.router, prefix="/panel")
    app.include_router(stats_routes.router, prefix="/stats")
    app.include_router(payments_routes.router, prefix="/payments")
    if authed:
        app.dependency_overrides[require_admin] = lambda: {"sub": 1, "role": "admin", "auth": "session"}
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_cache():
    dashboard_cache.clear()
    yield
    dashboard_cache.clear()


def test_requires_admin():
    resp = _client(authed=False).get("/metrics/money")
    assert resp.status_code == 401


def test_money_delegates_and_caches(monkeypatch):
    report = AsyncMock(return_value={"window_days": 7, "current": {"net_kopecks": 100}})
    monkeypatch.setattr(rev, "money_report", report)
    c = _client()
    assert c.get("/metrics/money?days=7").json()["current"]["net_kopecks"] == 100
    c.get("/metrics/money?days=7")
    report.assert_awaited_once_with(7)


def test_money_validates_window():
    assert _client().get("/metrics/money?days=0").status_code == 422
    assert _client().get("/metrics/money?days=400").status_code == 422


def test_money_failure_is_500_without_leaking(monkeypatch):
    monkeypatch.setattr(rev, "money_report", AsyncMock(side_effect=RuntimeError("password=secret")))
    resp = _client().get("/metrics/money")
    assert resp.status_code == 500
    assert "secret" not in resp.text


def test_subscribers_passes_grace(monkeypatch):
    rep = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(mx, "subscribers_report", rep)
    assert _client().get("/metrics/subscribers?days=14&grace_days=5").json() == {"ok": True}
    rep.assert_awaited_once_with(14, 5)


def test_cohorts(monkeypatch):
    monkeypatch.setattr(rev, "cohort_ltv", AsyncMock(return_value={"months": 6, "cohorts": []}))
    assert _client().get("/metrics/cohorts?months=6").json()["months"] == 6


def test_series_unit_whitelist(monkeypatch):
    monkeypatch.setattr(rev, "series", AsyncMock(return_value=[]))
    assert _client().get("/metrics/series?unit=week&periods=12").status_code == 200
    assert _client().get("/metrics/series?unit=hour").status_code == 422


def test_operations(monkeypatch):
    import database
    monkeypatch.setattr(database, "get_payment_errors_summary",
                        AsyncMock(return_value={"total": 2, "by_stage": [], "by_provider": []}), raising=False)
    monkeypatch.setattr(database, "get_recent_payment_errors",
                        AsyncMock(return_value=[{"id": 1, "raw_payload": "{}", "stage": "x"}]), raising=False)
    monkeypatch.setattr(mx, "payment_errors_daily", AsyncMock(return_value=[]))
    monkeypatch.setattr(mx, "provisioning_queue", AsyncMock(return_value={"available": False}))
    monkeypatch.setattr(mx, "operations_snapshot", AsyncMock(return_value={"pending_invoices": 1, "stuck_activations": 0}))
    body = _client().get("/metrics/operations?hours=24").json()
    assert body["errors"]["total"] == 2
    assert body["provisioning"] == {"available": False}
    assert "raw_payload" not in body["errors_recent"][0]


# ── Alerts (pure) ─────────────────────────────────────────────────────

_BASE = dict(
    money={"providers": [], "delta_pct": {"net": 5}},
    active={"expiring_7d": {"total": 0}},
    errors={"total": 0},
    queues={"stuck_activations": 0},
    provisioning={"available": False},
    panel={"available": True},
    nodes={"available": True, "offline": 0},
)


def test_no_alerts_when_healthy():
    assert metrics_routes.build_alerts(**_BASE) == []


def test_alerts_ordered_by_severity():
    args = dict(_BASE)
    args.update(
        errors={"total": 3, "by_stage": [{"stage": "amount_mismatch", "count": 3}]},
        nodes={"available": True, "offline": 2},
        active={"expiring_7d": {"total": 10, "auto_renew_on": 4}},
        money={"providers": [{"provider": "wata", "paid": 3, "expired": 9, "success_rate": 25.0}],
               "delta_pct": {"net": -40}},
    )
    alerts = metrics_routes.build_alerts(**args)
    keys = [a["key"] for a in alerts]
    assert keys[0] == "nodes_offline"
    assert "payment_errors" in keys and "provider_wata" in keys and "revenue_drop" in keys
    assert alerts[-1]["key"] == "expiring"
    assert "6" in alerts[-1]["detail"]


def test_panel_down_is_critical():
    args = dict(_BASE, panel={"available": False})
    alerts = metrics_routes.build_alerts(**args)
    assert alerts[0]["key"] == "panel_down" and alerts[0]["level"] == "critical"


def test_small_provider_sample_not_alerted():
    args = dict(_BASE, money={"providers": [{"provider": "x", "paid": 1, "expired": 3, "success_rate": 25.0}],
                              "delta_pct": {}})
    assert metrics_routes.build_alerts(**args) == []


# ── Panel ─────────────────────────────────────────────────────────────

SYSTEM = {
    "users": {"statusCounts": {"ACTIVE": 180, "LIMITED": 15, "EXPIRED": 40, "DISABLED": 5}, "totalUsers": 240},
    "onlineStats": {"lastDay": 90, "lastWeek": 150, "neverOnline": 20, "onlineNow": 33},
    "nodes": {"totalOnline": 3, "totalBytesLifetime": "5497558138880"},
    "memory": {"total": 8, "free": 2, "used": 6}, "cpu": {"cores": 4}, "uptime": 1000, "timestamp": 1,
}
BANDWIDTH = {k: {"current": "1.5 TiB", "previous": "1 TiB", "difference": "512 GiB"} for k in (
    "bandwidthLastTwoDays", "bandwidthLastSevenDays", "bandwidthLast30Days",
    "bandwidthCalendarMonth", "bandwidthCurrentYear")}
HWID = {"byPlatform": [{"platform": "ios", "count": 10, "byApp": []}, {"platform": "android", "count": 30, "byApp": []}],
        "stats": {"totalUniqueDevices": 40, "totalHwidDevices": 44, "averageHwidDevicesPerUser": 1.2}}
NODES = [
    {"uuid": "a", "name": "NL-1", "countryCode": "NL", "isConnected": True, "isDisabled": False,
     "isConnecting": False, "usersOnline": 20, "trafficUsedBytes": 100, "trafficLimitBytes": None,
     "xrayUptime": 50, "tags": []},
    {"uuid": "b", "name": "DE-1", "countryCode": "DE", "isConnected": False, "isDisabled": False,
     "isConnecting": False, "usersOnline": 0, "trafficUsedBytes": 5, "trafficLimitBytes": 1000,
     "xrayUptime": 0, "tags": [], "lastStatusMessage": "timeout"},
]


@pytest.fixture
def panel(monkeypatch):
    mocks = {
        "get_system_stats": AsyncMock(return_value=SYSTEM),
        "get_bandwidth_stats": AsyncMock(return_value=BANDWIDTH),
        "get_hwid_stats": AsyncMock(return_value=HWID),
        "get_nodes": AsyncMock(return_value=NODES),
        "get_nodes_metrics": AsyncMock(return_value={"nodes": []}),
        "get_nodes_usage": AsyncMock(return_value={"categories": ["2026-09-12"], "sparklineData": [10],
                                                   "topNodes": [], "series": []}),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(remnawave_api, name, m)
    monkeypatch.setattr(mx, "panel_entity_counts", AsyncMock(return_value={"premium_active": 120, "bypass_entities": 70}))
    return mocks


def test_panel_overview_and_discrepancy(panel):
    body = _client().get("/panel/overview").json()
    assert body["system"]["status_counts"]["ACTIVE"] == 180
    assert body["system"]["traffic_lifetime_bytes"] == 5 * 1024 ** 4
    assert body["bandwidth"]["week"]["current_bytes"] == int(1.5 * 1024 ** 4)
    assert body["hwid"]["by_platform"][0]["platform"] == "android"
    d = body["discrepancy"]
    assert d["db_expected"] == 190
    assert d["difference"] == 180 + 15 - 190


def test_panel_calls_are_cached(panel):
    c = _client()
    c.get("/panel/overview")
    c.get("/panel/overview")
    panel["get_system_stats"].assert_awaited_once()


def test_panel_unavailable_degrades(panel):
    panel["get_system_stats"].return_value = None
    body = _client().get("/panel/overview").json()
    assert body["system"] == {"available": False}
    assert body["discrepancy"] == {"available": False}


def test_panel_nodes(panel):
    body = _client().get("/panel/nodes").json()
    assert body["total"] == 2 and body["online"] == 1 and body["offline"] == 1
    assert body["nodes"][0]["name"] == "DE-1"  # offline first
    assert body["users_online"] == 20


def test_panel_bandwidth(panel):
    body = _client().get("/panel/bandwidth?days=7").json()
    assert body["available"] and body["total"] == [10]
    args = panel["get_nodes_usage"].await_args.args
    assert len(args[0]) == 10 and len(args[1]) == 10  # YYYY-MM-DD


async def test_nodes_usage_window_ends_on_the_msk_date_not_the_host_date(monkeypatch):
    """P2-17: date.today() is the host-local date. At 22:30 UTC it is already the
    next day in MSK (the admin's day, as everywhere on the dashboard)."""
    from datetime import datetime as real_datetime, timezone

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            t = real_datetime(2031, 1, 1, 22, 30, tzinfo=timezone.utc)   # 01:30 MSK, 2031-01-02
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)
    monkeypatch.setattr(panel_stats, "datetime", FrozenDatetime, raising=False)
    usage = AsyncMock(return_value=None)
    monkeypatch.setattr(remnawave_api, "get_nodes_usage", usage)
    monkeypatch.setattr(panel_stats, "cached", lambda key, ttl, load: load())

    await panel_stats.nodes_usage(7)

    assert usage.await_args.args == ("2030-12-27", "2031-01-02")


@pytest.mark.parametrize("raw,expected", [
    ("0", 0), ("123", 123), ("1 KiB", 1024), ("1.5 GiB", int(1.5 * 1024 ** 3)),
    ("2 TB", 2 * 1024 ** 4), (None, None), ("n/a", None), (42, 42),
])
def test_parse_size(raw, expected):
    assert panel_stats.parse_size(raw) == expected


# ── Legacy endpoints now canonical ─────────────────────────────────────

def test_stats_revenue_is_canonical(monkeypatch):
    monkeypatch.setattr(rev, "all_time", AsyncMock(return_value={
        "net_kopecks": 429_200, "gross_kopecks": 969_100, "payers": 4, "arppu_kopecks": 107_300}))
    monkeypatch.setattr(rev, "users_registered_before", AsyncMock(return_value=100))
    body = _client().get("/stats/revenue").json()
    assert body["total_revenue_rubles"] == 4292.0
    assert body["arppu_rubles"] == 1073.0
    assert body["arpu_rubles"] == 42.92


def test_payments_revenue_headline_excludes_shop(monkeypatch):
    from tests.services.test_revenue_definitions import NATIVE_TOPUPS, PAID_FACTS

    async def fake_totals(since, until=None):
        return rev.summarize_facts(PAID_FACTS + NATIVE_TOPUPS)

    monkeypatch.setattr(rev, "totals", fake_totals)
    body = _client().get("/payments/revenue?hours=24").json()
    assert body["revenue_rubles"] == 4292.0
    assert body["gross_rubles"] == 9691.0
    assert body["shop_rubles"] == 5300.0
    prov = _client().get("/payments/by-provider?hours=24").json()
    assert {p["provider"] for p in prov} >= {"platega", "telegram_payment", "unknown"}
