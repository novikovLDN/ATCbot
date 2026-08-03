"""
API module — HTTP endpoints for webhooks and health.
"""
import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.api import telegram_webhook
from app.api import payment_webhook
from app.api import deeplink_redirect
from app.api import subscription_proxy

logger = logging.getLogger(__name__)

# SECURITY: Disable OpenAPI/Swagger docs in production (information leak prevention)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class _BodyTooLarge(Exception):
    """Тело запроса перевалило за лимит прямо во время чтения.

    Бросается из обёртки receive и ловится в самом middleware — наружу
    из ASGI-стека не уходит.
    """


class RequestSizeLimitMiddleware:
    """SECURITY: рубит слишком большие тела запросов (защита от OOM).

    Path-aware: загрузка фото для рассылки честно принимает до 10 МБ
    (`app/api/dashboard/routes/broadcasts.py:upload_photo`), всем
    остальным — Telegram-вебхуку, вебхукам платёжек, JSON-ручкам
    дашборда — хватает 1 МБ.

    ПОЧЕМУ ЭТО НЕ BaseHTTPMiddleware С ПРОВЕРКОЙ Content-Length.
    Раньше middleware смотрел только заголовок Content-Length. Клиент,
    который шлёт тело с `Transfer-Encoding: chunked`, этот заголовок не
    отправляет вовсе — проверка тихо пропускала запрос любого размера.
    Дальше upload_photo делал `await file.read()` целиком в память, и
    один chunked-POST на сотни мегабайт раздувал процесс. Процесс тут
    один на всё: вместе с дашбордом ляжет и бот, и приём вебхуков
    Telegram.

    Поэтому считаем реально пришедшие байты в обёртке над `receive` и
    обрываем запрос, как только сумма превысила лимит. Больше лимита в
    память не попадёт независимо от заголовков. Content-Length, если он
    есть, проверяем заранее — это дешевле, чем тянуть тело.

    Написано как «голый» ASGI-класс, а не BaseHTTPMiddleware, потому что
    подменить канал receive у запроса можно только на этом уровне.
    """

    # Per-prefix exceptions: prefix → max bytes. First match wins.
    _PATH_OVERRIDES = (
        ("/dashboard/api/broadcasts/upload-photo", 10 * 1024 * 1024),
    )

    def __init__(self, app, max_size: int = 1 * 1024 * 1024):
        self.app = app
        self.max_size = max_size

    def _limit_for(self, path: str) -> int:
        for prefix, lim in self._PATH_OVERRIDES:
            if path.startswith(prefix):
                return lim
        return self.max_size

    @staticmethod
    def _content_length(scope) -> Optional[bytes]:
        for name, value in scope.get("headers") or ():
            if name == b"content-length":
                return value
        return None

    @staticmethod
    async def _reject(send, status: int, text: bytes) -> None:
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(text)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": text})

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope.get("path", ""))

        declared = self._content_length(scope)
        if declared is not None:
            try:
                if int(declared) > limit:
                    await self._reject(send, 413, b"Request body too large")
                    return
            except (TypeError, ValueError):
                await self._reject(send, 400, b"Invalid Content-Length")
                return

        received = 0
        # Ответ уже мог начаться — тогда 413 отправлять поздно, заголовки
        # ушли. Такое возможно только если обработчик начал отвечать, не
        # дочитав тело; тогда пробрасываем ошибку наверх.
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _BodyTooLarge()
            return message

        async def watched_send(message):
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, watched_send)
        except _BodyTooLarge:
            logger.warning(
                "REQUEST_BODY_TOO_LARGE path=%s limit=%d received>%d",
                scope.get("path", ""), limit, limit,
            )
            if started:
                raise
            await self._reject(send, 413, b"Request body too large")


app.add_middleware(RequestSizeLimitMiddleware, max_size=1 * 1024 * 1024)
app.include_router(telegram_webhook.router)
app.include_router(payment_webhook.router)
app.include_router(deeplink_redirect.router)

# Subscription-URL fallback (samopis → Remnawave premium translation) —
# mounted only when explicitly enabled to keep the production surface area small.
try:
    import config as _cfg
    if getattr(_cfg, "SUBSCRIPTION_PROXY_ENABLED", False):
        app.include_router(subscription_proxy.router)
        logger.info("SUBSCRIPTION_PROXY_ENABLED — mounted /sub/{uuid} + /api/sub/{token}")
except Exception:
    logger.exception("subscription_proxy mount failed")

