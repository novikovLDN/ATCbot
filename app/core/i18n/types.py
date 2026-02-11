"""
I18N type definitions and constants.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class I18nConfig:
    """Configuration for I18nManager."""
    locales_dir: str
    canonical_lang: str = "ru"
    strict_mode: bool = False  # True = dev (raise on missing), False = prod (log + fallback)
    rtl_langs: tuple = ("ar",)
    default_lang: str = "ru"


# CLDR plural categories
PLURAL_ONE = "one"
PLURAL_FEW = "few"
PLURAL_MANY = "many"
PLURAL_OTHER = "other"

# Plural rules per language (simplified CLDR)
# n mod 10 == 1 and n mod 100 != 11 -> one
# n mod 10 in (2,3,4) and n mod 100 not in (12,13,14) -> few
# n mod 10 == 0 or n mod 10 in (5,6,7,8,9) or n mod 100 in (11,12,13,14) -> many
PLURAL_RULES: dict[str, callable] = {
    "ru": lambda n: PLURAL_ONE if n % 10 == 1 and n % 100 != 11 else
                   PLURAL_FEW if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else PLURAL_MANY,
    "en": lambda n: PLURAL_ONE if n == 1 else PLURAL_OTHER,
    "uz": lambda n: PLURAL_ONE if n == 1 else PLURAL_OTHER,
    "tj": lambda n: PLURAL_ONE if n == 1 else PLURAL_OTHER,
    "ar": _ar_plural,
    "kk": lambda n: PLURAL_ONE if n % 10 == 1 and n % 100 != 11 else PLURAL_OTHER,
    "de": lambda n: PLURAL_ONE if n == 1 else PLURAL_OTHER,
}

# Arabic has 6 forms; we simplify to one/other for most use cases
PLURAL_ZERO = "zero"
PLURAL_TWO = "two"


def _ar_plural(n: int) -> str:
    if n == 0:
        return PLURAL_ZERO
    if n == 1:
        return PLURAL_ONE
    if n == 2:
        return PLURAL_TWO
    if 3 <= n <= 10:
        return PLURAL_FEW
    if 11 <= n <= 99:
        return PLURAL_MANY
    return PLURAL_OTHER
