"""
API module — HTTP endpoints for webhooks and health.
"""
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.api import telegram_webhook
from app.api import payment_webhook
from app.api import deeplink_redirect
from app.api import sub_aggregator_route

logger = logging.getLogger(__name__)

# SECURITY: Disable OpenAPI/Swagger docs in production (information leak prevention)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """SECURITY: Reject requests with body larger than max_size (DDoS protection).

    Path-aware: the dashboard broadcast photo-upload endpoint legitimately
    accepts up to 10 MB images (handler enforces its own 10 MB check at
    `app/api/dashboard/routes/broadcasts.py:upload_photo`). Cutting it at
    1 MB here used to fire 413 before the request even reached the
    handler. Other endpoints (Telegram webhook, payment webhooks,
    dashboard JSON APIs) stay on the 1 MB default — there's no
    legitimate reason for any of them to exceed it.
    """

    # Per-prefix exceptions: prefix → max bytes. First match wins.
    _PATH_OVERRIDES = (
        ("/dashboard/api/broadcasts/upload-photo", 10 * 1024 * 1024),
        ("/dashboard/api/broadcasts/upload-animation", 20 * 1024 * 1024),  # handler: 20 MB GIF/MP4
    )

    def __init__(self, app, max_size: int = 1 * 1024 * 1024):
        super().__init__(app)
        self.max_size = max_size

    def _limit_for(self, path: str) -> int:
        for prefix, lim in self._PATH_OVERRIDES:
            if path.startswith(prefix):
                return lim
        return self.max_size

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                lim = self._limit_for(request.url.path)
                if int(content_length) > lim:
                    return Response(status_code=413, content="Request body too large")
            except (ValueError, TypeError):
                return Response(status_code=400, content="Invalid Content-Length")
        return await call_next(request)


async def flush_alerts_on_shutdown() -> None:
    """HOW_IT_WORKS P2: on SIGTERM (Railway redeploy) uvicorn runs the app's
    shutdown handlers, but the provisioning worker's `finally` flush was never
    reached — buffered alert digests were lost on every deploy. Flush the
    provisioning digests and the admin_alerts held by the cooldown here.
    Best effort, bounded, never raises."""
    import asyncio
    from app.services import admin_alerts, provisioning
    bot = getattr(payment_webhook, "_bot", None) or getattr(telegram_webhook, "_bot", None)
    try:
        await asyncio.wait_for(provisioning.flush_alert_digests(bot, final=True), 5.0)
    except (Exception, asyncio.CancelledError) as e:
        logger.warning("SHUTDOWN_PROVISIONING_DIGEST_FLUSH_FAILED: %s: %s", type(e).__name__, e)
    if bot is not None:
        try:
            await asyncio.wait_for(admin_alerts.flush_all_digests(bot), 5.0)
        except (Exception, asyncio.CancelledError) as e:
            logger.warning("SHUTDOWN_ADMIN_ALERT_FLUSH_FAILED: %s: %s", type(e).__name__, e)
    left = {**provisioning.pending_alert_counts(),
            **{f"admin:{k}": v for k, v in admin_alerts.pending_digest_counts().items()}}
    if left:
        logger.critical("ALERTS_UNSENT_ON_SHUTDOWN: %s (payment_errors / logs hold them)", left)


async def drain_payment_tasks_on_shutdown() -> None:
    """SIGTERM (Railway redeploy): uvicorn runs the shutdown hooks before the
    process exits — give in-flight Telegram successful_payment finalizations up
    to 20 s (telegram_webhook.drain_payment_tasks; still running → CRITICAL log +
    admin alert). Registered BEFORE the alert flush so that alert is flushed too.
    Never raises."""
    await telegram_webhook.drain_payment_tasks()


app.router.on_shutdown.append(drain_payment_tasks_on_shutdown)
app.router.on_shutdown.append(flush_alerts_on_shutdown)
app.add_middleware(RequestSizeLimitMiddleware, max_size=1 * 1024 * 1024)
app.include_router(telegram_webhook.router)
app.include_router(payment_webhook.router)
app.include_router(deeplink_redirect.router)

# Sub-aggregator embedded endpoint — GET /a/{token}.
# Работает если SUB_AGGREGATOR_ENABLED=True в config.py. RF-1 nginx делает
# HTTPS reverse-proxy https://subscription.palantirdns.uk/{token} → сюда.
try:
    import config as _cfg
    if getattr(_cfg, "SUB_AGGREGATOR_ENABLED", False):
        app.include_router(sub_aggregator_route.router)
        logger.info("SUB_AGGREGATOR_ENABLED — mounted /a/{token}")
except Exception:
    logger.exception("sub_aggregator_route mount failed")

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
            from fastapi.staticfiles import StaticFiles
            app.mount("/dashboard", StaticFiles(directory=_dist, html=True), name="dashboard-spa")
            logger.info("DASHBOARD mounted: api+ws+static (dist=%s)", _dist)
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
