"""
Platega.io (SBP) Integration

Handles SBP payment creation and webhook processing.
Configuration: merchant_id/secret/API URL resolved via config.py only.
"""
import asyncio
import config
import database
import hmac
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from uuid import uuid4
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
# No fixed method: POST /v2/transaction/process, the payer picks it on Platega's page.
PAYMENT_METHOD_ANY = None
PAYMENT_METHOD_SUBSCRIPTION = 6

# SubscriptionInterval values (Platega spec)
SUBSCRIPTION_INTERVAL_DAY = 1
SUBSCRIPTION_INTERVAL_WEEK = 2
SUBSCRIPTION_INTERVAL_MONTH = 3
SUBSCRIPTION_INTERVAL_YEAR = 4

# Период тарифа → (interval, intervalCount) рекуррентной подписки.
# Доку §2.4: interval=3 — месяц (30 дней), intervalCount ≤ 12; interval=4 —
# год, intervalCount ≤ 3. Годовые периоды берём годом, а не 12×месяц: 12
# месяцев по 30 дней — это 360, а тариф продаётся как 365.
SUBSCRIPTION_PLANS: Dict[int, tuple] = {
    30:  (SUBSCRIPTION_INTERVAL_MONTH, 1),
    90:  (SUBSCRIPTION_INTERVAL_MONTH, 3),
    180: (SUBSCRIPTION_INTERVAL_MONTH, 6),
    365: (SUBSCRIPTION_INTERVAL_YEAR, 1),
    730: (SUBSCRIPTION_INTERVAL_YEAR, 2),
}


def subscription_plan_for(period_days) -> Optional[tuple]:
    """(interval, intervalCount) для периода тарифа, или None — если период
    рекуррентом не покрывается; вызывающий тогда ведёт на разовую оплату."""
    try:
        return SUBSCRIPTION_PLANS.get(int(period_days))
    except (TypeError, ValueError):
        return None


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


def is_subscription_visible_to(telegram_id: int) -> bool:
    """СБП-подписка теперь доступна всем юзерам — MVP-guard снят.

    Условие показа кнопки — только настроенность Platega (merchant_id
    + secret).  Возвращаемый True гарантирует, что create_subscription
    сможет реально дёрнуть API.
    """
    if not is_enabled():
        return False
    return True


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
    method: Optional[int] = PAYMENT_METHOD_SBP,
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
        method: Platega paymentMethod (2=SBP, 11=Card, 12=International);
                PAYMENT_METHOD_ANY (None) → /v2/transaction/process, the payer
                picks the method (the link comes back in `url`, not `redirect`)
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
    if method is PAYMENT_METHOD_ANY:
        path = "/v2/transaction/process"
    else:
        path = "/transaction/process"
        request_body["paymentMethod"] = method

    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{PLATEGA_API_URL}{path}",
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
    redirect_url = data.get("redirect") or data.get("url")   # v2 answers `url`

    if not transaction_id or not redirect_url:
        raise Exception(f"Invalid Platega response: missing transactionId or redirect. Response: {data}")

    logger.info(
        f"Platega transaction created: transaction_id={transaction_id}, "
        f"amount={amount_rubles} RUB, purchase_id={purchase_id}, "
        f"method={'any' if method is PAYMENT_METHOD_ANY else method}"
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
    `SubscriptionId` / `Status`=SUBSCRIPTION_*) — последние делегируются в
    _handle_subscription_callback после той же проверки заголовков.
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


# ═════════════════════════════════════════════════════════════════════
# Рекуррентные СБП-подписки (paymentMethod=6, migration 074)
# ═════════════════════════════════════════════════════════════════════

