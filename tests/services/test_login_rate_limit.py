"""Ограничение попыток входа в дашборд.

Дефект: на /auth/login не было никакой защиты от перебора — пароль можно
было подбирать с любой скоростью.
"""
import os

import pytest

os.environ.setdefault("APP_ENV", "stage")

from app.api.dashboard import auth as auth_mod


@pytest.fixture(autouse=True)
def clean_attempts():
    auth_mod._login_attempts.clear()
    yield
    auth_mod._login_attempts.clear()


IP = "203.0.113.7"


def test_first_attempts_allowed():
    for _ in range(auth_mod._LOGIN_MAX_ATTEMPTS):
        allowed, _ = auth_mod._login_attempts_allowed(IP)
        assert allowed is True
        auth_mod._register_failed_login(IP)


def test_blocked_after_limit():
    for _ in range(auth_mod._LOGIN_MAX_ATTEMPTS):
        auth_mod._register_failed_login(IP)
    allowed, retry_after = auth_mod._login_attempts_allowed(IP)
    assert allowed is False
    assert retry_after > 0


def test_successful_login_resets_counter():
    for _ in range(auth_mod._LOGIN_MAX_ATTEMPTS):
        auth_mod._register_failed_login(IP)
    auth_mod._reset_login_attempts(IP)
    allowed, _ = auth_mod._login_attempts_allowed(IP)
    assert allowed is True


def test_window_expires(monkeypatch):
    """После окна попытки снова разрешены."""
    base = 1_000_000.0
    monkeypatch.setattr(auth_mod._time, "time", lambda: base)
    for _ in range(auth_mod._LOGIN_MAX_ATTEMPTS):
        auth_mod._register_failed_login(IP)
    assert auth_mod._login_attempts_allowed(IP)[0] is False

    monkeypatch.setattr(
        auth_mod._time, "time", lambda: base + auth_mod._LOGIN_WINDOW_SECONDS + 1
    )
    assert auth_mod._login_attempts_allowed(IP)[0] is True


def test_other_ip_not_affected():
    for _ in range(auth_mod._LOGIN_MAX_ATTEMPTS):
        auth_mod._register_failed_login(IP)
    assert auth_mod._login_attempts_allowed("198.51.100.1")[0] is True


def test_expired_entries_are_pruned(monkeypatch):
    """Словарь не должен расти бесконечно — иначе это утечка памяти."""
    base = 1_000_000.0
    monkeypatch.setattr(auth_mod._time, "time", lambda: base)
    for i in range(50):
        auth_mod._register_failed_login(f"10.0.0.{i}")
    assert len(auth_mod._login_attempts) == 50

    monkeypatch.setattr(
        auth_mod._time, "time", lambda: base + auth_mod._LOGIN_WINDOW_SECONDS + 1
    )
    auth_mod._login_attempts_allowed("10.0.0.1")
    assert len(auth_mod._login_attempts) == 0, "протухшие записи не вычищены"


class _FakeClient:
    host = "192.0.2.55"


class _FakeRequest:
    def __init__(self, headers=None, client=True):
        self.headers = headers or {}
        self.client = _FakeClient() if client else None


def test_client_ip_prefers_forwarded_header():
    req = _FakeRequest({"x-forwarded-for": "198.51.100.9, 10.0.0.1"})
    assert auth_mod._client_ip(req) == "198.51.100.9"


def test_client_ip_falls_back_to_peer():
    assert auth_mod._client_ip(_FakeRequest()) == "192.0.2.55"


def test_client_ip_handles_missing_client():
    assert auth_mod._client_ip(_FakeRequest(client=False)) == "unknown"
