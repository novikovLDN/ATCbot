"""Модуль для автопродления подписок с баланса"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database
import localization
import config

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


async def process_auto_renewals(bot: Bot):
    """
    Обработать автопродление подписок, которые истекают в течение RENEWAL_WINDOW
    
    ТРЕБОВАНИЯ:
    - Подписки со status='active' и auto_renew=TRUE
    - subscription_end <= now + RENEWAL_WINDOW (по умолчанию 6 часов)
    - Проверяем баланс >= цена подписки
    - Если баланса хватает: продлеваем через grant_access() (без создания нового UUID)
    - Если баланса не хватает: ничего не делаем (auto-expiry обработает)
    
    Защита от повторного списания:
    - Используется last_auto_renewal_at для отслеживания последнего автопродления
    - Одна подписка обрабатывается только один раз за цикл
    - Идемпотентность: при рестарте не будет двойного списания
    - Атомарные транзакции для баланса и подписки
    """
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        # Находим подписки, которые истекают в течение RENEWAL_WINDOW и имеют auto_renew = true
        # Исключаем подписки, которые уже были обработаны в этом цикле (защита от повторного списания)
        now = datetime.now()
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
               AND (s.last_auto_renewal_at IS NULL OR s.last_auto_renewal_at < s.expires_at - INTERVAL '12 hours')""",
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
                    # Дополнительная проверка: убеждаемся, что подписка еще не была обработана
                    # (защита от race condition при параллельных вызовах)
                    current_sub = await conn.fetchrow(
                        """SELECT auto_renew, expires_at, last_auto_renewal_at 
                           FROM subscriptions 
                           WHERE telegram_id = $1""",
                        telegram_id
                    )
                    
                    if not current_sub or not current_sub["auto_renew"]:
                        logger.debug(f"Subscription {telegram_id} no longer has auto_renew enabled, skipping")
                        continue
                    
                    # Проверяем, не была ли подписка уже обработана
                    last_renewal = current_sub.get("last_auto_renewal_at")
                    if last_renewal:
                        if isinstance(last_renewal, str):
                            last_renewal = datetime.fromisoformat(last_renewal)
                        # Если автопродление было менее 12 часов назад - пропускаем (защита от повторного списания)
                        if (now - last_renewal).total_seconds() < 43200:  # 12 часов
                            logger.debug(f"Subscription {telegram_id} was already processed recently, skipping")
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
                            continue
                        
                        # Отмечаем, что автопродление было выполнено (защита от повторного списания)
                        await conn.execute(
                            "UPDATE subscriptions SET last_auto_renewal_at = $1 WHERE telegram_id = $2",
                            now, telegram_id
                        )
                        
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
                        import database
                        notification_already_sent = await database.is_payment_notification_sent(payment_id, conn=conn)
                        
                        if notification_already_sent:
                            logger.info(
                                f"NOTIFICATION_IDEMPOTENT_SKIP [type=auto_renewal, payment_id={payment_id}, user={telegram_id}]"
                            )
                            continue
                        
                        # Отправляем уведомление пользователю
                        expires_str = expires_at.strftime("%d.%m.%Y")
                        duration_days = duration.days
                        try:
                            text = localization.get_text(
                                language,
                                "auto_renewal_success",
                                days=duration_days,
                                expires_date=expires_str,
                                amount=amount_rubles
                            )
                        except (KeyError, TypeError):
                            # Fallback на старый формат, если локализация не обновлена
                            text = f"✅ Подписка автоматически продлена на {duration_days} дней.\n\nДействует до: {expires_str}\nС баланса списано: {amount_rubles:.2f} ₽"
                        
                        # Создаем inline клавиатуру для UX
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text="👤 Мой профиль",
                                callback_data="menu_profile"
                            )],
                            [InlineKeyboardButton(
                                text="⚙️ Управление автопродлением",
                                callback_data="menu_auto_renewal"
                            )]
                        ])
                        
                        await bot.send_message(telegram_id, text, reply_markup=keyboard)
                        
                        # ИДЕМПОТЕНТНОСТЬ: Помечаем уведомление как отправленное (после успешной отправки)
                        try:
                            sent = await database.mark_payment_notification_sent(payment_id, conn=conn)
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
    except Exception as e:
        logger.exception(f"Error in initial auto-renewal check: {e}")
    
    while True:
        try:
            # Ждем до следующей проверки (5-15 минут, по умолчанию 10 минут)
            await asyncio.sleep(AUTO_RENEWAL_INTERVAL_SECONDS)
            
            await process_auto_renewals(bot)
            
        except asyncio.CancelledError:
            logger.info("Auto-renewal task cancelled")
            break
        except Exception as e:
            logger.exception(f"Error in auto-renewal task: {e}")
            # При ошибке ждем половину интервала перед повтором (не блокируем надолго)
            await asyncio.sleep(AUTO_RENEWAL_INTERVAL_SECONDS // 2)

