"""
Unit tests for trial service layer.

Tests focus on business logic:
- Trial expiration checks
- Notification timing decisions
- Trial completion logic
- Edge cases
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock
from app.services.trials.service import (
    is_trial_expired,
    should_expire_trial,
    calculate_trial_timing,
    should_send_notification,
    should_send_final_reminder,
    get_notification_schedule,
    get_final_reminder_config,
)


class TestIsTrialExpired:
    """Tests for is_trial_expired function"""
    
    def test_trial_not_expired(self):
        """Trial with future expiry should not be expired"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        future = datetime(2024, 1, 18, 12, 0, 0)
        assert is_trial_expired(12345, future, now) is False
    
    def test_trial_expired(self):
        """Trial with past expiry should be expired"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        past = datetime(2024, 1, 12, 12, 0, 0)
        assert is_trial_expired(12345, past, now) is True
    
    def test_trial_expires_exactly_now(self):
        """Trial expiring exactly at now should be expired"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        assert is_trial_expired(12345, now, now) is True


# Note: should_expire_trial requires database connection and complex mocking.
# This is better tested as an integration test.


class TestCalculateTrialTiming:
    """Tests for calculate_trial_timing function"""
    
    def test_calculate_timing_from_expiry(self):
        """Should calculate timing correctly from expiry date"""
        expiry = datetime(2024, 1, 18, 12, 0, 0)
        now = datetime(2024, 1, 16, 12, 0, 0)  # 2 days before expiry
        
        result = calculate_trial_timing(expiry, now)
        
        assert result["hours_until_expiry"] == 48
        # hours_since_activation = 72 - 48 = 24
        assert result["hours_since_activation"] == 24
    
    def test_calculate_timing_at_expiry(self):
        """Should calculate timing correctly at expiry"""
        expiry = datetime(2024, 1, 18, 12, 0, 0)
        now = expiry  # Exactly at expiry
        
        result = calculate_trial_timing(expiry, now)
        
        assert result["hours_until_expiry"] == 0
        assert result["hours_since_activation"] == 72  # 3 days
    
    def test_calculate_timing_after_expiry(self):
        """Should handle timing after expiry"""
        expiry = datetime(2024, 1, 18, 12, 0, 0)
        now = datetime(2024, 1, 20, 12, 0, 0)  # 2 days after expiry
        
        result = calculate_trial_timing(expiry, now)
        
        assert result["hours_until_expiry"] == -48  # Negative (expired)
        assert result["hours_since_activation"] == 120  # 5 days


class TestShouldSendNotification:
    """Tests for should_send_notification function"""
    
    @pytest.mark.asyncio
    async def test_should_send_at_6h_mark(self):
        """Should send notification at 6h mark"""
        expiry = datetime(2024, 1, 18, 12, 0, 0)
        now = datetime(2024, 1, 15, 18, 0, 0)  # 6h after activation (72h before expiry)
        
        with patch('app.services.trials.service.database') as mock_db:
            mock_db.get_trial_notification_flag = AsyncMock(return_value=None)
            result = await should_send_notification(
                12345, expiry, now, timedelta(days=3), 6, "test_key", None
            )
            assert result is True
    
    @pytest.mark.asyncio
    async def test_should_not_send_before_6h(self):
        """Should not send notification before 6h mark"""
        expiry = datetime(2024, 1, 18, 12, 0, 0)
        now = datetime(2024, 1, 15, 17, 0, 0)  # 5h after activation
        
        with patch('app.services.trials.service.database') as mock_db:
            mock_db.get_trial_notification_flag = AsyncMock(return_value=None)
            result = await should_send_notification(
                12345, expiry, now, timedelta(days=3), 6, "test_key", None
            )
            assert result is False
    
    @pytest.mark.asyncio
    async def test_should_not_send_if_already_sent(self):
        """Should not send notification if already sent"""
        expiry = datetime(2024, 1, 18, 12, 0, 0)
        now = datetime(2024, 1, 15, 18, 0, 0)  # 6h after activation
        
        with patch('app.services.trials.service.database') as mock_db:
            mock_db.get_trial_notification_flag = AsyncMock(return_value=True)
            result = await should_send_notification(
                12345, expiry, now, timedelta(days=3), 6, "test_key", None
            )
            assert result is False


class TestShouldSendFinalReminder:
    """Tests for should_send_final_reminder function"""
    
    @pytest.mark.asyncio
    async def test_should_send_6h_before_expiry(self):
        """Should send final reminder 6h before expiry"""
        expiry = datetime(2024, 1, 18, 12, 0, 0)
        now = datetime(2024, 1, 18, 6, 0, 0)  # 6h before expiry
        
        with patch('app.services.trials.service.database') as mock_db:
            mock_db.get_trial_notification_flag = AsyncMock(return_value=None)
            result = await should_send_final_reminder(
                12345, expiry, now, timedelta(days=3), None
            )
            assert result is True
    
    @pytest.mark.asyncio
    async def test_should_not_send_before_6h_window(self):
        """Should not send final reminder before 6h window"""
        expiry = datetime(2024, 1, 18, 12, 0, 0)
        now = datetime(2024, 1, 18, 5, 0, 0)  # 7h before expiry
        
        with patch('app.services.trials.service.database') as mock_db:
            mock_db.get_trial_notification_flag = AsyncMock(return_value=None)
            result = await should_send_final_reminder(
                12345, expiry, now, timedelta(days=3), None
            )
            assert result is False
    
    @pytest.mark.asyncio
    async def test_should_not_send_if_already_sent(self):
        """Should not send final reminder if already sent"""
        expiry = datetime(2024, 1, 18, 12, 0, 0)
        now = datetime(2024, 1, 18, 6, 0, 0)
        
        with patch('app.services.trials.service.database') as mock_db:
            mock_db.get_trial_notification_flag = AsyncMock(return_value=True)
            result = await should_send_final_reminder(
                12345, expiry, now, timedelta(days=3), None
            )
            assert result is False


class TestNotificationSchedule:
    """Tests for notification schedule configuration"""
    
    def test_get_notification_schedule(self):
        """Should return correct notification schedule"""
        schedule = get_notification_schedule()
        
        assert len(schedule) == 2
        assert schedule[0]["hours"] == 6
        assert schedule[0]["key"] == "trial.notification_6h"
        assert schedule[0]["has_button"] is False
        
        assert schedule[1]["hours"] == 48
        assert schedule[1]["key"] == "trial.notification_60h"
        assert schedule[1]["has_button"] is True
        assert schedule[1]["db_flag"] == "trial_notif_60h_sent"
    
    def test_get_final_reminder_config(self):
        """Should return correct final reminder configuration"""
        config = get_final_reminder_config()
        
        assert config["hours_before_expiry"] == 6
        assert config["notification_key"] == "trial.notification_71h"
        assert config["has_button"] is True
        assert config["db_flag"] == "trial_notif_71h_sent"
