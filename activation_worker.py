"""Модуль для активации отложенных VPN подписок"""
import asyncio
import logging
import os
from datetime import datetime
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncpg
import database
import localization
import config
import vpn_utils
import admin_notifications

logger = logging.getLogger(__name__)

# Конфигурация интервала проверки активации (по умолчанию 5 минут)
ACTIVATION_INTERVAL_SECONDS = int(os.getenv("ACTIVATION_INTERVAL_SECONDS", "300"))  # 5 минут
if ACTIVATION_INTERVAL_SECONDS < 60:  # Минимум 1 минута
    ACTIVATION_INTERVAL_SECONDS = 60
if ACTIVATION_INTERVAL_SECONDS > 1800:  # Максимум 30 минут
    ACTIVATION_INTERVAL_SECONDS = 1800

# Максимальное количество попыток активации
MAX_ACTIVATION_ATTEMPTS = int(os.getenv("MAX_ACTIVATION_ATTEMPTS", "5"))
if MAX_ACTIVATION_ATTEMPTS < 1:
    MAX_ACTIVATION_ATTEMPTS = 1
if MAX_ACTIVATION_ATTEMPTS > 20:
    MAX_ACTIVATION_ATTEMPTS = 20


async def process_pending_activations(bot: Bot):
    """
    Обработать подписки с отложенной активацией (activation_status='pending')
    
    ИНВАРИАНТЫ:
    - НЕ трогаем payments
    - НЕ трогаем expires_at
    - НЕ создаём новые подписки
    - НЕ дублируем UUID
    - Только перевод состояния: pending -> active или failed
    
    Args:
        bot: Экземпляр Telegram бота для отправки уведомлений
    """
    if not database.DB_READY:
        logger.debug("Skipping activation worker: DB not ready")
        return
    
    if not config.VPN_ENABLED:
        logger.debug("Skipping activation worker: VPN API not enabled")
        return
    
    # RESILIENCE FIX: Handle temporary DB unavailability gracefully
    try:
        pool = await database.get_pool()
        if pool is None:
            logger.warning("Activation worker: Cannot get DB pool")
            return
    except (asyncpg.PostgresError, asyncio.TimeoutError, RuntimeError) as e:
        logger.warning(f"activation_worker: Database temporarily unavailable (pool acquisition failed): {type(e).__name__}: {str(e)[:100]}")
        return
    except Exception as e:
        logger.error(f"activation_worker: Unexpected error getting DB pool: {type(e).__name__}: {str(e)[:100]}")
        return
    
    try:
        async with pool.acquire() as conn:
            # Выбираем подписки с pending активацией и попытками меньше максимума
            pending_subscriptions = await conn.fetch(
                """SELECT telegram_id, id, activation_attempts, last_activation_error, expires_at, activated_at
                   FROM subscriptions
                   WHERE activation_status = 'pending'
                     AND activation_attempts < $1
                   ORDER BY id ASC
                   LIMIT 50""",
                MAX_ACTIVATION_ATTEMPTS
            )
            
            # Проверяем подписки для админ-уведомления (>= 2 попыток ИЛИ pending > 30 минут)
            # Используем activated_at как время создания подписки (когда она стала pending)
            from datetime import timedelta
            notification_threshold = datetime.now() - timedelta(minutes=30)
            pending_for_notification = await conn.fetch(
                """SELECT telegram_id, id, activation_attempts, last_activation_error, activated_at
                   FROM subscriptions
                   WHERE activation_status = 'pending'
                     AND (activation_attempts >= 2 
                          OR (activated_at IS NOT NULL AND activated_at < $1))
                   ORDER BY COALESCE(activated_at, '1970-01-01'::timestamp) ASC
                   LIMIT 10""",
                notification_threshold
            )
            
            # Отправляем уведомление админу, если есть подписки для уведомления
            # Используем флаг для предотвращения спама (отправляем не чаще раза в час)
            if pending_for_notification:
                oldest_pending_list = []
                for sub_row in pending_for_notification:
                    activated_at = sub_row.get("activated_at")
                    if activated_at and isinstance(activated_at, str):
                        try:
                            activated_at = datetime.fromisoformat(activated_at.replace('Z', '+00:00'))
                        except:
                            activated_at = datetime.now()
                    elif not activated_at:
                        activated_at = datetime.now()
                    
                    oldest_pending_list.append({
                        "subscription_id": sub_row["id"],
                        "telegram_id": sub_row["telegram_id"],
                        "attempts": sub_row["activation_attempts"],
                        "error": sub_row.get("last_activation_error") or "N/A",
                        "pending_since": activated_at
                    })
                
                total_pending_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
                ) or 0
                
                # Отправляем уведомление только если есть подписки, требующие внимания
                if total_pending_count > 0:
                    await admin_notifications.notify_admin_pending_activations(
                        bot, 
                        total_pending_count,
                        oldest_pending_list
                    )
            
            if not pending_subscriptions:
                logger.debug("No pending activations found")
                return
            
            logger.info(f"Found {len(pending_subscriptions)} pending activations to process")
            
            for sub_row in pending_subscriptions:
                telegram_id = sub_row["telegram_id"]
                subscription_id = sub_row["id"]
                current_attempts = sub_row["activation_attempts"]
                expires_at = sub_row["expires_at"]
                
                # Проверяем, что подписка ещё не истекла
                if expires_at and expires_at < datetime.now():
                    logger.warning(
                        f"ACTIVATION_SKIP_EXPIRED [subscription_id={subscription_id}, "
                        f"user={telegram_id}, expires_at={expires_at.isoformat()}]"
                    )
                    # Помечаем как failed, если подписка истекла до активации
                    try:
                        async with conn.transaction():
                            await conn.execute(
                                """UPDATE subscriptions
                                   SET activation_status = 'failed',
                                       last_activation_error = 'Subscription expired before activation'
                                   WHERE id = $1""",
                                subscription_id
                            )
                    except Exception as e:
                        logger.error(f"Failed to mark expired subscription as failed: {e}")
                    continue
                
                # Попытка активации
                logger.info(
                    f"ACTIVATION_RETRY_ATTEMPT [subscription_id={subscription_id}, "
                    f"user={telegram_id}, attempt={current_attempts + 1}/{MAX_ACTIVATION_ATTEMPTS}]"
                )
                
                try:
                    # Вызываем VPN API для создания UUID
                    vless_result = await vpn_utils.add_vless_user()
                    new_uuid = vless_result.get("uuid")
                    vless_url = vless_result.get("vless_url")
                    
                    # ВАЛИДАЦИЯ: Проверяем что UUID и VLESS URL получены
                    if not new_uuid:
                        error_msg = "VPN API returned empty UUID"
                        raise Exception(error_msg)
                    
                    if not vless_url:
                        error_msg = "VPN API returned empty vless_url"
                        raise Exception(error_msg)
                    
                    # ВАЛИДАЦИЯ: Проверяем VLESS ссылку
                    if not vpn_utils.validate_vless_link(vless_url):
                        error_msg = "VPN API returned invalid vless_url (contains flow=)"
                        raise Exception(error_msg)
                    
                    # Успешная активация - обновляем подписку
                    async with conn.transaction():
                        await conn.execute(
                            """UPDATE subscriptions
                               SET uuid = $1,
                                   vpn_key = $2,
                                   activation_status = 'active',
                                   activation_attempts = activation_attempts + 1,
                                   last_activation_error = NULL
                               WHERE id = $3""",
                            new_uuid, vless_url, subscription_id
                        )
                    
                    uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
                    logger.info(
                        f"ACTIVATION_SUCCESS [subscription_id={subscription_id}, "
                        f"user={telegram_id}, uuid={uuid_preview}, attempt={current_attempts + 1}]"
                    )
                    
                    # Отправляем уведомление пользователю
                    try:
                        user = await database.get_user(telegram_id)
                        language = user.get("language", "ru") if user else "ru"
                        
                        expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
                        
                        # Используем локализованный текст для успешной активации
                        text = localization.get_text(
                            language,
                            "payment_approved",
                            date=expires_str,
                            default=f"✅ Ваш VPN доступ активирован! Доступ до {expires_str}"
                        )
                        
                        # Используем стандартную клавиатуру для VPN ключа
                        import handlers
                        keyboard = handlers.get_vpn_key_keyboard(language)
                        
                        await bot.send_message(
                            telegram_id,
                            text,
                            reply_markup=keyboard,
                            parse_mode="HTML"
                        )
                        
                        logger.info(
                            f"ACTIVATION_NOTIFICATION_SENT [subscription_id={subscription_id}, user={telegram_id}]"
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send activation notification to user {telegram_id}: {e}"
                        )
                        # Не критично - подписка уже активирована
                    
                except Exception as e:
                    # Ошибка активации - увеличиваем счётчик попыток
                    error_msg = str(e)
                    new_attempts = current_attempts + 1
                    
                    logger.warning(
                        f"ACTIVATION_FAILED [subscription_id={subscription_id}, "
                        f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                        f"error={error_msg}]"
                    )
                    
                    try:
                        async with conn.transaction():
                            await conn.execute(
                                """UPDATE subscriptions
                                   SET activation_attempts = $1,
                                       last_activation_error = $2
                                   WHERE id = $3""",
                                new_attempts, error_msg, subscription_id
                            )
                            
                            # Если достигнут максимум попыток - помечаем как failed
                            if new_attempts >= MAX_ACTIVATION_ATTEMPTS:
                                await conn.execute(
                                    """UPDATE subscriptions
                                       SET activation_status = 'failed'
                                       WHERE id = $1""",
                                    subscription_id
                                )
                                
                                logger.error(
                                    f"ACTIVATION_FAILED_FINAL [subscription_id={subscription_id}, "
                                    f"user={telegram_id}, attempts={new_attempts}, error={error_msg}]"
                                )
                                
                                # Отправляем уведомление администратору
                                try:
                                    admin_message = (
                                        f"⚠️ **ОШИБКА АКТИВАЦИИ VPN ПОДПИСКИ**\n\n"
                                        f"Подписка ID: `{subscription_id}`\n"
                                        f"Пользователь: `{telegram_id}`\n"
                                        f"Попыток: {new_attempts}/{MAX_ACTIVATION_ATTEMPTS}\n"
                                        f"Ошибка: `{error_msg}`\n\n"
                                        f"Подписка помечена как `failed`.\n"
                                        f"Требуется ручная активация."
                                    )
                                    
                                    await bot.send_message(
                                        config.ADMIN_TELEGRAM_ID,
                                        admin_message,
                                        parse_mode="Markdown"
                                    )
                                    
                                    logger.info(
                                        f"Admin notification sent: Activation failed for subscription {subscription_id}"
                                    )
                                except Exception as admin_error:
                                    logger.error(
                                        f"Failed to send admin notification: {admin_error}"
                                    )
                    except Exception as db_error:
                        logger.error(
                            f"Failed to update activation attempts in DB: {db_error}"
                        )
                
                # Небольшая задержка между обработкой подписок
                await asyncio.sleep(0.5)
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
        logger.warning(f"activation_worker: Database temporarily unavailable in process_pending_activations: {type(e).__name__}: {str(e)[:100]}")
        # Не пробрасываем исключение - воркер должен продолжать работать
    except Exception as e:
        logger.error(f"activation_worker: Unexpected error in process_pending_activations: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("activation_worker: Full traceback in process_pending_activations", exc_info=True)
        # Не пробрасываем исключение - воркер должен продолжать работать


async def activation_worker_task(bot: Bot):
    """
    Фоновая задача для периодической обработки отложенных активаций
    
    Args:
        bot: Экземпляр Telegram бота
    """
    logger.info(f"Activation worker task started (interval={ACTIVATION_INTERVAL_SECONDS}s, max_attempts={MAX_ACTIVATION_ATTEMPTS})")
    
    while True:
        try:
            await process_pending_activations(bot)
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"activation_worker: Database temporarily unavailable in task loop: {type(e).__name__}: {str(e)[:100]}")
            # Продолжаем работу даже при ошибке
        except Exception as e:
            logger.error(f"activation_worker: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("activation_worker: Full traceback for task loop", exc_info=True)
            # Продолжаем работу даже при ошибке
        
        await asyncio.sleep(ACTIVATION_INTERVAL_SECONDS)
