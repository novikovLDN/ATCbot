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
PAYMENT_METHOD_SUBSCRIPTION = 6

# SubscriptionInterval values (Platega spec)
SUBSCRIPTION_INTERVAL_DAY = 1
SUBSCRIPTION_INTERVAL_WEEK = 2
SUBSCRIPTION_INTERVAL_MONTH = 3
SUBSCRIPTION_INTERVAL_YEAR = 4


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
    """
    if not database.DB_READY:
        logger.warning("Platega webhook: DB not ready — returning 500 for retry")
        raise TransientPaymentError("DB not ready")

    # Verify authentication headers (case-insensitive lookup)
    merchant_id = headers.get("x-merchantid", "") or headers.get("X-MerchantId", "")
    secret = headers.get("x-secret", "") or headers.get("X-Secret", "")

    # SECURITY: Reject if server-side credentials are not configured (prevents empty-string bypass)
    if not PLATEGA_MERCHANT_ID or not PLATEGA_SECRET:
        logger.error("Platega webhook: server credentials not configured")
        return {"status": "unauthorized"}

    if not hmac.compare_digest(str(merchant_id), str(PLATEGA_MERCHANT_ID)) or not hmac.compare_digest(str(secret), str(PLATEGA_SECRET)):
        logger.warning("Platega webhook: auth failed")
        return {"status": "unauthorized"}

    transaction_id = body.get("id") or body.get("transactionId")
    status = (body.get("status") or "").lower()

    logger.info(
        f"Platega webhook received: transaction_id={transaction_id}, status={status}"
    )

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
        return {"status": "invalid"}

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


# ═════════════════════════════════════════════════════════════════════
# Рекуррентные СБП-подписки (paymentMethod=6, migration 074)
# ═════════════════════════════════════════════════════════════════════

async def create_subscription(
    amount_rubles: float,
    interval: int,
    description: str,
    telegram_id: int,
    tariff_type: str,
    period_days: int,
    return_url: Optional[str] = None,
    failed_url: Optional[str] = None,
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

    Returns:
        {"subscription_id": str, "redirect_url": str}

    Raises:
        Exception: сеть/HTTP-ошибка/невалидный ответ. Ловить в handler'е.
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

    # Fire-and-forget запись в БД (fail-safe — не роняет ответ юзеру).
    try:
        from database import platega_subscriptions as _psub_db
        amount_kopecks = int(round(float(amount_rubles) * 100))
        await _psub_db.create_subscription(
            subscription_id=str(subscription_id),
            telegram_id=int(telegram_id),
            amount_kopecks=amount_kopecks,
            interval_days=int(period_days),
            tariff_type=str(tariff_type),
            description=description,
        )
    except Exception as db_err:
        logger.warning(
            "platega_sub_db_persist_failed: sub_id=%s tg=%s err=%s",
            subscription_id, telegram_id, db_err,
        )

    logger.info(
        "platega_subscription_created: sub_id=%s tg=%s amount=%.2f RUB "
        "interval=%s tariff=%s days=%s",
        subscription_id, telegram_id, amount_rubles, interval, tariff_type, period_days,
    )

    return {
        "subscription_id": str(subscription_id),
        "redirect_url": str(redirect_url),
    }


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
    """Обработать webhook о списании / статусе подписки Platega (paymentMethod=6).

    Ключи в callback'е ЗАГЛАВНЫЕ: Id, Amount, Currency, Status,
    PaymentMethod, Payload, SubscriptionId, NextChargeAt.

    Отдельные события статуса подписки (SUBSCRIPTION_ACTIVATED /
    SUBSCRIPTION_PAST_DUE / SUBSCRIPTION_CANCELLED / SUBSCRIPTION_FAILED)
    приходят в поле Status без Amount — обрабатываем defensive.

    Идемпотентность: PK на Id (charge_id) в platega_subscription_charges.
    Дубль → {"status": "duplicate"}.
    """
    if not database.DB_READY:
        logger.warning("Platega sub webhook: DB not ready — 500 for retry")
        raise TransientPaymentError("DB not ready")

    # ── Auth (тот же паттерн, что и в process_webhook_data) ─────────────
    merchant_id = headers.get("x-merchantid", "") or headers.get("X-MerchantId", "")
    secret = headers.get("x-secret", "") or headers.get("X-Secret", "")

    if not PLATEGA_MERCHANT_ID or not PLATEGA_SECRET:
        logger.error("Platega sub webhook: server credentials not configured")
        return {"status": "unauthorized"}

    if not hmac.compare_digest(str(merchant_id), str(PLATEGA_MERCHANT_ID)) or \
       not hmac.compare_digest(str(secret), str(PLATEGA_SECRET)):
        logger.warning("Platega sub webhook: auth failed")
        return {"status": "unauthorized"}

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
                        f"Сумма: <b>{_amt:.2f} ₽</b> · Каждый месяц.\n\n"
                        "Как только банк подтвердит списание — VPN автоматически "
                        "активируется. Уведомим сразу же."
                    )
                elif status_upper == "SUBSCRIPTION_PAST_DUE":
                    _text = (
                        "⚠️ <b>Не удалось списать по подписке</b>\n\n"
                        "Банк временно отклонил списание. Мы повторим попытку "
                        "автоматически. VPN пока продолжает работать до конца "
                        "оплаченного периода.\n\n"
                        "Обычно причина — недостаточно средств или лимит по СБП. "
                        "Проверьте счёт."
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
                    await bot.send_message(
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
    # ВАЖНО: делаем ДО record_charge, чтобы в charges записать реальный
    # telegram_id, а не 0. Если подписки в БД нет — fallback на Payload.
    sub_row = await _psub_db.get_subscription(str(subscription_id))
    if not sub_row:
        logger.warning(
            "platega_sub_webhook: subscription_id=%s not in DB — trying Payload fallback",
            subscription_id,
        )
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
        if not tg_id:
            logger.error(
                "platega_sub_webhook: no telegram_id (no DB row + no Payload). "
                "sub_id=%s charge_id=%s", subscription_id, charge_id,
            )
            return {"status": "error", "message": "no telegram_id"}
        # Восстановленной подписки нет в нашей БД → нельзя писать charge
        # (FK referenced row отсутствует). Всё равно логируем и выходим ok,
        # чтобы Platega не ретрайла бесконечно.
        return {"status": "ok", "event": "orphan_charge", "subscription_id": str(subscription_id)}
    else:
        tg_id = int(sub_row["telegram_id"])
        tariff = str(sub_row.get("tariff_type") or "basic")
        period_days = int(sub_row.get("interval_days") or 30)

    # Идемпотентность: если такой charge_id уже был — просто выходим.
    inserted = await _psub_db.record_charge(
        charge_id=str(charge_id),
        subscription_id=str(subscription_id),
        telegram_id=int(tg_id),
        amount_kopecks=amount_kopecks,
        status=status_upper,
    )
    if not inserted:
        logger.info(
            "platega_sub_webhook: duplicate charge_id=%s (already processed) — noop",
            charge_id,
        )
        return {"status": "duplicate"}

    next_dt = _parse_next_charge_at(next_charge_raw)

    # ── CONFIRMED → продлеваем VPN + уведомляем ─────────────────────────
    if status_upper == "CONFIRMED":
        try:
            await database.grant_access(
                telegram_id=tg_id,
                duration=timedelta(days=int(period_days)),
                source="platega_subscription",
                admin_telegram_id=None,
                tariff=tariff or "basic",
            )
        except Exception as e:
            logger.error(
                "platega_sub_grant_access_failed: sub_id=%s tg=%s err=%s",
                subscription_id, tg_id, e,
            )
            # Продление не удалось — но списание уже отражено в БД.
            # Возвращаем 500 → Platega ретрайнет, next_charge не обновляем.
            raise TransientPaymentError(
                f"grant_access failed for platega_sub {subscription_id}: {e}"
            )

        # Активируем подписку и обновляем next_charge_at (в статусе уже был Active?
        # — не важно, идемпотентно перебиваем).
        await _psub_db.update_subscription_status(
            subscription_id=str(subscription_id),
            status="Active",
            next_charge_at=next_dt,
        )

        # Уведомляем юзера (fail-safe).
        try:
            next_str = next_dt.strftime("%d.%m.%Y %H:%M UTC") if next_dt else "—"
            await bot.send_message(
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

        return {
            "status": "ok",
            "event": "charge_confirmed",
            "subscription_id": str(subscription_id),
            "charge_id": str(charge_id),
        }

    # ── CANCELED → PastDue + уведомление ────────────────────────────────
    if status_upper == "CANCELED":
        await _psub_db.update_subscription_status(
            subscription_id=str(subscription_id),
            status="PastDue",
            next_charge_at=next_dt,
        )
        try:
            await bot.send_message(
                chat_id=tg_id,
                text=(
                    "⚠️ <b>Списание не прошло</b>\n\n"
                    f"Не удалось списать <b>{amount_rubles:.2f} ₽</b> "
                    "по вашей СБП-подписке.\n\n"
                    "Проверьте баланс карты — попробуем повторить через 1–2 дня. "
                    "Если хотите отменить подписку — напишите в поддержку."
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

    # ── Прочие статусы (Pending и т.п.) — просто ok ─────────────────────
    logger.info(
        "platega_sub_webhook: non-terminal status=%s, sub_id=%s charge_id=%s",
        status_upper, subscription_id, charge_id,
    )
    return {"status": "ok", "event": "noop", "raw_status": status_upper}