# Admin web dashboard — mounted only when JWT_SECRET + DASHBOARD_BASE_URL are
# set (config.DASHBOARD_ENABLED). When disabled, the bot runs identically to
# the pre-dashboard build. When enabled:
#   /dashboard/api/*   — REST (auth, stats, users, ...)
#   /dashboard/ws      — WebSocket fan-out from app.events.bus
#   /dashboard/*       — static React SPA (mounted later, see DASHBOARD_DIST_DIR)
try:
    import config as _cfg
    if getattr(_cfg, "DASHBOARD_ENABLED", False):
        from app.api import dashboard as _dashboard
        app.include_router(_dashboard.router, prefix="/dashboard/api")
        app.include_router(_dashboard.ws_router, prefix="/dashboard")
        # Static SPA mount is conditional — only if dashboard/dist exists.
        # During Phase 1A (backend only) the directory may not be built yet.
        import os as _os
        _dist = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "dashboard", "dist")
        if _os.path.isdir(_dist):
            # Отдача собранного SPA одним маршрутом: существующий файл —
            # файлом, всё остальное — index.html, дальше роутит React.
            #
            # Почему не StaticFiles(html=True), как было раньше: он отдаёт
            # index.html только для каталогов, а для отсутствующего файла
            # ищет 404.html и, не найдя, поднимает 404. Роутер дашборда —
            # BrowserRouter с реальными путями /dashboard/users,
            # /dashboard/payments и т.д. Переходы внутри приложения работали,
            # но F5 на любой странице и прямая ссылка из мессенджера давали
            # 404 — то есть админ не мог открыть раздел по ссылке вообще.
            #
            # /dashboard/api и /dashboard/ws подключены выше и сюда не
            # доходят: у FastAPI побеждает первый совпавший маршрут.
            from fastapi.responses import FileResponse
            from app.api.spa_files import safe_asset_path as _safe_asset_path

            # realpath один раз на старте: сравнивать нужно с разрешёнными
            # симлинками, а каталог сборки между запросами не меняется.
            _dist_real = _os.path.realpath(_dist)
            _index = _os.path.join(_dist_real, "index.html")

            @app.get("/dashboard", include_in_schema=False)
            @app.get("/dashboard/{spa_path:path}", include_in_schema=False)
            async def _dashboard_spa(spa_path: str = ""):
                # Проверка пути — в app/api/spa_files.py, там же объяснение,
                # почему недостаточно normpath и голого startswith.
                asset = _safe_asset_path(_dist_real, spa_path)
                if asset:
                    return FileResponse(asset)
                # Всё остальное — index.html: маршрут доделает React.
                # Отдавать 404 нельзя, иначе F5 на разделе снова сломается.
                return FileResponse(_index)

            logger.info("DASHBOARD mounted: api+ws+spa (dist=%s)", _dist)
        else:
            logger.info("DASHBOARD mounted: api+ws only (no dist at %s yet)", _dist)
except Exception:
    logger.exception("dashboard mount failed")


@app.get("/health")
async def health():
    """
    Production-grade health check.

    Checks:
    1. database.DB_READY flag (safe-startup guard)
    2. Connection pool availability
    3. Actual DB connectivity via SELECT 1
    4. Redis connectivity (if configured)

    Returns:
        200 {"status": "ok", ...}      — all checks passed
        503 {"status": "degraded", ...} — DB or Redis not ready
    """
    import database
    from app.utils.redis_client import ping as redis_ping, is_configured as redis_configured

    result_body = {}

    # Check 1: DB_READY flag
    if not database.DB_READY:
        logger.warning("HEALTH_ENDPOINT db_ready=False")
        return JSONResponse(
            {"status": "degraded", "database": "not_ready"},
            status_code=503,
        )

    # Check 2: connection pool exists
    try:
        pool = await database.get_pool()
    except Exception as e:
        logger.error("HEALTH_ENDPOINT pool_error=%s", e)
        return JSONResponse(
            {"status": "degraded", "database": "pool_error"},
            status_code=503,
        )

    if not pool:
        logger.error("HEALTH_ENDPOINT pool=None")
        return JSONResponse(
            {"status": "degraded", "database": "pool_unavailable"},
            status_code=503,
        )

    # Check 3: actual DB connectivity
    try:
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
        if result != 1:
            raise ValueError(f"unexpected SELECT 1 result: {result}")
        result_body["database"] = "connected"
    except Exception as e:
        logger.error("HEALTH_ENDPOINT db_query_error=%s", e)
        return JSONResponse(
            {"status": "degraded", "database": "query_failed"},
            status_code=503,
        )

    # Check 4: Redis connectivity (if configured)
    if redis_configured():
        redis_ok = await redis_ping()
        result_body["redis"] = "connected" if redis_ok else "unavailable"
        if not redis_ok:
            logger.warning("HEALTH_ENDPOINT redis=unavailable")
            return JSONResponse(
                {"status": "degraded", **result_body},
                status_code=503,
            )

    # Check 5: Payment providers status (non-blocking, informational)
    payment_providers = {}
    try:
        import platega_service
        payment_providers["platega"] = "enabled" if platega_service.is_enabled() else "disabled"
    except Exception:
        payment_providers["platega"] = "import_error"
    try:
        import cryptobot_service
        payment_providers["cryptobot"] = "enabled" if cryptobot_service.is_enabled() else "disabled"
    except Exception:
        payment_providers["cryptobot"] = "import_error"

    # Check 6: VPN API status
    try:
        import config as _cfg
        payment_providers["vpn_api"] = "enabled" if _cfg.VPN_ENABLED else "disabled"
    except Exception:
        payment_providers["vpn_api"] = "unknown"

    result_body["payment_providers"] = payment_providers
    result_body["status"] = "ok"
    return JSONResponse(result_body)
