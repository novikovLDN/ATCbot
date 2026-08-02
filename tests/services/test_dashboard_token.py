"""Срок жизни и назначение ссылки входа в дашборд.

Дефект: magic-link выдавался на 30 суток и принимался require_admin как
полноценный admin-Bearer. Ссылка уходит в чат Telegram и остаётся в истории
навсегда, то есть давала доступ к админскому API на весь этот срок.
Комментарий в коде при этом утверждал, что вне окна установки пароля
токен «инертен» — код противоречил собственной документации.
"""
import os

import pytest

os.environ.setdefault("APP_ENV", "stage")
os.environ.setdefault("STAGE_JWT_SECRET", "test-jwt-secret-for-tests")


def test_ttl_is_hours_not_days():
    import config
    ttl = getattr(config, "DASHBOARD_MAGIC_LINK_TTL_HOURS", None)
    assert ttl is not None, "срок жизни ссылки должен настраиваться"
    assert ttl <= 24 * 7, "ссылка из чата не должна жить неделями"
    assert ttl >= 1


def test_ttl_falls_back_on_garbage_env(monkeypatch):
    """Мусор в переменной окружения не должен ронять загрузку конфига."""
    import importlib
    monkeypatch.setenv("STAGE_DASHBOARD_MAGIC_LINK_TTL_HOURS", "не-число")
    import config
    importlib.reload(config)
    assert config.DASHBOARD_MAGIC_LINK_TTL_HOURS == 24


@pytest.mark.skipif(
    not os.environ.get("STAGE_JWT_SECRET"), reason="нужен JWT_SECRET"
)
def test_token_carries_purpose_claim():
    """purpose позволяет отличить ссылку из чата от прочих токенов в логах."""
    import importlib
    import config
    importlib.reload(config)
    if not config.JWT_SECRET:
        pytest.skip("JWT_SECRET не сконфигурирован в тестовом окружении")

    import jwt as pyjwt
    from app.api.dashboard.auth import issue_login_token

    token = issue_login_token(12345)
    payload = pyjwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    assert payload["purpose"] == "magic_link"
    assert payload["role"] == "admin"
    assert payload["sub"] == "12345"


@pytest.mark.skipif(
    not os.environ.get("STAGE_JWT_SECRET"), reason="нужен JWT_SECRET"
)
def test_token_expires_within_configured_window():
    import importlib
    from datetime import datetime, timezone
    import config
    importlib.reload(config)
    if not config.JWT_SECRET:
        pytest.skip("JWT_SECRET не сконфигурирован в тестовом окружении")

    import jwt as pyjwt
    from app.api.dashboard.auth import issue_login_token

    payload = pyjwt.decode(
        issue_login_token(12345), config.JWT_SECRET, algorithms=["HS256"]
    )
    lifetime_hours = (payload["exp"] - payload["iat"]) / 3600
    assert lifetime_hours <= 24 * 7, "срок жизни ссылки вырос обратно до недель"
