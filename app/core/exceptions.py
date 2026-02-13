"""
Core domain exceptions for subscription/VPN lifecycle.

Used to distinguish business failures from system failures.
"""


class XraySyncError(Exception):
    """Raised when Xray API call fails (update-user, add-user, etc.)."""
    pass


class RenewalSyncError(Exception):
    """Raised when renewal cannot complete due to Xray sync failure.

    DB is NOT updated. Payment/renewal should be retried or escalated.
    """
    pass
