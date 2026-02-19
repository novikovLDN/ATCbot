"""
Global operational flags (kill switches) for production safety.

STEP 6 — PRODUCTION HARDENING & OPERATIONAL READINESS:
F1. GLOBAL OPERATIONAL FLAGS (KILL SWITCHES)

This module provides immutable feature flags that can be used to
disable risky operations during incidents without code changes.

IMPORTANT:
- Flags default to SAFE = True (enabled)
- Flags are read-only at runtime
- Flags have zero side effects
- Disabled = log + skip (no exceptions)
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureFlags:
    """
    Immutable feature flags for operational control.
    
    STEP 6 — F1: GLOBAL OPERATIONAL FLAGS
    These flags can disable risky operations during incidents.
    
    All flags default to True (safe, enabled).
    Set via environment variables:
    - FEATURE_PAYMENTS_ENABLED (default: true)
    - FEATURE_VPN_PROVISIONING_ENABLED (default: true)
    - FEATURE_AUTO_RENEWAL_ENABLED (default: true)
    - FEATURE_BACKGROUND_WORKERS_ENABLED (default: true)
    - FEATURE_ADMIN_ACTIONS_ENABLED (default: true)
    """
    payments_enabled: bool
    vpn_provisioning_enabled: bool
    auto_renewal_enabled: bool
    background_workers_enabled: bool
    admin_actions_enabled: bool
    
    def __post_init__(self):
        """Validate flags are boolean."""
        for field_name, field_value in self.__dict__.items():
            if not isinstance(field_value, bool):
                raise ValueError(f"Feature flag {field_name} must be boolean, got {type(field_value)}")


# Global singleton instance (read-only after initialization)
_feature_flags: Optional[FeatureFlags] = None


def _parse_bool_env(key: str, default: bool = True) -> bool:
    """
    Parse boolean from environment variable.
    
    Args:
        key: Environment variable name
        default: Default value if not set
        
    Returns:
        Boolean value
    """
    value = os.getenv(key, "").lower().strip()
    if value in ("true", "1", "yes", "on"):
        return True
    elif value in ("false", "0", "no", "off"):
        return False
    else:
        return default


def get_feature_flags() -> FeatureFlags:
    """
    Get global feature flags (singleton).
    
    STEP 6 — F1: GLOBAL OPERATIONAL FLAGS
    Returns immutable FeatureFlags instance.
    Flags are read-only at runtime.
    
    Returns:
        FeatureFlags instance
    """
    global _feature_flags
    
    if _feature_flags is None:
        # Startup diagnostic: confirm env var is present at init time (Railway injects before process start)
        _raw_auto_renewal = os.getenv("FEATURE_AUTO_RENEWAL_ENABLED", "<unset>")
        logger.info("[FEATURE_FLAGS] FEATURE_AUTO_RENEWAL_ENABLED raw env=%s", _raw_auto_renewal)

        _feature_flags = FeatureFlags(
            payments_enabled=_parse_bool_env("FEATURE_PAYMENTS_ENABLED", default=True),
            vpn_provisioning_enabled=_parse_bool_env("FEATURE_VPN_PROVISIONING_ENABLED", default=True),
            auto_renewal_enabled=_parse_bool_env("FEATURE_AUTO_RENEWAL_ENABLED", default=True),
            background_workers_enabled=_parse_bool_env("FEATURE_BACKGROUND_WORKERS_ENABLED", default=True),
            admin_actions_enabled=_parse_bool_env("FEATURE_ADMIN_ACTIONS_ENABLED", default=True),
        )
        
        logger.info(
            f"[FEATURE_FLAGS] Initialized: "
            f"payments={_feature_flags.payments_enabled}, "
            f"vpn_provisioning={_feature_flags.vpn_provisioning_enabled}, "
            f"auto_renewal={_feature_flags.auto_renewal_enabled}, "
            f"background_workers={_feature_flags.background_workers_enabled}, "
            f"admin_actions={_feature_flags.admin_actions_enabled}"
        )
    
    return _feature_flags
