"""
Notification Service Layer

This package provides business logic for notifications, reminders, and idempotency checks.
"""

from app.services.notifications.service import (
    should_send_reminder,
    get_reminder_type,
    check_notification_idempotency,
    mark_notification_sent,
    ReminderDecision,
    ReminderType,
)

from app.services.notifications.exceptions import (
    NotificationServiceError,
    NotificationAlreadySentError,
    InvalidReminderTypeError,
    ReminderNotApplicableError,
)

__all__ = [
    "should_send_reminder",
    "get_reminder_type",
    "check_notification_idempotency",
    "mark_notification_sent",
    "ReminderDecision",
    "ReminderType",
    "NotificationServiceError",
    "NotificationAlreadySentError",
    "InvalidReminderTypeError",
    "ReminderNotApplicableError",
]
