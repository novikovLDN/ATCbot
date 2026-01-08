"""
HTTP Health Check Server

Exposes /health endpoint for monitoring and diagnostics.
Endpoint does NOT depend on database - always responds.
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any
from aiohttp import web
import database

logger = logging.getLogger(__name__)


async def health_handler(request: web.Request) -> web.Response:
    """
    Health check endpoint handler
    
    Returns:
        JSON response with status, db_ready, and timestamp
        
    Response format:
        {
            "status": "ok" | "degraded",
            "db_ready": true | false,
            "timestamp": "2024-01-01T12:00:00Z"
        }
    
    Status rules:
        - "ok" if DB_READY == True
        - "degraded" if DB_READY == False
    
    IMPORTANT: This endpoint MUST NOT depend on database.
    It only reads the global DB_READY flag.
    """
    try:
        # Читаем глобальный флаг (не обращаемся к БД)
        db_ready = database.DB_READY
        
        # Определяем статус
        status = "ok" if db_ready else "degraded"
        
        # Формируем ответ
        response_data: Dict[str, Any] = {
            "status": status,
            "db_ready": db_ready,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        # HTTP статус код: 200 для обоих случаев (ok и degraded)
        # Мониторинг может различать по полю "status"
        return web.json_response(response_data, status=200)
        
    except Exception as e:
        # Критическая ошибка - логируем, но всё равно отвечаем
        logger.exception(f"Error in health endpoint: {e}")
        # Возвращаем degraded статус при ошибке
        response_data = {
            "status": "degraded",
            "db_ready": False,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "error": "Health check error"
        }
        return web.json_response(response_data, status=200)


async def create_health_app() -> web.Application:
    """Создать aiohttp приложение с health endpoint"""
    app = web.Application()
    
    # Регистрируем health endpoint
    app.router.add_get("/health", health_handler)
    
    # Опционально: корневой endpoint для простой проверки
    async def root_handler(request: web.Request) -> web.Response:
        return web.json_response({"service": "atlas-secure-bot", "health": "/health"})
    
    app.router.add_get("/", root_handler)
    
    return app


async def start_health_server(host: str = "0.0.0.0", port: int = 8080) -> web.AppRunner:
    """
    Запустить HTTP сервер для health checks
    
    Args:
        host: Хост для прослушивания (по умолчанию 0.0.0.0 для Railway)
        port: Порт для прослушивания (по умолчанию 8080)
    
    Returns:
        AppRunner для управления сервером
    """
    app = await create_health_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    logger.info(f"Health check server started on http://{host}:{port}/health")
    
    return runner


async def health_server_task(host: str = "0.0.0.0", port: int = 8080):
    """
    Фоновая задача для запуска health check сервера
    
    Args:
        host: Хост для прослушивания
        port: Порт для прослушивания
    """
    runner = None
    try:
        runner = await start_health_server(host, port)
        
        # Ждём бесконечно (сервер работает в фоне)
        # Задача будет отменена при остановке бота
        while True:
            await asyncio.sleep(3600)  # Спим час, чтобы не блокировать
            
    except asyncio.CancelledError:
        logger.info("Health server task cancelled")
        # Останавливаем сервер
        if runner:
            try:
                await runner.cleanup()
                logger.info("Health server stopped")
            except Exception as e:
                logger.error(f"Error stopping health server: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error in health server task: {e}")
        # Останавливаем сервер при ошибке
        if runner:
            try:
                await runner.cleanup()
            except:
                pass
        raise

