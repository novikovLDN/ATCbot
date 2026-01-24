"""
VPN Service Layer

This module provides business logic for VPN operations, specifically UUID removal.
It acts as a wrapper around vpn_utils, providing a clean interface for handlers
and background tasks while keeping business logic separate from implementation details.

All functions are pure business logic:
- No aiogram imports
- No logging
- No Telegram calls
- Pure business logic only
"""

from typing import Optional
import config
import vpn_utils


# ====================================================================================
# Domain Exceptions
# ====================================================================================

class VPNServiceError(Exception):
    """Base exception for VPN service errors"""
    pass


class VPNApiDisabled(VPNServiceError):
    """
    Raised when VPN API is not configured.
    
    This is NOT an error state - VPN API can be intentionally disabled.
    Callers should handle this gracefully.
    """
    pass


class VPNRemovalError(VPNServiceError):
    """Raised when UUID removal fails"""
    pass


# ====================================================================================
# VPN API Availability
# ====================================================================================

def is_vpn_api_available() -> bool:
    """
    Check if VPN API is configured and available.
    
    Returns:
        True if VPN API is enabled and configured, False otherwise
    """
    return config.VPN_ENABLED


def check_vpn_api_available() -> None:
    """
    Check if VPN API is available, raise exception if not.
    
    Raises:
        VPNApiDisabled: If VPN API is not configured
    """
    if not is_vpn_api_available():
        raise VPNApiDisabled("VPN API is not configured. XRAY_API_URL and XRAY_API_KEY must be set.")


# ====================================================================================
# UUID Removal Decision Logic
# ====================================================================================

def should_remove_uuid(
    uuid: Optional[str],
    subscription_status: Optional[str] = None,
    subscription_expired: bool = False
) -> bool:
    """
    Determine if UUID should be removed.
    
    Business rules:
    - UUID must exist (not None, not empty)
    - If subscription is expired, UUID should be removed
    - If subscription status is 'expired', UUID should be removed
    
    Args:
        uuid: UUID to check (can be None or empty)
        subscription_status: Current subscription status (optional)
        subscription_expired: Whether subscription has expired (optional)
        
    Returns:
        True if UUID should be removed, False otherwise
    """
    # UUID must exist
    if not uuid or not uuid.strip():
        return False
    
    # If subscription is explicitly expired, remove UUID
    if subscription_expired:
        return True
    
    # If subscription status is 'expired', remove UUID
    if subscription_status == 'expired':
        return True
    
    return False


# ====================================================================================
# VPN Operations
# ====================================================================================

async def remove_uuid(uuid: str) -> None:
    """
    Remove UUID from VPN API.
    
    This wraps vpn_utils.remove_vless_user() with domain-specific error handling.
    VPN API disabled is NOT an error - it raises VPNApiDisabled which callers
    should handle gracefully.
    
    Args:
        uuid: UUID to remove
        
    Raises:
        VPNApiDisabled: If VPN API is not configured (NOT an error state)
        VPNRemovalError: If removal fails for other reasons
        ValueError: If UUID is invalid
    """
    # Check if VPN API is available
    if not is_vpn_api_available():
        raise VPNApiDisabled("VPN API is not configured. Cannot remove UUID.")
    
    # Validate UUID
    if not uuid or not uuid.strip():
        raise ValueError(f"Invalid UUID: {uuid}")
    
    try:
        # Delegate to vpn_utils
        await vpn_utils.remove_vless_user(uuid)
    except ValueError as e:
        # Re-raise ValueError as-is (invalid UUID or config)
        # Check if it's about VPN API not configured
        error_str = str(e).lower()
        if "vpn api is not configured" in error_str or "xray_api_url" in error_str or "xray_api_key" in error_str:
            raise VPNApiDisabled(str(e)) from e
        raise ValueError(str(e)) from e
    except vpn_utils.AuthError as e:
        # Authentication errors are critical
        raise VPNRemovalError(f"VPN API authentication failed: {str(e)}") from e
    except vpn_utils.TimeoutError as e:
        # Timeout errors
        raise VPNRemovalError(f"VPN API timeout: {str(e)}") from e
    except vpn_utils.VPNAPIError as e:
        # Other VPN API errors
        raise VPNRemovalError(f"VPN API error: {str(e)}") from e
    except Exception as e:
        # Unexpected errors
        raise VPNRemovalError(f"Unexpected error removing UUID: {str(e)}") from e


async def remove_uuid_if_needed(
    uuid: Optional[str],
    subscription_status: Optional[str] = None,
    subscription_expired: bool = False
) -> bool:
    """
    Remove UUID if business logic determines it should be removed.
    
    This combines decision logic (should_remove_uuid) with removal operation.
    If VPN API is disabled, this is NOT an error - returns False gracefully.
    
    Args:
        uuid: UUID to potentially remove
        subscription_status: Current subscription status (optional)
        subscription_expired: Whether subscription has expired (optional)
        
    Returns:
        True if UUID was removed, False if removal was skipped or VPN API is disabled
        
    Raises:
        VPNRemovalError: If removal fails (only if VPN API is enabled)
    """
    # Check if UUID should be removed
    if not should_remove_uuid(uuid, subscription_status, subscription_expired):
        return False
    
    # Check if VPN API is available
    if not is_vpn_api_available():
        # VPN API disabled is NOT an error - just skip removal
        return False
    
    # Remove UUID
    await remove_uuid(uuid)
    return True
