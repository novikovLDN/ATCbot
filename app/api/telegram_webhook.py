"""
Telegram webhook endpoint.
Receives updates from Telegram and feeds them to aiogram Dispatcher.
"""
import asyncio
import hmac
import logging
import time
from fastapi import APIRouter, Request, Response, Header
from aiogram.types import Update
import config

logger = logging.getLogger(__name__)

router = APIRouter()

# Bot and Dispatcher are set from main.py at startup
_bot = None
_dp = None

# Liveness heartbeat — updated on every authenticated Telegram update.
# Imported by main.py watchdog to track "last sign of life from Telegram".
last_webhook_update_at: float = time.monotonic()


def setup(bot, dp):
    global _bot, _dp
    _bot = bot
    _dp = dp


# 25 s — Railway request timeout is 30 s.
_HANDLER_TIMEOUT = 25.0

# HOW_IT_WORKS P1-6: successful_payment updates run as shielded tasks (strong
# refs here). Telegram already charged the user and never resends the update,
# so the webhook timeout must not cancel the finalization half-way.
_payment_tasks: set = set()


def _is_payment_update(update) -> bool:
    msg = getattr(update, "message", None)
    return bool(msg is not None and getattr(msg, "successful_payment", None))


def _payment_update_info(update) -> str:
    msg = update.message
    sp = msg.successful_payment
    tg = getattr(getattr(msg, "from_user", None), "id", None)
    return (f"update_id={update.update_id} tg={tg} payload={sp.invoice_payload} "
            f"amount={sp.total_amount} {sp.currency}")


