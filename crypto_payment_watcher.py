"""Фоновая задача для автоматической проверки статуса CryptoBot платежей"""
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
import database
import localization
from payments import cryptobot

logger = logging.getLogger(__name__)

# Интервал проверки: 30 секунд
CHECK_INTERVAL_SECONDS = 30


async def check_crypto_payments(bot: Bot):
    """
    Проверка статуса CryptoBot платежей для всех pending purchases
    
    Логика:
    1. Получаем все pending purchases где provider_invoice_id IS NOT NULL
    2. Для каждого проверяем статус invoice через CryptoBot API
    3. Если invoice статус='paid' → финализируем покупку
    4. Отправляем пользователю подтверждение с VPN ключом
    
    КРИТИЧНО:
    - Idempotent: finalize_purchase защищен от повторной обработки
    - Не блокирует другие pending purchases при ошибке
    - Логирует только критичные ошибки
    """
    if not cryptobot.is_enabled():
        return
    
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            # Получаем pending purchases с provider_invoice_id (т.е. CryptoBot purchases)
            # Только не истёкшие покупки: status = 'pending' AND expires_at > (NOW() AT TIME ZONE 'UTC')
            pending_purchases = await conn.fetch(
                """SELECT * FROM pending_purchases 
                   WHERE status = 'pending' 
                   AND provider_invoice_id IS NOT NULL
                   AND expires_at > (NOW() AT TIME ZONE 'UTC')
                   ORDER BY created_at DESC
                   LIMIT 100"""
            )
            
            if not pending_purchases:
                return
            
            logger.info(f"Crypto payment watcher: checking {len(pending_purchases)} pending purchases")
            
            for row in pending_purchases:
                purchase = dict(row)
                purchase_id = purchase["purchase_id"]
                telegram_id = purchase["telegram_id"]
                invoice_id_str = purchase.get("provider_invoice_id")
                
                if not invoice_id_str:
                    continue
                
                try:
                    # Преобразуем invoice_id в int для CryptoBot API
                    invoice_id = int(invoice_id_str)
                    
                    # Проверяем статус invoice через CryptoBot API
                    invoice_status = await cryptobot.check_invoice_status(invoice_id)
                    status = invoice_status.get("status")
                    
                    if status != "paid":
                        # Оплата еще не выполнена
                        continue
                    
                    # Оплата успешна - финализируем покупку
                    payload = invoice_status.get("payload", "")
                    if not payload.startswith("purchase:"):
                        logger.error(f"Invalid payload format in CryptoBot invoice: invoice_id={invoice_id}, payload={payload}")
                        continue
                    
                    # Получаем сумму оплаты (USD string from API, convert back to RUB)
                    amount_usd_str = invoice_status.get("amount", "0")
                    try:
                        amount_usd = float(amount_usd_str) if amount_usd_str else 0.0
                        from payments.cryptobot import RUB_TO_USD_RATE
                        amount_rubles = amount_usd * RUB_TO_USD_RATE
                    except (ValueError, TypeError):
                        logger.error(f"Invalid amount in invoice status: {amount_usd_str}, invoice_id={invoice_id}")
                        continue
                    
                    # Финализируем покупку
                    result = await database.finalize_purchase(
                        purchase_id=purchase_id,
                        payment_provider="cryptobot",
                        amount_rubles=amount_rubles,
                        invoice_id=invoice_id_str
                    )
                    
                    if not result or not result.get("success"):
                        logger.error(f"Crypto payment finalization failed: purchase_id={purchase_id}, invoice_id={invoice_id}")
                        continue
                    
                    # Проверяем, является ли это пополнением баланса
                    is_balance_topup = result.get("is_balance_topup", False)
                    
                    user = await database.get_user(telegram_id)
                    language = user.get("language", "ru") if user else "ru"
                    
                    if is_balance_topup:
                        # Отправляем подтверждение пополнения баланса
                        amount = result.get("amount", amount_rubles)
                        text = localization.get_text(
                            language,
                            "balance_topup_success",
                            amount=amount,
                            default=f"✅ Баланс успешно пополнен на {amount:.2f} ₽"
                        )
                        
                        try:
                            await bot.send_message(telegram_id, text, parse_mode="HTML")
                            logger.info(
                                f"Crypto balance top-up auto-confirmed: user={telegram_id}, purchase_id={purchase_id}, "
                                f"invoice_id={invoice_id}, amount={amount} RUB"
                            )
                        except TelegramForbiddenError:
                            logger.info(f"User {telegram_id} blocked bot, skipping balance top-up confirmation message")
                        except Exception as e:
                            logger.error(f"Error sending balance top-up confirmation to user {telegram_id}: {e}")
                    else:
                        # Отправляем подтверждение покупки подписки
                        payment_id = result["payment_id"]
                        expires_at = result["expires_at"]
                        vpn_key = result["vpn_key"]
                        
                        expires_str = expires_at.strftime("%d.%m.%Y")
                        text = localization.get_text(language, "payment_approved", date=expires_str)
                        
                        # Импорт здесь для избежания circular import
                        import handlers
                        try:
                            await bot.send_message(telegram_id, text, reply_markup=handlers.get_vpn_key_keyboard(language), parse_mode="HTML")
                            await bot.send_message(telegram_id, f"<code>{vpn_key}</code>", parse_mode="HTML")
                            logger.info(
                                f"Crypto payment auto-confirmed: user={telegram_id}, purchase_id={purchase_id}, "
                                f"invoice_id={invoice_id}, payment_id={payment_id}"
                            )
                        except TelegramForbiddenError:
                            logger.info(f"User {telegram_id} blocked bot, skipping confirmation message")
                        except Exception as e:
                            logger.error(f"Error sending confirmation to user {telegram_id}: {e}")
                    
                except ValueError as e:
                    # Pending purchase уже обработан (idempotency)
                    logger.debug(f"Crypto payment already processed: purchase_id={purchase_id}, invoice_id={invoice_id_str}, error={e}")
                except Exception as e:
                    # Ошибка для одной покупки не должна ломать весь процесс
                    logger.error(f"Error checking crypto payment for purchase {purchase_id}: {e}", exc_info=True)
                    continue
                    
    except Exception as e:
        logger.exception(f"Error in check_crypto_payments: {e}")


