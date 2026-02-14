"""
Payment Webhook API

POST /webhook/payment — Unified payment webhook (CryptoBot, future providers).

Responsibilities:
1. Validate signature from provider (X-Crypto-Pay-API-Signature).
2. Parse payment event (invoice_paid).
3. Find payment by purchase_id from payload.
4. If status already "paid" → ignore (idempotency).
5. Mark payment as paid via database.finalize_purchase.
6. Activate subscription via vpn_client (grant_access).

Registration: health_server.create_health_app() calls cryptobot_service.register_webhook_route()
which registers POST /webhook/payment and POST /webhooks/cryptobot.

Security:
- Signature verification required.
- Idempotent: duplicate webhooks return 200, no re-activation.
- Amount tolerance: ±1 RUB.
- Pending expiry: 30 min (pending_purchases.expires_at).
"""

from aiohttp import web
from aiogram import Bot
from typing import Optional


async def register_payment_webhook(app: web.Application, bot: Optional[Bot]) -> None:
    """
    Register payment webhook routes.

    Delegates to cryptobot_service which handles CryptoBot webhook format.
    """
    import cryptobot_service
    if cryptobot_service.is_enabled() and bot:
        await cryptobot_service.register_webhook_route(app, bot)
