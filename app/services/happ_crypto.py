"""
Happ Crypto Link service — encrypts subscription URLs via Happ API.

Encrypted links prevent users from viewing/copying/sharing VPN configs.
Format: happ://crypt4/BASE64_ENCRYPTED_DATA

Usage:
    link = await get_or_create_crypto_link(telegram_id, sub_url)
"""
import asyncio
import logging

import httpx

import config
import database

logger = logging.getLogger(__name__)

_RETRY_DELAYS = [1, 3]


async def generate_crypto_link(sub_url: str) -> str | None:
    """Encrypt subscription URL via Happ Crypto API. Returns None on failure."""
    if not config.HAPP_CRYPTO_ENABLED:
        return None

    if not sub_url:
        return None

    for attempt, delay in enumerate(_RETRY_DELAYS):
        try:
            async with httpx.AsyncClient(timeout=config.HAPP_CRYPTO_TIMEOUT) as client:
                response = await client.post(
                    config.HAPP_CRYPTO_API_URL,
                    json={"url": sub_url},
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                crypto_link = response.text.strip()

                if not crypto_link.startswith("happ://crypt"):
                    logger.error("HAPP_CRYPTO_INVALID_RESPONSE: %s", crypto_link[:80])
                    return None

                logger.info("HAPP_CRYPTO_GENERATED: url_len=%d", len(crypto_link))
                return crypto_link

        except Exception as e:
            logger.warning("HAPP_CRYPTO_API_ERROR attempt=%d: %s", attempt + 1, e)
            if attempt < len(_RETRY_DELAYS) - 1:
                await asyncio.sleep(delay)

    logger.error("HAPP_CRYPTO_FAILED after %d retries", len(_RETRY_DELAYS))
    return None


async def get_or_create_crypto_link(telegram_id: int, sub_url: str) -> str | None:
    """Get cached crypto link or generate new one."""
    if not config.HAPP_CRYPTO_ENABLED or not sub_url:
        return None

    pool = await database.get_pool()
    async with pool.acquire() as conn:
        cached = await conn.fetchval(
            "SELECT happ_crypto_link FROM subscriptions WHERE telegram_id = $1 AND happ_crypto_link IS NOT NULL",
            telegram_id,
        )
        if cached:
            return cached

    crypto_link = await generate_crypto_link(sub_url)
    if crypto_link:
        async with pool.acquire() as conn:
            await conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS happ_crypto_link TEXT"
            )
            await conn.execute(
                "UPDATE subscriptions SET happ_crypto_link = $1 WHERE telegram_id = $2",
                crypto_link, telegram_id,
            )
        logger.info("HAPP_CRYPTO_CACHED: tg=%s", telegram_id)

    return crypto_link


async def invalidate_crypto_link(telegram_id: int):
    """Clear cached crypto link (call when UUID changes)."""
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE subscriptions SET happ_crypto_link = NULL WHERE telegram_id = $1",
                telegram_id,
            )
        logger.info("HAPP_CRYPTO_INVALIDATED: tg=%s", telegram_id)
    except Exception as e:
        logger.warning("HAPP_CRYPTO_INVALIDATE_ERROR: tg=%s %s", telegram_id, e)
