"""
Unit tests for trial service layer.

Tests focus on business logic:
- Trial expiration checks
- Notification timing decisions
- Trial completion logic
- Edge cases

Contract notes (current production code, app/services/trials/service.py):
- is_trial_expired / should_send_* are coroutines (async since 16a605b1).
- should_send_notification / should_send_final_reminder take a DB `conn`
  (paid-subscription guard) and return (bool, reason).
- Times are aware UTC (should_send_* pass `now` through database._to_db_utc).
- 82032580: final reminder moved to «последний час» — window (0.5h, 1h].
- b14644c7: periodic schedule is empty (T+6h and T+48h pushes removed).
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from app.services.trials.service import (
    is_trial_expired,
    should_expire_trial,
    calculate_trial_timing,
    should_send_notification,
    should_send_final_reminder,
    get_notification_schedule,
    get_final_reminder_config,
)


UTC = timezone.utc
ACTIVATION = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
EXPIRY = ACTIVATION + timedelta(hours=72)  # 2024-01-18 12:00 UTC


def _conn(paid_subscription=None):
    """Fake asyncpg connection: fetchrow answers the paid-subscription guard."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=paid_subscription)
    return conn


class TestIsTrialExpired:
    """Tests for is_trial_expired function"""

    @pytest.mark.asyncio
    async def test_trial_not_expired(self):
        """Trial with future expiry should not be expired"""
        now = ACTIVATION
        assert await is_trial_expired(12345, EXPIRY, now) is False

    @pytest.mark.asyncio
    async def test_trial_expired(self):
        """Trial with past expiry should be expired"""
        now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        past = datetime(2024, 1, 12, 12, 0, 0, tzinfo=UTC)
        assert await is_trial_expired(12345, past, now) is True

    @pytest.mark.asyncio
    async def test_trial_expires_exactly_now(self):
        """Trial expiring exactly at now should be expired"""
        now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        assert await is_trial_expired(12345, now, now) is True


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
        """After expiry hours_until_expiry is clamped to 0 (max(0.0, …) since
        16a605b1); hours_since_activation keeps growing (72 + 48)."""
        expiry = datetime(2024, 1, 18, 12, 0, 0)
        now = datetime(2024, 1, 20, 12, 0, 0)  # 2 days after expiry

        result = calculate_trial_timing(expiry, now)

        assert result["hours_until_expiry"] == 0.0
        assert result["hours_since_activation"] == 120  # 5 days


class TestShouldSendNotification:
    """Tests for should_send_notification function (generic schedule entry)."""

    SCHEDULE_6H = {"hours": 6, "key": "test_key", "has_button": False,
                   "db_flag": "trial_notif_6h_sent"}

    @pytest.mark.asyncio
    async def test_should_send_at_6h_mark(self):
        """Should send notification at 6h mark"""
        now = ACTIVATION + timedelta(hours=6)
        conn = _conn()
        result = await should_send_notification(
            12345, EXPIRY, EXPIRY, self.SCHEDULE_6H,
            {"trial_notif_6h_sent": False}, now, conn,
        )
        assert result == (True, None)
        # paid-subscription guard queried with naive UTC now (_to_db_utc)
        assert conn.fetchrow.await_args.args[1:] == (12345, now.replace(tzinfo=None))

    @pytest.mark.asyncio
    async def test_should_not_send_before_6h(self):
        """Should not send notification before 6h mark"""
        now = ACTIVATION + timedelta(hours=5)
        result = await should_send_notification(
            12345, EXPIRY, EXPIRY, self.SCHEDULE_6H,
            {"trial_notif_6h_sent": False}, now, _conn(),
        )
        assert result == (False, "too_early")

    @pytest.mark.asyncio
    async def test_should_not_send_if_already_sent(self):
        """Should not send notification if already sent"""
        now = ACTIVATION + timedelta(hours=6)
        result = await should_send_notification(
            12345, EXPIRY, EXPIRY, self.SCHEDULE_6H,
            {"trial_notif_6h_sent": True}, now, _conn(),
        )
        assert result == (False, "already_sent")


class TestShouldSendFinalReminder:
    """Tests for should_send_final_reminder — window (0.5h, 1h] before expiry."""

    @pytest.mark.asyncio
    async def test_should_send_6h_before_expiry(self):
        """Should send final reminder 1h before expiry (was 6h before 82032580)"""
        now = EXPIRY - timedelta(hours=1)
        result = await should_send_final_reminder(
            12345, EXPIRY, EXPIRY, False, now, _conn(),
        )
        assert result == (True, None)

    @pytest.mark.asyncio
    async def test_should_not_send_before_6h_window(self):
        """Should not send final reminder before the 1h window (and not at 6h)"""
        for hours_left in (6, 2):
            now = EXPIRY - timedelta(hours=hours_left)
            result = await should_send_final_reminder(
                12345, EXPIRY, EXPIRY, False, now, _conn(),
            )
            assert result == (False, "too_early")

    @pytest.mark.asyncio
    async def test_should_not_send_if_already_sent(self):
        """Should not send final reminder if already sent"""
        now = EXPIRY - timedelta(hours=1)
        conn = _conn()
        result = await should_send_final_reminder(
            12345, EXPIRY, EXPIRY, True, now, conn,
        )
        assert result == (False, "already_sent")
        conn.fetchrow.assert_not_awaited()


class TestNotificationSchedule:
    """Tests for notification schedule configuration"""

    def test_get_notification_schedule(self):
        """Periodic schedule is empty since b14644c7 (T+6h and duplicate T+48h
        notification_60h removed; remaining pushes are inline in
        trial_notifications.py)."""
        assert get_notification_schedule() == []

    def test_get_final_reminder_config(self):
        """Should return correct final reminder configuration"""
        config = get_final_reminder_config()

        assert config["hours_before_expiry"] == 1
        assert config["notification_key"] == "trial.notification_71h"
        assert config["has_button"] is True
        assert config["db_flag"] == "trial_notif_71h_sent"
