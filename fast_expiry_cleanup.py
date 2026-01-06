"""
Fast Expiry Cleanup - быстрая очистка истёкших подписок

Фоновая задача для немедленного отключения VPN-ключей
после окончания подписки (максимальная задержка 60 секунд).

Особенно важно для коротких доступов (10 минут, 1 день).
"""
import asyncio
import logging
from datetime import datetime
import database
import outline_api

logger = logging.getLogger(__name__)

# Интервал проверки: каждые 60 секунд
FAST_CLEANUP_INTERVAL_SECONDS = 60


async def fast_expiry_cleanup_task():
    """
    Fast Expiry Cleanup Task
    
    Фоновая задача для быстрого отключения истёкших подписок.
    Работает независимо от основного outline_cleanup.
    
    Логика:
    1. Находит все подписки с expires_at < now() и outline_key_id IS NOT NULL
    2. Удаляет ключи из Outline API
    3. ТОЛЬКО при успехе - очищает БД
    4. При ошибке - НЕ очищает БД, повторит в следующем цикле
    """
    logger.info("Fast expiry cleanup task started (interval: 60 seconds)")
    
    while True:
        try:
            await asyncio.sleep(FAST_CLEANUP_INTERVAL_SECONDS)
            
            # Получаем истёкшие подписки с Outline ключами
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT telegram_id, outline_key_id, vpn_key, expires_at 
                       FROM subscriptions 
                       WHERE expires_at < $1 
                       AND outline_key_id IS NOT NULL""",
                    datetime.now()
                )
            
            if not rows:
                continue
            
            logger.info(f"Fast cleanup: Found {len(rows)} expired subscriptions with Outline keys")
            
            for row in rows:
                telegram_id = row["telegram_id"]
                outline_key_id = row["outline_key_id"]
                expires_at = row["expires_at"]
                
                try:
                    # ATOMIC LOGIC: СНАЧАЛА удаляем ключ из Outline API
                    success = await outline_api.delete_outline_key(outline_key_id)
                    
                    if success:
                        # ТОЛЬКО если API ответил успехом - очищаем БД
                        pool = await database.get_pool()
                        async with pool.acquire() as conn:
                            async with conn.transaction():
                                # Проверяем, что ключ всё ещё существует (защита от race condition)
                                check_row = await conn.fetchrow(
                                    "SELECT outline_key_id FROM subscriptions WHERE telegram_id = $1 AND outline_key_id = $2",
                                    telegram_id, outline_key_id
                                )
                                
                                if check_row:
                                    # Ключ всё ещё существует - очищаем
                                    await conn.execute(
                                        """UPDATE subscriptions 
                                           SET outline_key_id = NULL, vpn_key = NULL 
                                           WHERE telegram_id = $1 AND outline_key_id = $2""",
                                        telegram_id, outline_key_id
                                    )
                                    
                                    # Логируем действие
                                    import config
                                    await database._log_audit_event_atomic(conn, "outline_key_fast_deleted", config.ADMIN_TELEGRAM_ID, telegram_id, 
                                        f"Fast-deleted expired Outline key {outline_key_id}, expired_at={expires_at.isoformat()}")
                                    
                                    logger.info(f"Fast cleanup: Deleted Outline key {outline_key_id} for expired subscription, user {telegram_id}, expired_at={expires_at}")
                                else:
                                    logger.debug(f"Fast cleanup: Outline key {outline_key_id} for user {telegram_id} was already cleaned up (race condition)")
                    else:
                        # При ошибке удаления НЕ чистим БД - повторим в следующем цикле
                        logger.warning(f"Fast cleanup: Failed to delete Outline key {outline_key_id} for user {telegram_id}, will retry in next cycle")
                    
                except Exception as e:
                    # При любой ошибке НЕ чистим БД - повторим в следующем цикле
                    logger.error(f"Fast cleanup: Error cleaning up Outline key {outline_key_id} for user {telegram_id}: {e}", exc_info=True)
            
        except asyncio.CancelledError:
            logger.info("Fast expiry cleanup task cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in fast expiry cleanup task: {e}", exc_info=True)
            # Продолжаем работу даже при ошибке
            await asyncio.sleep(10)  # Небольшая задержка перед следующей итерацией



