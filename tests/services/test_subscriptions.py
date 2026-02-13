"""
Unit tests for subscription service layer.

Tests focus on business logic:
- Subscription status determination
- Active/inactive checks
- Date parsing
- Edge cases
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock
from app.services.subscriptions.service import (
    parse_expires_at,
    is_subscription_active,
    get_subscription_status,
    SubscriptionStatus,
)


class TestParseExpiresAt:
    """Tests for parse_expires_at function"""
    
    def test_parse_none(self):
        """None should return None"""
        assert parse_expires_at(None) is None
    
    def test_parse_datetime(self):
        """datetime object should be returned as-is"""
        dt = datetime(2024, 1, 15, 12, 0, 0)
        assert parse_expires_at(dt) == dt
    
    def test_parse_iso_string(self):
        """ISO format string should be parsed correctly"""
        dt_str = "2024-01-15T12:00:00"
        result = parse_expires_at(dt_str)
        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
    
    def test_parse_iso_string_with_z(self):
        """ISO string with Z should be parsed correctly"""
        dt_str = "2024-01-15T12:00:00Z"
        result = parse_expires_at(dt_str)
        assert isinstance(result, datetime)
    
    def test_parse_invalid_string(self):
        """Invalid string should return None"""
        assert parse_expires_at("not-a-date") is None
    
    def test_parse_other_type(self):
        """Non-datetime, non-string should return None"""
        assert parse_expires_at(12345) is None


class TestIsSubscriptionActive:
    """Tests for is_subscription_active function"""
    
    def test_none_subscription(self):
        """None subscription should return False"""
        assert is_subscription_active(None) is False
    
    def test_active_subscription(self):
        """Active subscription with future expiry should return True"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        future = datetime(2024, 2, 15, 12, 0, 0)
        subscription = {
            "status": "active",
            "expires_at": future,
            "uuid": "test-uuid",
        }
        assert is_subscription_active(subscription, now) is True
    
    def test_expired_subscription(self):
        """Expired subscription should return False"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        past = datetime(2024, 1, 1, 12, 0, 0)
        subscription = {
            "status": "active",
            "expires_at": past,
            "uuid": "test-uuid",
        }
        assert is_subscription_active(subscription, now) is False
    
    def test_inactive_status(self):
        """Subscription with non-active status should return False"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        future = datetime(2024, 2, 15, 12, 0, 0)
        subscription = {
            "status": "inactive",
            "expires_at": future,
            "uuid": "test-uuid",
        }
        assert is_subscription_active(subscription, now) is False
    
    def test_no_uuid(self):
        """Subscription without UUID should return False"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        future = datetime(2024, 2, 15, 12, 0, 0)
        subscription = {
            "status": "active",
            "expires_at": future,
            "uuid": None,
        }
        assert is_subscription_active(subscription, now) is False
    
    def test_no_expires_at(self):
        """Subscription without expires_at should return False"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        subscription = {
            "status": "active",
            "expires_at": None,
            "uuid": "test-uuid",
        }
        assert is_subscription_active(subscription, now) is False
    
    def test_expires_at_exactly_now(self):
        """Subscription expiring exactly at now should return False"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        subscription = {
            "status": "active",
            "expires_at": now,
            "uuid": "test-uuid",
        }
        assert is_subscription_active(subscription, now) is False
    
    def test_expires_at_string_format(self):
        """Should handle expires_at as string"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        future_str = "2024-02-15T12:00:00"
        subscription = {
            "status": "active",
            "expires_at": future_str,
            "uuid": "test-uuid",
        }
        assert is_subscription_active(subscription, now) is True


class TestGetSubscriptionStatus:
    """Tests for get_subscription_status function"""
    
    def test_none_subscription(self):
        """None subscription should return inactive status"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        status = get_subscription_status(None, now)
        
        assert status.is_active is False
        assert status.has_subscription is False
        assert status.expires_at is None
        assert status.activation_status is None
        assert status.is_expired is False
    
    def test_active_subscription(self):
        """Active subscription should return correct status"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        future = datetime(2024, 2, 15, 12, 0, 0)
        subscription = {
            "status": "active",
            "expires_at": future,
            "uuid": "test-uuid",
            "activation_status": "active",
        }
        status = get_subscription_status(subscription, now)
        
        assert status.is_active is True
        assert status.has_subscription is True
        assert status.expires_at == future
        assert status.activation_status == "active"
        assert status.is_expired is False
    
    def test_expired_subscription(self):
        """Expired subscription should return correct status"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        past = datetime(2024, 1, 1, 12, 0, 0)
        subscription = {
            "status": "active",
            "expires_at": past,
            "uuid": "test-uuid",
            "activation_status": "active",
        }
        status = get_subscription_status(subscription, now)
        
        assert status.is_active is False
        assert status.has_subscription is True
        assert status.expires_at == past
        assert status.is_expired is True
    
    def test_pending_activation(self):
        """Subscription with pending activation should return correct status"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        future = datetime(2024, 2, 15, 12, 0, 0)
        subscription = {
            "status": "active",
            "expires_at": future,
            "uuid": None,
            "activation_status": "pending",
        }
        status = get_subscription_status(subscription, now)
        
        assert status.is_active is False  # No UUID means not active
        assert status.has_subscription is True
        assert status.expires_at == future
        assert status.activation_status == "pending"
        assert status.is_expired is False
    
    def test_default_activation_status(self):
        """Should default activation_status to 'active' if not provided"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        future = datetime(2024, 2, 15, 12, 0, 0)
        subscription = {
            "status": "active",
            "expires_at": future,
            "uuid": "test-uuid",
            # No activation_status
        }
        status = get_subscription_status(subscription, now)
        
        assert status.activation_status == "active"
    
    def test_uses_current_time_if_not_provided(self):
        """Should use datetime.now(timezone.utc) if now is not provided"""
        future = datetime(2099, 2, 15, 12, 0, 0)
        subscription = {
            "status": "active",
            "expires_at": future,
            "uuid": "test-uuid",
        }
        # Don't pass now - should use datetime.now(timezone.utc)
        status = get_subscription_status(subscription)
        
        # If subscription expires in 2099, it should be active now
        assert status.is_active is True
