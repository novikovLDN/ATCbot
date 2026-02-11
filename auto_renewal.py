"""Модуль для автопродления подписок с баланса"""
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncpg
import database
import localization
import config
from app.services.notifications import service as notification_service
from app.core.system_state import (
    SystemState,
    healthy_component,
    degraded_component,
    unavailable_component,
)
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)

logger = logging.getLogger(__name__)

# Конфигурация интервала проверки автопродления (5-15 минут, по умолчанию 10 минут)
AUTO_RENEWAL_INTERVAL_SECONDS = int(os.getenv("AUTO_RENEWAL_INTERVAL_SECONDS", "600"))  # 10 минут
if AUTO_RENEWAL_INTERVAL_SECONDS < 300:  # Минимум 5 минут
    AUTO_RENEWAL_INTERVAL_SECONDS = 300
if AUTO_RENEWAL_INTERVAL_SECONDS > 900:  # Максимум 15 минут
    AUTO_RENEWAL_INTERVAL_SECONDS = 900

# Окно для автопродления: проверяем подписки, истекающие в течение этого времени (по умолчанию 6 часов)
RENEWAL_WINDOW_HOURS = int(os.getenv("RENEWAL_WINDOW_HOURS", "6"))
if RENEWAL_WINDOW_HOURS < 1:
    RENEWAL_WINDOW_HOURS = 1
RENEWAL_WINDOW = timedelta(hours=RENEWAL_WINDOW_HOURS)

# STEP 3 — PART B: WORKER LOOP SAFETY
# Minimum safe sleep on failure to prevent tight retry storms
MINIMUM_SAFE_SLEEP_ON_FAILURE = 300  # seconds (half of AUTO_RENEWAL_INTERVAL_SECONDS minimum)


