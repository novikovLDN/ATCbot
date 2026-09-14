"""
Payment Webhook API (FastAPI)

Webhook endpoints for payment providers:
- POST /webhooks/platega, /platega/callback — Platega one-off payments
- POST /webhooks/platega-subscription, /platega/subscription-callback — Platega recurring
  (feature disabled: auth-checked, alert-only safety net, always 200)
- POST /webhooks/cryptobot — CryptoBot (Crypto Pay)
- POST /webhooks/wata — WATA (H2H)

Security:
- Signature/auth verification required per provider.
- Idempotent: duplicate webhooks return 200, no re-activation.

HTTP codes (docs/audit/02_payment_core_plan.md §D):
- 5xx ONLY when billing was not committed and a provider retry can help:
  TransientPaymentError (DB down, WATA key unavailable, Remnawave sync failed,
  Platega charge in progress), timeout, bot/service not initialised, or an
  unexpected exception.
- 200 for every dict result a provider service returns — see _STATUS_HTTP.
- 400 for a body that is not JSON.
"""

import asyncio
import importlib
import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.services.payments.confirmation import TransientPaymentError

# Outer timeout for entire webhook processing — must complete before
# Railway's 30s request timeout. Prevents event loop starvation if
# payment provider APIs are slow.
_WEBHOOK_TIMEOUT = 25.0

logger = logging.getLogger(__name__)

router = APIRouter()

_bot = None


# Single source of truth: result["status"] → HTTP code, for ALL provider routes.
# Lists every status produced today by platega_service, wata_service,
# cryptobot_service and app/services/payments/confirmation.py. Everything a
# service RETURNS is final for the provider (a retry would not change the
# outcome), so it is 200; transient failures are RAISED and handled in
# _run_webhook. An unlisted status is answered 200 with a warning log.
_STATUS_HTTP: dict[str, int] = {
    # processed / idempotent
    "ok": 200,                    # includes Platega chargeback/orphan_charge/noop events
    "already_processed": 200,
    "duplicate": 200,             # Platega recurring charge already recorded
    # rejected, admin alerted — payment NOT credited, retry would not help
    "amount_mismatch": 200,
    "provider_mismatch": 200,
    "rejected": 200,              # Platega VPN callback with bad amount/currency
    "invalid_amount": 200,        # WATA VPN callback
    "invalid_currency": 200,      # WATA VPN callback
    "not_found": 200,             # no pending_purchases row — a retry won't create it
    "invalid_status": 200,
    "error": 200,                 # permanent finalization error (PERMANENT alert sent)
    # auth / payload / no-op
    # P1-4: a callback that fails auth may be a PAID one with wrong keys on our
    # side → 500 so the provider retries (Platega: 3 × 5 min, platega_api.md §4)
    # + payment_errors + forced alert (_alert_rejected_callback).
    "unauthorized": 500,
    "invalid": 200,               # paid callback without purchase_id → alerted, retry won't help
    "ignored": 200,
    "disabled": 200,
    "refund_alerted": 200,
    "declined_notified": 200,
    # transient — produced by exceptions in _run_webhook; listed for completeness
    "transient_error": 500,
    "timeout": 500,
}


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


