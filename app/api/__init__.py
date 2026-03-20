"""
API module — HTTP endpoints for webhooks and health.
"""
import hmac
import logging
import os

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.api import telegram_webhook
from app.api import payment_webhook

logger = logging.getLogger(__name__)

# SECURITY: Optional bearer token for /metrics and /health/detailed.
# Set METRICS_AUTH_TOKEN env var in production to protect these endpoints.
_METRICS_AUTH_TOKEN = os.getenv("METRICS_AUTH_TOKEN", "")

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


@app.get("/metrics")
async def metrics(request: Request):
    """
    Extended metrics endpoint for monitoring.

    Returns full metrics snapshot: requests, latency, workers, DB pool, memory, errors.
    Protected by METRICS_AUTH_TOKEN when configured.
    """
    if _METRICS_AUTH_TOKEN:
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {_METRICS_AUTH_TOKEN}"
        if not hmac.compare_digest(auth.encode(), expected.encode()):
            return Response(status_code=403)
    try:
        from app.core.metrics import get_metrics
        m = get_metrics()
        snapshot = m.snapshot()

        # Add pool stats
        if database.DB_READY:
            try:
                pool = await database.get_pool()
                if pool:
                    snapshot["db_pool"] = {
                        "size": pool.get_size(),
                        "idle": pool.get_idle_size(),
                        "used": pool.get_size() - pool.get_idle_size(),
                        "min": pool.get_min_size(),
                        "max": pool.get_max_size(),
                    }
            except Exception:
                snapshot["db_pool"] = {"error": "unavailable"}

        return JSONResponse(snapshot)
    except Exception as e:
        logger.error("METRICS_ENDPOINT error=%s", e)
        return JSONResponse({"error": str(e)[:200]}, status_code=500)


@app.get("/health/detailed")
async def health_detailed(request: Request):
    """
    Detailed health check with all component statuses and metrics summary.
    More information than /health, designed for monitoring dashboards.
    Protected by METRICS_AUTH_TOKEN when configured.
    """
    if _METRICS_AUTH_TOKEN:
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {_METRICS_AUTH_TOKEN}"
        if not hmac.compare_digest(auth.encode(), expected.encode()):
            return Response(status_code=403)
    import database
    from app.utils.redis_client import ping as redis_ping, is_configured as redis_configured
    from app.core.system_state import recalculate_from_runtime

    result = {}

    # System state
    try:
        system_state = recalculate_from_runtime()
        severity = system_state.get_severity()
        result["system"] = {
            "severity": severity.value,
            "state": system_state.summary(),
        }
    except Exception as e:
        result["system"] = {"error": str(e)[:200]}

    # DB
    result["database"] = {"ready": database.DB_READY}
    if database.DB_READY:
        try:
            pool = await database.get_pool()
            if pool:
                import time
                start = time.monotonic()
                async with pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                latency_ms = (time.monotonic() - start) * 1000
                result["database"]["connected"] = True
                result["database"]["latency_ms"] = round(latency_ms, 1)
                result["database"]["pool"] = {
                    "size": pool.get_size(),
                    "idle": pool.get_idle_size(),
                    "used": pool.get_size() - pool.get_idle_size(),
                    "max": pool.get_max_size(),
                }
        except Exception as e:
            result["database"]["connected"] = False
            result["database"]["error"] = type(e).__name__

    # Redis
    if redis_configured():
        try:
            from app.utils.redis_client import info_stats as redis_info_stats
            redis_connected = await redis_ping()
            redis_data = {"connected": redis_connected}
            if redis_connected:
                stats = await redis_info_stats()
                if stats:
                    redis_data.update(stats)
                # Include blocked IPs count
                try:
                    from app.core.ip_abuse import get_blocked_count
                    redis_data["blocked_ips"] = await get_blocked_count()
                except Exception:
                    pass
            result["redis"] = redis_data
        except Exception:
            result["redis"] = {"connected": False}

    # Metrics summary
    try:
        from app.core.metrics import get_metrics
        m = get_metrics()
        snap = m.snapshot()
        result["metrics"] = {
            "requests_total": snap["requests"]["total"],
            "errors_total": snap["requests"]["errors"],
            "rate_per_sec": snap["requests"]["rate_per_sec"],
            "latency_p95_ms": round(snap["requests"]["latency"]["p95"] * 1000, 0),
            "concurrent": snap["concurrency"]["current"],
            "memory_mb": snap["process"]["memory_rss_mb"],
            "uptime_s": snap["process"]["uptime_seconds"],
        }
    except Exception:
        pass

    status_code = 200 if database.DB_READY else 503
    result["status"] = "ok" if database.DB_READY else "degraded"
    return JSONResponse(result, status_code=status_code)
