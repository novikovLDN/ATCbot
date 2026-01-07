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
import vpn_utils

logger = logging.getLogger(__name__)

# Интервал проверки: каждые 60 секунд
FAST_CLEANUP_INTERVAL_SECONDS = 60


async def fast_expiry_cleanup_task():
    """
    Fast Expiry Cleanup Task
    
    Фоновая задача для быстрого отключения истёкших подписок.
    Работает независимо от основного cleanup.
    
    Логика:
    1. Находит все подписки с expires_at < now() и status = 'active' и uuid IS NOT NULL
    2. Удаляет UUID из Xray API
    3. ТОЛЬКО при успехе - помечает статус как 'expired' и очищает UUID
    4. При ошибке - НЕ очищает БД, повторит в следующем цикле
    """
    logger.info("Fast expiry cleanup task started (interval: 60 seconds)")
    
    while True:
        try:
            await asyncio.sleep(FAST_CLEANUP_INTERVAL_SECONDS)
            
            # Получаем истёкшие подписки с активными UUID
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT telegram_id, uuid, vpn_key, expires_at, status 
                       FROM subscriptions 
                       WHERE expires_at < $1 
                       AND status = 'active'
                       AND uuid IS NOT NULL""",
                    datetime.now()
                )
            
            if not rows:
                continue
            
            logger.info(f"Fast cleanup: Found {len(rows)} expired subscriptions with active UUIDs")
            
            for row in rows:
                telegram_id = row["telegram_id"]
                uuid = row["uuid"]
                expires_at = row["expires_at"]
                
                try:
                    # ATOMIC LOGIC: СНАЧАЛА удаляем UUID из Xray API
                    await vpn_utils.remove_vless_user(uuid)
                    
                    # ТОЛЬКО если API ответил успехом - очищаем БД
                    pool = await database.get_pool()
                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            # Проверяем, что UUID всё ещё существует (защита от race condition)
                            check_row = await conn.fetchrow(
                                "SELECT uuid FROM subscriptions WHERE telegram_id = $1 AND uuid = $2 AND status = 'active'",
                                telegram_id, uuid
                            )
                            
                            if check_row:
                                # UUID всё ещё существует - помечаем как expired
                                await conn.execute(
                                    """UPDATE subscriptions 
                                       SET status = 'expired', uuid = NULL, vpn_key = NULL 
                                       WHERE telegram_id = $1 AND uuid = $2""",
                                    telegram_id, uuid
                                )
                                
                                # Логируем действие
                                import config
                                await database._log_audit_event_atomic(conn, "uuid_fast_deleted", config.ADMIN_TELEGRAM_ID, telegram_id, 
                                    f"Fast-deleted expired UUID {uuid}, expired_at={expires_at.isoformat()}")
                                
                                logger.info(f"Fast cleanup: Deleted UUID {uuid} for expired subscription, user {telegram_id}, expired_at={expires_at}")
                            else:
                                logger.debug(f"Fast cleanup: UUID {uuid} for user {telegram_id} was already cleaned up (race condition)")
                    
                except Exception as e:
                    # При любой ошибке НЕ чистим БД - повторим в следующем цикле
                    logger.error(f"Fast cleanup: Error cleaning up UUID {uuid} for user {telegram_id}: {e}", exc_info=True)
                    logger.warning(f"Fast cleanup: Will retry UUID {uuid} for user {telegram_id} in next cycle")
            
        except asyncio.CancelledError:
            logger.info("Fast expiry cleanup task cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in fast expiry cleanup task: {e}", exc_info=True)
            # Продолжаем работу даже при ошибке
            await asyncio.sleep(10)  # Небольшая задержка перед следующей итерацией