async def create_subscription(
    amount_rubles: float,
    interval: int,
    description: str,
    *,
    interval_count: int = 1,
    telegram_id: int,
    tariff_type: str,
    period_days: int,
    return_url: Optional[str] = None,
    failed_url: Optional[str] = None,
    bot: Optional[Bot] = None,
    is_combo: bool = False,
) -> Dict[str, Any]:
    """Создать рекуррентную СБП-подписку через Platega.

    POST /transaction/process с paymentMethod=6:
      Ответ.transactionId == subscription_id (не путать с id разового
      списания). После первого редиректа юзера по `redirect` (окно 30
      мин на привязку счёта в приложении банка) Platega начнёт слать
      callback'и на списания с ЗАГЛАВНЫМИ ключами (см.
      process_subscription_webhook_data).

    Args:
        amount_rubles: сумма ОДНОГО списания в рублях.
        interval: SubscriptionInterval — 1/2/3/4 (день/неделя/месяц/год).
                  MVP: обычно 3.
        description: показывается юзеру в форме привязки/приложении банка.
        telegram_id: юзер для payload/metadata (мостик для webhook'а).
        tariff_type: 'basic'/'plus'/... — на что подписан юзер.
        period_days: сколько дней VPN давать за одно списание (30/90/365).
        return_url, failed_url: куда редиректить после успех/фейл.
        bot: для алерта админу; по умолчанию — bot вебхук-роута.
        is_combo: подписка на combo-тариф (tariff_type при этом basic/plus).
                  Пишется в platega_subscriptions.is_combo (migration 082, если
                  применена) — по нему списания выдают combo-ГБ (T11).

    Returns:
        {"subscription_id": str, "redirect_url": str}

    Raises:
        Exception: сеть/HTTP-ошибка/невалидный ответ, а также НЕ удалось
        сохранить subscription_id в БД (ссылку юзеру отдавать нельзя).
        Ловить в handler'е.
    """
    if not is_enabled():
        raise Exception("Platega not configured")

    if interval not in (
        SUBSCRIPTION_INTERVAL_DAY,
        SUBSCRIPTION_INTERVAL_WEEK,
        SUBSCRIPTION_INTERVAL_MONTH,
        SUBSCRIPTION_INTERVAL_YEAR,
    ):
        raise ValueError(f"Invalid SubscriptionInterval: {interval}")

    # Доку §2.4: день ≤ 31, неделя ≤ 4, месяц ≤ 12, год ≤ 3. Превышение
    # Platega отклонит 400 — ловим здесь, чтобы юзер не ждал редиректа.
    max_count = {
        SUBSCRIPTION_INTERVAL_DAY: 31,
        SUBSCRIPTION_INTERVAL_WEEK: 4,
        SUBSCRIPTION_INTERVAL_MONTH: 12,
        SUBSCRIPTION_INTERVAL_YEAR: 3,
    }[interval]
    if not 1 <= int(interval_count) <= max_count:
        raise ValueError(
            f"intervalCount={interval_count} out of range for interval={interval} (1..{max_count})"
        )

    # payload: наш «мостик» — вернётся в callback'ах и позволит нам
    # восстановить telegram_id/tariff/days без похода в БД. Кладём как
    # JSON-строку (Platega возвращает её обратно нетронутой).
    payload_str = json.dumps({
        "telegram_id": int(telegram_id),
        "tariff": str(tariff_type),
        "days": int(period_days),
    })

    # Гарантированные redirect URL (Platega помечает как REQUIRED).
    _fb_ok, _fb_fail = _safe_redirect_urls()
    request_body: Dict[str, Any] = {
        "paymentMethod": PAYMENT_METHOD_SUBSCRIPTION,
        "paymentDetails": {
            "amount": round(float(amount_rubles), 2),
            "currency": "RUB",
            "interval": int(interval),
            # Доку §6.1: поле передаётся явно. Один цикл списания =
            # interval × intervalCount (90 дней = месяц × 3).
            "intervalCount": int(interval_count),
        },
        "description": (description or "Atlas Secure VPN — подписка")[:250],
        "payload": payload_str,
        "metadata": {"userId": str(telegram_id)},
        "return": return_url or _fb_ok,
        "failedUrl": failed_url or _fb_fail,
    }

    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{PLATEGA_API_URL}/transaction/process",
                headers=_get_headers(),
                json=request_body,
            )
            if 400 <= response.status_code < 500:
                logger.error(
                    "Platega subscription create client error: status=%d resp=%s",
                    response.status_code, response.text[:400],
                )
                raise Exception(
                    f"Platega subscription create failed: {response.status_code}"
                )
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
    subscription_id = data.get("transactionId")  # для paymentMethod=6 — это ID подписки
    redirect_url = data.get("redirect")

    if not subscription_id or not redirect_url:
        raise Exception(
            f"Invalid Platega subscription response: missing transactionId/redirect: {data}"
        )

    # Запись subscription_id в БД ОБЯЗАТЕЛЬНА: без строки в
    # platega_subscriptions списания этой подписки станут orphan (FK на
    # charges) — деньги спишутся, доступ не выдастся. Поэтому при ошибке БД
    # ссылку на привязку юзеру НЕ отдаём (handler покажет ошибку создания
    # платежа); непривязанная подписка сама уйдёт в Failed через 30 мин.
    from database import platega_subscriptions as _psub_db
    try:
        await _psub_db.create_subscription(
            subscription_id=str(subscription_id),
            telegram_id=int(telegram_id),
            amount_kopecks=int(round(float(amount_rubles) * 100)),
            interval_days=int(period_days),
            tariff_type=str(tariff_type),
            description=description,
            is_combo=bool(is_combo),
        )
    except Exception as db_err:
        logger.error(
            "platega_sub_db_persist_failed: sub_id=%s tg=%s err=%s — link withheld",
            subscription_id, telegram_id, db_err,
        )
        await _alert_admin(bot or _webhook_bot(), (
            "Platega subscription created but NOT saved to DB, link withheld from user\n"
            f"Subscription: {subscription_id}\n"
            f"User TG ID: {telegram_id}\n"
            f"Amount: {float(amount_rubles):.2f} RUB\n"
            f"Tariff: {tariff_type} ({period_days} days)\n"
            f"Error: {type(db_err).__name__}: {str(db_err)[:200]}\n"
            "Unbound subscription expires in 30 min (Failed). Any charge for it will arrive as orphan."
        ))
        raise RuntimeError(
            f"Platega subscription {subscription_id} persist failed: {db_err}"
        ) from db_err

    logger.info(
        "platega_subscription_created: sub_id=%s tg=%s amount=%.2f RUB "
        "interval=%s tariff=%s days=%s",
        subscription_id, telegram_id, amount_rubles, interval, tariff_type, period_days,
    )

    return {
        "subscription_id": str(subscription_id),
        "redirect_url": str(redirect_url),
    }


async def cancel_subscription(subscription_id: str) -> bool:
    """POST /subscription/{id}/cancel — остановить будущие списания (доку §6.4).

    Ручка идемпотентна: повтор по уже отменённой подписке даёт тот же ответ.
    Плательщик может отменить и сам, по ссылке из письма после списания —
    тогда придёт callback SUBSCRIPTION_CANCELLED и статус приедет сам.

    Возвращает True, только если Platega ответила 200 — то есть отмена
    подтверждена синхронно. Сама функция в БД не пишет: это решает
    вызывающий, и оба места (карточка в дашборде и /platega_sub_cancel)
    пишут статус ТОЛЬКО по True. На False не пишет никто — «отменили у
    себя, а у провайдера живо» означало бы списания у живого человека.
    Асинхронный SUBSCRIPTION_CANCELLED всё равно придёт и поставит то же.
    """
    if not is_enabled() or not subscription_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{PLATEGA_API_URL}/subscription/{subscription_id}/cancel",
                headers=_get_headers(),
            )
        if resp.status_code != 200:
            logger.warning(
                "platega_cancel_subscription: sub_id=%s status=%d body=%s",
                subscription_id, resp.status_code, resp.text[:300],
            )
            return False
        logger.info("platega_subscription_cancelled: sub_id=%s", subscription_id)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("platega_cancel_subscription error sub_id=%s: %s", subscription_id, e)
        return False


