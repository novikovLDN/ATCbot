"""
Shared pytest fixtures.

The first thing this file does is set the environment variables that
``config.py`` requires at import time. Without these, ``import config``
calls ``sys.exit(1)`` and the test session never starts. CI passes the
same vars via the workflow YAML; local runs use the defaults below.

Adding a per-process fixture is not enough because ``config`` is imported
during test collection, before any fixture runs. Hence: module-level
side effects.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _ensure_test_env() -> None:
    os.environ.setdefault("APP_ENV", "local")
    os.environ.setdefault("LOCAL_BOT_TOKEN", "0:test-token")
    os.environ.setdefault("LOCAL_ADMIN_TELEGRAM_ID", "1")
    # asyncpg URL is parsed but not connected to during pure-unit tests.
    os.environ.setdefault(
        "LOCAL_DATABASE_URL",
        "postgresql://atcs:atcs@localhost:5432/atcs_test",
    )
    os.environ.setdefault("LOCAL_WEBHOOK_URL", "https://test.invalid/webhook")
    os.environ.setdefault("LOCAL_WEBHOOK_SECRET", "test-secret")


_ensure_test_env()


@pytest.fixture
def mock_datetime():
    """Fixed datetime for deterministic tests."""
    return datetime(2024, 1, 15, 12, 0, 0)


@pytest.fixture
def mock_subscription_active(mock_datetime):
    future = datetime(2024, 2, 15, 12, 0, 0)
    return {
        "telegram_id": 12345,
        "status": "active",
        "expires_at": future,
        "uuid": "test-uuid-123",
        "vpn_key": "test-vpn-key",
        "activation_status": "active",
        "auto_renew": False,
    }


@pytest.fixture
def mock_subscription_expired(mock_datetime):
    past = datetime(2024, 1, 1, 12, 0, 0)
    return {
        "telegram_id": 12345,
        "status": "active",
        "expires_at": past,
        "uuid": "test-uuid-123",
        "vpn_key": "test-vpn-key",
        "activation_status": "active",
        "auto_renew": False,
    }


@pytest.fixture
def mock_subscription_pending():
    future = datetime(2024, 2, 15, 12, 0, 0)
    return {
        "telegram_id": 12345,
        "status": "active",
        "expires_at": future,
        "uuid": None,
        "vpn_key": None,
        "activation_status": "pending",
        "auto_renew": False,
    }


@pytest.fixture
def mock_database():
    db = MagicMock()
    db.get_user = AsyncMock()
    db.get_subscription = AsyncMock()
    db.get_subscription_any = AsyncMock()
    db.is_trial_available = AsyncMock()
    db.is_vip_user = AsyncMock()
    db.get_user_discount = AsyncMock()
    db.get_user_extended_stats = AsyncMock()
    db.get_subscription_history = AsyncMock()
    db.check_and_disable_expired_subscription = AsyncMock()
    return db


@pytest.fixture(autouse=True)
def _reset_runtime_singletons():
    """Wipe in-process singletons between tests so flaky cross-test state is gone.

    Cleared:
      - ``app.core.metrics`` registry (counters, gauges, histograms)
      - ``app.core.worker_registry`` heartbeats
      - ``app.core.rate_limit`` token bucket map
    """
    yield
    try:
        from app.core import metrics as _metrics
        _metrics.get_registry().reset()
    except Exception:
        pass
    try:
        from app.core import worker_registry
        worker_registry.reset()
    except Exception:
        pass
    try:
        from app.core import rate_limit as _rl
        _rl._rate_limiter = None  # noqa: SLF001 — singleton reset for test isolation
    except Exception:
        pass
