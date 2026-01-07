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
    """Обработать автопродление подписок, которые истекают в течение 24 часов"""
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        # Находим подписки, которые истекают в течение 24 часов и имеют auto_renew = true
        now = datetime.now()
        expires_threshold = now + timedelta(hours=24)
        
        subscriptions = await conn.fetch(
            """SELECT s.*, u.language 
               FROM subscriptions s
               JOIN users u ON s.telegram_id = u.telegram_id
               WHERE s.expires_at <= $1 
               AND s.expires_at > $2
               AND s.auto_renew = TRUE""",
            expires_threshold, now
        )
        
        logger.info(f"Found {len(subscriptions)} subscriptions for auto-renewal check")
        
        for sub_row in subscriptions:
            subscription = dict(sub_row)
            telegram_id = subscription["telegram_id"]
            language = subscription.get("language", "ru")
            
            try:
                # Получаем последний утвержденный платеж для определения тарифа
                last_payment = await database.get_last_approved_payment(telegram_id)
                
                if not last_payment:
                    # Если нет платежа, используем дефолтный тариф "1" (1 месяц)
                    tariff_key = "1"
                else:
                    tariff_key = last_payment.get("tariff", "1")
                
                tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
                base_price = tariff_data["price"]
                
                # Применяем скидки (VIP, персональная)
                is_vip = await database.is_vip_user(telegram_id)
                if is_vip:
                    amount = int(base_price * 0.70)
                else:
                    personal_discount = await database.get_user_discount(telegram_id)
                    if personal_discount:
                        discount_percent = personal_discount["discount_percent"]
                        amount = int(base_price * (1 - discount_percent / 100))
                    else:
                        amount = base_price
                
                amount_rubles = float(amount)
                balance_rubles = await database.get_user_balance(telegram_id)
                
                if balance_rubles >= amount_rubles:
                    # Баланса хватает - продлеваем подписку
                    months = tariff_data["months"]
                    duration = timedelta(days=months * 30)
                    
                    # Списываем баланс
                    success = await database.decrease_balance(
                        telegram_id=telegram_id,
                        amount=amount_rubles,
                        source="subscription_payment",
                        description=f"Автопродление подписки {tariff_key} месяца(ев)"
                    )
                    
                    if not success:
                        logger.error(f"Failed to decrease balance for auto-renewal: user={telegram_id}")
                        continue
                    
                    # Продлеваем подписку
                    expires_at, vpn_key, is_renewal = await database.grant_access(
                        telegram_id=telegram_id,
                        duration=duration,
                        source="payment",
                        admin_telegram_id=None,
                        admin_grant_days=None
                    )
                    
                    if expires_at is None:
                        logger.error(f"Failed to grant access for auto-renewal: user={telegram_id}")
                        # Возвращаем деньги
                        await database.increase_balance(
                            telegram_id=telegram_id,
                            amount=amount_rubles,
                            source="refund",
                            description=f"Возврат средств за неудачное автопродление"
                        )
                        continue
                    
                    # Создаем запись о платеже для аналитики
                    await conn.execute(
                        "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'approved')",
                        telegram_id, tariff_key, amount
                    )
                    
                    # Отправляем уведомление
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
                    
                    logger.info(f"Auto-renewal successful: user={telegram_id}, tariff={tariff_key}, amount={amount_rubles} RUB")
                    
                else:
                    # Баланса не хватает - отключаем auto_renew и отправляем уведомление
                    shortage = amount_rubles - balance_rubles
                    
                    # Отключаем автопродление
                    await conn.execute(
                        "UPDATE subscriptions SET auto_renew = FALSE WHERE telegram_id = $1",
                        telegram_id
                    )
                    
                    # Проверяем, отправляли ли уже уведомление (через last_notification_sent_at)
                    last_notification = subscription.get("last_notification_sent_at")
                    should_notify = True
                    if last_notification:
                        if isinstance(last_notification, str):
                            last_notification = datetime.fromisoformat(last_notification)
                        if (now - last_notification).total_seconds() < 86400:  # 24 часа
                            should_notify = False
                    
                    if should_notify:
                        try:
                            text = localization.get_text(
                                language,
                                "auto_renewal_insufficient_balance",
                                default=f"⚠️ Недостаточно средств для автопродления подписки.\n\nТребуется: {amount_rubles:.2f} ₽\nНа балансе: {balance_rubles:.2f} ₽\nНе хватает: {shortage:.2f} ₽\n\nАвтопродление отключено. Пополните баланс и включите автопродление снова."
                            )
                        except KeyError:
                            text = f"⚠️ Недостаточно средств для автопродления подписки.\n\nТребуется: {amount_rubles:.2f} ₽\nНа балансе: {balance_rubles:.2f} ₽\nНе хватает: {shortage:.2f} ₽\n\nАвтопродление отключено. Пополните баланс и включите автопродление снова."
                        
                        await bot.send_message(telegram_id, text)
                        
                        # Обновляем время последнего уведомления
                        await conn.execute(
                            "UPDATE subscriptions SET last_notification_sent_at = $1 WHERE telegram_id = $2",
                            now, telegram_id
                        )
                        
                        logger.info(f"Auto-renewal disabled and notification sent: user={telegram_id}, shortage={shortage:.2f} RUB")
                    
            except Exception as e:
                logger.exception(f"Error processing auto-renewal for user {telegram_id}: {e}")


async def auto_renewal_task(bot: Bot):
    """Фоновая задача для автопродления подписок (запускается 1 раз в сутки)"""
    logger.info("Auto-renewal task started")
    
    while True:
        try:
            await process_auto_renewals(bot)
            # Ждем 24 часа до следующей проверки
            await asyncio.sleep(86400)  # 24 часа в секундах
        except asyncio.CancelledError:
            logger.info("Auto-renewal task cancelled")
            break
        except Exception as e:
            logger.exception(f"Error in auto-renewal task: {e}")
            # При ошибке ждем 1 час перед повтором
            await asyncio.sleep(3600)

