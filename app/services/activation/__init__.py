"""
Activation Service Layer

This package provides business logic for subscription activation, retry logic, and status management.
"""

from app.services.activation.service import (
    get_pending_subscriptions,
    should_retry_activation,
    attempt_activation,
    mark_activation_failed,
    is_subscription_expired,
    get_pending_for_notification,
)

from app.services.activation.exceptions import (
    ActivationServiceError,
    ActivationNotAllowedError,
    ActivationMaxAttemptsReachedError,
    ActivationFailedError,
    VPNActivationError,
)

__all__ = [
    "get_pending_subscriptions",
    "should_retry_activation",
    "attempt_activation",
    "mark_activation_failed",
    "is_subscription_expired",
    "get_pending_for_notification",
    "ActivationServiceError",
    "ActivationNotAllowedError",
    "ActivationMaxAttemptsReachedError",
    "ActivationFailedError",
    "VPNActivationError",
]
