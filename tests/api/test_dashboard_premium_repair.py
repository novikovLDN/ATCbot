"""/dashboard/api/premium-repair/*: admin-only, CSRF Origin check, Idempotency-Key,
audit log on check / start / resume / pause / stop / CSV; and one end-to-end
pass (check → report → CSV → trial apply → resume) on the panel fake."""
import asyncio
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI  # noqa: E402

import config  # noqa: E402
import database  # noqa: E402
from app.api import dashboard  # noqa: E402
from app.api.dashboard import idempotency, security  # noqa: E402
from app.services import admin_auth, premium_repair_job, purchase_flow, remnawave_api  # noqa: E402
from tests.fakes.premium_repair_world import MemKV, install_world, patches  # noqa: E402

ORIGIN = "http://testserver"
BASE = "/dashboard/api/premium-repair"
STATUS = {
    "running": True, "running_kind": "apply", "rate_per_sec": 2.0, "report": None,
    "check": {"state": "done"},
    "apply": {"state": "running", "total": 4, "done": 0, "fixed": 0, "errors": 0, "skipped": 0, "limit": None},
}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", 1)

    async def lookup(token):
        return 1 if token == "good" else None

    async def no_redis():
        return None

    monkeypatch.setattr(admin_auth, "lookup_session", lookup)
    monkeypatch.setattr(idempotency, "_redis", no_redis)
    monkeypatch.setattr(security, "_redis", no_redis)
    idempotency.clear_memory_state()
    yield
    idempotency.clear_memory_state()


