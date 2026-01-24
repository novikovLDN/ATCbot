"""
Notification Service Domain Exceptions

All exceptions raised by the notification service layer.
"""


class NotificationServiceError(Exception):
    """Base exception for notification service errors"""
    pass


class NotificationAlreadySentError(NotificationServiceError):
    """Raised when notification has already been sent (idempotency check)"""
    pass


class InvalidReminderTypeError(NotificationServiceError):
    """Raised when reminder type is invalid"""
    pass


class ReminderNotApplicableError(NotificationServiceError):
    """Raised when reminder is not applicable for subscription type"""
    pass
