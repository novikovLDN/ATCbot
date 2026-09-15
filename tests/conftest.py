"""
Pytest configuration and shared fixtures for service layer tests.
"""
import os
# Ensure config.py can import in unit-test mode (required env vars stubbed).
# Real CI sets these explicitly; this is a fallback for ad-hoc `pytest` runs.
os.environ.setdefault("APP_ENV", "stage")
os.environ.setdefault("STAGE_BOT_TOKEN", "test-bot-token")
os.environ.setdefault("STAGE_DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("STAGE_ADMIN_TELEGRAM_ID", "1")
os.environ.setdefault("STAGE_WEBHOOK_URL", "https://test.example/telegram/webhook")
os.environ.setdefault("STAGE_WEBHOOK_SECRET", "test-secret")
# Product default of USE_NEW_PROVISIONING is "on" (owner decision 2026-09-14).
# Hermetic tests that never mention the flag keep exercising the legacy path;
# tests of the new core set "on" explicitly, flag-semantics tests delenv it.
# Prefixed by the ACTIVE env: CI runs with APP_ENV=local, where a STAGE_ stub
# is never read and the flag silently defaulted to "on" (25 red CI tests).
os.environ.setdefault(f"{os.environ['APP_ENV'].upper()}_USE_NEW_PROVISIONING", "off")

import sys

import pytest
from datetime import datetime
from typing import Dict, Any, Optional
from unittest.mock import AsyncMock, MagicMock


def _reset_provisioning_alerts():
    mod = sys.modules.get("app.services.provisioning")
    if mod is not None and hasattr(mod, "reset_alert_state"):
        mod.reset_alert_state()


@pytest.fixture(autouse=True)
def _isolate_provisioning_alert_budget():
    """app.services.provisioning keeps a process-local per-window alert budget
    and digest buffer; without a reset one test's failures would exhaust the
    next test's immediate alerts."""
    _reset_provisioning_alerts()
    yield
    _reset_provisioning_alerts()


@pytest.fixture
def mock_datetime():
    """Fixed datetime for deterministic tests"""
    return datetime(2024, 1, 15, 12, 0, 0)


@pytest.fixture
def mock_subscription_active(mock_datetime):
    """Mock active subscription"""
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
    """Mock expired subscription"""
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
    """Mock subscription with pending activation"""
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
    """Mock database module"""
    db = MagicMock()
    db.get_user = AsyncMock()
    db.get_subscription = AsyncMock()
    db.get_subscription_any = AsyncMock()
    db.is_trial_available = AsyncMock()
    db.get_user_discount = AsyncMock()
    db.get_user_extended_stats = AsyncMock()
    db.get_subscription_history = AsyncMock()
    db.check_and_disable_expired_subscription = AsyncMock()
    return db