async def cleanup_expired_purchases():
    """
    Очистка истёкших pending purchases
    
    Помечает как 'expired' все покупки где:
    - status = 'pending'
    - expires_at <= now_utc
    
    Безопасно: не удаляет покупки, только меняет статус
    """
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            # Получаем список истёкших покупок перед обновлением для логирования
            expired_purchases = await conn.fetch("""
                SELECT id, purchase_id, telegram_id, expires_at
                FROM pending_purchases 
                WHERE status = 'pending' 
                AND expires_at IS NOT NULL
                AND expires_at <= (NOW() AT TIME ZONE 'UTC')
            """)
            
            if not expired_purchases:
                return
            
            # Помечаем как expired
            result = await conn.execute("""
                UPDATE pending_purchases 
                SET status = 'expired'
                WHERE status = 'pending' 
                AND expires_at IS NOT NULL
                AND expires_at <= (NOW() AT TIME ZONE 'UTC')
            """)
            
            # Логируем каждую истёкшую покупку
            for purchase in expired_purchases:
                logger.info(
                    f"crypto_invoice_expired: purchase_id={purchase['purchase_id']}"
                )
                    
    except Exception as e:
        logger.error(f"Error in cleanup_expired_purchases: {e}", exc_info=True)


async def crypto_payment_watcher_task(bot: Bot):
    """
    Фоновая задача для автоматической проверки CryptoBot платежей
    
    Запускается каждые CHECK_INTERVAL_SECONDS (30 секунд)
    """
    logger.info(f"Crypto payment watcher task started: interval={CHECK_INTERVAL_SECONDS}s")
    
    # Первая проверка сразу при запуске
    try:
        await check_crypto_payments(bot)
        await cleanup_expired_purchases()
    except Exception as e:
        logger.exception(f"Error in initial crypto payment check: {e}")
    
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            await check_crypto_payments(bot)
            await cleanup_expired_purchases()
        except asyncio.CancelledError:
            logger.info("Crypto payment watcher task cancelled")
            break
        except Exception as e:
            logger.exception(f"Error in crypto payment watcher task: {e}")
            # При ошибке ждем половину интервала перед повтором
            await asyncio.sleep(CHECK_INTERVAL_SECONDS // 2)
