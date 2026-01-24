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
import admin_notifications
from app.services.activation import service as activation_service
from app.services.activation.exceptions import (
    ActivationServiceError,
    ActivationNotAllowedError,
    ActivationMaxAttemptsReachedError,
    ActivationFailedError,
    VPNActivationError,
)

logger = logging.getLogger(__name__)

# Конфигурация интервала проверки активации (по умолчанию 5 минут)
ACTIVATION_INTERVAL_SECONDS = int(os.getenv("ACTIVATION_INTERVAL_SECONDS", "300"))  # 5 минут
if ACTIVATION_INTERVAL_SECONDS < 60:  # Минимум 1 минута
    ACTIVATION_INTERVAL_SECONDS = 60
if ACTIVATION_INTERVAL_SECONDS > 1800:  # Максимум 30 минут
    ACTIVATION_INTERVAL_SECONDS = 1800

# Максимальное количество попыток активации (используется для логирования)
MAX_ACTIVATION_ATTEMPTS = activation_service.get_max_activation_attempts()


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
            # Get pending subscriptions using activation service
            pending_subscriptions = await activation_service.get_pending_subscriptions(
                max_attempts=MAX_ACTIVATION_ATTEMPTS,
                limit=50,
                conn=conn
            )
            
            # Get subscriptions for admin notification using activation service
            pending_for_notification = await activation_service.get_pending_for_notification(
                threshold_minutes=activation_service.get_notification_threshold_minutes(),
                conn=conn
            )
            
            # Send admin notification if needed
            if pending_for_notification:
                total_pending_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
                ) or 0
                
                if total_pending_count > 0:
                    await admin_notifications.notify_admin_pending_activations(
                        bot, 
                        total_pending_count,
                        pending_for_notification
                    )
            
            if not pending_subscriptions:
                logger.debug("No pending activations found")
                return
            
            logger.info(f"Found {len(pending_subscriptions)} pending activations to process")
            
            for pending_sub in pending_subscriptions:
                telegram_id = pending_sub.telegram_id
                subscription_id = pending_sub.subscription_id
                current_attempts = pending_sub.activation_attempts
                expires_at = pending_sub.expires_at
                
                # Check if subscription expired using activation service
                if activation_service.is_subscription_expired(expires_at):
                    logger.warning(
                        f"ACTIVATION_SKIP_EXPIRED [subscription_id={subscription_id}, "
                        f"user={telegram_id}, expires_at={expires_at.isoformat() if expires_at else 'N/A'}]"
                    )
                    # Mark as failed using activation service
                    try:
                        await activation_service.mark_expired_subscription_failed(
                            subscription_id,
                            conn=conn
                        )
                    except Exception as e:
                        logger.error(f"Failed to mark expired subscription as failed: {e}")
                    continue
                
                # Attempt activation using activation service
                logger.info(
                    f"ACTIVATION_RETRY_ATTEMPT [subscription_id={subscription_id}, "
                    f"user={telegram_id}, attempt={current_attempts + 1}/{MAX_ACTIVATION_ATTEMPTS}]"
                )
                
                try:
                    result = await activation_service.attempt_activation(
                        subscription_id=subscription_id,
                        telegram_id=telegram_id,
                        current_attempts=current_attempts,
                        conn=conn
                    )
                    
                    uuid_preview = f"{result.uuid[:8]}..." if result.uuid and len(result.uuid) > 8 else (result.uuid or "N/A")
                    logger.info(
                        f"ACTIVATION_SUCCESS [subscription_id={subscription_id}, "
                        f"user={telegram_id}, uuid={uuid_preview}, attempt={result.attempts}]"
                    )
                    
                    # Send notification to user
                    try:
                        user = await database.get_user(telegram_id)
                        language = user.get("language", "ru") if user else "ru"
                        
                        expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
                        
                        # Use localized text for successful activation
                        text = localization.get_text(
                            language,
                            "payment_approved",
                            date=expires_str,
                            default=f"✅ Ваш VPN доступ активирован! Доступ до {expires_str}"
                        )
                        
                        # Use standard keyboard for VPN key
                        import handlers
                        keyboard = handlers.get_vpn_key_keyboard(language)
                        
                        await bot.send_message(
                            telegram_id,
                            text,
                            reply_markup=keyboard,
                            parse_mode="HTML"
                        )
                        
                        # Send VPN key
                        if result.vpn_key:
                            await bot.send_message(
                                telegram_id,
                                f"<code>{result.vpn_key}</code>",
                                parse_mode="HTML"
                            )
                        
                        logger.info(
                            f"ACTIVATION_NOTIFICATION_SENT [subscription_id={subscription_id}, user={telegram_id}]"
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send activation notification to user {telegram_id}: {e}"
                        )
                        # Not critical - subscription is already activated
                    
                except VPNActivationError as e:
                    # VPN API error - increment attempt counter
                    error_msg = str(e)
                    new_attempts = current_attempts + 1
                    
                    logger.warning(
                        f"ACTIVATION_FAILED [subscription_id={subscription_id}, "
                        f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                        f"error={error_msg}]"
                    )
                    
                    try:
                        await activation_service.mark_activation_failed(
                            subscription_id=subscription_id,
                            new_attempts=new_attempts,
                            error_msg=error_msg,
                            max_attempts=MAX_ACTIVATION_ATTEMPTS,
                            conn=conn
                        )
                        
                        # If max attempts reached, send admin notification
                        if new_attempts >= MAX_ACTIVATION_ATTEMPTS:
                            logger.error(
                                f"ACTIVATION_FAILED_FINAL [subscription_id={subscription_id}, "
                                f"user={telegram_id}, attempts={new_attempts}, error={error_msg}]"
                            )
                            
                            # Send admin notification
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
                
                except ActivationFailedError as e:
                    # Other activation error
                    error_msg = str(e)
                    new_attempts = current_attempts + 1
                    
                    logger.warning(
                        f"ACTIVATION_FAILED [subscription_id={subscription_id}, "
                        f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                        f"error={error_msg}]"
                    )
                    
                    try:
                        await activation_service.mark_activation_failed(
                            subscription_id=subscription_id,
                            new_attempts=new_attempts,
                            error_msg=error_msg,
                            max_attempts=MAX_ACTIVATION_ATTEMPTS,
                            conn=conn
                        )
                    except Exception as db_error:
                        logger.error(
                            f"Failed to update activation attempts in DB: {db_error}"
                        )
                
                # Small delay between processing subscriptions
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