@pytest.fixture
def audit(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(database, "_log_audit_event_atomic_standalone", mock, raising=False)
    return mock


@pytest.fixture
def svc(monkeypatch, audit):
    ok = {"ok": True, "status": STATUS}
    mocks = {
        "start_check": AsyncMock(return_value=ok),
        "start_apply": AsyncMock(return_value=ok),
        "resume": AsyncMock(return_value=ok),
        "pause": AsyncMock(return_value={"ok": True, "status": {**STATUS, "apply": {"state": "paused"}}}),
        "stop": AsyncMock(return_value={"ok": True, "status": {**STATUS, "apply": {"state": "stopped"}}}),
        "get_status": AsyncMock(return_value=STATUS),
        "get_report": AsyncMock(return_value={"kind": "check", "total": 1, "offset": 0, "rows": [{"telegram_id": 5}]}),
        "report_csv": AsyncMock(return_value={"kind": "check", "generated_at": "x", "rows": 1,
                                              "text": "telegram_id,target\n5,2027-01-01\n"}),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(premium_repair_job, name, mock)
    mocks["audit"] = audit
    return mocks


def app() -> FastAPI:
    a = FastAPI()
    a.include_router(dashboard.router, prefix="/dashboard/api")
    return a


def client(cookie="good") -> TestClient:
    c = TestClient(app())
    if cookie:
        c.cookies.set(admin_auth.COOKIE_NAME, cookie)
    return c


def post(c, path, *, origin=ORIGIN, key=None, body=None):
    headers = {"Origin": origin} if origin else {}
    if key:
        headers["Idempotency-Key"] = key
    return c.post(f"{BASE}{path}", headers=headers, json=body if body is not None else {})


def test_not_logged_in_is_401(svc):
    c = client(cookie=None)
    for path in ("/status", "/report", "/report.csv"):
        assert c.get(f"{BASE}{path}").status_code == 401
    assert post(c, "/check").status_code == 401
    assert post(c, "/start").status_code == 401
    svc["start_check"].assert_not_awaited()
    svc["start_apply"].assert_not_awaited()
    svc["report_csv"].assert_not_awaited()


def test_cross_site_post_is_refused(svc):
    for path in ("/check", "/start", "/stop"):
        assert post(client(), path, origin="https://evil.example").status_code == 403
        assert post(client(), path, origin=None).status_code == 403
    svc["start_check"].assert_not_awaited()
    svc["start_apply"].assert_not_awaited()
    svc["stop"].assert_not_awaited()


def test_status_and_report(svc):
    c = client()
    assert c.get(f"{BASE}/status").json() == STATUS
    assert c.get(f"{BASE}/report?offset=0&limit=20").json()["rows"] == [{"telegram_id": 5}]
    assert svc["get_report"].await_args.args == (0, 20)
    assert c.get(f"{BASE}/report?limit=0").status_code == 422


def test_csv_export_is_text_csv_and_audited(svc):
    r = client().get(f"{BASE}/report.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and ".csv" in r.headers["content-disposition"]
    assert r.text.startswith("telegram_id,target")
    assert svc["audit"].await_args.args[0] == "premium_repair_report_csv"


def test_csv_without_a_report_is_404(svc):
    svc["report_csv"].return_value = None
    r = client().get(f"{BASE}/report.csv")
    assert r.status_code == 404 and r.json()["detail"] == "no_report"


@pytest.mark.parametrize("path, action", [
    ("/check", "premium_repair_check_start"),
    ("/start", "premium_repair_apply_start"),
    ("/resume", "premium_repair_apply_resume"),
    ("/pause", "premium_repair_apply_pause"),
    ("/stop", "premium_repair_apply_stop"),
])
def test_actions_are_audited(svc, path, action):
    r = post(client(), path, key=f"k-{action}")
    assert r.status_code == 200 and r.json()["ok"] is True
    args = svc["audit"].await_args.args
    assert args[0] == action and args[1] == 1


def test_start_passes_the_admin_and_the_trial_limit(svc):
    post(client(), "/start", body={"limit": 20})
    assert svc["start_apply"].await_args.args == (1,)
    assert svc["start_apply"].await_args.kwargs == {"limit": 20}
    post(client(), "/start")
    assert svc["start_apply"].await_args.kwargs == {"limit": None}


@pytest.mark.parametrize("limit", [0, -1, "x"])
def test_bad_limit_is_422(svc, limit):
    assert post(client(), "/start", body={"limit": limit}).status_code == 422
    svc["start_apply"].assert_not_awaited()


def test_second_job_is_409_and_not_audited(svc):
    svc["start_apply"].return_value = {"ok": False, "error": "already_running", "status": STATUS}
    r = post(client(), "/start")
    assert r.status_code == 409 and r.json()["detail"] == "already_running"
    svc["audit"].assert_not_awaited()


def test_same_idempotency_key_starts_once(svc):
    c = client()
    a = post(c, "/start", key="repair-key-0001")
    b = post(c, "/start", key="repair-key-0001")
    assert a.status_code == b.status_code == 200
    assert b.headers.get("idempotency-replayed") == "true"
    assert svc["start_apply"].await_count == 1


# ── end to end on the panel fake (same event loop as the job) ─────────

@pytest.fixture
def world(monkeypatch, audit):
    w = install_world(monkeypatch)
    w.store = MemKV()
    monkeypatch.setattr(premium_repair_job, "_store", w.store)
    monkeypatch.setattr(purchase_flow, "_alert_bot", lambda: object())
    premium_repair_job.reset_for_tests()
    yield w
    premium_repair_job.reset_for_tests()


async def _idle():
    for _ in range(1000):
        if not premium_repair_job.is_running():
            return
        await asyncio.sleep(0)
    raise AssertionError("job still running")


async def test_check_report_csv_trial_apply_and_resume(world, audit, monkeypatch):
    gate = asyncio.Event()                     # the panel stream waits → the check is "running"
    real_stream = remnawave_api.get_all_users

    async def gated_stream(*args, **kwargs):
        await gate.wait()
        return await real_stream(*args, **kwargs)

    monkeypatch.setattr(remnawave_api, "get_all_users", gated_stream)
    transport = httpx.ASGITransport(app=app())
    headers = {"Origin": ORIGIN}
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN,
                                 cookies={admin_auth.COOKIE_NAME: "good"}) as c:
        r = await c.post(f"{BASE}/check", headers=headers, json={})
        assert r.status_code == 200 and r.json()["status"]["running_kind"] == "check"
        second = await c.post(f"{BASE}/start", headers=headers, json={})
        assert second.status_code == 409 and second.json()["detail"] == "already_running"
        gate.set()
        await _idle()
        st = (await c.get(f"{BASE}/status")).json()
        assert st["check"]["state"] == "done" and st["check"]["would_fix"] == 4
        rep = (await c.get(f"{BASE}/report?limit=2")).json()
        assert rep["total"] == 4 and len(rep["rows"]) == 2
        csv_r = await c.get(f"{BASE}/report.csv")
        assert csv_r.status_code == 200 and csv_r.text.splitlines()[0].startswith("telegram_id,panel_id")
        assert len(csv_r.text.splitlines()) == 5
        assert world.http.writes() == []

        r = await c.post(f"{BASE}/start", headers={**headers, "Idempotency-Key": "e2e-start-0001"},
                         json={"limit": 1})
        assert r.status_code == 200
        await _idle()
        st = (await c.get(f"{BASE}/status")).json()
        assert (st["apply"]["state"], st["apply"]["fixed"], st["apply"]["limit"]) == ("paused", 1, 1)
        r = await c.post(f"{BASE}/resume", headers=headers, json={})
        assert r.status_code == 200
        await _idle()
        st = (await c.get(f"{BASE}/status")).json()
        assert (st["apply"]["state"], st["apply"]["fixed"]) == ("done", 4)
    assert len(patches(world.http)) == 4
    world.alerts.assert_awaited_once()
    actions = [call.args[0] for call in audit.await_args_list]
    assert actions == ["premium_repair_check_start", "premium_repair_report_csv",
                       "premium_repair_apply_start", "premium_repair_apply_resume"]
