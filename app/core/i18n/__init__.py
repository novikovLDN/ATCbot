"""
Enterprise I18N architecture for Atlas Secure.

Strict localization: RU canonical, fallback to RU, no hardcoded text.
"""

from .manager import I18nManager
from .types import I18nConfig

__all__ = ["I18nManager", "I18nConfig"]
