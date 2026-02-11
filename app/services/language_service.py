# -*- coding: utf-8 -*-
"""
Central language resolution for Atlas Secure.
ALL user language must be obtained via resolve_user_language.
"""
import logging

import database

DEFAULT_LANGUAGE = "ru"  # Canonical fallback when DB unavailable or user missing
logger = logging.getLogger(__name__)


async def resolve_user_language(telegram_id: int) -> str:
    """
    Get user language from DB. If missing, set to DEFAULT_LANGUAGE and persist.

    This is the ONLY valid way to obtain user language in handlers.
    """
    user = await database.get_user(telegram_id)

    if not user:
        logger.debug(f"[I18N] language resolved: {DEFAULT_LANGUAGE} for user {telegram_id} (no user)")
        return DEFAULT_LANGUAGE

    lang = user.get("language")
    if not lang:
        await database.update_user_language(telegram_id, DEFAULT_LANGUAGE)
        logger.debug(f"[I18N] language resolved: {DEFAULT_LANGUAGE} for user {telegram_id} (set default)")
        return DEFAULT_LANGUAGE

    logger.debug(f"[I18N] language resolved: {lang} for user {telegram_id}")
    return lang
