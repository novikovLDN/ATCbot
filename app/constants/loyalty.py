"""
Loyalty program status names and screen assets (UI layer).

Mapping: paid_referrals_count → status_name. Percent and thresholds are unchanged.
Tiers: 0–24 → Silver Access (10%), 25–49 → Gold Access (25%), 50+ → Platinum Access (45%).

Images: Telegram file_id per status key (silver / gold / platinum). No local files.
"""

from typing import Optional, Tuple

# (min_inclusive, max_inclusive or None for 50+), status_name, cashback_percent (display only)
LOYALTY_TIERS = (
    (0, 24, "Silver Access", 10),
    (25, 49, "Gold Access", 25),
    (50, None, "Platinum Access", 45),
)

# PROD Telegram file_id для экранов лояльности. Silver Access / Gold Access / Platinum Access.
# Ключи: первый токен статуса в lower case (silver, gold, platinum).
LOYALTY_IMAGES: dict[str, str] = {
    "silver": "AgACAgQAAxkBAAJScml9A0BApbtV9A4KZIxOm9tzpc4cAALLDGsb51fpU3JUrQ2oI_pHAQADAgADeQADOAQ",
    "gold": "AgACAgQAAxkBAAJSc2l9A1o_OygFNFIZltf6yE-LihBXAALMDGsb51fpUzDZ_QtvyjkgAQADAgADeQADOAQ",
    "platinum": "AgACAgQAAxkBAAJSdGl9A20VE6seuPTglngaDvNj5zBZAALNDGsb51fpUxWyM5gXzGqCAQADAgADeQADOAQ",
}

LOYALTY_PHOTOS: dict[str, str] = {
    "silver": "AgACAgQAAxkBAAJScml9A0BApbtV9A4KZIxOm9tzpc4cAALLDGsb51fpU3JUrQ2oI_pHAQADAgADeQADOAQ",
    "gold": "AgACAgQAAxkBAAJSc2l9A1o_OygFNFIZltf6yE-LihBXAALMDGsb51fpUzDZ_QtvyjkgAQADAgADeQADOAQ",
    "platinum": "AgACAgQAAxkBAAJSdGl9A20VE6seuPTglngaDvNj5zBZAALNDGsb51fpUxWyM5gXzGqCAQADAgADeQADOAQ",
}


def get_loyalty_status_names(total_referrals: int) -> Tuple[str, Optional[str]]:
    """
    Return (current_status_name, next_status_name) by total referrals count.
    
    ⚠️ ВАЖНО: Уровень определяется СТРОГО по total_referrals (всего приглашено).
    Пороги соответствуют LOYALTY_TIERS: 0-24 → Silver, 25-49 → Gold, 50+ → Platinum
    
    Args:
        total_referrals: Общее количество приглашённых рефералов
    
    Returns:
        Tuple[str, Optional[str]]: (current_status_name, next_status_name)
    """
    n = max(0, total_referrals)
    if n >= 50:
        return ("Platinum Access", None)
    if n >= 25:
        return ("Gold Access", "Platinum Access")
    return ("Silver Access", "Gold Access")  # Базовый уровень для 0-24


def get_loyalty_screen_attachment(current_status_key: str) -> Optional[str]:
    """
    Return Telegram file_id for the loyalty screen image for the given status.
    current_status_key: status name ("Silver Access" / "Gold Access" / "Platinum Access")
                        or key ("silver" / "gold" / "platinum"). Normalized to key internally.
    No dependencies on telegram_id, DB, or handlers.
    """
    if not current_status_key:
        return None
    key = current_status_key.lower().split()[0]
    return LOYALTY_IMAGES.get(key)


def get_loyalty_photo_id(status_name: str) -> Optional[str]:
    """
    Return PROD Telegram file_id for the loyalty screen by status name.
    status_name: "Silver Access" / "Gold Access" / "Platinum Access" (or key).
    Used only for «Программа лояльности» screen; no handlers logic.
    """
    if not status_name:
        return None
    key = status_name.lower().split()[0]
    return LOYALTY_PHOTOS.get(key)
