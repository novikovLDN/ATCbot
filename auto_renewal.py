"""Модуль для автопродления подписок с баланса"""
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot
import database
import localization
import config

logger = logging.getLogger(__name__)


async def process_auto_renewals(bot: Bot):
    """
    Обработать автопродление подписок, которые истекают в течение 24 часов
    
    Защита от повторного списания:
    - Используется last_auto_renewal_at для отслеживания последнего автопродления
    - Одна подписка обрабатывается только один раз за цикл
    - Идемпотентность: при рестарте не будет двойного списания
    """
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        # Находим подписки, которые истекают в течение 24 часов и имеют auto_renew = true
        # Исключаем подписки, которые уже были обработаны в этом цикле (защита от повторного списания)
        now = datetime.now()
        expires_threshold = now + timedelta(hours=24)
        
        subscriptions = await conn.fetch(
            """SELECT s.*, u.language, u.balance
               FROM subscriptions s
               JOIN users u ON s.telegram_id = u.telegram_id
               WHERE s.expires_at <= $1 
               AND s.expires_at > $2
               AND s.auto_renew = TRUE
               AND (s.last_auto_renewal_at IS NULL OR s.last_auto_renewal_at < s.expires_at - INTERVAL '1 day')""",
            expires_threshold, now
        )
        
        logger.info(f"Found {len(subscriptions)} subscriptions for auto-renewal check")
        
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
                    
                    if not last_payment:
                        # Если нет платежа, используем дефолтный тариф "1" (1 месяц)
                        tariff_key = "1"
                    else:
                        tariff_key = last_payment.get("tariff", "1")
                    
                    tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
                    base_price = tariff_data["price"]
                    
                    # Применяем скидки (VIP, персональная) - та же логика, что при покупке
                    is_vip = await database.is_vip_user(telegram_id)
                    if is_vip:
                        amount = int(base_price * 0.70)  # 30% скидка
                    else:
                        personal_discount = await database.get_user_discount(telegram_id)
                        if personal_discount:
                            discount_percent = personal_discount["discount_percent"]
                            amount = int(base_price * (1 - discount_percent / 100))
                        else:
                            amount = base_price
                    
                    amount_rubles = float(amount)
                    
                    # Получаем баланс пользователя (в копейках из БД, конвертируем в рубли)
                    user_balance_kopecks = subscription.get("balance", 0) or 0
                    balance_rubles = user_balance_kopecks / 100.0
                    
                    if balance_rubles >= amount_rubles:
                        # Баланса хватает - продлеваем подписку
                        months = tariff_data["months"]
                        duration = timedelta(days=months * 30)
                        
                        # Списываем баланс (source = auto_renew для идентификации)
                        success = await database.decrease_balance(
                            telegram_id=telegram_id,
                            amount=amount_rubles,
                            source="auto_renew",
                            description=f"Автопродление подписки на {months} месяц(ев)"
                        )
                        
                        if not success:
                            logger.error(f"Failed to decrease balance for auto-renewal: user={telegram_id}")
                            continue
                        
                        # Продлеваем подписку через единую функцию grant_access
                        result = await database.grant_access(
                            telegram_id=telegram_id,
                            duration=duration,
                            source="payment",
                            admin_telegram_id=None,
                            admin_grant_days=None,
                            conn=conn  # Используем существующее соединение для атомарности
                        )
                        
                        expires_at = result["subscription_end"]
                        vpn_key = result.get("vless_url") or result["uuid"]
                        is_renewal = result.get("vless_url") is None  # Если vless_url is None, значит это продление
                        
                        if expires_at is None or vpn_key is None:
                            logger.error(f"Failed to grant access for auto-renewal: user={telegram_id}")
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
                        await conn.execute(
                            "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'approved')",
                            telegram_id, tariff_key, amount
                        )
                        
                        # Отправляем уведомление пользователю (спокойный текст, без CTA)
                        expires_str = expires_at.strftime("%d.%m.%Y")
                        try:
                            text = localization.get_text(
                                language,
                                "auto_renewal_success",
                                default=f"✅ Подписка автоматически продлена до {expires_str}\n\nС баланса списано: {amount_rubles:.2f} ₽"
                            )
                        except KeyError:
                            text = f"✅ Подписка автоматически продлена до {expires_str}\n\nС баланса списано: {amount_rubles:.2f} ₽"
                        
                        await bot.send_message(telegram_id, text)
                        
                        logger.info(f"Auto-renewal successful: user={telegram_id}, tariff={tariff_key}, amount={amount_rubles} RUB, expires_at={expires_str}")
                        
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
    
    Запускается каждые 6 часов для проверки подписок, истекающих в течение 24 часов.
    Это обеспечивает:
    - Своевременное продление (не пропустим подписки)
    - Безопасность при рестартах (не будет двойного списания благодаря last_auto_renewal_at)
    - Идемпотентность (повторные вызовы безопасны)
    """
    logger.info("Auto-renewal task started")
    
    # Первая проверка сразу при запуске
    try:
        await process_auto_renewals(bot)
    except Exception as e:
        logger.exception(f"Error in initial auto-renewal check: {e}")
    
    while True:
        try:
            # Ждем 6 часов до следующей проверки (проверяем чаще, чем раз в сутки, для надежности)
            await asyncio.sleep(21600)  # 6 часов в секундах
            
            await process_auto_renewals(bot)
            
        except asyncio.CancelledError:
            logger.info("Auto-renewal task cancelled")
            break
        except Exception as e:
            logger.exception(f"Error in auto-renewal task: {e}")
            # При ошибке ждем 1 час перед повтором
            await asyncio.sleep(3600)

