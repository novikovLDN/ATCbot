"""
Payment Webhook API (FastAPI)

Webhook endpoints for payment providers:
- POST /webhooks/platega — Platega (SBP) payment notifications
- POST /webhooks/cryptobot — CryptoBot (Crypto Pay) payment notifications
- POST /webhooks/lava — DISABLED: logs + admin alert only, never credits (security hotfix)

Security:
- Signature/auth verification required per provider.
- Idempotent: duplicate webhooks return 200, no re-activation.
- Amount tolerance: ±1 RUB.
- Pending expiry: 30 min (pending_purchases.expires_at).
"""

import asyncio
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.payments.confirmation import TransientPaymentError

# Outer timeout for entire webhook processing — must complete before
# Railway's 30s request timeout. Prevents event loop starvation if
# payment provider APIs are slow.
_WEBHOOK_TIMEOUT = 25.0

logger = logging.getLogger(__name__)

router = APIRouter()

_bot = None


async def _log_pe(
    stage: str,
    provider: str,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Fire-and-forget payment_errors logger. Webhooks must respond
    fast; never let logging slow down or break a webhook reply."""
    try:
        import database
        await database.log_payment_error(
            stage=stage,
            payment_provider=provider,
            error_code=error_code,
            error_message=error_message,
        )
    except Exception as e:
        logger.warning("payment_errors log skipped (%s): %s", stage, e)


def setup(bot):
    """Store bot instance for webhook handlers."""
    global _bot
    _bot = bot


async def _handle_platega_webhook(request: Request):
    """Handle Platega (SBP) webhook callback."""
    if _bot is None:
        logger.critical("Platega webhook received but bot is not initialized — setup() not called")
        await _log_pe("setup_missing", "platega", error_message="bot not initialized")
        return JSONResponse({"status": "error"}, status_code=500)
    try:
        import platega_service
        if not platega_service.is_enabled():
            logger.warning("Platega webhook received but service is disabled")
            return JSONResponse({"status": "disabled"})

        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"Platega webhook: invalid JSON: {e}")
            await _log_pe("webhook_invalid_json", "platega", error_message=str(e)[:300])
            return JSONResponse({"status": "invalid"}, status_code=400)

        result = await asyncio.wait_for(
            platega_service.process_webhook_data(headers, body, _bot),
            timeout=_WEBHOOK_TIMEOUT,
        )
        return JSONResponse(result)

    except ImportError:
        logger.error("platega_service not available")
        await _log_pe("service_missing", "platega")
        return JSONResponse({"status": "error"}, status_code=500)
    except ValueError as e:
        # Idempotency: already-processed payment — return 200 so provider stops retrying
        logger.info(f"Platega webhook: already processed: {e}")
        return JSONResponse({"status": "already_processed"})
    except TransientPaymentError as e:
        logger.error(f"Platega webhook transient error (returning 500 for retry): {e}")
        await _log_pe("transient", "platega", error_message=str(e)[:500])
        return JSONResponse({"status": "transient_error"}, status_code=500)
    except asyncio.TimeoutError:
        logger.error("Platega webhook timeout (returning 500 for retry)")
        await _log_pe("timeout", "platega", error_message=f">{_WEBHOOK_TIMEOUT}s")
        return JSONResponse({"status": "timeout"}, status_code=500)
    except Exception as e:
        logger.exception(f"Platega webhook error: {e}")
        await _log_pe("unhandled_exception", "platega",
                      error_code=type(e).__name__,
                      error_message=str(e)[:500])
        return JSONResponse({"status": "error"}, status_code=500)


@router.post("/webhooks/platega")
async def platega_webhook(request: Request):
    return await _handle_platega_webhook(request)


@router.post("/platega/callback")
async def platega_callback(request: Request):
    """Alias route — Platega dashboard sends webhooks to this URL."""
    return await _handle_platega_webhook(request)


async def _handle_platega_subscription_webhook(request: Request):
    """Handle Platega recurring-subscription (paymentMethod=6) callback.

    ОТДЕЛЬНЫЙ endpoint — не трогаем /webhooks/platega, чтобы не
    ломать существующий разовый flow (у которого ключи строчные:
    id/status/paymentDetails.amount). Здесь ключи ЗАГЛАВНЫЕ:
    Id / SubscriptionId / Amount / Status / NextChargeAt / Payload.
    Идемпотентность — по Callback.Id (см. platega_service).
    """
    if _bot is None:
        logger.critical(
            "Platega sub webhook received but bot is not initialized — setup() not called"
        )
        await _log_pe(
            "setup_missing", "platega_subscription", error_message="bot not initialized",
        )
        return JSONResponse({"status": "error"}, status_code=500)
    try:
        import platega_service
        if not platega_service.is_enabled():
            logger.warning("Platega sub webhook received but service is disabled")
            return JSONResponse({"status": "disabled"})

        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"Platega sub webhook: invalid JSON: {e}")
            await _log_pe(
                "webhook_invalid_json", "platega_subscription",
                error_message=str(e)[:300],
            )
            return JSONResponse({"status": "invalid"}, status_code=400)

        result = await asyncio.wait_for(
            platega_service.process_subscription_webhook_data(headers, body, _bot),
            timeout=_WEBHOOK_TIMEOUT,
        )
        return JSONResponse(result)

    except ImportError:
        logger.error("platega_service not available")
        await _log_pe("service_missing", "platega_subscription")
        return JSONResponse({"status": "error"}, status_code=500)
    except ValueError as e:
        # Идемпотентность: уже обработанное списание → 200, чтобы провайдер не ретрайл.
        logger.info(f"Platega sub webhook: already processed: {e}")
        return JSONResponse({"status": "already_processed"})
    except TransientPaymentError as e:
        logger.error(f"Platega sub webhook transient error (500 for retry): {e}")
        await _log_pe(
            "transient", "platega_subscription", error_message=str(e)[:500],
        )
        return JSONResponse({"status": "transient_error"}, status_code=500)
    except asyncio.TimeoutError:
        logger.error("Platega sub webhook timeout (500 for retry)")
        await _log_pe(
            "timeout", "platega_subscription", error_message=f">{_WEBHOOK_TIMEOUT}s",
        )
        return JSONResponse({"status": "timeout"}, status_code=500)
    except Exception as e:
        logger.exception(f"Platega sub webhook error: {e}")
        await _log_pe(
            "unhandled_exception", "platega_subscription",
            error_code=type(e).__name__,
            error_message=str(e)[:500],
        )
        return JSONResponse({"status": "error"}, status_code=500)


@router.post("/webhooks/platega-subscription")
async def platega_subscription_webhook(request: Request):
    return await _handle_platega_subscription_webhook(request)


@router.post("/platega/subscription-callback")
async def platega_subscription_callback(request: Request):
    """Alias route — на случай если в Platega dashboard подписочный
    URL зарегистрируют без /webhooks-префикса."""
    return await _handle_platega_subscription_webhook(request)


async def _handle_cryptobot_webhook(request: Request):
    """Handle CryptoBot (Crypto Pay) webhook callback."""
    if _bot is None:
        logger.critical("CryptoBot webhook received but bot is not initialized — setup() not called")
        await _log_pe("setup_missing", "cryptobot", error_message="bot not initialized")
        return JSONResponse({"status": "error"}, status_code=500)
    try:
        import cryptobot_service
        if not cryptobot_service.is_enabled():
            logger.warning("CryptoBot webhook received but service is disabled")
            return JSONResponse({"status": "disabled"})

        headers = {k.lower(): v for k, v in request.headers.items()}
        raw_body = await request.body()
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"CryptoBot webhook: invalid JSON: {e}")
            await _log_pe("webhook_invalid_json", "cryptobot", error_message=str(e)[:300])
            return JSONResponse({"status": "invalid"}, status_code=400)

        result = await asyncio.wait_for(
            cryptobot_service.process_webhook_data(headers, raw_body, body, _bot),
            timeout=_WEBHOOK_TIMEOUT,
        )
        return JSONResponse(result)

    except ImportError:
        logger.error("cryptobot_service not available")
        await _log_pe("service_missing", "cryptobot")
        return JSONResponse({"status": "error"}, status_code=500)
    except ValueError as e:
        logger.info(f"CryptoBot webhook: already processed: {e}")
        return JSONResponse({"status": "already_processed"})
    except TransientPaymentError as e:
        logger.error(f"CryptoBot webhook transient error (returning 500 for retry): {e}")
        await _log_pe("transient", "cryptobot", error_message=str(e)[:500])
        return JSONResponse({"status": "transient_error"}, status_code=500)
    except asyncio.TimeoutError:
        logger.error("CryptoBot webhook timeout (returning 500 for retry)")
        await _log_pe("timeout", "cryptobot", error_message=f">{_WEBHOOK_TIMEOUT}s")
        return JSONResponse({"status": "timeout"}, status_code=500)
    except Exception as e:
        logger.exception(f"CryptoBot webhook error: {e}")
        await _log_pe("unhandled_exception", "cryptobot",
                      error_code=type(e).__name__,
                      error_message=str(e)[:500])
        return JSONResponse({"status": "error"}, status_code=500)


@router.post("/webhooks/cryptobot")
async def cryptobot_webhook(request: Request):
    return await _handle_cryptobot_webhook(request)


async def _handle_lava_webhook(request: Request):
    """Lava webhook — DISABLED (security hotfix).

    lava_service.process_webhook_data never verified the webhook signature,
    and lookup_pending_purchase accepts any provider's pending purchase, so
    an unsigned POST could finalize any purchase (incl. balance top-ups
    credited with the amount from the request body). Lava is no longer
    used, so nothing is credited here anymore: the request is logged to
    payment_errors and the admin is alerted to verify a genuine payment
    in the Lava dashboard and grant it manually.

    lava_service.is_enabled() and the LAVA_* env vars are intentionally
    left untouched: several payment screens use it to show the WATA button.
    """
    try:
        raw_body = await request.body()
    except Exception:  # noqa: BLE001
        raw_body = b""
    preview = raw_body[:500].decode("utf-8", "replace")
    logger.critical("LAVA_WEBHOOK_REJECTED (disabled): body=%s", preview)
    await _log_pe("lava_webhook_disabled", "lava", error_message=preview[:300])

    if _bot is not None:
        order_id = amount = status = None
        try:
            import json
            data = json.loads(raw_body or b"{}")
            if isinstance(data, dict):
                order_id = data.get("order_id")
                amount = data.get("amount")
                status = data.get("status")
        except Exception:  # noqa: BLE001
            pass
        try:
            from app.services.admin_alerts import send_alert
            await send_alert(
                _bot,
                "payment",
                "Lava webhook получен, но Lava отключена — НИЧЕГО НЕ ЗАЧИСЛЕНО.\n"
                f"order_id: {order_id}\n"
                f"amount: {amount}\n"
                f"status: {status}\n"
                "Если это настоящая оплата — сверить в кабинете Lava и выдать вручную. "
                "Все такие запросы пишутся в payment_errors (stage=lava_webhook_disabled).",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Lava disabled-webhook admin alert failed: %s", e)

    return JSONResponse({"status": "disabled"})


@router.post("/webhooks/lava")
async def lava_webhook(request: Request):
    return await _handle_lava_webhook(request)


# ── Wata (wata.pro) — H2H REST API ────────────────────────────────

async def _handle_wata_webhook(request: Request):
    """Обработчик webhook'ов от Wata.

    Особенности:
      - Raw body ОБЯЗАТЕЛЕН для проверки RSA-SHA512 подписи (X-Signature).
        Если middleware пересобрал JSON — подпись не сойдётся.
      - kind=Payment + transactionStatus=Paid → confirm.
      - Всё остальное (Pending / Declined / Refund) → ignored.
    """
    if _bot is None:
        logger.critical("Wata webhook received but bot is not initialized")
        await _log_pe("setup_missing", "wata", error_message="bot not initialized")
        return JSONResponse({"status": "error"}, status_code=500)
    try:
        import wata_service
        if not wata_service.is_enabled():
            logger.warning("Wata webhook received but service is disabled")
            return JSONResponse({"status": "disabled"})

        headers = {k.lower(): v for k, v in request.headers.items()}
        # Raw body для подписи + parsed JSON для логики.
        raw = await request.body()
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"Wata webhook: invalid JSON: {e}")
            await _log_pe("webhook_invalid_json", "wata", error_message=str(e)[:300])
            return JSONResponse({"status": "invalid"}, status_code=400)

        result = await asyncio.wait_for(
            wata_service.process_webhook_data(headers, raw, body, _bot),
            timeout=_WEBHOOK_TIMEOUT,
        )
        return JSONResponse(result)

    except ImportError:
        logger.error("wata_service not available")
        await _log_pe("service_missing", "wata")
        return JSONResponse({"status": "error"}, status_code=500)
    except ValueError as e:
        logger.info(f"Wata webhook: already processed: {e}")
        return JSONResponse({"status": "already_processed"})
    except TransientPaymentError as e:
        logger.error(f"Wata webhook transient error: {e}")
        await _log_pe("transient", "wata", error_message=str(e)[:500])
        return JSONResponse({"status": "transient_error"}, status_code=500)
    except asyncio.TimeoutError:
        logger.error("Wata webhook timeout")
        await _log_pe("timeout", "wata", error_message=f">{_WEBHOOK_TIMEOUT}s")
        return JSONResponse({"status": "timeout"}, status_code=500)
    except Exception as e:
        logger.exception(f"Wata webhook error: {e}")
        await _log_pe("unhandled_exception", "wata",
                      error_code=type(e).__name__,
                      error_message=str(e)[:500])
        return JSONResponse({"status": "error"}, status_code=500)


@router.post("/webhooks/wata")
async def wata_webhook(request: Request):
    return await _handle_wata_webhook(request)
