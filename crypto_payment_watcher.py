"""Фоновая задача для автоматической проверки статуса CryptoBot платежей"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from typing import Dict, Optional
import database
import localization
from payments import cryptobot

logger = logging.getLogger(__name__)

# Интервал проверки: 30 секунд
CHECK_INTERVAL_SECONDS = 30

# Файл для хранения purchase_id → invoice_id mapping (не DB, просто файл)
INVOICE_MAPPING_FILE = Path("data/crypto_invoice_mapping.json")


def _load_invoice_mapping() -> Dict[str, int]:
    """Загрузить mapping purchase_id → invoice_id из файла"""
    if not INVOICE_MAPPING_FILE.exists():
        return {}
    
    try:
        with open(INVOICE_MAPPING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading invoice mapping: {e}")
        return {}


def _save_invoice_mapping(mapping: Dict[str, int]):
    """Сохранить mapping purchase_id → invoice_id в файл"""
    try:
        INVOICE_MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(INVOICE_MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving invoice mapping: {e}")


def add_invoice_mapping(purchase_id: str, invoice_id: int):
    """Добавить mapping purchase_id → invoice_id (публичная функция для handlers)"""
    mapping = _load_invoice_mapping()
    mapping[purchase_id] = invoice_id
    _save_invoice_mapping(mapping)


def _remove_invoice_mapping(purchase_id: str):
    """Удалить mapping purchase_id → invoice_id"""
    mapping = _load_invoice_mapping()
    if purchase_id in mapping:
        del mapping[purchase_id]
        _save_invoice_mapping(mapping)


async def check_crypto_payments(bot: Bot):
    """
    Проверка статуса CryptoBot платежей для всех pending purchases
    
    Логика:
    1. Получаем все pending purchases (status='pending')
    2. Для каждого с invoice_id (из mapping файла) проверяем статус через CryptoBot API
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
        invoice_mapping = _load_invoice_mapping()
        if not invoice_mapping:
            return
        
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            # Получаем pending purchases, для которых есть invoice_id
            purchase_ids = list(invoice_mapping.keys())
            if not purchase_ids:
                return
            
            # Проверяем только те, которые еще pending
            placeholders = ",".join([f"${i+1}" for i in range(len(purchase_ids))])
            pending_purchases = await conn.fetch(
                f"""SELECT * FROM pending_purchases 
                   WHERE purchase_id IN ({placeholders})
                   AND status = 'pending'""",
                *purchase_ids
            )
            
            if not pending_purchases:
                return
            
            logger.info(f"Crypto payment watcher: checking {len(pending_purchases)} pending purchases")
            
            for row in pending_purchases:
                purchase = dict(row)
                purchase_id = purchase["purchase_id"]
                telegram_id = purchase["telegram_id"]
                invoice_id = invoice_mapping.get(purchase_id)
                
                if not invoice_id:
                    continue
                
                try:
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
                        invoice_id=str(invoice_id)
                    )
                    
                    if not result or not result.get("success"):
                        logger.error(f"Crypto payment finalization failed: purchase_id={purchase_id}, invoice_id={invoice_id}")
                        continue
                    
                    # Удаляем mapping после успешной финализации
                    _remove_invoice_mapping(purchase_id)
                    
                    # Отправляем подтверждение пользователю
                    payment_id = result["payment_id"]
                    expires_at = result["expires_at"]
                    vpn_key = result["vpn_key"]
                    
                    user = await database.get_user(telegram_id)
                    language = user.get("language", "ru") if user else "ru"
                    
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
                    logger.debug(f"Crypto payment already processed: purchase_id={purchase_id}, invoice_id={invoice_id}")
                    _remove_invoice_mapping(purchase_id)
                except Exception as e:
                    # Ошибка для одной покупки не должна ломать весь процесс
                    logger.error(f"Error checking crypto payment for purchase {purchase_id}: {e}", exc_info=True)
                    continue
                    
    except Exception as e:
        logger.exception(f"Error in check_crypto_payments: {e}")


async def crypto_payment_watcher_task(bot: Bot):
    """
    Фоновая задача для автоматической проверки CryptoBot платежей
    
    Запускается каждые CHECK_INTERVAL_SECONDS (30 секунд)
    """
    logger.info(f"Crypto payment watcher task started: interval={CHECK_INTERVAL_SECONDS}s")
    
    # Первая проверка сразу при запуске
    try:
        await check_crypto_payments(bot)
    except Exception as e:
        logger.exception(f"Error in initial crypto payment check: {e}")
    
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            await check_crypto_payments(bot)
        except asyncio.CancelledError:
            logger.info("Crypto payment watcher task cancelled")
            break
        except Exception as e:
            logger.exception(f"Error in crypto payment watcher task: {e}")
            # При ошибке ждем половину интервала перед повтором
            await asyncio.sleep(CHECK_INTERVAL_SECONDS // 2)
