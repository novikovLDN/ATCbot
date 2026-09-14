"""
Platega.io (SBP) Integration

Handles SBP payment creation and webhook processing.
Configuration: merchant_id/secret/API URL resolved via config.py only.
"""
import config
import database
import hmac
import json
import logging
import math
from typing import Optional, Dict, Any
import httpx
from aiogram import Bot
from app.services.payments.confirmation import TransientPaymentError
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

# Configuration — single source: config.py
PLATEGA_MERCHANT_ID = config.PLATEGA_MERCHANT_ID
PLATEGA_SECRET = config.PLATEGA_SECRET
PLATEGA_API_URL = config.PLATEGA_API_URL


def is_enabled() -> bool:
    """Check if Platega is configured (merchant_id + secret)."""
    return bool(PLATEGA_MERCHANT_ID and PLATEGA_SECRET)


def _get_headers() -> Dict[str, str]:
    """Get authentication headers for Platega API."""
    return {
        "X-MerchantId": PLATEGA_MERCHANT_ID,
        "X-Secret": PLATEGA_SECRET,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


PAYMENT_METHOD_SBP = 2
PAYMENT_METHOD_CARD = 11
PAYMENT_METHOD_INTL = 12
# paymentMethod=6 (рекуррентная СБП-подписка) отключён — см. _handle_subscription_callback.


def _safe_redirect_urls() -> tuple[str, str]:
    """Return (success_url, fail_url) that ALWAYS resolve to a public HTTPS
    endpoint, even if PUBLIC_BASE_URL is empty.

    Platega docs list `return` and `failedUrl` as REQUIRED — omitting them
    causes 400.  Fallback chain:
      1. PUBLIC_BASE_URL + /payment/{success,fail}   (production preference)
      2. https://t.me/{BOT_USERNAME}                (all Telegram-hosted
                                                     users end up back in the
                                                     chat with our bot; no
                                                     404, no cert issues)
    """
    base = (getattr(config, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    if base:
        return f"{base}/payment/success", f"{base}/payment/fail"
    bot_username = (getattr(config, "BOT_USERNAME", "") or "").lstrip("@").strip()
    fallback = f"https://t.me/{bot_username}" if bot_username else "https://t.me/telegram"
    return fallback, fallback


def _extract_amount(payment_details: Any, fallback: float = 0.0) -> float:
    """Parse Platega `paymentDetails` — сервер иногда шлёт строку
    ("100 RUB"), иногда объект ({"amount": 100, "currency": "RUB"}).
    Возвращаем сумму в рублях float, при неудаче — fallback."""
    if isinstance(payment_details, dict):
        try:
            return float(payment_details.get("amount") or fallback)
        except (TypeError, ValueError):
            return fallback
    if isinstance(payment_details, (int, float)):
        return float(payment_details)
    if isinstance(payment_details, str):
        # "100 RUB" / "100.5 RUB"
        for tok in payment_details.split():
            try:
                return float(tok)
            except ValueError:
                continue
    return fallback


def _extract_currency(body: Dict[str, Any]) -> Optional[str]:
    """Валюта callback'а: плоское `currency` (формат из доки §4), затем
    `paymentDetails` (dict {currency} или строка "100 RUB").
    None — валюта в callback'е не пришла."""
    cur = body.get("currency")
    details = body.get("paymentDetails")
    if not cur and isinstance(details, dict):
        cur = details.get("currency")
    if not cur and isinstance(details, str):
        for tok in details.split():
            if tok.isalpha():
                cur = tok
                break
    return str(cur).strip().upper() if cur else None


def _verify_auth(headers: dict) -> bool:
    """Проверить статические креды Platega в заголовках X-MerchantId / X-Secret.

    Fail-closed: если серверные креды не заданы — False (иначе пустые
    заголовки прошли бы сравнение с пустой строкой). Регистр ключей любой.
    """
    if not PLATEGA_MERCHANT_ID or not PLATEGA_SECRET:
        logger.error("Platega webhook: server credentials not configured")
        return False
    merchant_id = headers.get("x-merchantid", "") or headers.get("X-MerchantId", "")
    secret = headers.get("x-secret", "") or headers.get("X-Secret", "")
    if not hmac.compare_digest(str(merchant_id), str(PLATEGA_MERCHANT_ID)) or \
       not hmac.compare_digest(str(secret), str(PLATEGA_SECRET)):
        logger.warning("Platega webhook: auth failed")
        return False
    return True


def _auth_failure_detail(headers: dict, body: Any) -> Dict[str, Any]:
    """`unauthorized` result with a reason for the admin alert (no secrets).
    payment_webhook answers 500 (Platega retries) and alerts (P1-4)."""
    if not PLATEGA_MERCHANT_ID or not PLATEGA_SECRET:
        why = "server PLATEGA_MERCHANT_ID / PLATEGA_SECRET not configured"
    elif not (headers.get("x-merchantid") or headers.get("X-MerchantId")) or \
            not (headers.get("x-secret") or headers.get("X-Secret")):
        why = "X-MerchantId / X-Secret header missing"
    else:
        why = "X-MerchantId / X-Secret do not match PLATEGA_MERCHANT_ID / PLATEGA_SECRET"
    b = body if isinstance(body, dict) else {}
    tx = b.get("id") or b.get("transactionId") or b.get("Id")
    st = b.get("status") or b.get("Status")
    return {"status": "unauthorized",
            "_detail": f"Platega auth failed: {why}; tx={tx} status={st} (body not verified)"}


def _is_subscription_callback(body: Dict[str, Any]) -> bool:
    """Callback по подписке (docs/providers/platega_api.md §6.5): ключи
    UpperCamel, есть `SubscriptionId` или `Status` = SUBSCRIPTION_*."""
    if body.get("SubscriptionId"):
        return True
    return str(body.get("Status") or "").upper().startswith("SUBSCRIPTION_")


# Покупки магазина (docs/audit/SCOPE.md, «магазин как есть»): новые проверки
# суммы/валюты к ним НЕ применяем. Классификация — как в ветке
# mark_pending_purchase_paid в confirmation.process_confirmed_payment, но без
# proxy: proxy не входит в магазин по SCOPE, для него проверки действуют.
_SHOP_PURCHASE_TYPES = ("telegram_stars", "telegram_premium", "steam", "spotify")
_SHOP_TARIFF_PREFIXES = ("apple_id_", "steam_", "spotify_")


def _is_shop_purchase(pending: Dict[str, Any]) -> bool:
    purchase_type = pending.get("purchase_type") or "subscription"
    tariff = pending.get("tariff") or ""
    return purchase_type in _SHOP_PURCHASE_TYPES or tariff.startswith(_SHOP_TARIFF_PREFIXES)


def _webhook_bot() -> Optional[Bot]:
    """Bot, сохранённый роутом вебхуков при старте (payment_webhook.setup)."""
    try:
        from app.api import payment_webhook
        return payment_webhook._bot
    except Exception:  # noqa: BLE001
        return None


async def _alert_admin(bot: Optional[Bot], message: str, *, force: bool = True) -> None:
    """Алерт админу (категория payment). Никогда не роняет вызывающий код."""
    if bot is None:
        logger.error("PLATEGA_ADMIN_ALERT_NO_BOT: %s", message.replace("\n", " | "))
        return
    try:
        from app.services.admin_alerts import send_alert
        await send_alert(bot, "payment", message, force=force)
    except Exception as e:  # noqa: BLE001
        logger.error("platega_admin_alert_failed: %s", e)


def _apply_markup(price_kopecks: int, percent: int) -> int:
    if percent <= 0:
        return price_kopecks
    return math.ceil(price_kopecks * (1 + percent / 100.0))


def apply_sbp_markup(price_kopecks: int) -> int:
    return _apply_markup(price_kopecks, config.SBP_MARKUP_PERCENT)


def apply_card_markup(price_kopecks: int) -> int:
    return _apply_markup(price_kopecks, config.PLATEGA_CARD_MARKUP_PERCENT)


def apply_intl_markup(price_kopecks: int) -> int:
    return _apply_markup(price_kopecks, config.PLATEGA_INTL_MARKUP_PERCENT)


async def create_transaction(
    amount_rubles: float,
    description: str,
    purchase_id: str,
    return_url: Optional[str] = None,
    failed_url: Optional[str] = None,
    method: int = PAYMENT_METHOD_SBP,
    telegram_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create a Platega payment transaction.

    Args:
        amount_rubles: Payment amount in rubles (already with markup applied)
        description: Payment description
        purchase_id: Internal purchase ID (stored in payload)
        return_url: Redirect URL after successful payment (auto-fallback if None)
        failed_url: Redirect URL after failed payment (auto-fallback if None)
        method: Platega paymentMethod (2=SBP, 11=Card, 12=International)
        telegram_id: Buyer's Telegram ID for `metadata.userId` (антифрод —
                    Platega может выключить магазин, если поле отсутствует
                    для категорий, где его требуют).

    Returns:
        {"transaction_id": str, "redirect_url": str}

    Raises:
        Exception on API errors
    """
    if not is_enabled():
        raise Exception("Platega not configured")

    # Гарантированные redirect URL — Platega помечает их как REQUIRED,
    # без них будет 400.
    _fb_ok, _fb_fail = _safe_redirect_urls()
    _return_url = return_url or _fb_ok
    _failed_url = failed_url or _fb_fail

    request_body: Dict[str, Any] = {
        "paymentMethod": method,
        # ВАЖНО: не передаём поле `id` — Platega docs, rule #1 (генерирует
        # сама).  Раньше слали random UUID, работало по инерции.
        "paymentDetails": {
            "amount": round(amount_rubles, 2),
            "currency": "RUB",
        },
        "description": description[:250] if description else "Atlas Secure VPN",
        "payload": json.dumps({"purchase_id": purchase_id}),
        "return": _return_url,
        "failedUrl": _failed_url,
    }
    if telegram_id is not None:
        # metadata.userId — обязателен для магазинов ряда категорий
        # (иначе антифрод отключается + возможна блокировка магазина).
        request_body["metadata"] = {"userId": str(telegram_id)}

    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{PLATEGA_API_URL}/transaction/process",
                headers=_get_headers(),
                json=request_body,
            )
            if 400 <= response.status_code < 500:
                logger.error(
                    f"Platega API client error: status={response.status_code}, "
                    f"response={response.text[:300]}"
                )
                raise Exception(f"Platega API error: {response.status_code}")
            if response.status_code != 200:
                response.raise_for_status()
            return response

    response = await retry_async(
        _make_request,
        retries=2,
        base_delay=1.0,
        max_delay=5.0,
        retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError),
    )

    data = response.json()
    transaction_id = data.get("transactionId")
    redirect_url = data.get("redirect")

    if not transaction_id or not redirect_url:
        raise Exception(f"Invalid Platega response: missing transactionId or redirect. Response: {data}")

    logger.info(
        f"Platega transaction created: transaction_id={transaction_id}, "
        f"amount={amount_rubles} RUB, purchase_id={purchase_id}, method={method}"
    )

    return {
        "transaction_id": transaction_id,
        "redirect_url": redirect_url,
    }


async def process_webhook_data(headers: dict, body: dict, bot: Bot) -> dict:
    """
    Process Platega webhook data (framework-agnostic).

    Args:
        headers: Request headers dict
        body: Parsed JSON body
        bot: Bot instance for sending messages

    Returns:
        Response dict with "status" key

    На общий callback URL приходят три типа тела (docs/providers/platega_api.md
    §6.5): разовый платёж (lowerCamel) и два типа по подпискам (UpperCamel,
    `SubscriptionId` / `Status`=SUBSCRIPTION_*) — последние после той же
    проверки заголовков уходят в _handle_subscription_callback (рекуррент
    отключён: только алерт админу, ничего не выдаётся).
    """
    if not database.DB_READY:
        logger.warning("Platega webhook: DB not ready — returning 500 for retry")
        raise TransientPaymentError("DB not ready")

    if not _verify_auth(headers):
        return _auth_failure_detail(headers, body)

    if _is_subscription_callback(body):
        return await _handle_subscription_callback(body, bot)

    transaction_id = body.get("id") or body.get("transactionId")
    status = (body.get("status") or "").lower()

    logger.info(
        f"Platega webhook received: transaction_id={transaction_id}, status={status}"
    )

    if status == "chargebacked":
        return await _handle_chargeback(body, transaction_id, bot)

    # Only process confirmed/completed payments
    if status not in ("confirmed", "completed", "paid"):
        logger.info(f"Platega webhook: ignoring status={status}")
        return {"status": "ignored"}

    # Delegate to shared confirmation logic
    from app.services.payments.confirmation import (
        extract_purchase_id, lookup_pending_purchase, process_confirmed_payment,
    )

    payload_raw = body.get("payload")
    purchase_id = extract_purchase_id(payload_raw)

    if not purchase_id:
        logger.error(f"Platega webhook: could not extract purchase_id, payload={payload_raw}")
        return {"status": "invalid",
                "_detail": f"Platega paid callback (status={status}) without purchase_id in payload; tx={transaction_id}"}

    lookup = await lookup_pending_purchase("platega", purchase_id)
    if lookup["status"] != "ok":
        return lookup

    pending_purchase = lookup["purchase"]
    telegram_id = lookup["telegram_id"]

    # Get payment amount — Platega возвращает paymentDetails то строкой
    # ("100 RUB"), то dict'ом ({amount, currency}); также fallback на
    # плоское поле `amount` (некоторые webhook-варианты).
    raw_amount = _extract_amount(
        body.get("paymentDetails"),
        fallback=float(body.get("amount") or 0),
    )
    expected_amount = pending_purchase["price_kopecks"] / 100.0

    # Магазин — «как есть» (SCOPE.md): сумма и валюта по-старому. Для
    # VPN-покупок callback без суммы или не в RUB не засчитываем (раньше
    # подставлялась ожидаемая цена) — алерт админу, без 500 (ретрай не поможет).
    is_shop = _is_shop_purchase(pending_purchase)
    if not is_shop:
        currency = _extract_currency(body)
        if currency is not None and currency != "RUB":
            return await _reject_one_off_callback(
                bot, purchase_id=purchase_id, transaction_id=transaction_id,
                telegram_id=telegram_id, reason=f"currency {currency}, expected RUB",
                raw_amount=raw_amount, expected_amount=expected_amount,
            )
    if raw_amount <= 0 and not is_shop:
        return await _reject_one_off_callback(
            bot, purchase_id=purchase_id, transaction_id=transaction_id,
            telegram_id=telegram_id, reason="amount missing or zero",
            raw_amount=raw_amount, expected_amount=expected_amount,
        )
    if raw_amount <= 0:
        logger.warning(
            f"Platega webhook: amount missing or zero, using stored price. "
            f"purchase_id={purchase_id}, raw_amount={raw_amount}, expected={expected_amount}"
        )
        amount_rubles = expected_amount
    elif abs(raw_amount - expected_amount) > 1.0:
        logger.warning(
            f"Platega webhook: amount mismatch. purchase_id={purchase_id}, "
            f"webhook_amount={raw_amount}, expected={expected_amount}"
        )
        amount_rubles = raw_amount
    else:
        amount_rubles = raw_amount

    logger.info(
        f"payment_event_received: provider=platega, user={telegram_id}, "
        f"transaction_id={transaction_id}, purchase_id={purchase_id}, "
        f"amount={amount_rubles:.2f} RUB"
    )

    return await process_confirmed_payment(
        provider="platega",
        purchase_id=purchase_id,
        amount_rubles=amount_rubles,
        invoice_id=str(transaction_id),
        telegram_id=telegram_id,
        bot=bot,
    )


async def _reject_one_off_callback(
    bot: Bot,
    *,
    purchase_id: str,
    transaction_id: Any,
    telegram_id: int,
    reason: str,
    raw_amount: float,
    expected_amount: float,
) -> dict:
    """CONFIRMED-callback по VPN-покупке с невалидной суммой/валютой: не
    засчитываем, принудительный алерт. Ответ 200 со статусом rejected."""
    logger.error(
        "PLATEGA_CALLBACK_REJECTED: purchase_id=%s tx=%s user=%s reason=%s "
        "raw_amount=%s expected=%.2f",
        purchase_id, transaction_id, telegram_id, reason, raw_amount, expected_amount,
    )
    await _alert_admin(bot, (
        "Platega: CONFIRMED callback REJECTED, payment not credited\n"
        f"Purchase: {purchase_id}\n"
        f"Transaction: {transaction_id}\n"
        f"User TG ID: {telegram_id}\n"
        f"Reason: {reason}\n"
        f"Callback amount: {raw_amount}\n"
        f"Expected: {expected_amount:.2f} RUB\n"
        "Check the transaction in Platega (GET /transaction/{id}); grant manually if the payment is real."
    ))
    return {"status": "rejected", "reason": reason, "purchase_id": purchase_id}


async def _handle_chargeback(body: dict, transaction_id: Any, bot: Bot) -> dict:
    """CHARGEBACKED — возврат по транзакции. Решение владельца (SCOPE.md):
    лог + принудительный алерт админу, доступ автоматически НЕ отзываем."""
    from app.services.payments.confirmation import extract_purchase_id

    purchase_id = extract_purchase_id(body.get("payload"))
    try:
        amount = _extract_amount(
            body.get("paymentDetails"), fallback=float(body.get("amount") or 0),
        )
    except (TypeError, ValueError):
        amount = 0.0
    currency = _extract_currency(body) or "?"
    logger.error(
        "PLATEGA_CHARGEBACK: purchase_id=%s tx=%s amount=%.2f %s",
        purchase_id, transaction_id, amount, currency,
    )
    await _alert_admin(bot, (
        "Platega CHARGEBACK (refund)\n"
        f"Purchase: {purchase_id or '-'}\n"
        f"Transaction: {transaction_id}\n"
        f"Amount: {amount:.2f} {currency}\n"
        "Access was NOT revoked automatically, decide manually."
    ))
    return {"status": "ok", "event": "chargeback", "purchase_id": purchase_id}


async def check_transaction_status(transaction_id: str) -> Optional[Dict[str, Any]]:
    """GET /transaction/{id} — статус одноразовой транзакции.

    Симметричный аналог wata_service.check_link_status: fallback для
    восстановления, когда webhook не дошёл.  Возвращает None на 404 /
    сетевые ошибки — вызывающий пусть решает, ретрайть ли позже.
    """
    if not is_enabled() or not transaction_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{PLATEGA_API_URL}/transaction/{transaction_id}",
                headers=_get_headers(),
            )
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                logger.warning(
                    "platega_check_transaction: status=%d body=%s",
                    resp.status_code, resp.text[:300],
                )
                return None
            return resp.json()
    except Exception as e:
        logger.error("platega_check_transaction error tx_id=%s: %s", transaction_id, e)
        return None


# ═════════════════════════════════════════════════════════════════════
# Рекуррентные СБП-подписки (paymentMethod=6) — ОТКЛЮЧЕНЫ (решение владельца).
# Код создания/списаний удалён. В Platega могли остаться подписки из беты,
# поэтому их callback'и (общий URL и /webhooks/platega-subscription) не
# теряем: только лог + payment_errors + принудительный алерт админу, 200.
# Доступ/деньги из этих callback'ов НЕ выдаём и в platega_subscriptions НЕ пишем.
# ═════════════════════════════════════════════════════════════════════

_RECURRING_DISABLED_INSTRUCTION = (
    "Рекуррентные подписки отключены. Отмените подписку в кабинете Platega "
    "(POST /subscription/{id}/cancel) и при необходимости сделайте "
    "возврат/выдачу вручную."
)


async def process_subscription_webhook_data(
    headers: dict, body: dict, bot: Bot,
) -> dict:
    """Точка входа отдельного роута /webhooks/platega-subscription (и алиаса).

    Проверки те же, что у process_webhook_data (DB_READY → 500 для ретрая,
    X-MerchantId/X-Secret → unauthorized); дальше — только алерт.
    """
    if not database.DB_READY:
        logger.warning("Platega sub webhook: DB not ready — 500 for retry")
        raise TransientPaymentError("DB not ready")

    if not _verify_auth(headers):
        return _auth_failure_detail(headers, body)

    return await _handle_subscription_callback(body, bot)


def _payload_telegram_id(payload_raw: Any) -> Optional[int]:
    """telegram_id из Payload подписки (так его клал прежний create_subscription)."""
    try:
        if not payload_raw:
            return None
        p = json.loads(payload_raw) if isinstance(payload_raw, str) else dict(payload_raw)
        return int(p.get("telegram_id") or 0) or None
    except Exception:  # noqa: BLE001
        return None


async def _handle_subscription_callback(body: dict, bot: Bot) -> dict:
    """Callback по рекуррентной подписке (UpperCamel: Id, SubscriptionId,
    Status, Amount, NextChargeAt, Payload). Вызывать ТОЛЬКО после _verify_auth.

    Рекуррент отключён: ничего не выдаём и не пишем в platega_subscriptions —
    лог + строка payment_errors + принудительный алерт админу. Ответ 200
    (ретраи Platega ситуацию не исправят; действие — за админом).
    """
    charge_id = body.get("Id") or body.get("id")
    subscription_id = body.get("SubscriptionId") or body.get("subscriptionId")
    status = str(body.get("Status") or body.get("status") or "").strip().upper()
    amount_raw = body.get("Amount") if body.get("Amount") is not None else body.get("amount")
    payload_raw = body.get("Payload") if body.get("Payload") is not None else body.get("payload")
    try:
        amount = float(amount_raw) if amount_raw is not None else None
    except (TypeError, ValueError):
        amount = None
    currency = body.get("Currency") or body.get("currency") or "RUB"

    # Кто подписан — только для алерта: Payload, затем read-only строка из БД.
    tg_id = _payload_telegram_id(payload_raw)
    if tg_id is None and subscription_id:
        try:
            from database import platega_subscriptions as _psub_db
            row = await _psub_db.get_subscription(str(subscription_id))
            if row and row.get("telegram_id"):
                tg_id = int(row["telegram_id"])
        except Exception as e:  # noqa: BLE001
            logger.warning("platega_sub_disabled: subscription lookup failed: %s", e)

    logger.error(
        "PLATEGA_RECURRING_DISABLED_CALLBACK: sub_id=%s charge_id=%s status=%s amount=%s tg=%s",
        subscription_id, charge_id, status, amount_raw, tg_id,
    )

    try:
        await database.log_payment_error(
            stage="platega_recurring_disabled",
            telegram_id=tg_id,
            payment_provider="platega",
            amount_rubles=amount,
            error_code=status or None,
            error_message=(
                f"recurring disabled: subscription={subscription_id} charge={charge_id} "
                f"status={status}; nothing granted"
            ),
            raw_payload=body if isinstance(body, dict) else None,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("platega_sub_disabled: payment_errors log failed: %s", e)

    money_line = (
        "⚠️ Деньги СПИСАНЫ, доступ НЕ выдан.\n" if status == "CONFIRMED" else ""
    )
    amount_str = f"{amount:.2f} {currency}" if amount is not None else "-"
    await _alert_admin(bot, (
        "Platega: callback по РЕКУРРЕНТНОЙ подписке (функция удалена)\n"
        f"SubscriptionId: {subscription_id or '-'}\n"
        f"Charge Id: {charge_id or '-'}\n"
        f"Status: {status or '-'}\n"
        f"Amount: {amount_str}\n"
        f"User TG ID: {tg_id or '-'}\n"
        f"{money_line}"
        f"{_RECURRING_DISABLED_INSTRUCTION}"
    ), force=True)

    return {
        "status": "ok",
        "event": "recurring_disabled",
        "subscription_id": str(subscription_id) if subscription_id else None,
    }