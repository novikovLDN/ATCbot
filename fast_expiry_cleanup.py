"""
Fast Expiry Cleanup - автоматическое отключение истёкших VPN подписок

Фоновая задача для немедленного отзыва VPN-доступа после истечения подписки.
Работает асинхронно, не блокирует основной event loop бота.

Требования:
- Запускается каждые 1-5 минут (настраивается через переменную окружения)
- Использует UTC время для сравнения дат
- Идемпотентна (безопасно запускать несколько раз)
- Устойчива к сетевым ошибкам (повтор в следующем цикле)
"""
import asyncio
import logging
import os
from datetime import datetime
import database
import vpn_utils
from vpn_utils import VPNAPIError, TimeoutError, AuthError

logger = logging.getLogger(__name__)

# Интервал проверки: 1-5 минут (настраивается через переменную окружения)
# По умолчанию: 60 секунд (1 минута)
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "60"))
# Ограничиваем интервал от 60 секунд (1 минута) до 300 секунд (5 минут)
CLEANUP_INTERVAL_SECONDS = max(60, min(300, CLEANUP_INTERVAL_SECONDS))


async def fast_expiry_cleanup_task():
    """
    Fast Expiry Cleanup Task
    
    Автоматическая фоновая задача для отключения истёкших VPN подписок.
    Работает асинхронно, не блокирует основной event loop бота.
    
    Логика:
    1. Находит все подписки где:
       - status = 'active'
       - expires_at (subscription_end) < текущее UTC время
       - uuid IS NOT NULL
    2. Для каждой подписки:
       - Вызывает POST {XRAY_API_URL}/remove-user/{uuid} с заголовком X-API-Key
       - Если API вызов успешен - обновляет статус на 'expired' и очищает uuid/vpn_key
    3. Защита от повторного удаления: проверка что UUID всё ещё существует перед обновлением БД
    4. При ошибке сети - НЕ очищает БД, повторит в следующем цикле
    
    Идемпотентность:
    - remove-user идемпотентен (отсутствие UUID на сервере не считается ошибкой)
    - Повторное удаление одного UUID безопасно
    - Защита от race condition через processing_uuids множество
    
    Не блокирует event loop:
    - Использует async/await для всех операций
    - Сетевые запросы выполняются асинхронно
    - База данных операции выполняются через asyncpg
    """
    logger.info(
        f"Fast expiry cleanup task started (interval: {CLEANUP_INTERVAL_SECONDS} seconds, "
        f"range: 60-300 seconds, using UTC time)"
    )
    
    # Множество для отслеживания UUID, которые мы уже обрабатываем (защита от race condition)
    processing_uuids = set()
    
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            
            # Получаем текущее UTC время для сравнения
            # PostgreSQL TIMESTAMP хранит без timezone, поэтому используем naive datetime
            now_utc = datetime.utcnow()
            
            # Получаем истёкшие подписки с активными UUID
            # Используем expires_at (в БД) - это и есть subscription_end
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT telegram_id, uuid, vpn_key, expires_at, status 
                       FROM subscriptions 
                       WHERE status = 'active'
                       AND expires_at < $1
                       AND uuid IS NOT NULL
                       ORDER BY expires_at ASC""",
                    now_utc
                )
            
            if not rows:
                continue
            
            logger.info(f"cleanup: FOUND_EXPIRED [count={len(rows)}]")
            
            for row in rows:
                telegram_id = row["telegram_id"]
                uuid = row["uuid"]
                expires_at = row["expires_at"]
                
                # ЗАЩИТА: Проверяем что подписка действительно истекла (используем UTC)
                if expires_at >= now_utc:
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
                    
                    # VPN AUDIT LOG: Логируем успешное удаление UUID при автоматическом истечении
                    try:
                        await database._log_vpn_lifecycle_audit_async(
                            action="vpn_expire",
                            telegram_id=telegram_id,
                            uuid=uuid,
                            source="auto-expiry",
                            result="success",
                            details=f"Auto-expired subscription, expires_at={expires_at.isoformat()}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to log VPN expire audit (non-blocking): {e}")
                    
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
                                # Дополнительная проверка: убеждаемся что подписка действительно истекла (UTC)
                                check_expires_at = check_row["expires_at"]
                                if check_expires_at >= now_utc:
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
                                    
                                    # Логируем действие в audit_log (legacy, для совместимости)
                                    import config
                                    await database._log_audit_event_atomic(
                                        conn, 
                                        "uuid_fast_deleted", 
                                        config.ADMIN_TELEGRAM_ID, 
                                        telegram_id, 
                                        f"Fast-deleted expired UUID {uuid_preview}, expired_at={expires_at.isoformat()}"
                                    )
                                    
                                    # VPN AUDIT LOG: Логируем успешное истечение подписки (non-blocking)
                                    try:
                                        await database._log_vpn_lifecycle_audit_async(
                                            action="vpn_expire",
                                            telegram_id=telegram_id,
                                            uuid=uuid,
                                            source="auto-expiry",
                                            result="success",
                                            details=f"Subscription expired and UUID removed, expires_at={expires_at.isoformat()}"
                                        )
                                    except Exception as e:
                                        logger.warning(f"Failed to log VPN expire audit (non-blocking): {e}")
                                    
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
                    # VPN AUDIT LOG: Логируем ошибку удаления UUID при автоматическом истечении
                    try:
                        await database._log_vpn_lifecycle_audit_async(
                            action="vpn_expire",
                            telegram_id=telegram_id,
                            uuid=uuid,
                            source="auto-expiry",
                            result="error",
                            details=f"Failed to remove UUID via VPN API: {str(e)}, will retry"
                        )
                    except Exception:
                        pass  # Не блокируем при ошибке логирования
                    
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



