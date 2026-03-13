"""
API module — HTTP endpoints for webhooks and health.
"""
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.api import telegram_webhook
from app.api import payment_webhook

# SECURITY: Disable OpenAPI/Swagger docs in production (information leak prevention)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """SECURITY: Reject requests with body larger than max_size (DDoS protection)."""

    def __init__(self, app, max_size: int = 1 * 1024 * 1024):
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size:
            return Response(status_code=413, content="Request body too large")
        return await call_next(request)


app.add_middleware(RequestSizeLimitMiddleware, max_size=1 * 1024 * 1024)
app.include_router(telegram_webhook.router)
app.include_router(payment_webhook.router)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})
