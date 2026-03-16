"""
API module — HTTP endpoints for webhooks and health.
"""
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.api import telegram_webhook
from app.api import payment_webhook

logger = logging.getLogger(__name__)

# SECURITY: Disable OpenAPI/Swagger docs in production (information leak prevention)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """SECURITY: Reject requests with body larger than max_size (DDoS protection)."""

    def __init__(self, app, max_size: int = 1 * 1024 * 1024):
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_size:
                    return Response(status_code=413, content="Request body too large")
            except (ValueError, TypeError):
                return Response(status_code=400, content="Invalid Content-Length")
        return await call_next(request)


app.add_middleware(RequestSizeLimitMiddleware, max_size=1 * 1024 * 1024)
app.include_router(telegram_webhook.router)
app.include_router(payment_webhook.router)


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
