"""
Pytest configuration and shared fixtures for service layer tests.
"""
import pytest
from datetime import datetime
from typing import Dict, Any, Optional
from unittest.mock import AsyncMock, MagicMock


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
    db.is_vip_user = AsyncMock()
    db.get_user_discount = AsyncMock()
    db.get_user_extended_stats = AsyncMock()
    db.get_subscription_history = AsyncMock()
    db.check_and_disable_expired_subscription = AsyncMock()
    return db