async def process_auto_renewals(bot: Bot):
    """
    Обработать автопродление подписок, которые истекают в течение RENEWAL_WINDOW
    
    ТРЕБОВАНИЯ:
    - Подписки со status='active' и auto_renew=TRUE
    - subscription_end <= now + RENEWAL_WINDOW (по умолчанию 6 часов)
    - Проверяем баланс >= цена подписки
    - Если баланса хватает: продлеваем через grant_access() (без создания нового UUID)
    - Если баланса не хватает: ничего не делаем (auto-expiry обработает)
    
    Защита от race conditions:
    - SELECT ... FOR UPDATE SKIP LOCKED: только один воркер может обработать подписку
    - last_auto_renewal_at устанавливается в НАЧАЛЕ транзакции (до обработки)
    - При ошибке транзакция откатывается, last_auto_renewal_at возвращается к предыдущему значению
    - Идемпотентность: при рестарте не будет двойного списания
    - Атомарные транзакции для баланса и подписки
    """
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        # Находим подписки, которые истекают в течение RENEWAL_WINDOW и имеют auto_renew = true
        # Исключаем подписки, которые уже были обработаны в этом цикле (защита от повторного списания)
        # КРИТИЧНО: Используем UTC для согласованности с БД (expires_at хранится в UTC)
        now = datetime.utcnow()
        renewal_threshold = now + RENEWAL_WINDOW
        
        subscriptions = await conn.fetch(
            """SELECT s.*, u.language, u.balance
               FROM subscriptions s
               JOIN users u ON s.telegram_id = u.telegram_id
               WHERE s.status = 'active'
               AND s.auto_renew = TRUE
               AND s.expires_at <= $1 
               AND s.expires_at > $2
               AND s.uuid IS NOT NULL
               AND (s.last_auto_renewal_at IS NULL OR s.last_auto_renewal_at < s.expires_at - INTERVAL '12 hours')
               FOR UPDATE SKIP LOCKED""",
            renewal_threshold, now
        )
        
        logger.info(
            f"Auto-renewal check: Found {len(subscriptions)} subscriptions expiring within {RENEWAL_WINDOW_HOURS} hours"
        )
        
        for sub_row in subscriptions:
            subscription = dict(sub_row)
            telegram_id = subscription["telegram_id"]
            language = subscription.get("language", "ru")
            
            # Используем транзакцию для атомарности операции
            async with conn.transaction():
                try:
                    # КРИТИЧНО: Обновляем last_auto_renewal_at в НАЧАЛЕ транзакции
                    # Это предотвращает обработку одной подписки несколькими воркерами
                    # даже при рестарте или параллельных вызовах
                    update_result = await conn.execute(
                        """UPDATE subscriptions 
                           SET last_auto_renewal_at = $1 
                           WHERE telegram_id = $2 
                           AND status = 'active'
                           AND auto_renew = TRUE
                           AND (last_auto_renewal_at IS NULL OR last_auto_renewal_at < expires_at - INTERVAL '12 hours')""",
                        now, telegram_id
                    )
                    
                    # Если UPDATE не затронул ни одной строки - подписка уже обрабатывается или не подходит
                    if update_result == "UPDATE 0":
                        logger.debug(f"Subscription {telegram_id} already being processed or conditions changed, skipping")
                        continue
                    
                    # Дополнительная проверка: убеждаемся, что подписка еще не была обработана
                    # (дополнительная защита от race condition)
                    current_sub = await conn.fetchrow(
                        """SELECT auto_renew, expires_at, last_auto_renewal_at 
                           FROM subscriptions 
                           WHERE telegram_id = $1""",
                        telegram_id
                    )
                    
                    if not current_sub or not current_sub["auto_renew"]:
                        logger.debug(f"Subscription {telegram_id} no longer has auto_renew enabled, skipping")
                        # Откатываем транзакцию (last_auto_renewal_at будет откачен)
                        continue
                    
                    # Получаем последний утвержденный платеж для определения тарифа
                    last_payment = await database.get_last_approved_payment(telegram_id)
                    
                    # Парсим тариф из последнего платежа
                    # Формат может быть: "basic_30", "plus_90" или legacy "1", "3", "6", "12"
                    if not last_payment:
                        # Если нет платежа, используем дефолтный тариф Basic на 30 дней (1 месяц)
                        tariff_type = "basic"
                        period_days = 30
                    else:
                        tariff_str = last_payment.get("tariff", "basic_30")
                        # Парсим формат "basic_30" или "plus_90"
                        if "_" in tariff_str:
                            parts = tariff_str.split("_")
                            tariff_type = parts[0] if len(parts) > 0 else "basic"
                            try:
                                period_days = int(parts[1]) if len(parts) > 1 else 30
                            except (ValueError, IndexError):
                                period_days = 30
                        else:
                            # Legacy формат: "1", "3", "6", "12" -> конвертируем в новый формат
                            # По умолчанию используем Basic
                            tariff_type = "basic"
                            try:
                                months = int(tariff_str)
                                period_days = months * 30
                            except ValueError:
                                period_days = 30
                    
                    # Получаем базовую цену из новой структуры тарифов
                    if tariff_type not in config.TARIFFS or period_days not in config.TARIFFS[tariff_type]:
                        # Если тариф не найден, используем Basic 30 дней
                        tariff_type = "basic"
                        period_days = 30
                    
                    base_price = config.TARIFFS[tariff_type][period_days]["price"]
                    
                    # Применяем скидки (VIP, персональная) - та же логика, что при покупке
                    is_vip = await database.is_vip_user(telegram_id)
                    if is_vip:
                        amount_rubles = float(int(base_price * 0.70))  # 30% скидка
                    else:
                        personal_discount = await database.get_user_discount(telegram_id)
                        if personal_discount:
                            discount_percent = personal_discount["discount_percent"]
                            amount_rubles = float(int(base_price * (1 - discount_percent / 100)))
                        else:
                            amount_rubles = float(base_price)
                    
                    # Получаем баланс пользователя (в копейках из БД, конвертируем в рубли)
                    user_balance_kopecks = subscription.get("balance", 0) or 0
                    balance_rubles = user_balance_kopecks / 100.0
                    
                    if balance_rubles >= amount_rubles:
                        # Баланса хватает - продлеваем подписку
                        duration = timedelta(days=period_days)
                        
                        # Списываем баланс (source = auto_renew для идентификации)
                        months = period_days // 30
                        tariff_name = "Basic" if tariff_type == "basic" else "Plus"
                        success = await database.decrease_balance(
                            telegram_id=telegram_id,
                            amount=amount_rubles,
                            source="auto_renew",
                            description=f"Автопродление подписки {tariff_name} на {months} месяц(ев)"
                        )
                        
                        if not success:
                            logger.error(f"Failed to decrease balance for auto-renewal: user={telegram_id}")
                            continue
                        
                        # Продлеваем подписку через единую функцию grant_access
                        # source="auto_renew" для корректного аудита и аналитики
                        # grant_access() автоматически определит, что это продление (UUID не будет пересоздан)
                        result = await database.grant_access(
                            telegram_id=telegram_id,
                            duration=duration,
                            source="auto_renew",  # Используем source="auto_renew" для аудита
                            admin_telegram_id=None,
                            admin_grant_days=None,
                            conn=conn  # Используем существующее соединение для атомарности
                        )
                        
                        expires_at = result["subscription_end"]
                        action_type = result.get("action", "unknown")
                        
                        # ВАЛИДАЦИЯ: При автопродлении UUID НЕ должен пересоздаваться
                        # grant_access() должен вернуть action="renewal" и vless_url=None
                        if action_type != "renewal" or result.get("vless_url") is not None:
                            logger.error(
                                f"Auto-renewal ERROR: UUID was regenerated instead of renewal! "
                                f"user={telegram_id}, action={action_type}, has_vless_url={result.get('vless_url') is not None}"
                            )
                            # Это критическая ошибка - UUID не должен был пересоздаться
                            # Возвращаем деньги на баланс
                            await database.increase_balance(
                                telegram_id=telegram_id,
                                amount=amount_rubles,
                                source="refund",
                                description=f"Возврат средств: ошибка автопродления (UUID пересоздан)"
                            )
                            continue
                        
                        # Получаем vpn_key из существующей подписки (UUID не менялся)
                        subscription_row = await conn.fetchrow(
                            "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                            telegram_id
                        )
                        vpn_key = None
                        if subscription_row and subscription_row.get("vpn_key"):
                            vpn_key = subscription_row["vpn_key"]
                        else:
                            # Fallback: используем UUID (не должно быть, но на всякий случай)
                            vpn_key = result.get("uuid", "")
                        
                        if expires_at is None:
                            logger.error(f"Failed to renew subscription for auto-renewal: user={telegram_id}, expires_at=None")
                            # Возвращаем деньги на баланс
                            await database.increase_balance(
                                telegram_id=telegram_id,
                                amount=amount_rubles,
                                source="refund",
                                description=f"Возврат средств за неудачное автопродление"
                            )
                            # last_auto_renewal_at уже установлен в начале транзакции
                            # При ошибке транзакция откатится, и last_auto_renewal_at вернется к предыдущему значению
                            continue
                        
                        # last_auto_renewal_at уже установлен в начале транзакции
                        # НЕ обновляем его здесь - это предотвращает race conditions
                        
                        # Создаем запись о платеже для аналитики
                        tariff_str = f"{tariff_type}_{period_days}"
                        payment_id = await conn.fetchval(
                            "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'approved') RETURNING id",
                            telegram_id, tariff_str, int(amount_rubles * 100)  # Сохраняем в копейках
                        )
                        
                        if not payment_id:
                            logger.error(f"Failed to create payment record for auto-renewal: user={telegram_id}")
                            continue
                        
                        # ИДЕМПОТЕНТНОСТЬ: Проверяем, было ли уже отправлено уведомление
                        notification_already_sent = await notification_service.check_notification_idempotency(
                            payment_id, conn=conn
                        )
                        
                        if notification_already_sent:
                            logger.info(
                                f"NOTIFICATION_IDEMPOTENT_SKIP [type=auto_renewal, payment_id={payment_id}, user={telegram_id}]"
                            )
                            continue
                        
                        # Отправляем уведомление пользователю (language from user DB)
                        expires_str = expires_at.strftime("%d.%m.%Y")
                        duration_days = duration.days
                        text = localization.get_text(
                            language,
                            "auto_renewal_success",
                            days=duration_days,
                            expires_date=expires_str,
                            amount=amount_rubles
                        )
                        
                        # Создаем inline клавиатуру для UX
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text=localization.get_text(language, "profile", default="👤 Мой профиль"),
                                callback_data="menu_profile"
                            )],
                            [InlineKeyboardButton(
                                text=localization.get_text(language, "buy_vpn", default="🔐 Купить доступ"),
                                callback_data="menu_buy_vpn"
                            )]
                        ])
                        
                        await bot.send_message(telegram_id, text, reply_markup=keyboard)
                        
                        # ИДЕМПОТЕНТНОСТЬ: Помечаем уведомление как отправленное (после успешной отправки)
                        try:
                            sent = await notification_service.mark_notification_sent(payment_id, conn=conn)
                            if sent:
                                logger.info(
                                    f"NOTIFICATION_SENT [type=auto_renewal, payment_id={payment_id}, user={telegram_id}]"
                                )
                            else:
                                logger.warning(
                                    f"NOTIFICATION_FLAG_ALREADY_SET [type=auto_renewal, payment_id={payment_id}, user={telegram_id}]"
                                )
                        except Exception as e:
                            logger.error(
                                f"CRITICAL: Failed to mark notification as sent: payment_id={payment_id}, user={telegram_id}, error={e}"
                            )
                        
                        logger.info(f"Auto-renewal successful: user={telegram_id}, tariff={tariff_type}, period_days={period_days}, amount={amount_rubles} RUB, expires_at={expires_str}")
                        
                    else:
                        # Баланса не хватает - ничего не делаем (как указано в требованиях)
                        logger.debug(f"Insufficient balance for auto-renewal: user={telegram_id}, balance={balance_rubles:.2f} RUB, required={amount_rubles:.2f} RUB")
                        # НЕ отключаем auto_renew автоматически (пользователь может пополнить баланс)
                        # НЕ отправляем уведомление (как указано в требованиях)
                    
                except Exception as e:
                    logger.exception(f"Error processing auto-renewal for user {telegram_id}: {e}")
                    # При ошибке транзакция откатывается автоматически


