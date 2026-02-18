"""
API module — HTTP endpoints for webhooks and health.
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.api import telegram_webhook

app = FastAPI()
app.include_router(telegram_webhook.router)

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})