async def _alert_webhook_failure(provider: str, branch: str, detail: str) -> None:
    """P1-1: forced admin alert for every non-200 / anomalous _run_webhook
    outcome, within the shared per-window budget (provisioning alert
    aggregation, kind "webhook"): a provider retry storm gives up to
    ALERT_IMMEDIATE_PER_WINDOW alerts, then ONE digest — never a flood, never
    dropped. Never raises."""
    try:
        from app.services import provisioning
        bot = _bot
        if bot is None:  # setup_missing: fall back to the Telegram webhook's bot
            from app.api import telegram_webhook
            bot = getattr(telegram_webhook, "_bot", None)
        text = (
            f"Payment webhook FAILED: provider={provider} branch={branch}\n"
            f"{detail[:500]}\n"
            "5xx → the provider retries the webhook; 200 → it does not. If a payment is "
            "stuck, check payment_errors / the provider dashboard and finalize manually."
        )
        await provisioning.report_payment_alert(
            "webhook", text, reason=f"{provider} {branch}: {detail[:80]}",
            key=f"{provider}:{branch}", bot=bot,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("webhook failure alert skipped (%s/%s): %s", provider, branch, e)


def setup(bot):
    """Store bot instance for webhook handlers."""
    global _bot
    _bot = bot


def _json_result(provider: str, result: Any) -> JSONResponse:
    status = result.get("status") if isinstance(result, dict) else None
    code = _STATUS_HTTP.get(status)
    if code is None:
        logger.warning(
            "WEBHOOK_UNMAPPED_STATUS provider=%s status=%r — answering 200", provider, status,
        )
        code = 200
    if isinstance(result, dict):
        # "_…" keys are internal (alert details) — never echoed to the caller.
        result = {k: v for k, v in result.items() if not str(k).startswith("_")}
    return JSONResponse(result, status_code=code)


# P1-4: dict statuses that are a lost-payment risk → payment_errors stage.
_ALERT_STATUSES = {"unauthorized": "webhook_unauthorized", "invalid": "webhook_invalid"}


async def _alert_rejected_callback(provider: str, result: dict) -> None:
    """Callback rejected before it could be matched to a purchase: wrong keys /
    signature (`unauthorized`) or a paid callback without purchase_id
    (`invalid`). payment_errors + forced alert within the "webhook" budget
    (flood → one digest). Never raises."""
    status = result.get("status")
    detail = str(result.get("_detail") or "no detail")
    await _log_pe(_ALERT_STATUSES[status], provider, error_code=status, error_message=detail[:500])
    if status == "unauthorized":
        note = ("Answered 500 → the provider retries (Platega: 3 times, 5 min apart). If this is not "
                "a forged request, the provider keys / signature secret in Railway are WRONG: every "
                "paid callback is rejected until they are fixed.")
    else:
        note = ("Paid callback without purchase_id / orderId — answered 200 (a retry carries the same "
                "body). Find the payment in the provider dashboard and finalize it manually.")
    await _alert_webhook_failure(provider, status, f"{detail}\n{note}")


async def _run_webhook(
    provider: str, coro_factory: Callable[[], Awaitable[Any]],
) -> Response:
    """Shared error handling for every provider route.

    `coro_factory()` returns the provider result (dict → _STATUS_HTTP) or a
    ready Response (e.g. 400 on invalid JSON). Exceptions keep the historical
    mapping: ValueError → 200 already_processed; TransientPaymentError /
    timeout → 500 so the provider retries; anything else → 500.
    P1-1: every branch except a dict/Response result also alerts the admin
    (_alert_webhook_failure; budgeted + digest).
    """
    if _bot is None:
        logger.critical("%s webhook received but bot is not initialized — setup() not called", provider)
        await _log_pe("setup_missing", provider, error_message="bot not initialized")
        await _alert_webhook_failure(provider, "setup_missing", "bot not initialized (setup() not called)")
        return JSONResponse({"status": "error"}, status_code=500)
    try:
        result = await coro_factory()
    except ImportError:
        logger.error("%s webhook: provider service not available", provider)
        await _log_pe("service_missing", provider)
        await _alert_webhook_failure(provider, "service_missing", "provider service module not importable")
        return JSONResponse({"status": "error"}, status_code=500)
    except ValueError as e:
        # Idempotency: already-processed payment — 200 so the provider stops retrying.
        # confirmation handles its own duplicates (PurchaseAlreadyProcessed), so a
        # ValueError reaching here is anomalous (e.g. a non-numeric WATA/CryptoBot
        # amount): the answer stays 200 (a retry changes nothing), the admin is told.
        logger.warning("%s webhook: ValueError (answered 200): %s", provider, e)
        await _alert_webhook_failure(provider, "value_error", f"{type(e).__name__}: {e} (answered 200)")
        return JSONResponse({"status": "already_processed"})
    except TransientPaymentError as e:
        logger.error("%s webhook transient error (returning 500 for retry): %s", provider, e)
        await _log_pe("transient", provider, error_message=str(e)[:500])
        if not getattr(e, "alerted", False):  # confirmation already alerted → one alert per incident
            await _alert_webhook_failure(provider, "transient", f"TransientPaymentError: {e}")
        return JSONResponse({"status": "transient_error"}, status_code=500)
    except asyncio.TimeoutError:
        logger.error("%s webhook timeout (returning 500 for retry)", provider)
        await _log_pe("timeout", provider, error_message=f">{_WEBHOOK_TIMEOUT}s")
        await _alert_webhook_failure(
            provider, "timeout",
            f"processing took >{_WEBHOOK_TIMEOUT}s; confirmation keeps running in the background",
        )
        return JSONResponse({"status": "timeout"}, status_code=500)
    except Exception as e:
        logger.exception("%s webhook error: %s", provider, e)
        await _log_pe("unhandled_exception", provider,
                      error_code=type(e).__name__,
                      error_message=str(e)[:500])
        await _alert_webhook_failure(provider, "unhandled_exception", f"{type(e).__name__}: {e}")
        return JSONResponse({"status": "error"}, status_code=500)
    if isinstance(result, Response):
        return result
    if isinstance(result, dict) and result.get("status") in _ALERT_STATUSES:
        await _alert_rejected_callback(provider, result)
    return _json_result(provider, result)


async def _provider_webhook(
    request: Request,
    provider: str,
    module_name: str,
    call: Callable[[Any, dict, bytes, Any], Awaitable[Any]],
) -> Response:
    """is_enabled → headers/raw body/JSON → `call(service, headers, raw, body)`
    under _WEBHOOK_TIMEOUT, wrapped by _run_webhook."""

    async def run():
        service = importlib.import_module(module_name)
        if not service.is_enabled():
            logger.warning("%s webhook received but service is disabled", provider)
            return {"status": "disabled"}
        headers = {k.lower(): v for k, v in request.headers.items()}
        # Raw body is required for signature checks (CryptoBot HMAC, WATA RSA).
        raw = await request.body()
        try:
            body = await request.json()
        except Exception as e:
            logger.error("%s webhook: invalid JSON: %s", provider, e)
            await _log_pe("webhook_invalid_json", provider, error_message=str(e)[:300])
            return JSONResponse({"status": "invalid"}, status_code=400)
        return await asyncio.wait_for(
            call(service, headers, raw, body), timeout=_WEBHOOK_TIMEOUT,
        )

    return await _run_webhook(provider, run)


async def _handle_platega_webhook(request: Request):
    """Handle Platega (SBP/card) one-off payment callback."""
    return await _provider_webhook(
        request, "platega", "platega_service",
        lambda svc, headers, raw, body: svc.process_webhook_data(headers, body, _bot),
    )


@router.post("/webhooks/platega")
async def platega_webhook(request: Request):
    return await _handle_platega_webhook(request)


@router.post("/platega/callback")
async def platega_callback(request: Request):
    """Alias route — Platega dashboard sends webhooks to this URL."""
    return await _handle_platega_webhook(request)


async def _handle_platega_subscription_webhook(request: Request):
    """Handle Platega recurring-subscription (paymentMethod=6) callback.

    Рекуррентные подписки отключены. Роут оставлен как страховка для
    подписок, оставшихся в Platega после беты: X-MerchantId/X-Secret
    проверяются как раньше, дальше platega_service только логирует,
    пишет payment_errors и шлёт принудительный алерт админу (200).
    Доступ по этим callback'ам не выдаётся.
    """
    return await _provider_webhook(
        request, "platega_subscription", "platega_service",
        lambda svc, headers, raw, body: svc.process_subscription_webhook_data(headers, body, _bot),
    )


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
    return await _provider_webhook(
        request, "cryptobot", "cryptobot_service",
        lambda svc, headers, raw, body: svc.process_webhook_data(headers, raw, body, _bot),
    )


@router.post("/webhooks/cryptobot")
async def cryptobot_webhook(request: Request):
    return await _handle_cryptobot_webhook(request)


# ── Wata (wata.pro) — H2H REST API ────────────────────────────────

async def _handle_wata_webhook(request: Request):
    """Обработчик webhook'ов от Wata.

    Особенности:
      - Raw body ОБЯЗАТЕЛЕН для проверки RSA-SHA512 подписи (X-Signature).
        Если middleware пересобрал JSON — подпись не сойдётся.
      - kind=Payment + transactionStatus=Paid → confirm.
      - Declined → уведомление юзеру; Refund → алерт; прочее → ignored.
    """
    return await _provider_webhook(
        request, "wata", "wata_service",
        lambda svc, headers, raw, body: svc.process_webhook_data(headers, raw, body, _bot),
    )


@router.post("/webhooks/wata")
async def wata_webhook(request: Request):
    return await _handle_wata_webhook(request)
