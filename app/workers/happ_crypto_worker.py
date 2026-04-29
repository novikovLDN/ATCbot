"""
Batch worker: generate Happ crypto links for all existing users.

Rate limited to 5 req/s to avoid overloading crypto.happ.su API.
Triggered via admin panel or run once on deploy.
"""
import asyncio
import logging

import config
import database
from app.services.happ_crypto import generate_crypto_link
from app.services import remnawave_api

logger = logging.getLogger(__name__)

BATCH_RATE_LIMIT = 5  # requests per second


async def migrate_all_crypto_links(bot=None, admin_chat_id=None):
    """Generate crypto links for all users with remnawave_uuid but no crypto link."""
    if not config.HAPP_CRYPTO_ENABLED:
        logger.info("HAPP_CRYPTO_MIGRATION: disabled, skipping")
        return

    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS happ_crypto_link TEXT")
        rows = await conn.fetch(
            """SELECT telegram_id, remnawave_uuid FROM subscriptions
               WHERE remnawave_uuid IS NOT NULL
               AND happ_crypto_link IS NULL
               AND status = 'active'
               ORDER BY telegram_id"""
        )

    total = len(rows)
    if total == 0:
        logger.info("HAPP_CRYPTO_MIGRATION: no users to migrate")
        if bot and admin_chat_id:
            await bot.send_message(admin_chat_id, "✅ Все пользователи уже имеют crypto-ссылки.", parse_mode="HTML")
        return

    logger.info("HAPP_CRYPTO_MIGRATION: starting for %d users", total)
    if bot and admin_chat_id:
        await bot.send_message(admin_chat_id, f"🔄 Генерация crypto-ссылок: {total} пользователей...", parse_mode="HTML")

    success = 0
    failed = 0

    for i, row in enumerate(rows):
        telegram_id = row["telegram_id"]
        rmn_uuid = row["remnawave_uuid"]

        try:
            traffic = await remnawave_api.get_user_traffic(rmn_uuid)
            if not traffic:
                failed += 1
                continue

            sub_url = traffic.get("subscriptionUrl", "")
            if not sub_url:
                failed += 1
                continue

            crypto_link = await generate_crypto_link(sub_url)
            if crypto_link:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE subscriptions SET happ_crypto_link = $1 WHERE telegram_id = $2",
                        crypto_link, telegram_id,
                    )
                success += 1
            else:
                failed += 1

        except Exception as e:
            logger.warning("HAPP_CRYPTO_MIGRATION_ERROR: tg=%s %s", telegram_id, e)
            failed += 1

        # Rate limit
        if (i + 1) % BATCH_RATE_LIMIT == 0:
            await asyncio.sleep(1.0)

        # Progress every 100 users
        if (i + 1) % 100 == 0:
            logger.info("HAPP_CRYPTO_MIGRATION: %d/%d (success=%d, failed=%d)", i + 1, total, success, failed)

    logger.info("HAPP_CRYPTO_MIGRATION: DONE total=%d success=%d failed=%d", total, success, failed)
    if bot and admin_chat_id:
        await bot.send_message(
            admin_chat_id,
            f"✅ Миграция crypto-ссылок завершена\n\n"
            f"Всего: {total}\nУспешно: {success}\nОшибок: {failed}",
            parse_mode="HTML",
        )
