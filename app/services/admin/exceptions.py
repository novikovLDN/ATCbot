"""
Admin Service Domain Exceptions

All exceptions raised by the admin service layer.
"""


class AdminServiceError(Exception):
    """Base exception for admin service errors"""
    pass


class UserNotFoundError(AdminServiceError):
    """Raised when user is not found"""
    pass


class InvalidAdminActionError(AdminServiceError):
    """Raised when admin action is invalid or not allowed"""
    pass


# ====================================================================================
# Admin Operation Errors (reissue, grant, etc.)
# ====================================================================================

class AdminOperationError(AdminServiceError):
    """Base exception for admin operations (reissue, grant, etc.)"""
    pass


class SubscriptionNotFoundError(AdminOperationError):
    """Raised when subscription is not found or not active"""
    pass


class ReissueFailedError(AdminOperationError):
    """Raised when VPN key reissue fails"""
    pass
