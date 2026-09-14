# -*- coding: utf-8 -*-
"""
Modular I18N architecture for Atlas Secure.
Supported: русский (default) + English. Остальные локали удалены
2026-XX по решению о фокусе на CIS/EN аудиторию.

Legacy юзеры с language='de'/'ar'/'kk'/'tj'/'uz' в БД автоматом
получат тексты на русском (fallback через LANGUAGES.get()).
"""

import logging

from . import ru, en

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "ru"

LANGUAGES = {
    "ru": ru.LANG,
    "en": en.LANG,
}


def get_text(language: str, key: str, strict: bool = None, **kwargs) -> str:
    """
    Get localized text for key in given language.

    Args:
        language: 'ru' | 'en' (любой другой → русский)
        key: Dot-separated key (e.g. main.profile, common.back)
        strict: Deprecated, kept for backward compatibility.
        **kwargs: Format placeholders (e.g. user="John" для {user})

    Returns:
        Localized string, optionally formatted. Never raises.
    """
    # 1. Try requested language (unknown lang → default ru)
    lang_dict = LANGUAGES.get(language, LANGUAGES[DEFAULT_LANGUAGE])
    text = lang_dict.get(key)

    if text is not None:
        if kwargs:
            return text.format(**kwargs)
        return text

    # 2. Fallback to English if requested was ru
    en_dict = LANGUAGES["en"]
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
