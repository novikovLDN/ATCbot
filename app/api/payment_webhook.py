"""
Payment Webhook API

Webhook endpoints for payment providers:
- POST /webhooks/platega — Platega (SBP) payment notifications
- POST /webhooks/crypto2328 — 2328.io crypto payment notifications

Registration: health_server.create_health_app() calls
platega_service.register_webhook_route() and crypto2328_service.register_webhook_route().

Security:
- Signature/auth verification required per provider.
- Idempotent: duplicate webhooks return 200, no re-activation.
- Amount tolerance: ±1 RUB.
- Pending expiry: 30 min (pending_purchases.expires_at).
"""

from aiohttp import web
from aiogram import Bot
from typing import Optional


async def register_payment_webhook(app: web.Application, bot: Optional[Bot]) -> None:
    """
    Register payment webhook routes for all configured providers.
    """
    if not bot:
        return

    # Platega (SBP)
    try:
        import platega_service
        if platega_service.is_enabled():
            await platega_service.register_webhook_route(app, bot)
    except ImportError:
        pass

    # 2328.io (Crypto)
    try:
        import crypto2328_service
        if crypto2328_service.is_enabled():
            await crypto2328_service.register_webhook_route(app, bot)
    except ImportError:
        pass
