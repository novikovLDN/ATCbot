"""
Referral Service Layer

Deterministic, immutable, payment-safe referral tracking.
"""

from app.services.referrals.service import (
    process_referral_registration,
    activate_referral,
    get_referral_state,
    ReferralState,
)

__all__ = [
    "process_referral_registration",
    "activate_referral",
    "get_referral_state",
    "ReferralState",
]
