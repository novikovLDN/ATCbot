#!/usr/bin/env python3
"""
Полная синхронизация подписок из БД в Xray.
DB — источник истины. Xray — исполнитель.

Использование:
    python -m scripts.full_xray_resync

Или из кода:
    from scripts.full_xray_resync import full_xray_resync
    await full_xray_resync()
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def full_xray_resync() -> int:
    """
    Синхронизирует всех пользователей с активной подпиской из БД в Xray.
    Для каждого: ensure_user_in_xray(uuid, expires_at).
    UUID из БД не меняется.

    Returns:
        Количество успешно синхронизированных подписок.
    """
    import database
    import vpn_utils

    if not database.DB_READY:
        logger.error("Database not ready")
        return 0

    pool = await database.get_pool()
    if not pool:
        logger.error("Database pool not available")
        return 0

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.telegram_id, s.uuid, s.expires_at
               FROM subscriptions s
               WHERE s.status = 'active'
                 AND s.uuid IS NOT NULL
                 AND s.expires_at > NOW()"""
        )

    count = 0
    for row in rows:
        telegram_id = row["telegram_id"]
        uuid_val = row["uuid"]
        expires_at_raw = row["expires_at"]
        expires_at = database._from_db_utc(expires_at_raw) if expires_at_raw else None
        if not expires_at:
            logger.warning(f"Skip telegram_id={telegram_id}: no expires_at")
            continue
        try:
            await vpn_utils.ensure_user_in_xray(
                telegram_id=telegram_id,
                uuid=uuid_val,
                subscription_end=expires_at
            )
            count += 1
            logger.info(f"full_xray_resync: synced telegram_id={telegram_id} uuid={uuid_val[:8]}...")
        except Exception as e:
            logger.error(f"full_xray_resync: FAILED telegram_id={telegram_id} uuid={uuid_val[:8] if uuid_val else 'N/A'}... error={e}")
            raise  # Fail fast on first error

    logger.info(f"full_xray_resync: done, synced {count} subscriptions")
    return count


async def main() -> None:
    import database
    await database.init_db()
    await full_xray_resync()


if __name__ == "__main__":
    asyncio.run(main())
