"""
Wata (wata.pro) — REST H2H integration.

API docs: https://api.wata.pro/api/h2h/
Auth: Bearer JWT в заголовке Authorization.
Webhook signature: RSA-SHA512, публичный ключ через GET /public-key.

Модель интеграции:
  1. create_invoice() → POST /links (создаёт payment link, возвращает url).
     Юзер редиректится по этому url → форма Wata (карта/СБП/T-Pay).
  2. Wata шлёт webhook на /webhooks/wata с X-Signature (RSA-SHA512).
  3. process_webhook_data() парсит тело, верифицирует подпись,
     ищет pending_purchase по orderId, вызывает
     process_confirmed_payment (тот же generic pipeline что и Lava).

Основные отличия от Lava:
  - Bearer JWT вместо HMAC-подписи запросов
  - Ответ webhook'а — прямо в теле JSON, статус в `transactionStatus`
    (Created / Pending / Paid / Declined). Учитываем только Paid.
  - Sandbox через отдельный host: api-sandbox.wata.pro
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

import config
import database
from aiogram import Bot
from app.services.payments.confirmation import TransientPaymentError
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────

WATA_ACCESS_TOKEN = (getattr(config, "WATA_ACCESS_TOKEN", "") or "").strip()
WATA_SANDBOX = bool(getattr(config, "WATA_SANDBOX", False))
WATA_API_URL = (
    "https://api-sandbox.wata.pro/api/h2h"
    if WATA_SANDBOX
    else "https://api.wata.pro/api/h2h"
)
# Кэш публичного ключа для проверки webhook-подписей.
_PUBLIC_KEY_PEM: Optional[str] = None


def is_enabled() -> bool:
    return bool(WATA_ACCESS_TOKEN)


def is_visible_to(telegram_id: int) -> bool:
    """Пока Wata в закрытом бета-режиме — показываем кнопку только
    админу. Убрать проверку → показать всем, когда протестируется."""
    if not is_enabled():
        return False
    try:
        return int(telegram_id) == int(config.ADMIN_TELEGRAM_ID)
    except Exception:
        return False


logger.info(
    "WATA_CONFIG: token_len=%d sandbox=%s api=%s enabled=%s",
    len(WATA_ACCESS_TOKEN), WATA_SANDBOX, WATA_API_URL, is_enabled(),
)


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {WATA_ACCESS_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# ── Create payment link ─────────────────────────────────────────────

async def create_invoice(
    amount_rubles: float,
    purchase_id: str,
    comment: str = "",
    expire_minutes: int = 60,
    user_id: Optional[int] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """POST /api/h2h/links — создать одноразовую платёжную ссылку.

    Return: {"invoice_id": <link.id>, "payment_url": <link.url>}
    """
    if not is_enabled():
        raise Exception("Wata not configured")

    # Wata требует min 10 RUB, max 999_999.99
    amount = round(float(amount_rubles), 2)
    if amount < 10:
        raise ValueError(f"Wata min amount = 10 RUB, got {amount}")
    if amount > 999_999.99:
        raise ValueError(f"Wata max amount = 999999.99 RUB, got {amount}")

    hook_base = (getattr(config, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    success_url = f"{hook_base}/payment/success" if hook_base else None
    fail_url = f"{hook_base}/payment/fail" if hook_base else None

    body: Dict[str, Any] = {
        "amount": amount,
        "currency": "RUB",
        "orderId": purchase_id,
        # 30 дней макс. Задаём в UTC.
        "expirationDateTime": _iso_expire(expire_minutes),
    }
    if comment:
        body["description"] = comment[:500]
    if user_id is not None:
        body["userId"] = str(user_id)
    if email:
        body["email"] = email[:128]
    if success_url:
        body["successRedirectUrl"] = success_url
    if fail_url:
        body["failRedirectUrl"] = fail_url

    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{WATA_API_URL}/links",
                headers=_headers(),
                content=json.dumps(body),
            )
            if response.status_code != 200:
                logger.error(
                    "Wata API error: status=%d response=%s",
                    response.status_code, response.text[:500],
                )
                raise Exception(
                    f"Wata create_invoice failed: {response.status_code}",
                )
            return response

    response = await retry_async(
        _make_request,
        retries=2,
        base_delay=1.0,
        max_delay=5.0,
        retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError),
    )
    resp = response.json()
    invoice_id = resp.get("id")
    payment_url = resp.get("url")
    if not invoice_id or not payment_url:
        raise Exception(f"Wata: invalid response — missing id/url: {resp}")

    logger.info(
        "Wata link created: id=%s amount=%.2f RUB purchase_id=%s url=%s",
        invoice_id, amount, purchase_id, payment_url[:80],
    )
    return {"invoice_id": invoice_id, "payment_url": payment_url}


def _iso_expire(minutes: int) -> str:
    """UTC ISO-8601 время истечения ссылки — сейчас + N минут."""
    from datetime import timedelta
    dt = datetime.now(timezone.utc) + timedelta(minutes=max(10, int(minutes)))
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Check link status (fallback / verification) ─────────────────────

async def check_link_status(link_id: str) -> Optional[Dict[str, Any]]:
    """GET /api/h2h/links/{id} — статус платёжной ссылки.

    Rate limit: 1 GET раз в 30с на конкретный id. Используем ТОЛЬКО
    как fallback для webhook-верификации (не polling).
    """
    if not is_enabled() or not link_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{WATA_API_URL}/links/{link_id}",
                headers=_headers(),
            )
            if response.status_code == 429:
                logger.warning("Wata link status: rate limited (30s)")
                return None
            if response.status_code != 200:
                logger.error(
                    "Wata link status failed: id=%s status=%d",
                    link_id, response.status_code,
                )
                return None
            return response.json()
    except Exception as e:
        logger.error("Wata check_link_status error: %s", e)
        return None


async def check_transaction(transaction_id: str) -> Optional[Dict[str, Any]]:
    """GET /api/h2h/transactions/{id} — статус транзакции.
    Rate limit: 1 GET / 30с на конкретный id."""
    if not is_enabled() or not transaction_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{WATA_API_URL}/transactions/{transaction_id}",
                headers=_headers(),
            )
            if response.status_code == 429:
                return None
            if response.status_code != 200:
                return None
            return response.json()
    except Exception as e:
        logger.error("Wata check_transaction error: %s", e)
        return None


# ── Webhook signature verification (RSA-SHA512) ─────────────────────

async def _get_public_key() -> Optional[str]:
    """Получить PKCS1 публичный ключ для проверки webhook-подписи.

    Кэшируется до перезапуска процесса — ключ Wata меняется редко.
    """
    global _PUBLIC_KEY_PEM
    if _PUBLIC_KEY_PEM:
        return _PUBLIC_KEY_PEM
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{WATA_API_URL}/public-key")
            if response.status_code != 200:
                logger.error(
                    "Wata public-key fetch failed: status=%d",
                    response.status_code,
                )
                return None
            data = response.json()
            _PUBLIC_KEY_PEM = data.get("value")
            logger.info("Wata public key fetched (len=%d)", len(_PUBLIC_KEY_PEM or ""))
            return _PUBLIC_KEY_PEM
    except Exception as e:
        logger.error("Wata public-key error: %s", e)
        return None


async def _verify_webhook_signature(raw_body: bytes, signature_b64: str) -> bool:
    """Проверить X-Signature (RSA-SHA512) полученного webhook'а.

    Fail-open: если ключ не удалось получить или крипто-либа
    недоступна, логируем warning и разрешаем (иначе рискуем терять
    легитимные оплаты при проблемах доступа к api.wata.pro).
    """
    if not signature_b64:
        logger.warning("Wata webhook: missing X-Signature header")
        return False
    pem = await _get_public_key()
    if not pem:
        logger.warning("Wata webhook: no public key — skipping signature check")
        return True
    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        logger.warning(
            "Wata webhook: cryptography lib missing — skipping signature check",
        )
        return True
    try:
        pub = serialization.load_pem_public_key(pem.encode("utf-8"))
        sig = base64.b64decode(signature_b64)
        pub.verify(sig, raw_body, padding.PKCS1v15(), hashes.SHA512())
        return True
    except Exception as e:
        logger.error("Wata webhook: signature verification failed: %s", e)
        return False


# ── Webhook processing ─────────────────────────────────────────────

async def _notify_user_declined(
    bot: Bot, order_id: str, transaction_id: Optional[str],
    error_code: Optional[str], error_description: Optional[str],
) -> None:
    """Оплата отклонена — уведомить юзера + логировать для админа.

    Юзеру: короткий текст + error_code как «билет» для поддержки.
    Админу: полный error_description в логах + событие в bus.

    Fail-open: если pending_purchase не найден, юзеру не пишем
    (нечего связать), но лог остаётся.
    """
    try:
        pending = await database.get_pending_purchase_by_id(
            order_id, check_expiry=False,
        )
    except Exception as e:
        logger.warning("Wata declined: pending lookup failed order=%s: %s", order_id, e)
        return
    if not pending:
        logger.warning("Wata declined: pending not found for order=%s", order_id)
        return

    telegram_id = int(pending.get("telegram_id") or 0)
    if not telegram_id:
        return

    # Помечаем pending как expired — юзер сможет попробовать заново.
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE pending_purchases SET status = 'expired' "
                "WHERE purchase_id = $1 AND status = 'pending'",
                order_id,
            )
    except Exception as e:
        logger.warning("Wata declined: mark expired failed: %s", e)

    # Короткий человекочитаемый билет для поддержки:
    # берём последние 8 символов order_id + errorCode если есть.
    ticket = f"WATA-{order_id[-8:].upper()}"
    if error_code:
        ticket += f"-{error_code}"

    text = (
        "❌ <b>Оплата не прошла</b>\n\n"
        f"Что-то пошло не так на стороне банка. Ваш заказ "
        f"<code>{order_id[-12:]}</code> отменён, деньги не списаны.\n\n"
        f"🎫 <b>Код обращения:</b> <code>{ticket}</code>\n\n"
        f"Попробуйте ещё раз или обратитесь в поддержку с этим кодом — "
        f"мы поможем разобраться."
    )
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/atlas_suppbot")],
        [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="mini_shop")],
    ])
    try:
        await bot.send_message(
            chat_id=telegram_id, text=text,
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Wata declined: user notify failed tg=%s: %s", telegram_id, e)

    # Админу — уведомление с полным контекстом для дебага.
    try:
        import config as _cfg
        admin_text = (
            f"❌ <b>Wata: отклонённая оплата</b>\n\n"
            f"Юзер: <code>{telegram_id}</code>\n"
            f"Заказ: <code>{order_id}</code>\n"
            f"Транзакция: <code>{transaction_id or '—'}</code>\n"
            f"🎫 Тикет юзеру: <code>{ticket}</code>\n\n"
            f"<b>Error code:</b> <code>{error_code or '—'}</code>\n"
            f"<b>Description:</b> {error_description or '—'}"
        )
        await bot.send_message(
            chat_id=int(_cfg.ADMIN_TELEGRAM_ID),
            text=admin_text, parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Wata declined: admin notify failed: %s", e)

    logger.info(
        "Wata declined notified: tg=%s order=%s tx=%s code=%s ticket=%s",
        telegram_id, order_id, transaction_id, error_code, ticket,
    )


async def process_webhook_data(
    headers: dict, raw_body: bytes, body: dict, bot: Bot,
) -> dict:
    """Обработать webhook от Wata.

    Успешный платёж → transactionStatus == "Paid" + kind == "Payment".
    Всё остальное (Pending / Declined / Refund) сейчас игнорируем.
    Для confirm вызываем тот же generic process_confirmed_payment
    что и Lava/Platega — единый pipeline финализации.
    """
    if not database.DB_READY:
        raise TransientPaymentError("DB not ready")
    if not is_enabled():
        return {"status": "disabled"}

    signature = headers.get("x-signature") or headers.get("X-Signature") or ""
    if not await _verify_webhook_signature(raw_body, signature):
        logger.error("Wata webhook: invalid signature (body=%s)", raw_body[:200])
        return {"status": "invalid_signature"}

    tx_status = str(body.get("transactionStatus") or "").strip()
    kind = str(body.get("kind") or "").strip()
    order_id = body.get("orderId")
    transaction_id = body.get("transactionId") or body.get("id")
    amount = body.get("amount")
    error_code = body.get("errorCode")
    error_description = body.get("errorDescription")

    logger.info(
        "Wata webhook: order=%s tx=%s status=%s kind=%s amount=%s err=%s",
        order_id, transaction_id, tx_status, kind, amount, error_code,
    )

    if kind == "Refund":
        # Возвраты обрабатываются отдельно (пока не нужно юзер-нотификаций).
        logger.info("Wata webhook: refund kind=%s tx=%s — noop for now", kind, transaction_id)
        return {"status": "ignored"}
    if kind != "Payment":
        logger.info("Wata webhook: ignoring kind=%s", kind)
        return {"status": "ignored"}
    if not order_id:
        logger.error("Wata webhook: missing orderId")
        return {"status": "invalid"}

    # ── Declined → уведомить юзера с error-кодом ──────────────────────
    if tx_status == "Declined":
        await _notify_user_declined(
            bot, order_id, transaction_id, error_code, error_description,
        )
        return {"status": "declined_notified"}
    if tx_status != "Paid":
        # Created / Pending — промежуточные, ждём следующий webhook.
        logger.info("Wata webhook: ignoring intermediate status=%s", tx_status)
        return {"status": "ignored"}

    from app.services.payments.confirmation import (
        lookup_pending_purchase, process_confirmed_payment,
    )
    lookup = await lookup_pending_purchase("wata", order_id)
    if lookup["status"] != "ok":
        return lookup
    pending_purchase = lookup["purchase"]
    telegram_id = lookup["telegram_id"]

    # Sanity: сравнить сумму с pending. Если разница > 1 руб — warning.
    raw_amount = float(amount) if amount else 0.0
    expected_amount = pending_purchase["price_kopecks"] / 100.0
    if raw_amount <= 0:
        amount_rubles = expected_amount
    elif abs(raw_amount - expected_amount) > 1.0:
        logger.warning(
            "Wata webhook: amount mismatch. order=%s webhook=%s expected=%s",
            order_id, raw_amount, expected_amount,
        )
        amount_rubles = raw_amount
    else:
        amount_rubles = raw_amount

    logger.info(
        "payment_event_received: provider=wata user=%s tx=%s order=%s amount=%.2f RUB",
        telegram_id, transaction_id, order_id, amount_rubles,
    )
    return await process_confirmed_payment(
        provider="wata",
        purchase_id=order_id,
        amount_rubles=amount_rubles,
        invoice_id=str(transaction_id or order_id),
        telegram_id=telegram_id,
        bot=bot,
    )
