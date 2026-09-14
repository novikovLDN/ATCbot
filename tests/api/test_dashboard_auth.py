"""Dashboard auth hardening (recon P1-1 / P1-2).

Covers: session cookie as the only general auth; the magic-link bearer
accepted only in the bootstrap state and only while fresh; the removed
/auth/verify probe; login / setup rate limits with lockout; the
Origin/Referer CSRF check; cookie-only WebSocket auth.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import config  # noqa: E402
from app.api import dashboard  # noqa: E402
from app.api.dashboard import auth as auth_mod  # noqa: E402
from app.api.dashboard import security  # noqa: E402
from app.api.dashboard import ws as ws_mod  # noqa: E402
from app.services import admin_auth, admin_passkeys, dashboard_cache  # noqa: E402
from database import revenue as rev  # noqa: E402

SECRET = "x" * 48
ADMIN = 1
ORIGIN = "http://testserver"


class State:
    has_password = False
    passkeys = 0


@pytest.fixture(autouse=True)
def env(monkeypatch):
    State.has_password = False
    State.passkeys = 0
    monkeypatch.setattr(config, "JWT_SECRET", SECRET)
    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", ADMIN)
    monkeypatch.setattr(config, "DASHBOARD_BASE_URL", "https://admin.example", raising=False)

    async def lookup(token):
        return ADMIN if token == "good" else None

    async def creds_exist():
        return State.has_password

    async def get_creds():
        return {"username": "boss", "password_hash": "h"} if State.has_password else None

    async def pk_count():
        return State.passkeys

    monkeypatch.setattr(admin_auth, "lookup_session", lookup)
    monkeypatch.setattr(admin_auth, "credentials_exist", creds_exist)
    monkeypatch.setattr(admin_auth, "get_credentials", get_creds)
    monkeypatch.setattr(admin_auth, "verify_password", lambda p, h: p == "correct")
    monkeypatch.setattr(admin_auth, "create_session", AsyncMock(return_value="newtok"))
    monkeypatch.setattr(admin_auth, "revoke_session", AsyncMock())
    monkeypatch.setattr(admin_auth, "set_credentials", AsyncMock(return_value=True))
    monkeypatch.setattr(admin_passkeys, "passkey_count", pk_count)
    monkeypatch.setattr(rev, "money_report", AsyncMock(return_value={"ok": True}))

    async def no_redis():
        return None

    monkeypatch.setattr(security, "_redis", no_redis)
    security.clear_memory_state()
    dashboard_cache.clear()
    yield
    security.clear_memory_state()
    dashboard_cache.clear()


def client() -> TestClient:
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/dashboard/api")
    app.include_router(dashboard.ws_router, prefix="/dashboard")
    return TestClient(app)


def token(iat_delta: timedelta = timedelta(0), ttl: timedelta = timedelta(minutes=15), sub=ADMIN) -> str:
    iat = datetime.now(timezone.utc) + iat_delta
    return jwt.encode({"sub": str(sub), "role": "admin", "iat": iat, "exp": iat + ttl}, SECRET, algorithm="HS256")


PROTECTED = "/dashboard/api/metrics/money"


# ── General auth ──────────────────────────────────────────────────────

def test_no_credentials_401():
    assert client().get(PROTECTED).status_code == 401


def test_session_cookie_ok():
    c = client()
    c.cookies.set(admin_auth.COOKIE_NAME, "good")
    assert c.get(PROTECTED).status_code == 200


def test_bad_session_cookie_401():
    c = client()
    c.cookies.set(admin_auth.COOKIE_NAME, "stale")
    assert c.get(PROTECTED).status_code == 401


def test_bearer_ok_only_in_bootstrap_state():
    r = client().get(PROTECTED, headers={"Authorization": f"Bearer {token()}"})
    assert r.status_code == 200


def test_bearer_rejected_once_password_exists():
    State.has_password = True
    r = client().get(PROTECTED, headers={"Authorization": f"Bearer {token()}"})
    assert r.status_code == 401


def test_bearer_rejected_once_passkey_exists():
    State.passkeys = 1
    r = client().get(PROTECTED, headers={"Authorization": f"Bearer {token()}"})
    assert r.status_code == 401


def test_legacy_30_day_link_rejected_even_in_bootstrap():
    old = token(iat_delta=-timedelta(days=2), ttl=timedelta(days=30))
    assert client().get(PROTECTED, headers={"Authorization": f"Bearer {old}"}).status_code == 401


def test_issued_link_lives_15_minutes():
    payload = jwt.decode(auth_mod.issue_login_token(ADMIN), SECRET, algorithms=["HS256"])
    assert payload["exp"] - payload["iat"] == 15 * 60
    assert payload["typ"] == "magic"


def test_verify_probe_removed():
    r = client().get(f"/dashboard/api/auth/verify?token={token()}")
    assert r.status_code in (404, 405)


# ── Setup (magic link bootstrap) ──────────────────────────────────────

def _setup(c, tok, **headers):
    return c.post("/dashboard/api/auth/setup", headers={"Origin": ORIGIN, **headers},
                  json={"username": "boss", "password": "longenough", "bootstrap_token": tok})


def test_setup_with_fresh_link_sets_session():
    r = _setup(client(), token())
    assert r.status_code == 200
    assert admin_auth.COOKIE_NAME in r.headers.get("set-cookie", "")


def test_setup_refused_after_password():
    State.has_password = True
    assert _setup(client(), token()).status_code == 409


def test_setup_refused_with_stale_link():
    assert _setup(client(), token(iat_delta=-timedelta(minutes=16))).status_code == 401


def test_setup_rate_limited():
    c = client()
    for _ in range(security.SETUP_IP.max_failures):
        assert _setup(c, "not-a-valid-token-at-all").status_code == 401
    r = _setup(c, token())
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0


# ── Login rate limit / lockout ────────────────────────────────────────

def _login(c, username="boss", password="wrong"):
    return c.post("/dashboard/api/auth/login", headers={"Origin": ORIGIN},
                  json={"username": username, "password": password})


def test_login_ok():
    State.has_password = True
    r = _login(client(), password="correct")
    assert r.status_code == 200


def test_login_lockout_per_username_blocks_even_correct_password():
    State.has_password = True
    c = client()
    for _ in range(security.LOGIN_USER.max_failures):
        assert _login(c).status_code == 401
    r = _login(c, password="correct")
    assert r.status_code == 429
    assert r.json()["detail"] == "too_many_attempts"


def test_login_lockout_per_ip_across_usernames():
    State.has_password = True
    c = client()
    for i in range(security.LOGIN_IP.max_failures):
        assert _login(c, username=f"guess{i}").status_code == 401
    assert _login(c, username="boss", password="correct").status_code == 429


def test_login_success_resets_counter():
    State.has_password = True
    c = client()
    for _ in range(security.LOGIN_USER.max_failures - 1):
        _login(c)
    assert _login(c, password="correct").status_code == 200
    assert _login(c).status_code == 401  # counter restarted, not locked


def test_client_ip_uses_last_forwarded_hop():
    req = SimpleNamespace(headers={"x-forwarded-for": "6.6.6.6, 203.0.113.9"}, client=None)
    assert security.client_ip(req) == "203.0.113.9"


# ── CSRF ──────────────────────────────────────────────────────────────

LOGOUT = "/dashboard/api/auth/logout"


def test_csrf_cross_origin_post_with_cookie_rejected():
    c = client()
    c.cookies.set(admin_auth.COOKIE_NAME, "good")
    r = c.post(LOGOUT, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_csrf_same_origin_ok():
    c = client()
    c.cookies.set(admin_auth.COOKIE_NAME, "good")
    assert c.post(LOGOUT, headers={"Origin": ORIGIN}).status_code == 200


def test_csrf_configured_base_url_ok():
    c = client()
    c.cookies.set(admin_auth.COOKIE_NAME, "good")
    assert c.post(LOGOUT, headers={"Origin": "https://admin.example"}).status_code == 200


def test_csrf_referer_fallback():
    c = client()
    c.cookies.set(admin_auth.COOKIE_NAME, "good")
    assert c.post(LOGOUT, headers={"Referer": f"{ORIGIN}/dashboard/users"}).status_code == 200
    assert c.post(LOGOUT, headers={"Referer": "https://evil.example/x"}).status_code == 403


def test_csrf_cookie_without_origin_rejected():
    c = client()
    c.cookies.set(admin_auth.COOKIE_NAME, "good")
    assert c.post(LOGOUT).status_code == 403


def test_csrf_sec_fetch_site_cross_site_rejected():
    c = client()
    c.cookies.set(admin_auth.COOKIE_NAME, "good")
    r = c.post(LOGOUT, headers={"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_login_csrf_cross_origin_rejected():
    State.has_password = True
    r = client().post("/dashboard/api/auth/login", headers={"Origin": "https://evil.example"},
                      json={"username": "boss", "password": "correct"})
    assert r.status_code == 403


def test_get_not_subject_to_csrf():
    c = client()
    c.cookies.set(admin_auth.COOKIE_NAME, "good")
    assert c.get(PROTECTED, headers={"Origin": "https://evil.example"}).status_code == 200


def test_every_subrouter_has_csrf():
    """A mutating protected route outside auth: cross-origin → 403 before
    the handler (or auth) runs."""
    c = client()
    c.cookies.set(admin_auth.COOKIE_NAME, "good")
    r = c.post("/dashboard/api/incident", headers={"Origin": "https://evil.example"},
               json={"is_active": False})
    assert r.status_code == 403


# ── WebSocket ─────────────────────────────────────────────────────────

def test_ws_query_token_is_ignored():
    with pytest.raises(WebSocketDisconnect) as exc:
        with client().websocket_connect(f"/dashboard/ws?token={token()}"):
            pass
    assert exc.value.code == 4001


async def test_ws_authorization_rules():
    def fake(cookie, origin):
        return SimpleNamespace(
            cookies={admin_auth.COOKIE_NAME: cookie} if cookie else {},
            headers={"origin": origin, "host": "testserver"} if origin else {"host": "testserver"},
        )
    assert await ws_mod._authorized(fake("good", ORIGIN)) is True
    assert await ws_mod._authorized(fake("good", "https://evil.example")) is False
    assert await ws_mod._authorized(fake(None, ORIGIN)) is False
    assert await ws_mod._authorized(fake("stale", ORIGIN)) is False
