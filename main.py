import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
import config
import database
import handlers
import reminders
import healthcheck
# import outline_cleanup  # DISABLED - мигрировали на Xray Core
import fast_expiry_cleanup
import auto_renewal

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
    
    # ====================================================================================
    # SAFE STARTUP GUARD: Инициализация базы данных с защитой от краша
    # ====================================================================================
    # Бот должен ВСЕГДА запускаться, даже если БД недоступна.
    # В случае ошибки бот работает в деградированном режиме.
    # ====================================================================================
    try:
        success = await database.init_db()
        if success:
            logger.info("✅ База данных инициализирована успешно")
            database.DB_READY = True
        else:
            logger.error("❌ DB INIT FAILED — RUNNING IN DEGRADED MODE")
            database.DB_READY = False
    except Exception as e:
        # КРИТИЧЕСКИ ВАЖНО: Не пробрасываем исключение, не останавливаем процесс
        logger.exception("❌ DB INIT FAILED — RUNNING IN DEGRADED MODE")
        logger.error(f"Database initialization error: {type(e).__name__}: {e}")
        database.DB_READY = False
        # Продолжаем запуск бота в деградированном режиме
    
    # Запуск фоновой задачи для напоминаний (только если БД готова)
    reminder_task = None
    if database.DB_READY:
        reminder_task = asyncio.create_task(reminders.reminders_task(bot))
        logger.info("Reminders task started")
    else:
        logger.warning("Reminders task skipped (DB not ready)")
    
    # Запуск фоновой задачи для health-check
    healthcheck_task = asyncio.create_task(healthcheck.health_check_task(bot))
    logger.info("Health check task started")
    
    # ====================================================================================
    # SAFE STARTUP GUARD: Фоновая задача повторной инициализации БД
    # ====================================================================================
    # Пытается восстановить соединение с БД каждые 30 секунд
    # ====================================================================================
    # Переменные для отслеживания восстановленных задач (для db_retry_task)
    recovered_tasks = {
        "reminder": None,
        "fast_cleanup": None,
        "auto_renewal": None
    }
    
    async def db_retry_task():
        """Фоновая задача для повторной попытки инициализации БД"""
        nonlocal reminder_task, fast_cleanup_task, auto_renewal_task, recovered_tasks
        retry_interval = 30  # секунд
        while True:
            try:
                await asyncio.sleep(retry_interval)
                if not database.DB_READY:
                    logger.info(f"🔄 Retrying database initialization...")
                    try:
                        success = await database.init_db()
                        if success:
                            database.DB_READY = True
                            logger.info("✅ DATABASE RECOVERY SUCCESSFUL — RESUMING FULL FUNCTIONALITY")
                            # Запускаем задачи, которые были пропущены
                            if reminder_task is None and recovered_tasks["reminder"] is None:
                                recovered_tasks["reminder"] = asyncio.create_task(reminders.reminders_task(bot))
                                logger.info("Reminders task started (recovered)")
                            if fast_cleanup_task is None and recovered_tasks["fast_cleanup"] is None:
                                recovered_tasks["fast_cleanup"] = asyncio.create_task(fast_expiry_cleanup.fast_expiry_cleanup_task())
                                logger.info("Fast expiry cleanup task started (recovered)")
                            if auto_renewal_task is None and recovered_tasks["auto_renewal"] is None:
                                recovered_tasks["auto_renewal"] = asyncio.create_task(auto_renewal.auto_renewal_task(bot))
                                logger.info("Auto-renewal task started (recovered)")
                        else:
                            logger.warning("Database initialization retry failed, will retry later")
                    except Exception as e:
                        logger.warning(f"Database initialization retry error: {type(e).__name__}: {e}")
                        logger.debug("Full retry error:", exc_info=True)
            except asyncio.CancelledError:
                logger.info("DB retry task cancelled")
                break
            except Exception as e:
                logger.exception(f"Unexpected error in DB retry task: {e}")
                # Продолжаем работу даже при ошибках в retry задаче
                await asyncio.sleep(retry_interval)
    
    db_retry_task_instance = asyncio.create_task(db_retry_task())
    logger.info("DB retry task started (will retry every 30 seconds if DB not ready)")
    
    # Outline cleanup task DISABLED - мигрировали на Xray Core (VLESS)
    # Старая задача outline_cleanup больше не используется
    # cleanup_task = asyncio.create_task(outline_cleanup.outline_cleanup_task())
    # logger.info("Outline cleanup task started")
    cleanup_task = None
    logger.info("Outline cleanup task disabled (using Xray Core now)")
    
    # Запуск фоновой задачи для быстрой очистки истёкших подписок (только если БД готова)
    fast_cleanup_task = None
    if database.DB_READY:
        fast_cleanup_task = asyncio.create_task(fast_expiry_cleanup.fast_expiry_cleanup_task())
        logger.info("Fast expiry cleanup task started")
    else:
        logger.warning("Fast expiry cleanup task skipped (DB not ready)")
    
    # Запуск фоновой задачи для автопродления подписок (только если БД готова)
    auto_renewal_task = None
    if database.DB_READY:
        auto_renewal_task = asyncio.create_task(auto_renewal.auto_renewal_task(bot))
        logger.info("Auto-renewal task started")
    else:
        logger.warning("Auto-renewal task skipped (DB not ready)")
    
    # Запуск бота
    if database.DB_READY:
        logger.info("✅ Бот запущен в полнофункциональном режиме")
    else:
        logger.warning("⚠️ Бот запущен в ДЕГРАДИРОВАННОМ режиме (БД недоступна)")
    try:
        await dp.start_polling(bot)
    finally:
        # Отменяем все фоновые задачи
        db_retry_task_instance.cancel()
        if reminder_task:
            reminder_task.cancel()
        if recovered_tasks.get("reminder"):
            recovered_tasks["reminder"].cancel()
        healthcheck_task.cancel()
        if auto_renewal_task:
            auto_renewal_task.cancel()
        if recovered_tasks.get("auto_renewal"):
            recovered_tasks["auto_renewal"].cancel()
        if cleanup_task:
            cleanup_task.cancel()
        if fast_cleanup_task:
            fast_cleanup_task.cancel()
        if recovered_tasks.get("fast_cleanup"):
            recovered_tasks["fast_cleanup"].cancel()
        
        # Ожидаем завершения всех задач
        try:
            await db_retry_task_instance
        except asyncio.CancelledError:
            pass
        if reminder_task:
            try:
                await reminder_task
            except asyncio.CancelledError:
                pass
        if recovered_tasks.get("reminder"):
            try:
                await recovered_tasks["reminder"]
            except asyncio.CancelledError:
                pass
        try:
            await healthcheck_task
        except asyncio.CancelledError:
            pass
        if auto_renewal_task:
            try:
                await auto_renewal_task
            except asyncio.CancelledError:
                pass
        if recovered_tasks.get("auto_renewal"):
            try:
                await recovered_tasks["auto_renewal"]
            except asyncio.CancelledError:
                pass
        if cleanup_task:
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
        if fast_cleanup_task:
            try:
                await fast_cleanup_task
            except asyncio.CancelledError:
                pass
        if recovered_tasks.get("fast_cleanup"):
            try:
                await recovered_tasks["fast_cleanup"]
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

