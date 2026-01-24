"""
Payment Service Domain Exceptions

All exceptions raised by the payment service layer.
"""


class PaymentServiceError(Exception):
    """Base exception for payment service errors"""
    pass


class InvalidPaymentPayloadError(PaymentServiceError):
    """Raised when payment payload format is invalid"""
    pass


class PaymentAmountMismatchError(PaymentServiceError):
    """Raised when payment amount doesn't match expected amount"""
    pass


class PaymentAlreadyProcessedError(PaymentServiceError):
    """Raised when payment has already been processed (idempotency check)"""
    pass


class PaymentFinalizationError(PaymentServiceError):
    """Raised when payment finalization fails"""
    pass
