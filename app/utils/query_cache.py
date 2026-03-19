"""
Redis-backed query cache for frequently accessed, rarely changing data.

Reduces DB load for hot-path queries (user language, tariffs).
Falls back gracefully when Redis is unavailable — cache miss = DB read.

TTL strategy:
- User language: 24h (changes rarely, user triggers invalidation)
- User balance: no cache (changes frequently, must be fresh)
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "qc:"
_USER_LANG_TTL = 86400  # 24 hours
_USER_LANG_PREFIX = f"{_CACHE_PREFIX}lang:"


async def _get_redis():
    """Get Redis client, return None if unavailable."""
    try:
        from app.utils.redis_client import get_redis
        return await get_redis()
    except Exception:
        return None


# ── User Language Cache ────────────────────────────────────────────

async def get_cached_user_language(telegram_id: int) -> Optional[str]:
    """Get user language from cache. Returns None on miss."""
    redis = await _get_redis()
    if not redis:
        return None
    try:
        val = await redis.get(f"{_USER_LANG_PREFIX}{telegram_id}")
        return val if val else None
    except Exception as e:
        logger.debug("query_cache: lang get failed for %s: %s", telegram_id, e)
        return None


async def set_cached_user_language(telegram_id: int, language: str) -> None:
    """Cache user language with TTL."""
    redis = await _get_redis()
    if not redis:
        return
    try:
        await redis.setex(f"{_USER_LANG_PREFIX}{telegram_id}", _USER_LANG_TTL, language)
    except Exception as e:
        logger.debug("query_cache: lang set failed for %s: %s", telegram_id, e)


async def invalidate_user_language(telegram_id: int) -> None:
    """Remove user language from cache (call on language change)."""
    redis = await _get_redis()
    if not redis:
        return
    try:
        await redis.delete(f"{_USER_LANG_PREFIX}{telegram_id}")
    except Exception as e:
        logger.debug("query_cache: lang invalidate failed for %s: %s", telegram_id, e)


# ── Batch Language Resolution ──────────────────────────────────────

async def get_cached_user_languages_batch(telegram_ids: list[int]) -> dict[int, Optional[str]]:
    """Get multiple user languages from cache in one round-trip (MGET)."""
    result: dict[int, Optional[str]] = {}
    if not telegram_ids:
        return result

    redis = await _get_redis()
    if not redis:
        return {tid: None for tid in telegram_ids}

    try:
        keys = [f"{_USER_LANG_PREFIX}{tid}" for tid in telegram_ids]
        values = await redis.mget(keys)
        for tid, val in zip(telegram_ids, values):
            result[tid] = val if val else None
        return result
    except Exception as e:
        logger.debug("query_cache: batch lang get failed: %s", e)
        return {tid: None for tid in telegram_ids}


async def set_cached_user_languages_batch(languages: dict[int, str]) -> None:
    """Cache multiple user languages in one pipeline."""
    if not languages:
        return
    redis = await _get_redis()
    if not redis:
        return
    try:
        pipe = redis.pipeline(transaction=False)
        for tid, lang in languages.items():
            pipe.setex(f"{_USER_LANG_PREFIX}{tid}", _USER_LANG_TTL, lang)
        await pipe.execute()
    except Exception as e:
        logger.debug("query_cache: batch lang set failed: %s", e)
