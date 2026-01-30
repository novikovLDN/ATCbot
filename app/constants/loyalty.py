"""
Loyalty program status names (UI layer).

Mapping: paid_referrals_count → status_name. Percent and thresholds are unchanged.
Tiers: 0–24 → Silver Access (10%), 25–49 → Gold Access (25%), 50+ → Platinum Access (45%).

Future: get_loyalty_screen_attachment() can return photo by status for the loyalty screen.
"""

from typing import Optional, Tuple

# (min_inclusive, max_inclusive or None for 50+), status_name, cashback_percent (display only)
LOYALTY_TIERS = (
    (0, 24, "Silver Access", 10),
    (25, 49, "Gold Access", 25),
    (50, None, "Platinum Access", 45),
)


def get_loyalty_status_names(paid_referrals_count: int) -> Tuple[str, Optional[str]]:
    """
    Return (current_status_name, next_status_name) by paid referrals count.
    Uses existing tier boundaries (0–24, 25–49, 50+); does not change business logic.
    """
    n = max(0, paid_referrals_count)
    if n >= 50:
        return ("Platinum Access", None)
    if n >= 25:
        return ("Gold Access", "Platinum Access")
    return ("Silver Access", "Gold Access")


def get_loyalty_screen_attachment(telegram_id: int) -> Tuple[Optional[str], str]:
    """
    Placeholder for future: return (photo_file_id_or_path, caption) for loyalty screen.
    Currently returns (None, "") so the handler keeps using edit_message_text.
    When photos are added: return (file_id or path for Silver/Gold/Platinum image, caption).
    """
    _ = telegram_id
    return (None, "")
