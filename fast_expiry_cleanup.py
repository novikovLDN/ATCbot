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
    1. Находит все подписки с status = 'active' и expires_at < now() и uuid IS NOT NULL
    2. Для каждой: вызывает POST /remove-user/{uuid} (идемпотентно)
    3. ТОЛЬКО при успехе - помечает статус как 'expired' и очищает UUID
    4. Защита от повторного удаления: проверка что UUID всё ещё существует перед обновлением БД
    5. При ошибке - НЕ чистит БД, повторит в следующем цикле
    
    Идемпотентность:
    - remove-user идемпотентен (отсутствие UUID на сервере не считается ошибкой)
    - Повторное удаление одного UUID безопасно
    """
    logger.info("Fast expiry cleanup task started (interval: 60 seconds)")
    
    # Множество для отслеживания UUID, которые мы уже обрабатываем (защита от race condition)
    processing_uuids = set()
    
    while True:
        try:
            await asyncio.sleep(FAST_CLEANUP_INTERVAL_SECONDS)
            
            # Получаем истёкшие подписки с активными UUID
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT telegram_id, uuid, vpn_key, expires_at, status 
                       FROM subscriptions 
                       WHERE status = 'active'
                       AND expires_at < $1
                       AND uuid IS NOT NULL""",
                    datetime.now()
                )
            
            if not rows:
                continue
            
            logger.info(f"cleanup: FOUND_EXPIRED [count={len(rows)}]")
            
            for row in rows:
                telegram_id = row["telegram_id"]
                uuid = row["uuid"]
                expires_at = row["expires_at"]
                
                # ЗАЩИТА: Проверяем что подписка действительно истекла
                now = datetime.now()
                if expires_at >= now:
                    logger.warning(
                        f"cleanup: SKIP_NOT_EXPIRED [user={telegram_id}, expires_at={expires_at.isoformat()}, "
                        f"now={now.isoformat()}]"
                    )
                    continue
                
                # ЗАЩИТА ОТ ПОВТОРНОГО УДАЛЕНИЯ: проверяем что UUID не обрабатывается
                if uuid in processing_uuids:
                    uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                    logger.debug(
                        f"cleanup: SKIP_ALREADY_PROCESSING [user={telegram_id}, uuid={uuid_preview}] - "
                        "UUID already being processed"
                    )
                    continue
                
                # Добавляем UUID в множество обрабатываемых
                processing_uuids.add(uuid)
                uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                
                try:
                    logger.info(
                        f"cleanup: REMOVING_UUID [user={telegram_id}, uuid={uuid_preview}, "
                        f"expires_at={expires_at.isoformat()}]"
                    )
                    
                    # Вызываем POST /remove-user/{uuid} (идемпотентно)
                    # Если UUID уже удалён - это не ошибка
                    await vpn_utils.remove_vless_user(uuid)
                    logger.info(f"cleanup: VPN_API_REMOVED [user={telegram_id}, uuid={uuid_preview}]")
                    
                    # ТОЛЬКО если API ответил успехом - очищаем БД
                    pool = await database.get_pool()
                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            # ЗАЩИТА ОТ ПОВТОРНОГО УДАЛЕНИЯ: проверяем что UUID всё ещё существует
                            # и подписка всё ещё активна и истекла
                            check_row = await conn.fetchrow(
                                """SELECT uuid, expires_at, status 
                                   FROM subscriptions 
                                   WHERE telegram_id = $1 
                                   AND uuid = $2 
                                   AND status = 'active'""",
                                telegram_id, uuid
                            )
                            
                            if check_row:
                                # Дополнительная проверка: убеждаемся что подписка действительно истекла
                                check_expires_at = check_row["expires_at"]
                                if check_expires_at >= datetime.now():
                                    logger.warning(
                                        f"cleanup: SKIP_RENEWED [user={telegram_id}, uuid={uuid_preview}, "
                                        f"expires_at={check_expires_at.isoformat()}] - subscription was renewed"
                                    )
                                    continue
                                
                                # UUID всё ещё существует и подписка истекла - помечаем как expired
                                update_result = await conn.execute(
                                    """UPDATE subscriptions 
                                       SET status = 'expired', uuid = NULL, vpn_key = NULL 
                                       WHERE telegram_id = $1 
                                       AND uuid = $2 
                                       AND status = 'active'""",
                                    telegram_id, uuid
                                )
                                
                                # Верифицируем что обновление прошло
                                if update_result == "UPDATE 1":
                                    logger.info(
                                        f"cleanup: SUBSCRIPTION_EXPIRED [user={telegram_id}, uuid={uuid_preview}, "
                                        f"expires_at={expires_at.isoformat()}]"
                                    )
                                    
                                    # Логируем действие в audit_log
                                    import config
                                    await database._log_audit_event_atomic(
                                        conn, 
                                        "uuid_fast_deleted", 
                                        config.ADMIN_TELEGRAM_ID, 
                                        telegram_id, 
                                        f"Fast-deleted expired UUID {uuid_preview}, expired_at={expires_at.isoformat()}"
                                    )
                                    
                                    logger.info(
                                        f"cleanup: SUCCESS [user={telegram_id}, uuid={uuid_preview}, "
                                        f"expires_at={expires_at.isoformat()}]"
                                    )
                                else:
                                    logger.warning(
                                        f"cleanup: UPDATE_FAILED [user={telegram_id}, uuid={uuid_preview}, "
                                        f"update_result={update_result}] - UUID may have been updated by another process"
                                    )
                            else:
                                # UUID уже удалён или подписка уже неактивна
                                logger.debug(
                                    f"cleanup: UUID_ALREADY_CLEANED [user={telegram_id}, uuid={uuid_preview}] - "
                                    "UUID was already removed or subscription is no longer active"
                                )
                    
                except vpn_utils.AuthError as e:
                    # Ошибка аутентификации - критическая, не retry
                    logger.error(
                        f"cleanup: AUTH_ERROR [user={telegram_id}, uuid={uuid_preview}, error={str(e)}] - "
                        "VPN API authentication failed"
                    )
                    # Не удаляем из processing_uuids, чтобы не повторять попытки с неверными креденшелами
                    
                except (vpn_utils.TimeoutError, vpn_utils.VPNAPIError) as e:
                    # VPN API ошибки - логируем и пропускаем (не чистим БД, повторим в следующем цикле)
                    logger.error(
                        f"cleanup: VPN_API_ERROR [user={telegram_id}, uuid={uuid_preview}, error={str(e)}, "
                        f"error_type={type(e).__name__}] - will retry in next cycle"
                    )
                    
                except ValueError as e:
                    # VPN API не настроен - пропускаем
                    if "VPN API is not configured" in str(e):
                        logger.warning(
                            f"cleanup: VPN_API_DISABLED [user={telegram_id}, uuid={uuid_preview}] - "
                            "VPN API is not configured, skipping"
                        )
                    else:
                        logger.error(
                            f"cleanup: VALUE_ERROR [user={telegram_id}, uuid={uuid_preview}, error={str(e)}]"
                        )
                    
                except Exception as e:
                    # При любой другой ошибке - логируем и пропускаем (не чистим БД)
                    logger.error(
                        f"cleanup: UNEXPECTED_ERROR [user={telegram_id}, uuid={uuid_preview}, "
                        f"error={str(e)}, error_type={type(e).__name__}] - will retry in next cycle"
                    )
                    logger.exception(f"cleanup: EXCEPTION_TRACEBACK [user={telegram_id}, uuid={uuid_preview}]")
                    
                finally:
                    # Удаляем UUID из множества обрабатываемых
                    processing_uuids.discard(uuid)
            
        except asyncio.CancelledError:
            logger.info("Fast expiry cleanup task cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in fast expiry cleanup task: {e}", exc_info=True)
            # Продолжаем работу даже при ошибке
            await asyncio.sleep(10)  # Небольшая задержка перед следующей итерацией



