# -*- coding: utf-8 -*-
"""
Modular I18N architecture for Atlas Secure.
Strict localization: no hardcoded UI strings in logic.
"""

from . import ru, en, uz, tj, de, kk, ar

LANGUAGES = {
    "ru": ru.LANG,
    "en": en.LANG,
    "uz": uz.LANG,
    "tj": tj.LANG,
    "de": de.LANG,
    "kk": kk.LANG,
    "ar": ar.LANG,
}


def get_text(language: str, key: str, strict: bool = False, **kwargs) -> str:
    """
    Get localized text for key in given language.

    Args:
        language: Language code (ru, en, uz, tj, de, kk, ar)
        key: Dot-separated key (e.g. main.profile, common.back)
        strict: If True, raise ValueError on missing key. If False, return [MISSING:key]
        **kwargs: Format placeholders (e.g. user="John" for {user})

    Returns:
        Localized string, optionally formatted.
    """
    lang_dict = LANGUAGES.get(language, LANGUAGES["ru"])
    text = lang_dict.get(key)

    if text is None:
        if strict:
            raise ValueError(f"[I18N] Missing key: {key} ({language})")
        return f"[MISSING:{key}]"

    if kwargs:
        return text.format(**kwargs)

    return text


__all__ = ["get_text", "LANGUAGES"]