async def check_subscription_status(subscription_id: str) -> Optional[Dict[str, Any]]:
    """GET /subscription/{id} — детальный статус подписки.

    Полезно для диагностики (когда webhook'и SUBSCRIPTION_* не пришли)
    и админ-команд.

    ВАЖНО: детальная ручка возвращает `status`/`intervalUnit` СТРОКАМИ
    («Active», «Month») — в отличие от списочной, которая шлёт числами.
    Нормализуем к единому виду (строка) для консистентности.

    Returns:
        dict с полями подписки, None если 404 / ошибка.
    """
    if not is_enabled() or not subscription_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{PLATEGA_API_URL}/subscription/{subscription_id}",
                headers=_get_headers(),
            )
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                logger.warning(
                    "platega_check_subscription: status=%d body=%s",
                    resp.status_code, resp.text[:300],
                )
                return None
            return resp.json()
    except Exception as e:
        logger.error("platega_check_subscription error sub_id=%s: %s", subscription_id, e)
        return None


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


def _parse_next_charge_at(raw: Any) -> Optional[datetime]:
    """Разобрать NextChargeAt из webhook payload (ISO-8601 str)."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        # Platega шлёт вида "2026-08-09T09:10:00Z"
        s = str(raw).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception as e:
        logger.warning("platega_sub: failed to parse NextChargeAt=%r: %s", raw, e)
        return None


async def process_subscription_webhook_data(
    headers: dict, body: dict, bot: Bot,
) -> dict:
    """Точка входа отдельного роута /webhooks/platega-subscription (и алиаса).

    Те же тела приходят и на общий callback URL — там их распознаёт
    process_webhook_data и делегирует в _handle_subscription_callback.
    """
    if not database.DB_READY:
        logger.warning("Platega sub webhook: DB not ready — 500 for retry")
        raise TransientPaymentError("DB not ready")

    if not _verify_auth(headers):
        return _auth_failure_detail(headers, body)

    return await _handle_subscription_callback(body, bot)


def _charge_cadence(sub_row: Optional[Dict[str, Any]]) -> str:
    """«Каждый месяц» / «Каждые 3 месяца» — по периоду подписки.

    Было захардкожено «Каждый месяц» со времён MVP, где других периодов не
    существовало. Теперь их пять, и юзеру с годовой подпиской нельзя писать
    про месяц.
    """
    days = int((sub_row or {}).get("interval_days") or 30)
    return {
        30: "Каждый месяц",
        90: "Каждые 3 месяца",
        180: "Каждые 6 месяцев",
        365: "Каждый год",
        730: "Каждые 2 года",
    }.get(days, f"Каждые {days} дн.")


async def _safe_send(bot: Bot, chat_id, text: str, **kwargs):
    """Сообщение юзеру по правилу проекта — через safe_send_message.

    Прямой bot.send_message бросает на заблокировавшем бота пользователе, а
    мы шлём это из обработчика вебхука: исключение здесь превратилось бы в
    500, Platega повторила бы списание, и всё ради того, чтобы узнать, что
    юзер закрыл чат. safe_send_message гасит такое и помечает недоступного.
    """
    from app.utils.telegram_safe import safe_send_message
    return await safe_send_message(bot, chat_id, text, **kwargs)


async def _handle_subscription_callback(body: dict, bot: Bot) -> dict:
    """Обработать callback о списании / статусе подписки Platega (paymentMethod=6).

    Вызывать ТОЛЬКО после _verify_auth.

    Ключи в callback'е ЗАГЛАВНЫЕ: Id, Amount, Currency, Status,
    PaymentMethod, Payload, SubscriptionId, NextChargeAt.

    Отдельные события статуса подписки (SUBSCRIPTION_ACTIVATED /
    SUBSCRIPTION_PAST_DUE / SUBSCRIPTION_CANCELLED / SUBSCRIPTION_FAILED)
    приходят в поле Status без Amount — обрабатываем defensive.

    Идемпотентность списаний: PK на Id (charge_id) в
    platega_subscription_charges; CONFIRMED считается обработанным только
    после выдачи доступа (см. _process_confirmed_charge).
    Дубль → {"status": "duplicate"}.
    """
    # ── Extract fields (ЗАГЛАВНЫЕ + строчные fallback на всякий случай) ─
    charge_id       = body.get("Id") or body.get("id")
    subscription_id = body.get("SubscriptionId") or body.get("subscriptionId")
    status_raw      = str(body.get("Status") or body.get("status") or "").strip()
    amount_raw      = body.get("Amount") if body.get("Amount") is not None else body.get("amount")
    next_charge_raw = body.get("NextChargeAt") or body.get("nextChargeAt")
    payload_raw     = body.get("Payload") if body.get("Payload") is not None else body.get("payload")

    logger.info(
        "platega_sub_webhook: sub_id=%s charge_id=%s status=%s amount=%s next=%s",
        subscription_id, charge_id, status_raw, amount_raw, next_charge_raw,
    )

    if not subscription_id:
        logger.error("platega_sub_webhook: missing SubscriptionId, body=%s", str(body)[:400])
        return {"status": "invalid"}

    status_upper = status_raw.upper()
    from database import platega_subscriptions as _psub_db

    # ── Отдельное событие статуса подписки (без Amount / без Id-списания) ──
    if status_upper in ("SUBSCRIPTION_ACTIVATED", "SUBSCRIPTION_PAST_DUE",
                        "SUBSCRIPTION_CANCELLED", "SUBSCRIPTION_FAILED"):
        # Маппим на статусы platega_subscriptions.status
        status_map = {
            "SUBSCRIPTION_ACTIVATED": "Active",
            "SUBSCRIPTION_PAST_DUE":  "PastDue",
            "SUBSCRIPTION_CANCELLED": "Cancelled",
            "SUBSCRIPTION_FAILED":    "Failed",
        }
        new_status = status_map[status_upper]
        next_dt = _parse_next_charge_at(next_charge_raw)
        await _psub_db.update_subscription_status(
            subscription_id=str(subscription_id),
            status=new_status,
            next_charge_at=next_dt,
        )
        logger.info(
            "platega_sub_status_event: sub_id=%s new_status=%s",
            subscription_id, new_status,
        )

        # Уведомляем юзера о смене статуса подписки — важно, потому
        # что от привязки до первого списания может пройти время, и
        # юзер должен понимать, что происходит.
        try:
            sub_row_for_notify = await _psub_db.get_subscription(str(subscription_id))
            if sub_row_for_notify:
                _tg = int(sub_row_for_notify["telegram_id"])
                _amt = int(sub_row_for_notify.get("amount_kopecks") or 0) / 100.0
                if status_upper == "SUBSCRIPTION_ACTIVATED":
                    _text = (
                        "✅ <b>СБП-подписка активирована</b>\n\n"
                        "Счёт привязан, первое списание пройдёт в ближайшее время.\n"
                        f"Сумма: <b>{_amt:.2f} ₽</b> · {_charge_cadence(sub_row_for_notify)}.\n\n"
                        "Как только банк подтвердит списание — VPN автоматически "
                        "активируется. Уведомим сразу же."
                    )
                elif status_upper == "SUBSCRIPTION_PAST_DUE":
                    _text = (
                        "⚠️ <b>Не удалось списать по подписке</b>\n\n"
                        "Банк отклонил списание. Повторных попыток не будет — "
                        "автопродление остановлено. VPN работает до конца "
                        "оплаченного периода.\n\n"
                        "Чтобы продлить доступ, оплатите тариф заново или "
                        "напишите в поддержку."
                    )
                elif status_upper == "SUBSCRIPTION_CANCELLED":
                    _text = (
                        "❌ <b>СБП-подписка отменена</b>\n\n"
                        "Больше списаний не будет. VPN продолжает работать до "
                        "конца оплаченного периода — потом можно оформить снова."
                    )
                else:  # SUBSCRIPTION_FAILED
                    _text = (
                        "❌ <b>СБП-подписка не активировалась</b>\n\n"
                        "Привязка счёта не завершилась в течение 30 минут. "
                        "Попробуйте оформить подписку заново либо оплатите обычным СБП."
                    )
                try:
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(
                            text="🛡 Поддержка", url="https://t.me/atlas_suppbot",
                        )],
                    ])
                    await _safe_send(bot,
                        chat_id=_tg, text=_text,
                        reply_markup=kb, parse_mode="HTML",
                    )
                except Exception as _e:
                    logger.warning(
                        "platega_sub_status_notify_failed: tg=%s status=%s err=%s",
                        _tg, status_upper, _e,
                    )
        except Exception as _e:  # noqa: BLE001
            logger.warning(
                "platega_sub_status_lookup_failed: sub_id=%s err=%s",
                subscription_id, _e,
            )

        return {"status": "ok", "event": "status", "new_status": new_status}

    # ── Событие списания: должен быть Id (charge_id) ────────────────────
    if not charge_id:
        logger.error("platega_sub_webhook: missing Id (charge_id), body=%s", str(body)[:400])
        return {"status": "invalid"}

    # Amount может прийти в рублях (Platega спека) — храним в копейках.
    try:
        amount_rubles = float(amount_raw) if amount_raw is not None else 0.0
    except (TypeError, ValueError):
        amount_rubles = 0.0
    amount_kopecks = int(round(amount_rubles * 100))

    # ── Восстанавливаем telegram_id / tariff / days из БД подписки ──────
    sub_row = await _psub_db.get_subscription(str(subscription_id))
    if not sub_row:
        tg_id: Optional[int] = None
        tariff = "basic"
        period_days = 30
        try:
            if payload_raw:
                p = json.loads(payload_raw) if isinstance(payload_raw, str) else dict(payload_raw)
                tg_id = int(p.get("telegram_id") or 0) or None
                tariff = str(p.get("tariff") or "basic")
                period_days = int(p.get("days") or 30)
        except Exception as e:
            logger.warning("platega_sub_webhook: bad Payload: %s", e)
        # Подписки нет в нашей БД → charge не записать (FK), доступ
        # автоматически не выдаём. Списание могло быть реальным — принудительный
        # алерт. Без 500: ретраи Platega ситуацию не исправят.
        logger.error(
            "PLATEGA_SUB_ORPHAN_CHARGE: sub_id=%s charge_id=%s status=%s amount=%.2f tg=%s",
            subscription_id, charge_id, status_upper, amount_rubles, tg_id,
        )
        await _alert_admin(bot, (
            "Platega subscription charge for UNKNOWN subscription (orphan)\n"
            f"Subscription: {subscription_id}\n"
            f"Charge: {charge_id}\n"
            f"Status: {status_upper}\n"
            f"Amount: {amount_rubles:.2f} RUB\n"
            f"User TG ID (Payload): {tg_id or '-'}\n"
            f"Tariff/days (Payload): {tariff}/{period_days}\n"
            "Not in platega_subscriptions: nothing recorded, access NOT granted. Check manually."
        ))
        if not tg_id:
            return {"status": "error", "message": "no telegram_id"}
        return {"status": "ok", "event": "orphan_charge", "subscription_id": str(subscription_id)}

    tg_id = int(sub_row["telegram_id"])
    tariff = str(sub_row.get("tariff_type") or "basic")
    period_days = int(sub_row.get("interval_days") or 30)
    next_dt = _parse_next_charge_at(next_charge_raw)

    # ── CONFIRMED → продлеваем VPN + уведомляем ─────────────────────────
    if status_upper == "CONFIRMED":
        return await _process_confirmed_charge(
            bot,
            subscription_id=str(subscription_id),
            charge_id=str(charge_id),
            tg_id=tg_id,
            tariff=tariff,
            period_days=period_days,
            amount_rubles=amount_rubles,
            amount_kopecks=amount_kopecks,
            next_dt=next_dt,
            # is_combo: migration 082; NULL/нет колонки → не combo (§G3).
            is_combo=bool(sub_row.get("is_combo")),
            expected_amount_kopecks=sub_row.get("amount_kopecks"),
        )

    # ── CANCELED → PastDue + уведомление ────────────────────────────────
    if status_upper == "CANCELED":
        try:
            inserted = await _psub_db.record_charge(
                charge_id=str(charge_id),
                subscription_id=str(subscription_id),
                telegram_id=int(tg_id),
                amount_kopecks=amount_kopecks,
                status=status_upper,
            )
        except Exception as e:
            # Ошибка БД — не «дубль»: 500, Platega повторит callback.
            logger.error(
                "platega_sub_record_charge_failed: sub_id=%s charge_id=%s err=%s",
                subscription_id, charge_id, e,
            )
            raise TransientPaymentError(
                f"record_charge failed for platega charge {charge_id}: {e}"
            ) from e
        if not inserted:
            logger.info(
                "platega_sub_webhook: duplicate charge_id=%s (already processed) — noop",
                charge_id,
            )
            return {"status": "duplicate"}

        await _psub_db.update_subscription_status(
            subscription_id=str(subscription_id),
            status="PastDue",
            next_charge_at=next_dt,
        )
        try:
            await _safe_send(bot,
                chat_id=tg_id,
                text=(
                    "⚠️ <b>Списание не прошло</b>\n\n"
                    f"Не удалось списать <b>{amount_rubles:.2f} ₽</b> "
                    "по вашей СБП-подписке. Повторных попыток не будет — "
                    "автопродление остановлено.\n\n"
                    "VPN работает до конца оплаченного периода. Чтобы продлить "
                    "доступ, оплатите тариф заново или напишите в поддержку."
                ),
                parse_mode="HTML",
            )
        except Exception as notify_err:
            logger.warning(
                "platega_sub_canceled_notify_failed: tg=%s err=%s",
                tg_id, notify_err,
            )
        return {
            "status": "ok",
            "event": "charge_canceled",
            "subscription_id": str(subscription_id),
            "charge_id": str(charge_id),
        }

    # ── Прочие статусы — у списаний по доке их нет (только CONFIRMED /
    # CANCELED). В charges ничего не пишем: такая строка заблокировала бы
    # последующий CONFIRMED с тем же Id как «дубль».
    logger.info(
        "platega_sub_webhook: non-terminal status=%s, sub_id=%s charge_id=%s",
        status_upper, subscription_id, charge_id,
    )
    return {"status": "ok", "event": "noop", "raw_status": status_upper}


# Строка списания 'PROCESSING' старше этого считается брошенной (краш
# процесса посреди выдачи) и перезахватывается ретраем. Platega повторяет
# callback через 5 мин (docs/providers/platega_api.md §4); выдача занимает
# секунды. 4 мин < 5 мин — первый же ретрай подберёт брошенное списание.
_CHARGE_STALE_AFTER_SECONDS = 240.0

# T11: entrypoint of provisioning_flags / job source / grant_access source.
_RECURRING_ENTRYPOINT = "platega_recurring"
# Outcome of an apply task: another attempt already confirmed this charge.
_OUTCOME_DUPLICATE = "duplicate"


class _ChargeAlreadyConfirmed(Exception):
    """confirm_charge inside the tx found no 'PROCESSING' row: a parallel
    attempt (stale re-claim) committed first. Rolls back our grant."""

# Задачи выдачи по списаниям (см. _process_confirmed_charge). Держим ссылки:
# event loop хранит задачи слабо, незавершённую задачу мог бы собрать GC.
_INFLIGHT_CHARGE_TASKS: set = set()


def _on_charge_task_done(task: "asyncio.Task") -> None:
    _INFLIGHT_CHARGE_TASKS.discard(task)
    if not task.cancelled():
        # Ошибка уже залогирована и отправлена админу внутри задачи; забираем
        # её, чтобы не было «Task exception was never retrieved», если
        # ожидавший вебхук уже отвалился по таймауту.
        task.exception()


async def _process_confirmed_charge(
    bot: Bot,
    *,
    subscription_id: str,
    charge_id: str,
    tg_id: int,
    tariff: str,
    period_days: int,
    amount_rubles: float,
    amount_kopecks: int,
    next_dt: Optional[datetime],
    is_combo: bool = False,
    expected_amount_kopecks: Optional[int] = None,
) -> dict:
    """CONFIRMED-списание: деньги уже списаны → продлеваем VPN.

    Списание считается обработанным ТОЛЬКО после успешной выдачи. Раньше
    строка charge коммитилась ДО grant_access, и при сбое выдачи ретрай
    Platega считался дублем: деньги списаны, доступа нет (audit P0-D).
      claim_charge ('PROCESSING') → grant_access → confirm_charge ('CONFIRMED')
    Сбой выдачи → release_charge + алерт + 500 (Platega повторит).
    Ошибка БД → 500. Настоящий дубль ('CONFIRMED') → 200 без продления.

    USE_NEW_PROVISIONING on для "platega_recurring" (T11): после claim —
    _apply_confirmed_charge_outbox (одна tx: confirm + grant(defer) + enqueue).
    Иначе — прежний _apply_confirmed_charge.
    """
    from database import platega_subscriptions as _psub_db

    try:
        claim = await _psub_db.claim_charge(
            charge_id=charge_id,
            subscription_id=subscription_id,
            telegram_id=int(tg_id),
            amount_kopecks=amount_kopecks,
            stale_after_seconds=_CHARGE_STALE_AFTER_SECONDS,
        )
    except Exception as e:
        logger.error(
            "platega_sub_claim_failed: sub_id=%s charge_id=%s err=%s",
            subscription_id, charge_id, e,
        )
        await _alert_admin(bot, (
            "Platega subscription: DB error on PAID charge (500, Platega will retry)\n"
            f"Subscription: {subscription_id}\n"
            f"Charge: {charge_id}\n"
            f"User TG ID: {tg_id}\n"
            f"Amount: {amount_rubles:.2f} RUB\n"
            f"Error: {type(e).__name__}: {str(e)[:200]}"
        ), force=False)
        raise TransientPaymentError(
            f"claim_charge failed for platega charge {charge_id}: {e}"
        ) from e

    if claim == "done":
        logger.info(
            "platega_sub_webhook: duplicate charge_id=%s (already granted) — noop",
            charge_id,
        )
        return {"status": "duplicate"}
    if claim == "in_progress":
        logger.warning(
            "platega_sub_webhook: charge_id=%s is being processed — 500 for retry",
            charge_id,
        )
        raise TransientPaymentError(f"platega charge {charge_id} is being processed")
    if claim == "reclaimed":
        logger.error(
            "PLATEGA_SUB_STALE_CHARGE_RECLAIMED: sub_id=%s charge_id=%s tg=%s",
            subscription_id, charge_id, tg_id,
        )
        await _alert_admin(bot, (
            "Platega subscription: stale PROCESSING charge re-claimed "
            "(previous attempt crashed or hung)\n"
            f"Subscription: {subscription_id}\n"
            f"Charge: {charge_id}\n"
            f"User TG ID: {tg_id}\n"
            "Granting again: verify the previous attempt did not already extend access."
        ))

    apply_kwargs: Dict[str, Any] = dict(
        subscription_id=subscription_id,
        charge_id=charge_id,
        tg_id=tg_id,
        tariff=tariff,
        period_days=period_days,
        amount_rubles=amount_rubles,
        amount_kopecks=amount_kopecks,
        next_dt=next_dt,
    )
    # Сверка суммы — ОБЩАЯ для обоих путей выдачи (CLAUDE.md: «проверка
    # подписи + идемпотентность + валидация суммы — обязательно»). Раньше
    # она жила внутри outbox-варианта, и при выключенном флаге списание на
    # любую сумму выдавало полный период без единого слова админу.
    # Доступ всё равно выдаём: деньги уже списаны, отказ отнял бы у юзера
    # оплаченный период — но админ узнаёт об этом принудительным алертом.
    if expected_amount_kopecks is not None and int(amount_kopecks) != int(expected_amount_kopecks):
        logger.error(
            "PLATEGA_SUB_AMOUNT_MISMATCH: sub_id=%s charge_id=%s tg=%s charged_k=%s expected_k=%s",
            subscription_id, charge_id, tg_id, amount_kopecks, expected_amount_kopecks,
        )
        await _alert_admin(bot, (
            "Platega subscription: charge amount differs from the subscription amount\n"
            f"Subscription: {subscription_id}\n"
            f"Charge: {charge_id}\n"
            f"User TG ID: {tg_id}\n"
            f"Charged: {amount_kopecks / 100:.2f} RUB, expected: {int(expected_amount_kopecks) / 100:.2f} RUB\n"
            f"Tariff: {tariff}{' (combo)' if is_combo else ''} ({period_days} days)\n"
            "Access is granted as usual. Check the subscription in Platega."
        ))

    from app.services import provisioning_flags
    apply_kwargs.update(is_combo=is_combo, expected_amount_kopecks=expected_amount_kopecks)
    apply_fn = (
        _apply_confirmed_charge_outbox
        if provisioning_flags.is_on(_RECURRING_ENTRYPOINT)
        else _apply_confirmed_charge
    )

    # Выдача — в отдельной задаче под asyncio.shield: таймаут вебхука
    # (wait_for в роуте) отменяет только ожидание, а не выдачу посередине.
    # Иначе доступ остался бы в неизвестном состоянии, а claim — висеть.
    task = asyncio.ensure_future(apply_fn(bot, **apply_kwargs))
    _INFLIGHT_CHARGE_TASKS.add(task)
    task.add_done_callback(_on_charge_task_done)
    outcome = await asyncio.shield(task)
    if outcome == _OUTCOME_DUPLICATE:
        return {"status": "duplicate"}

    return {
        "status": "ok",
        "event": "charge_confirmed",
        "subscription_id": subscription_id,
        "charge_id": charge_id,
    }


async def _apply_confirmed_charge(
    bot: Bot,
    *,
    subscription_id: str,
    charge_id: str,
    tg_id: int,
    tariff: str,
    period_days: int,
    amount_rubles: float,
    amount_kopecks: int,
    next_dt: Optional[datetime],
    is_combo: bool = False,
    expected_amount_kopecks: Optional[int] = None,
) -> None:
    """Выдать доступ по захваченному списанию и отметить его обработанным.

    Аварийный путь (USE_NEW_PROVISIONING=off): выдаёт только премиум-период,
    ГБ combo он не начисляет — это умеет только outbox-путь. Поэтому combo
    здесь сопровождается принудительным алертом: молча недодать оплаченные
    ГБ хуже, чем разбудить админа.
    """
    from database import platega_subscriptions as _psub_db

    if is_combo:
        logger.error(
            "PLATEGA_SUB_COMBO_ON_LEGACY_PATH: sub_id=%s charge_id=%s tg=%s — ГБ не начислены",
            subscription_id, charge_id, tg_id,
        )
        await _alert_admin(bot, (
            "Platega subscription: combo charge on the emergency path\n"
            f"Subscription: {subscription_id}\n"
            f"Charge: {charge_id}\n"
            f"User TG ID: {tg_id}\n"
            "Premium period granted, combo GB NOT granted (the legacy path cannot).\n"
            "Grant the GB by hand, or switch USE_NEW_PROVISIONING back on."
        ), force=True)

    try:
        await database.grant_access(
            telegram_id=tg_id,
            duration=timedelta(days=int(period_days)),
            # Платный период каталога продлевается КАЛЕНДАРНЫМИ месяцами
            # (решение владельца 2026-09-14) — так делают все остальные
            # платные выдачи; без этого рекуррент считал бы голые 30 дней.
            tariff_period_days=int(period_days),
            source="platega_subscription",
            admin_telegram_id=None,
            tariff=tariff or "basic",
        )
    except Exception as e:
        logger.error(
            "platega_sub_grant_access_failed: sub_id=%s charge_id=%s tg=%s err=%s",
            subscription_id, charge_id, tg_id, e,
        )
        # Снимаем захват, чтобы ретрай Platega выдал доступ заново.
        try:
            await _psub_db.release_charge(charge_id)
            next_step = (
                "Platega will retry the callback (up to 3x every 5 min). "
                "If all retries fail, extend access manually."
            )
        except Exception as rel_err:
            logger.error(
                "platega_sub_release_failed: charge_id=%s err=%s", charge_id, rel_err,
            )
            next_step = (
                "Claim could NOT be released (DB error): a retry after ~4 min will "
                "re-claim it. Check manually."
            )
        await _alert_admin(bot, (
            "Platega subscription: PAID charge, access NOT granted\n"
            f"Subscription: {subscription_id}\n"
            f"Charge: {charge_id}\n"
            f"User TG ID: {tg_id}\n"
            f"Amount: {amount_rubles:.2f} RUB\n"
            f"Tariff: {tariff} ({period_days} days)\n"
            f"Error: {type(e).__name__}: {str(e)[:200]}\n"
            f"{next_step}"
        ))
        raise TransientPaymentError(
            f"grant_access failed for platega_sub {subscription_id}: {e}"
        ) from e

    try:
        confirmed = await _psub_db.confirm_charge(
            charge_id=charge_id,
            subscription_id=subscription_id,
            amount_kopecks=amount_kopecks,
        )
        if not confirmed:
            logger.warning(
                "platega_sub_confirm_noop: charge_id=%s was not PROCESSING", charge_id,
            )
    except Exception as e:
        # Доступ УЖЕ выдан — 500 отдавать нельзя (ретрай = двойное продление).
        # Строка остаётся 'PROCESSING', счётчики подписки отстанут на одно списание.
        logger.error(
            "platega_sub_confirm_failed: sub_id=%s charge_id=%s err=%s",
            subscription_id, charge_id, e,
        )
        await _alert_admin(bot, (
            "Platega subscription: access granted but charge NOT marked confirmed (DB error)\n"
            f"Subscription: {subscription_id}\n"
            f"Charge: {charge_id}\n"
            f"User TG ID: {tg_id}\n"
            f"Error: {type(e).__name__}: {str(e)[:200]}\n"
            "Row stays PROCESSING: if Platega re-sends this charge, access may be extended twice."
        ))

    await _finish_confirmed_charge(
        bot,
        subscription_id=subscription_id,
        tg_id=tg_id,
        tariff=tariff,
        period_days=period_days,
        amount_rubles=amount_rubles,
        next_dt=next_dt,
    )


async def _finish_confirmed_charge(
    bot: Bot,
    *,
    subscription_id: str,
    tg_id: int,
    tariff: str,
    period_days: int,
    amount_rubles: float,
    next_dt: Optional[datetime],
) -> None:
    """После выдачи: подписка Active + next_charge_at, уведомление юзеру."""
    from database import platega_subscriptions as _psub_db

    # Активируем подписку и обновляем next_charge_at (идемпотентно).
    await _psub_db.update_subscription_status(
        subscription_id=subscription_id,
        status="Active",
        next_charge_at=next_dt,
    )

    # Уведомляем юзера (fail-safe).
    try:
        next_str = next_dt.strftime("%d.%m.%Y %H:%M UTC") if next_dt else "—"
        await _safe_send(bot,
            chat_id=tg_id,
            text=(
                f"✅ <b>Списание прошло</b>\n\n"
                f"Сумма: <b>{amount_rubles:.2f} ₽</b>\n"
                f"Тариф: <b>{tariff}</b> ({period_days} дн.)\n"
                f"Следующее списание: <code>{next_str}</code>"
            ),
            parse_mode="HTML",
        )
    except Exception as notify_err:
        logger.warning(
            "platega_sub_confirmed_notify_failed: tg=%s err=%s",
            tg_id, notify_err,
        )


async def _apply_confirmed_charge_outbox(
    bot: Bot,
    *,
    subscription_id: str,
    charge_id: str,
    tg_id: int,
    tariff: str,
    period_days: int,
    amount_rubles: float,
    amount_kopecks: int,
    next_dt: Optional[datetime],
    is_combo: bool = False,
    expected_amount_kopecks: Optional[int] = None,
) -> Optional[str]:
    """T11 (flag on): claimed charge → ONE DB transaction
         confirm_charge(conn)            'PROCESSING' → 'CONFIRMED' + counters (row lock first)
         grant_access(defer_panel=True)  DB-only extension, zero HTTP
         provisioning.enqueue            job "platega_charge:{charge_id}": premium + GB
       → commit → provisioning.run_now (panel; failure = queued retry + alert).

    No "access granted but charge not confirmed" window: all three commit
    together or not at all. Failure before commit → release_charge + forced
    alert + TransientPaymentError (500, Platega retries, grants once).
    A parallel attempt that committed first (stale re-claim) → confirm_charge
    returns False → rollback → "duplicate" (no second extension).

    Entitlement: tariffs.for_purchase(tariff_key(tariff, is_combo), period)
    — tariff_key() сам нормализует легаси biz_* в Plus (0f298b2e).
    — basic/plus +10 GB, combo = COMBO_TARIFFS GB, legacy biz_* renews as plus
    (owner §G0). Old subscriptions without is_combo are non-combo (open §G3).

    Amount: a charge Amount different from platega_subscriptions.amount_kopecks
    → forced admin alert, access is granted anyway (the money is already taken;
    refusing would only lose the user's period).
    """
    from database import platega_subscriptions as _psub_db
    from app.services import provisioning, tariffs

    # Сверка суммы сделана в _process_confirmed_charge — общая для обоих путей.
    key = f"platega_charge:{charge_id}"
    try:
        ent = tariffs.for_purchase(tariffs.tariff_key(tariff or "basic", bool(is_combo)), int(period_days))
        pool = await database.get_pool()
        if pool is None:
            raise RuntimeError("DB pool is None")
        async with pool.acquire() as conn:
            async with conn.transaction():
                confirmed = await _psub_db.confirm_charge(
                    charge_id=charge_id,
                    subscription_id=subscription_id,
                    amount_kopecks=amount_kopecks,
                    conn=conn,
                )
                if not confirmed:
                    raise _ChargeAlreadyConfirmed(charge_id)
                grant = await database.grant_access(
                    telegram_id=tg_id,
                    duration=timedelta(days=int(period_days)),
                    tariff_period_days=int(period_days),  # calendar months
                    source=_RECURRING_ENTRYPOINT,
                    admin_telegram_id=None,
                    conn=conn,
                    _caller_holds_transaction=True,
                    defer_panel=True,
                    tariff=tariff or "basic",
                )
                premium_until = (grant or {}).get("subscription_end")
                if premium_until is None:
                    raise RuntimeError("grant_access returned no subscription_end")
                job_id = await provisioning.enqueue(
                    conn,
                    key=key,
                    telegram_id=tg_id,
                    ent=ent,
                    premium_until=premium_until,
                    source=_RECURRING_ENTRYPOINT,
                    context={
                        "subscription_id": subscription_id,
                        "charge_id": charge_id,
                        "amount_kopecks": int(amount_kopecks),
                    },
                )
    except _ChargeAlreadyConfirmed:
        logger.warning(
            "platega_sub_webhook: charge_id=%s confirmed by a parallel attempt — "
            "rolled back, no second extension",
            charge_id,
        )
        return _OUTCOME_DUPLICATE
    except Exception as e:
        logger.error(
            "platega_sub_outbox_tx_failed: sub_id=%s charge_id=%s tg=%s err=%s",
            subscription_id, charge_id, tg_id, e,
        )
        # Nothing committed: the claim stays 'PROCESSING' → drop it so the
        # Platega retry grants from scratch. If the commit itself succeeded
        # but reported an error, the row is 'CONFIRMED' and this is a no-op.
        try:
            await _psub_db.release_charge(charge_id)
            next_step = (
                "Platega will retry the callback (up to 3x every 5 min). "
                "If all retries fail, extend access manually."
            )
        except Exception as rel_err:
            logger.error(
                "platega_sub_release_failed: charge_id=%s err=%s", charge_id, rel_err,
            )
            next_step = (
                "Claim could NOT be released (DB error): a retry after ~4 min will "
                "re-claim it. Check manually."
            )
        await _alert_admin(bot, (
            "Platega subscription: PAID charge, access NOT granted\n"
            f"Subscription: {subscription_id}\n"
            f"Charge: {charge_id}\n"
            f"User TG ID: {tg_id}\n"
            f"Amount: {amount_rubles:.2f} RUB\n"
            f"Tariff: {tariff}{' (combo)' if is_combo else ''} ({period_days} days)\n"
            f"Error: {type(e).__name__}: {str(e)[:200]}\n"
            f"{next_step}"
        ))
        raise TransientPaymentError(
            f"outbox grant failed for platega_sub {subscription_id} charge {charge_id}: {e}"
        ) from e

    logger.info(
        "PLATEGA_SUB_CHARGE_ENQUEUED: sub_id=%s charge_id=%s tg=%s job=%s tariff_key=%s "
        "premium_until=%s bypass_bytes=%s",
        subscription_id, charge_id, tg_id, job_id, ent.tariff_key,
        premium_until.isoformat(), ent.bypass_bytes,
    )
    # Committed: billing, access and the job are durable. The panel part runs
    # now; if it fails the job stays queued (worker retries) and admins are
    # alerted by provisioning — the webhook still answers 200.
    await provisioning.run_now(job_id, bot=bot)

    await _finish_confirmed_charge(
        bot,
        subscription_id=subscription_id,
        tg_id=tg_id,
        tariff=tariff,
        period_days=period_days,
        amount_rubles=amount_rubles,
        next_dt=next_dt,
    )
    return None

