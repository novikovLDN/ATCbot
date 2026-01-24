"""
Trial Service Package
"""

from app.services.trials.service import (
    is_trial_available,
    is_trial_expired,
    should_expire_trial,
    calculate_trial_timing,
    should_send_notification,
    should_send_final_reminder,
    mark_trial_completed,
    should_send_completion_notification,
    prepare_notification_payload,
    get_notification_schedule,
    get_final_reminder_config,
    TrialServiceError,
    TrialExpiredError,
    InvalidTrialStateError,
)

__all__ = [
    "is_trial_available",
    "is_trial_expired",
    "should_expire_trial",
    "calculate_trial_timing",
    "should_send_notification",
    "should_send_final_reminder",
    "mark_trial_completed",
    "should_send_completion_notification",
    "prepare_notification_payload",
    "get_notification_schedule",
    "get_final_reminder_config",
    "TrialServiceError",
    "TrialExpiredError",
    "InvalidTrialStateError",
]
