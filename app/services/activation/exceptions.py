"""
Activation Service Domain Exceptions

All exceptions raised by the activation service layer.
"""


class ActivationServiceError(Exception):
    """Base exception for activation service errors"""
    pass


class ActivationNotAllowedError(ActivationServiceError):
    """Raised when activation is not allowed (e.g., subscription expired)"""
    pass


class ActivationMaxAttemptsReachedError(ActivationServiceError):
    """Raised when maximum activation attempts have been reached"""
    pass


class ActivationFailedError(ActivationServiceError):
    """Raised when activation attempt fails"""
    pass


class VPNActivationError(ActivationServiceError):
    """Raised when VPN API activation fails"""
    pass
