"""
Loyalty program status names and screen assets (UI layer).

Mapping: paid_referrals_count → status_name. Percent and thresholds are unchanged.
Tiers: 0–24 → Silver Access (10%), 25–49 → Gold Access (25%), 50+ → Platinum Access (45%).

Images: Telegram file_id per status and env (stage/prod). No local files.
"""

from typing import Optional, Tuple

# (min_inclusive, max_inclusive or None for 50+), status_name, cashback_percent (display only)
LOYALTY_TIERS = (
    (0, 24, "Silver Access", 10),
    (25, 49, "Gold Access", 25),
    (50, None, "Platinum Access", 45),
)

# Telegram file_id per status and environment. Placeholders until real file_ids are set.
# Inject real file_ids (e.g. from bot.send_photo in a setup chat) for stage/prod.
LOYALTY_IMAGES: dict[str, dict[str, Optional[str]]] = {
    "Silver Access": {"stage": None, "prod": None},
    "Gold Access": {"stage": None, "prod": None},
    "Platinum Access": {"stage": None, "prod": None},
}


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


def get_loyalty_screen_attachment(status_name: str, env: str) -> Optional[str]:
    """
    Return Telegram file_id for the loyalty screen image for the given status and environment.
    Uses LOYALTY_IMAGES; no local files. Returns None if no image is configured (placeholder or missing).
    env: "stage" | "prod" | "local" (local falls back to stage).
    """
    env_key = (env or "prod").lower()
    if env_key == "local":
        env_key = "stage"
    if env_key not in ("stage", "prod"):
        env_key = "prod"
    mapping = LOYALTY_IMAGES.get(status_name)
    if not mapping:
        return None
    return mapping.get(env_key)
