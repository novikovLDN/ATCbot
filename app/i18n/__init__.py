# -*- coding: utf-8 -*-
"""
Modular I18N architecture for Atlas Secure.
Strict localization: no hardcoded UI strings in logic.

Language resolution:
- If language not in LANGUAGES → use DEFAULT_LANGUAGE (ru)
- Otherwise → use exact language module
- If key missing in requested language → fallback to English
- If key missing in all languages → return key (safe fallback, never crash)
"""

import logging

from . import ru, en, uz, tj, de, kk, ar

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "ru"

LANGUAGES = {
    "ru": ru.LANG,
    "en": en.LANG,
    "uz": uz.LANG,
    "tj": tj.LANG,
    "de": de.LANG,
    "kk": kk.LANG,
    "ar": ar.LANG,
}


def get_text(language: str, key: str, strict: bool = None, **kwargs) -> str:
    """
    Get localized text for key in given language.

    Args:
        language: Language code (ru, en, uz, tj, de, kk, ar)
        key: Dot-separated key (e.g. main.profile, common.back)
        strict: Deprecated, kept for backward compatibility. No longer raises.
        **kwargs: Format placeholders (e.g. user="John" for {user})

    Returns:
        Localized string, optionally formatted. Never raises.
    """
    # 1. Try requested language
    lang_dict = LANGUAGES.get(language, LANGUAGES[DEFAULT_LANGUAGE])
    text = lang_dict.get(key)

    if text is not None:
        if kwargs:
            return text.format(**kwargs)
        return text

    # 2. Fallback to English
    en_dict = LANGUAGES.get("en", {})
    if key in en_dict:
        logger.warning("I18N fallback to EN for key=%s, lang=%s", key, language)
        text = en_dict[key]
        if kwargs:
            return text.format(**kwargs)
        return text

    # 3. Return key itself (safe fallback)
    logger.error("I18N missing key in all languages: %s", key)
    return key


__all__ = ["get_text", "LANGUAGES"]
