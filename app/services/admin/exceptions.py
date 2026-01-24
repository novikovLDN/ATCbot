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
