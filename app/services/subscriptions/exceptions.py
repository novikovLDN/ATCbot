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


class PaymentFinalizationError(SubscriptionServiceError):
    """Raised when payment finalization fails"""
    pass