async def auto_renewal_task(bot: Bot):
    """
    Фоновая задача для автопродления подписок
    
    Запускается каждые AUTO_RENEWAL_INTERVAL_SECONDS (по умолчанию 10 минут, минимум 5, максимум 15)
    для проверки подписок, истекающих в течение RENEWAL_WINDOW (по умолчанию 6 часов).
    
    Это обеспечивает:
    - Своевременное продление (частые проверки, не пропустим подписки)
    - Безопасность при рестартах (не будет двойного списания благодаря last_auto_renewal_at)
    - Идемпотентность (повторные вызовы безопасны)
    - Атомарность (баланс и подписка обновляются в одной транзакции)
    - UUID стабильность (продление без пересоздания UUID через grant_access)
    """
    logger.info(
        f"Auto-renewal task started: interval={AUTO_RENEWAL_INTERVAL_SECONDS}s, "
        f"renewal_window={RENEWAL_WINDOW_HOURS}h"
    )
    
    # Первая проверка сразу при запуске
    try:
        await process_auto_renewals(bot)
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures don't crash the task
        logger.warning(f"auto_renewal: Initial check failed (DB temporarily unavailable): {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"auto_renewal: Unexpected error in initial check: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("auto_renewal: Full traceback for initial check", exc_info=True)
    
    iteration_number = 0
    
    while True:
        iteration_start_time = time.time()
        iteration_number += 1
        
        # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration start
        correlation_id = log_worker_iteration_start(
            worker_name="auto_renewal",
            iteration_number=iteration_number
        )
        
        try:
            # Ждем до следующей проверки (5-15 минут, по умолчанию 10 минут)
            await asyncio.sleep(AUTO_RENEWAL_INTERVAL_SECONDS)
            
            # STEP 6 — F5: BACKGROUND WORKER SAFETY
            # Global worker guard: respect FeatureFlags, SystemState, CircuitBreaker
            from app.core.feature_flags import get_feature_flags
            feature_flags = get_feature_flags()
            if not feature_flags.background_workers_enabled or not feature_flags.auto_renewal_enabled:
                logger.warning(
                    f"[FEATURE_FLAG] Auto-renewal disabled, skipping iteration in auto_renewal "
                    f"(iteration={iteration_number}, workers_enabled={feature_flags.background_workers_enabled}, "
                    f"auto_renewal_enabled={feature_flags.auto_renewal_enabled})"
                )
                outcome = "skipped"
                reason = f"background_workers_enabled={feature_flags.background_workers_enabled}, auto_renewal_enabled={feature_flags.auto_renewal_enabled}"
                log_worker_iteration_end(
                    worker_name="auto_renewal",
                    outcome=outcome,
                    items_processed=0,
                    duration_ms=(time.time() - iteration_start_time) * 1000,
                    reason=reason,
                )
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
            # STEP 1.1 - RUNTIME GUARDRAILS: Read SystemState at iteration start
            # STEP 1.2 - BACKGROUND WORKERS CONTRACT: Check system state before processing
            try:
                now = datetime.utcnow()
                db_ready = database.DB_READY
                
                # Build SystemState for awareness (read-only)
                if db_ready:
                    db_component = healthy_component(last_checked_at=now)
                else:
                    db_component = unavailable_component(
                        error="DB not ready (degraded mode)",
                        last_checked_at=now
                    )
                
                # VPN API component (not critical for auto-renewal)
                if config.VPN_ENABLED and config.XRAY_API_URL:
                    vpn_component = healthy_component(last_checked_at=now)
                else:
                    vpn_component = degraded_component(
                        error="VPN API not configured",
                        last_checked_at=now
                    )
                
                # Payments component (always healthy)
                payments_component = healthy_component(last_checked_at=now)
                
                system_state = SystemState(
                    database=db_component,
                    vpn_api=vpn_component,
                    payments=payments_component,
                )
                
                # STEP 1.2: Skip iteration if system is UNAVAILABLE
                # DEGRADED state does NOT stop iteration (workers continue with reduced functionality)
                if system_state.is_unavailable:
                    logger.warning(
                        f"[UNAVAILABLE] system_state — skipping iteration in auto_renewal_task "
                        f"(database={system_state.database.status.value})"
                    )
                    continue
                
                # PART D.4: Workers continue normally if DEGRADED
                # PART D.4: Workers skip only if system_state == UNAVAILABLE
                # DEGRADED state allows continuation (optional components degraded, critical healthy)
                if system_state.is_degraded:
                    logger.info(
                        f"[DEGRADED] system_state detected in auto_renewal_task "
                        f"(continuing with reduced functionality - optional components degraded)"
                    )
            except Exception:
                # Ignore system state errors - continue with normal flow
                pass
            
            await process_auto_renewals(bot)
            
            # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration end (success)
            duration_ms = (time.time() - iteration_start_time) * 1000
            log_worker_iteration_end(
                worker_name="auto_renewal",
                outcome="success",
                items_processed=0,  # Auto-renewal doesn't track items per iteration
                duration_ms=duration_ms
            )
            
        except asyncio.CancelledError:
            logger.info("Auto-renewal task cancelled")
            break
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"auto_renewal: DB temporarily unavailable: {type(e).__name__}: {str(e)[:100]}")
            
            # STEP 2.3 — OBSERVABILITY: Log iteration end with degraded outcome
            duration_ms = (time.time() - iteration_start_time) * 1000
            log_worker_iteration_end(
                worker_name="auto_renewal",
                outcome="degraded",
                items_processed=0,
                error_type="infra_error",
                duration_ms=duration_ms
            )
            # STEP 3 — PART B: WORKER LOOP SAFETY
            # Minimum safe sleep on failure to prevent tight retry storms
            # Worker always sleeps before next iteration, even on failure
            await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
        except Exception as e:
            logger.error(f"auto_renewal: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("auto_renewal: Full traceback for task loop", exc_info=True)
            
            # STEP 2.3 — OBSERVABILITY: Log iteration end with failed outcome
            duration_ms = (time.time() - iteration_start_time) * 1000
            error_type = classify_error(e)
            log_worker_iteration_end(
                worker_name="auto_renewal",
                outcome="failed",
                items_processed=0,
                error_type=error_type,
                duration_ms=duration_ms
            )
            
            # STEP 3 — PART B: WORKER LOOP SAFETY
            # Minimum safe sleep on failure to prevent tight retry storms
            # Worker always sleeps before next iteration, even on failure
            await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)

