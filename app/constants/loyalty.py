"""
Loyalty program status names and screen assets (UI layer).

Tiers «Круга Амбассадоров» (миграция 059_ambassador_cashback_floor):
- 0-24:   Проводник  (10%)
- 25-49:  Хранитель  (20%)
- 50-74:  Инсайдер   (30%)
- 75-99:  Лидер      (40%)
- 100+:   Амбассадор (45%, навсегда)

Картинки экрана — переиспользуем существующие 3 (silver/gold/platinum)
до перерисовки. Новые тиры мапятся: Проводник/Хранитель → silver,
Инсайдер/Лидер → gold, Амбассадор → platinum.
"""

from typing import Optional, Tuple

# (min_inclusive, max_inclusive or None, status_name, cashback_percent)
LOYALTY_TIERS = (
    (0,   24,   "Проводник",   10),
    (25,  49,   "Хранитель",   20),
    (50,  74,   "Инсайдер",    30),
    (75,  99,   "Лидер",       40),
    (100, None, "Амбассадор",  45),
)

# PROD Telegram file_id для экранов лояльности.
LOYALTY_IMAGES: dict[str, str] = {
    "silver":   "AgACAgQAAxkBAAJScml9A0BApbtV9A4KZIxOm9tzpc4cAALLDGsb51fpU3JUrQ2oI_pHAQADAgADeQADOAQ",
    "gold":     "AgACAgQAAxkBAAJSc2l9A1o_OygFNFIZltf6yE-LihBXAALMDGsb51fpUzDZ_QtvyjkgAQADAgADeQADOAQ",
    "platinum": "AgACAgQAAxkBAAJSdGl9A20VE6seuPTglngaDvNj5zBZAALNDGsb51fpUxWyM5gXzGqCAQADAgADeQADOAQ",
}

LOYALTY_PHOTOS: dict[str, str] = dict(LOYALTY_IMAGES)

# Маппинг тира → ключ картинки. Включает legacy-имена для обратной
# совместимости со старыми кэшированными сообщениями/инсайдерскими ссылками.
_TIER_TO_IMAGE_KEY: dict[str, str] = {
    "Проводник":       "silver",
    "Хранитель":       "silver",
    "Инсайдер":        "gold",
    "Лидер":           "gold",
    "Амбассадор":      "platinum",
    # Legacy
    "Silver Access":   "silver",
    "Gold Access":     "gold",
    "Platinum Access": "platinum",
}


# ── Tier emoji (premium emoji_id или unicode fallback) ────────────────
#
# Структура: {tier_name: (premium_emoji_id | None, unicode_fallback)}.
# Если premium_emoji_id задан — рендерим через <tg-emoji>, иначе unicode.
# Пока premium_emoji_id == None — заглушка цветным кружком из ТЗ.
# Заменить, когда продакт пришлёт нужные emoji_id для каждого тира.
TIER_EMOJI: dict[str, Tuple[Optional[str], str]] = {
    "Проводник":  (None, "🟢"),
    "Хранитель":  (None, "🔵"),
    "Инсайдер":   (None, "🟣"),
    "Лидер":      (None, "🟠"),
    "Амбассадор": (None, "👑"),
}


# Родительный падеж тиров — для фраз «До Хранителя», «До Амбассадора».
_TIER_GENITIVE: dict[str, str] = {
    "Проводник":  "Проводника",
    "Хранитель":  "Хранителя",
    "Инсайдер":   "Инсайдера",
    "Лидер":      "Лидера",
    "Амбассадор": "Амбассадора",
}


def tier_genitive(tier_name: str) -> str:
    """Return tier name in genitive case (для конструкций «До <тир>»)."""
    return _TIER_GENITIVE.get(tier_name, tier_name)


def tier_emoji_html(tier_name: str) -> str:
    """Return HTML-snippet for tier emoji (premium if id set, else unicode)."""
    entry = TIER_EMOJI.get(tier_name)
    if not entry:
        return "🎖"
    eid, fallback = entry
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback


def get_loyalty_status_names(total_referrals: int) -> Tuple[str, Optional[str]]:
    """Return (current_status_name, next_status_name) by paid referrals count.

    Уровень определяется СТРОГО по total_referrals (количеству оплативших).
    """
    n = max(0, total_referrals)
    if n >= 100:
        return ("Амбассадор", None)
    if n >= 75:
        return ("Лидер", "Амбассадор")
    if n >= 50:
        return ("Инсайдер", "Лидер")
    if n >= 25:
        return ("Хранитель", "Инсайдер")
    return ("Проводник", "Хранитель")


def get_loyalty_screen_attachment(current_status_key: str) -> Optional[str]:
    """Return Telegram file_id for the loyalty screen image for the given status."""
    return get_loyalty_photo_id(current_status_key)


def get_loyalty_photo_id(status_name: str) -> Optional[str]:
    """Return PROD Telegram file_id for the loyalty screen by status name."""
    if not status_name:
        return None
    key = _TIER_TO_IMAGE_KEY.get(status_name)
    if key is None:
        # Fallback: первый токен в lower-case (для legacy «silver»/«gold»/…)
        key = status_name.lower().split()[0] if status_name else None
    return LOYALTY_PHOTOS.get(key) if key else None
