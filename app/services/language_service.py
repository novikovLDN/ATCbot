# -*- coding: utf-8 -*-
"""
Central language resolution for Atlas Secure.
ALL user language must be obtained via resolve_user_language.
"""
import logging
from typing import Optional

import database
from app.utils.query_cache import (
    get_cached_user_language,
    set_cached_user_language,
    invalidate_user_language,
    get_cached_user_languages_batch,
    set_cached_user_languages_batch,
)

DEFAULT_LANGUAGE = "ru"  # Canonical fallback when DB unavailable or user missing
logger = logging.getLogger(__name__)


async def resolve_user_language(telegram_id: int) -> str:
    """
    Get user language from cache, then DB. If missing, set to DEFAULT_LANGUAGE and persist.

    This is the ONLY valid way to obtain user language in handlers.
    """
    # Try cache first
    cached = await get_cached_user_language(telegram_id)
    if cached:
        return cached

    try:
        user = await database.get_user(telegram_id)
    except Exception as e:
        logger.warning(f"Failed to get user for language resolution (user={telegram_id}): {e}")
        return DEFAULT_LANGUAGE

    if not user:
        logger.debug(f"[I18N] language resolved: {DEFAULT_LANGUAGE} for user {telegram_id} (no user)")
        return DEFAULT_LANGUAGE

    lang = user.get("language")
    if not lang:
        await database.update_user_language(telegram_id, DEFAULT_LANGUAGE)
        lang = DEFAULT_LANGUAGE
        logger.debug(f"[I18N] language resolved: {DEFAULT_LANGUAGE} for user {telegram_id} (set default)")

    # Populate cache
    await set_cached_user_language(telegram_id, lang)
    logger.debug(f"[I18N] language resolved: {lang} for user {telegram_id}")
    return lang


async def resolve_user_languages_batch(telegram_ids: list[int]) -> dict[int, str]:
    """
    Batch-resolve languages for multiple users (eliminates N+1 in workers).

    1. Check Redis cache (MGET) for all IDs
    2. For cache misses, batch-fetch from DB in one query
    3. Populate cache for fetched values
    """
    if not telegram_ids:
        return {}

    result: dict[int, str] = {}

    # Step 1: Check cache
    cached = await get_cached_user_languages_batch(telegram_ids)
    missing_ids = []
    for tid in telegram_ids:
        lang = cached.get(tid)
        if lang:
            result[tid] = lang
        else:
            missing_ids.append(tid)

    if not missing_ids:
        return result

    # Step 2: Batch fetch from DB
    try:
        pool = await database.get_pool()
        if pool:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT telegram_id, language FROM users WHERE telegram_id = ANY($1::bigint[])",
                    missing_ids,
                )
                to_cache: dict[int, str] = {}
                fetched_ids = set()
                for row in rows:
                    tid = row["telegram_id"]
                    lang = row["language"] or DEFAULT_LANGUAGE
                    result[tid] = lang
                    to_cache[tid] = lang
                    fetched_ids.add(tid)

                # Users not found in DB get default
                for tid in missing_ids:
                    if tid not in fetched_ids:
                        result[tid] = DEFAULT_LANGUAGE
                        to_cache[tid] = DEFAULT_LANGUAGE

                # Step 3: Populate cache
                await set_cached_user_languages_batch(to_cache)
        else:
            for tid in missing_ids:
                result[tid] = DEFAULT_LANGUAGE
    except Exception as e:
        logger.warning("Batch language resolution failed: %s", e)
        for tid in missing_ids:
            result[tid] = DEFAULT_LANGUAGE

    return result
