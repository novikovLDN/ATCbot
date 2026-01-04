"""
Background task для автоматического удаления истекших Outline ключей
"""
import asyncio
import logging
from datetime import datetime
import database
import outline_api

logger = logging.getLogger(__name__)

# Интервал проверки (10-15 минут)
CLEANUP_INTERVAL_SECONDS = 10 * 60  # 10 минут


async def outline_cleanup_task():
    """
    Background task для автоматического удаления истекших Outline ключей
    
    Периодически:
    1. Находит подписки с expires_at < now и outline_key_id IS NOT NULL
    2. Удаляет ключи из Outline
    3. Очищает outline_key_id и vpn_key в БД
    4. Логирует действия
    """
    logger.info("Outline cleanup task started")
    
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            
            # Получаем истекшие подписки с Outline ключами
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
            
            logger.info(f"Found {len(rows)} expired subscriptions with Outline keys to cleanup")
            
            for row in rows:
                telegram_id = row["telegram_id"]
                outline_key_id = row["outline_key_id"]
                expires_at = row["expires_at"]
                
                try:
                    # Удаляем ключ из Outline
                    success = await outline_api.delete_outline_key(outline_key_id)
                    if success:
                        logger.info(f"Deleted Outline key {outline_key_id} for expired subscription, user {telegram_id}, expired_at={expires_at}")
                    else:
                        logger.warning(f"Failed to delete Outline key {outline_key_id} for user {telegram_id}")
                    
                    # Очищаем outline_key_id и vpn_key в БД
                    pool = await database.get_pool()
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """UPDATE subscriptions 
                               SET outline_key_id = NULL, vpn_key = NULL 
                               WHERE telegram_id = $1""",
                            telegram_id
                        )
                    
                    # Логируем действие
                    import config
                    await database._log_audit_event_atomic_standalone(
                        "outline_key_auto_deleted",
                        config.ADMIN_TELEGRAM_ID,
                        telegram_id,
                        f"Auto-deleted expired Outline key {outline_key_id}, expired_at={expires_at.isoformat()}"
                    )
                    
                except Exception as e:
                    logger.error(f"Error cleaning up Outline key {outline_key_id} for user {telegram_id}: {e}", exc_info=True)
            
        except asyncio.CancelledError:
            logger.info("Outline cleanup task cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in outline cleanup task: {e}", exc_info=True)
            # Продолжаем работу даже при ошибке
            await asyncio.sleep(60)  # Небольшая задержка перед следующей итерацией

