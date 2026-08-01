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
from typing import Optional

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


def get_text(language: str, key: str, default: Optional[str] = None, **kwargs) -> str:
    """
    Get localized text for key in given language.

    Args:
        language: Language code (ru, en, uz, tj, de, kk, ar)
        key: Dot-separated key (e.g. main.profile, common.back)
        default: Текст на случай, если ключа нет ни в одном языке.
            Третьим позиционным аргументом раньше стоял параметр strict, из-за
            чего более сотни вызовов вида get_text(lang, key, "Запасной текст")
            молча теряли запасной текст, и пользователь видел сырой ключ.
        **kwargs: Format placeholders (e.g. user="John" for {user})

    Returns:
        Localized string, optionally formatted. Never raises.
    """
    # 1. Try requested language
    lang_dict = LANGUAGES.get(language, LANGUAGES[DEFAULT_LANGUAGE])
    text = lang_dict.get(key)

    if text is not None:
        if kwargs:
            return _safe_format(text, key, kwargs)
        return text

    # 2. Fallback to English
    en_dict = LANGUAGES.get("en", {})
    if key in en_dict:
        logger.warning("I18N fallback to EN for key=%s, lang=%s", key, language)
        text = en_dict[key]
        if kwargs:
            return _safe_format(text, key, kwargs)
        return text

    # 3. Запасной текст вызывающего кода, если он передан
    if isinstance(default, str) and default:
        logger.error("I18N missing key in all languages: %s (использован запасной текст)", key)
        if kwargs:
            return _safe_format(default, key, kwargs)
        return default

    # 4. Return key itself (safe fallback)
    logger.error("I18N missing key in all languages: %s", key)
    return key


def _safe_format(text: str, key: str, kwargs: dict) -> str:
    """Подставить плейсхолдеры, не роняя обработчик из-за одного плохого ключа.

    Пропущенный или лишний плейсхолдер раньше поднимал KeyError прямо в
    обработчике: пользователь получал не текст, а отсутствие ответа.
    """
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError) as e:
        logger.error(
            "I18N format failed for key=%s: %s: %s — возвращён неформатированный текст",
            key, type(e).__name__, e,
        )
        return text


__all__ = ["get_text", "LANGUAGES"]
