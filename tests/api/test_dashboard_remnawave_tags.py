"""/dashboard/api/remnawave-tags/*: admin-only, CSRF Origin check, Idempotency-Key,
audit log on start / resume / pause / stop."""
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI  # noqa: E402

import config  # noqa: E402
import database  # noqa: E402
from app.api import dashboard  # noqa: E402
from app.api.dashboard import idempotency, security  # noqa: E402
from app.services import admin_auth, remnawave_tags  # noqa: E402

ORIGIN = "http://testserver"
BASE = "/dashboard/api/remnawave-tags"
STATUS = {"state": "running", "running": True, "total": 10, "done": 0, "patched": 0, "errors": 0}


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
def svc(monkeypatch):
    mocks = {
        "start": AsyncMock(return_value={"ok": True, "status": STATUS}),
        "resume": AsyncMock(return_value={"ok": True, "status": STATUS}),
        "pause": AsyncMock(return_value={"ok": True, "status": {**STATUS, "state": "paused"}}),
        "stop": AsyncMock(return_value={"ok": True, "status": {**STATUS, "state": "stopped"}}),
        "get_status": AsyncMock(return_value=STATUS),
        "preview": AsyncMock(return_value={"differ": 3, "tags": []}),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(remnawave_tags, name, mock)
    mocks["audit"] = AsyncMock()
    monkeypatch.setattr(database, "_log_audit_event_atomic_standalone", mocks["audit"], raising=False)
    return mocks


def client(cookie="good") -> TestClient:
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/dashboard/api")
    c = TestClient(app)
    if cookie:
        c.cookies.set(admin_auth.COOKIE_NAME, cookie)
    return c


def post(c, path, *, origin=ORIGIN, key=None):
    headers = {"Origin": origin} if origin else {}
    if key:
        headers["Idempotency-Key"] = key
    return c.post(f"{BASE}{path}", headers=headers, json={})


def test_not_logged_in_is_401(svc):
    c = client(cookie=None)
    assert c.get(f"{BASE}/status").status_code == 401
    assert c.get(f"{BASE}/preview").status_code == 401
    assert post(c, "/start").status_code == 401
    svc["start"].assert_not_awaited()


def test_cross_site_post_is_refused(svc):
    r = post(client(), "/start", origin="https://evil.example")
    assert r.status_code == 403
    svc["start"].assert_not_awaited()
    assert post(client(), "/start", origin=None).status_code == 403


def test_status_and_preview(svc):
    c = client()
    assert c.get(f"{BASE}/status").json() == STATUS
    assert c.get(f"{BASE}/preview").json()["differ"] == 3


def test_preview_unavailable_is_503(svc):
    svc["preview"].side_effect = remnawave_tags.TagBackfillUnavailable("panel_unavailable")
    r = client().get(f"{BASE}/preview")
    assert r.status_code == 503 and r.json()["detail"] == "panel_unavailable"


@pytest.mark.parametrize("path, action", [
    ("/start", "remnawave_tags_backfill_start"),
    ("/resume", "remnawave_tags_backfill_resume"),
    ("/pause", "remnawave_tags_backfill_pause"),
    ("/stop", "remnawave_tags_backfill_stop"),
])
def test_actions_are_audited(svc, path, action):
    r = post(client(), path, key=f"k-{action}")
    assert r.status_code == 200 and r.json()["ok"] is True
    audit = svc["audit"].await_args.args
    assert audit[0] == action and audit[1] == 1


def test_start_passes_the_admin(svc):
    post(client(), "/start")
    assert svc["start"].await_args.args == (1,)


def test_already_running_is_409(svc):
    svc["start"].return_value = {"ok": False, "error": "already_running", "status": STATUS}
    r = post(client(), "/start")
    assert r.status_code == 409 and r.json()["detail"] == "already_running"
    svc["audit"].assert_not_awaited()


def test_same_idempotency_key_starts_once(svc):
    c = client()
    a = post(c, "/start", key="tags-key-0001")
    b = post(c, "/start", key="tags-key-0001")
    assert a.status_code == b.status_code == 200
    assert b.headers.get("idempotency-replayed") == "true"
    assert svc["start"].await_count == 1
