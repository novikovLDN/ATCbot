import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
import config
import database
import handlers
import reminders
import healthcheck

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    # Конфигурация уже проверена в config.py
    # Если переменные окружения не заданы, программа завершится с ошибкой
    
    # Инициализация бота и диспетчера
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Регистрация handlers
    dp.include_router(handlers.router)
    
    # Инициализация базы данных
    await database.init_db()
    logger.info("База данных инициализирована")
    
    # Запуск фоновой задачи для напоминаний
    reminder_task = asyncio.create_task(reminders.reminders_task(bot))
    logger.info("Reminders task started")
    
    # Запуск фоновой задачи для health-check
    healthcheck_task = asyncio.create_task(healthcheck.health_check_task(bot))
    logger.info("Health check task started")
    
    # Запуск бота
    logger.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        healthcheck_task.cancel()
        try:
            await reminder_task
        except asyncio.CancelledError:
            pass
        try:
            await healthcheck_task
        except asyncio.CancelledError:
            pass
        # Закрываем пул соединений к БД
        await database.close_pool()
        logger.info("Database connection pool closed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

