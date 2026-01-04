"""Модуль для health-check основных компонентов системы"""
import asyncio
import logging
from typing import Tuple, List
from aiogram import Bot
import database
import config

logger = logging.getLogger(__name__)

# Минимальное количество свободных VPN-ключей (если меньше - отправляется алерт)
MIN_FREE_VPN_KEYS = 5


async def check_database_connection() -> Tuple[bool, str]:
    """Проверить подключение к PostgreSQL
    
    Returns:
        Кортеж (is_ok, message) - статус проверки и сообщение
    """
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            # Выполняем простой запрос для проверки подключения
            result = await conn.fetchval("SELECT 1")
            if result == 1:
                return True, "PostgreSQL подключение: OK"
            else:
                return False, "PostgreSQL подключение: Ошибка (неожиданный результат)"
    except Exception as e:
        return False, f"PostgreSQL подключение: Ошибка ({str(e)})"


async def check_connection_pool() -> Tuple[bool, str]:
    """Проверить доступность пула соединений
    
    Returns:
        Кортеж (is_ok, message) - статус проверки и сообщение
    """
    try:
        pool = await database.get_pool()
        if pool is None:
            return False, "Пул соединений: Не создан"
        
        # Проверяем, что пул активен
        if pool.is_closing():
            return False, "Пул соединений: Закрывается"
        
        return True, "Пул соединений: OK"
    except Exception as e:
        return False, f"Пул соединений: Ошибка ({str(e)})"


async def check_vpn_keys() -> Tuple[bool, str]:
    """Проверить количество свободных VPN-ключей
    
    Returns:
        Кортеж (is_ok, message) - статус проверки и сообщение
    """
    try:
        stats = await database.get_vpn_keys_stats()
        free_keys = stats.get("free", 0)
        
        if free_keys < MIN_FREE_VPN_KEYS:
            return False, f"VPN-ключи: Критически мало ({free_keys} свободных, минимум: {MIN_FREE_VPN_KEYS})"
        else:
            return True, f"VPN-ключи: OK ({free_keys} свободных)"
    except Exception as e:
        return False, f"VPN-ключи: Ошибка проверки ({str(e)})"


async def perform_health_check() -> Tuple[bool, list]:
    """Выполнить health-check всех компонентов
    
    Returns:
        Кортеж (all_ok, messages) - общий статус и список сообщений
    """
    messages = []
    all_ok = True
    
    # Проверка PostgreSQL
    db_ok, db_msg = await check_database_connection()
    messages.append(db_msg)
    if not db_ok:
        all_ok = False
    
    # Проверка пула соединений
    pool_ok, pool_msg = await check_connection_pool()
    messages.append(pool_msg)
    if not pool_ok:
        all_ok = False
    
    # Проверка VPN-ключей
    keys_ok, keys_msg = await check_vpn_keys()
    messages.append(keys_msg)
    if not keys_ok:
        all_ok = False
    
    return all_ok, messages


async def send_health_alert(bot: Bot, messages: List[str]):
    """Отправить алерт администратору о проблемах с системой
    
    Args:
        bot: Экземпляр бота
        messages: Список сообщений о проблемах
    """
    try:
        alert_text = "🚨 Health Check Alert\n\nОбнаружены проблемы:\n\n"
        alert_text += "\n".join(f"• {msg}" for msg in messages)
        
        await bot.send_message(config.ADMIN_TELEGRAM_ID, alert_text)
        logger.warning(f"Health check alert sent to admin: {alert_text}")
        
        # Записываем событие в audit_log
        details = "; ".join(messages)
        await database._log_audit_event_atomic_standalone(
            "health_check_alert",
            config.ADMIN_TELEGRAM_ID,
            None,
            details
        )
    except Exception as e:
        logger.error(f"Error sending health check alert to admin: {e}", exc_info=True)


async def health_check_task(bot: Bot):
    """Фоновая задача для health-check (выполняется каждые 10 минут)"""
    while True:
        try:
            all_ok, messages = await perform_health_check()
            
            if not all_ok:
                # Отправляем алерт только если есть проблемы
                await send_health_alert(bot, messages)
                logger.warning(f"Health check failed: {messages}")
            else:
                logger.info("Health check passed: all components OK")
                
        except Exception as e:
            logger.exception(f"Error in health_check_task: {e}")
            # При критической ошибке тоже отправляем алерт
            try:
                error_msg = f"Критическая ошибка health-check: {str(e)}"
                await send_health_alert(bot, [error_msg])
            except:
                pass  # Не падаем, если не можем отправить алерт
        
        # Ждем 10 минут до следующей проверки
        await asyncio.sleep(10 * 60)  # 10 минут в секундах

