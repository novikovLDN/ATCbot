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
from vpn_utils import VPNAPIError, TimeoutError, AuthError

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
            
            if len(rows) > 0:
                logger.info(f"cleanup: FOUND_EXPIRED [count={len(rows)}]")
            
            for row in rows:
                telegram_id = row["telegram_id"]
                uuid = row["uuid"]
                expires_at = row["expires_at"]
                
                try:
                    # ЗАЩИТА: Проверяем что подписка действительно истекла
                    if expires_at >= datetime.now():
                        logger.warning(
                            f"Fast cleanup: Subscription for user {telegram_id} with UUID {uuid} "
                            f"has expires_at={expires_at.isoformat()} >= now, skipping"
                        )
                        continue
                    
                    # ATOMIC LOGIC: СНАЧАЛА удаляем UUID из Xray API
                    # Безопасное логирование UUID
                    uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                    logger.info(
                        f"cleanup: REMOVING_UUID [user={telegram_id}, uuid={uuid_preview}, expires_at={expires_at.isoformat()}]"
                    )
                    
                    await vpn_utils.remove_vless_user(uuid)
                    logger.info(f"cleanup: VPN_API_REMOVED [user={telegram_id}, uuid={uuid_preview}]")
                    
                    # ТОЛЬКО если API ответил успехом - очищаем БД
                    pool = await database.get_pool()
                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            # Проверяем, что UUID всё ещё существует (защита от race condition)
                            check_row = await conn.fetchrow(
                                "SELECT uuid, expires_at FROM subscriptions WHERE telegram_id = $1 AND uuid = $2 AND status = 'active'",
                                telegram_id, uuid
                            )
                            
                            if check_row:
                                # Дополнительная проверка: убеждаемся что подписка действительно истекла
                                check_expires_at = check_row["expires_at"]
                                if check_expires_at >= datetime.now():
                                    logger.warning(
                                        f"Fast cleanup: Subscription for user {telegram_id} was renewed, "
                                        f"expires_at={check_expires_at.isoformat()}, skipping cleanup"
                                    )
                                    continue
                                
                                # UUID всё ещё существует и подписка истекла - помечаем как expired
                                await conn.execute(
                                    """UPDATE subscriptions 
                                       SET status = 'expired', uuid = NULL, vpn_key = NULL 
                                       WHERE telegram_id = $1 AND uuid = $2""",
                                    telegram_id, uuid
                                )
                                logger.info(f"cleanup: SUBSCRIPTION_EXPIRED [user={telegram_id}, uuid={uuid_preview}]")
                                
                                # Логируем действие
                                import config
                                await database._log_audit_event_atomic(conn, "uuid_fast_deleted", config.ADMIN_TELEGRAM_ID, telegram_id, 
                                    f"Fast-deleted expired UUID {uuid}, expired_at={expires_at.isoformat()}")
                                
                                # Безопасное логирование UUID
                                uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                                logger.info(
                                    f"cleanup: SUCCESS [user={telegram_id}, uuid={uuid_preview}, expired_at={expires_at.isoformat()}]"
                                )
                            else:
                                logger.debug(
                                    f"Fast cleanup: UUID_ALREADY_CLEANED [action=expire_race, user={telegram_id}, "
                                    f"uuid={uuid}] - race condition"
                                )
                    
                except (ValueError, vpn_utils.VPNAPIError, vpn_utils.TimeoutError) as e:
                    # VPN API ошибки - логируем и пропускаем (не чистим БД)
                    uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                    if "VPN API is not configured" in str(e) or isinstance(e, ValueError):
                        logger.warning(
                            f"cleanup: VPN_API_DISABLED [user={telegram_id}, uuid={uuid_preview}] - skipping"
                        )
                    else:
                        logger.error(
                            f"cleanup: VPN_API_ERROR [user={telegram_id}, uuid={uuid_preview}, error={str(e)}] - will retry"
                        )
                except Exception as e:
                    # При любой ошибке НЕ чистим БД - повторим в следующем цикле
                    uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                    logger.error(
                        f"cleanup: ERROR [user={telegram_id}, uuid={uuid_preview}, error={str(e)}] - will retry"
                    )
                    logger.warning(f"cleanup: Will retry UUID {uuid_preview} for user {telegram_id} in next cycle")
            
        except asyncio.CancelledError:
            logger.info("Fast expiry cleanup task cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in fast expiry cleanup task: {e}", exc_info=True)
            # Продолжаем работу даже при ошибке
            await asyncio.sleep(10)  # Небольшая задержка перед следующей итерацией



