"""
Loyalty program status names and screen assets (UI layer).

Tiers «Круга Амбассадоров» (миграция 059_ambassador_cashback_floor):
- 0-24:   Проводник  (10%)
- 25-49:  Хранитель  (20%)
- 50-74:  Инсайдер   (30%)
- 75-99:  Лидер      (40%)
- 100+:   Амбассадор (45%, навсегда)
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


# ── Tier emoji (premium emoji_id или unicode fallback) ────────────────
#
# Структура: {tier_name: (premium_emoji_id | None, unicode_fallback)}.
# Если premium_emoji_id задан — рендерим через <tg-emoji>, иначе unicode.
TIER_EMOJI: dict[str, Tuple[Optional[str], str]] = {
    "Проводник":  ("5425141507050973573", "🏄"),
    "Хранитель":  ("5399988331729664856", "👀"),
    "Инсайдер":   ("5413623448440160154", "👨‍💻"),
    "Лидер":      ("5278467510604160626", "💰"),
    "Амбассадор": ("5229011542011299168", "👑"),
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