async def _alert_payment_update(update, branch: str, detail: str) -> None:
    """Admin alert for a successful_payment update that timed out / failed /
    was cancelled — within the shared payment "webhook" budget (flood → one
    digest, never dropped). Never raises."""
    try:
        from app.services import provisioning
        tg = getattr(getattr(update.message, "from_user", None), "id", None)
        text = (
            f"Telegram successful_payment {branch}: {_payment_update_info(update)}\n{detail[:500]}\n"
            "Telegram does not resend successful_payment: check payment_errors / the user's "
            "subscription and finalize manually if it is missing."
        )
        await provisioning.report_payment_alert(
            "webhook", text, reason=f"telegram {branch}: {detail[:80]}",
            telegram_id=tg, key=f"telegram:{update.update_id}", bot=_bot,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("TELEGRAM_PAYMENT_ALERT_SKIPPED update_id=%s: %s", getattr(update, "update_id", None), e)


def _on_payment_task_done(update):
    def _done(task: "asyncio.Task") -> None:
        _payment_tasks.discard(task)
        if task.cancelled():
            branch, detail = "cancelled", "processing was cancelled before it finished (shutdown?)"
        else:
            exc = task.exception()
            if exc is None:
                return
            branch, detail = "failed", f"{type(exc).__name__}: {exc}"
        logger.critical("TELEGRAM_PAYMENT_UPDATE_%s %s: %s", branch.upper(), _payment_update_info(update), detail)
        try:
            alert = asyncio.get_running_loop().create_task(_alert_payment_update(update, branch, detail))
            _payment_tasks.add(alert)
            alert.add_done_callback(_payment_tasks.discard)
        except RuntimeError:  # loop closing — the CRITICAL log above is the record
            pass
    return _done


# Shutdown (SIGTERM / Railway redeploy): in-flight successful_payment finalizations
# get this long before the process exits (app shutdown hook + main()'s finally).
PAYMENT_DRAIN_TIMEOUT_S = 20.0


def _unfinished_payment_tasks() -> set:
    return {t for t in list(_payment_tasks) if not t.done()}


async def drain_payment_tasks(timeout: float = PAYMENT_DRAIN_TIMEOUT_S) -> int:
    """Wait (bounded by `timeout`) for the in-flight Telegram successful_payment
    tasks. Telegram never resends the update: exiting mid-finalization would leave
    money taken and nothing granted. The tasks are NOT cancelled here. Returns how
    many are still running afterwards — logged CRITICAL + one admin alert.
    Never raises."""
    try:
        pending = _unfinished_payment_tasks()
        if not pending:
            return 0
        logger.warning(
            "SHUTDOWN_PAYMENT_DRAIN: waiting up to %.0fs for %d Telegram payment task(s)",
            timeout, len(pending),
        )
        _done, still = await asyncio.wait(pending, timeout=timeout)
        if not still:
            logger.info("SHUTDOWN_PAYMENT_DRAIN_DONE: every Telegram payment task finished")
            return 0
        logger.critical(
            "SHUTDOWN_PAYMENT_TASKS_UNFINISHED: %d Telegram payment task(s) still running after %.0fs",
            len(still), timeout,
        )
        try:
            from app.services import provisioning
            text = (
                f"Shutdown: {len(still)} Telegram successful_payment finalization(s) still running "
                f"after {timeout:.0f}s — the process is exiting. Telegram does not resend "
                "successful_payment: check payment_errors / the users' subscriptions and "
                "finalize manually if one is missing."
            )
            await asyncio.wait_for(provisioning.report_payment_alert(
                "webhook", text, reason="telegram shutdown: payment tasks unfinished",
                telegram_id=None, key="telegram:shutdown_drain", bot=_bot,
            ), 5.0)
        except (Exception, asyncio.CancelledError) as e:  # noqa: BLE001
            logger.warning("SHUTDOWN_PAYMENT_ALERT_FAILED: %s: %s", type(e).__name__, e)
        return len(still)
    except Exception as e:  # noqa: BLE001
        logger.warning("SHUTDOWN_PAYMENT_DRAIN_FAILED: %s: %s", type(e).__name__, e)
        return len(_unfinished_payment_tasks())


async def _feed_payment_update(update) -> None:
    task = asyncio.ensure_future(_dp.feed_webhook_update(_bot, update))
    _payment_tasks.add(task)
    task.add_done_callback(_on_payment_task_done(update))
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=_HANDLER_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error(
            "WEBHOOK_PAYMENT_HANDLER_SLOW %s — still running in the background, returning 200",
            _payment_update_info(update),
        )
        await _alert_payment_update(
            update, "timeout",
            f"processing took >{_HANDLER_TIMEOUT:.0f}s; it keeps running in the background "
            "(not cancelled) — a second alert follows only if it fails",
        )


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    # Validate secret token FIRST (before heartbeat — reject unauthorized requests early)
    if not config.WEBHOOK_SECRET:
        logger.error("WEBHOOK_SECRET not configured")
        return Response(status_code=503)

    if not hmac.compare_digest(
        (x_telegram_bot_api_secret_token or "").encode(),
        config.WEBHOOK_SECRET.encode(),
    ):
        logger.warning(
            "WEBHOOK_SECRET_MISMATCH ip=%s",
            request.client.host if request.client else "unknown"
        )
        return Response(status_code=403)

    # Update liveness heartbeat AFTER validation (only for authenticated requests)
    global last_webhook_update_at
    last_webhook_update_at = time.monotonic()

    # SECURITY: Reject oversized request bodies (DDoS / memory exhaustion protection)
    # Telegram updates are typically < 10 KB; 1 MB is a generous upper bound.
    MAX_BODY_SIZE = 1 * 1024 * 1024  # 1 MB
    content_length = request.headers.get("content-length")
    try:
        if content_length and int(content_length) > MAX_BODY_SIZE:
            logger.warning(
                "WEBHOOK_BODY_TOO_LARGE ip=%s content_length=%s",
                request.client.host if request.client else "unknown",
                content_length,
            )
            return Response(status_code=413)
    except (ValueError, TypeError):
        logger.warning(
            "WEBHOOK_INVALID_CONTENT_LENGTH ip=%s content_length=%s",
            request.client.host if request.client else "unknown",
            content_length,
        )
        return Response(status_code=400)

    # Parse and feed update to aiogram with timeout
    try:
        body = await request.json()
        update = Update.model_validate(body)
        logger.debug("WEBHOOK_UPDATE update_id=%s", update.update_id)

        if _is_payment_update(update):
            # P1-6: never cancel payment finalization (shielded; alerts inside)
            await _feed_payment_update(update)
            return Response(status_code=200)

        # Wrap handler execution with timeout (25s — Railway request timeout is 30s)
        try:
            await asyncio.wait_for(
                _dp.feed_webhook_update(_bot, update),
                timeout=_HANDLER_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.error(
                "WEBHOOK_HANDLER_TIMEOUT update_id=%s — returning 200 to prevent retry",
                update.update_id
            )
            # Return 200 anyway — prevents Telegram from retrying
            return Response(status_code=200)
    except Exception as e:
        logger.error("WEBHOOK_PROCESSING_ERROR error=%s", e)
        # Return 200 anyway — prevents Telegram from retrying a bad update
        return Response(status_code=200)

    return Response(status_code=200)
