"""
Subscription service domain exceptions.
"""


class SubscriptionServiceError(Exception):
    """Base exception for subscription service errors"""
    pass


class InvalidTariffError(SubscriptionServiceError):
    """Raised when tariff or period is invalid"""
    pass


class PriceCalculationError(SubscriptionServiceError):
    """Raised when price calculation fails"""
    pass


class PurchaseCreationError(SubscriptionServiceError):
    """Raised when purchase creation fails"""
    pass


from app.services.payments.exceptions import PaymentFinalizationError  # noqa: F401 — re-export, single source of truth
