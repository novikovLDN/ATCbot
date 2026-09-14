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
     process_confirmed_payment (тот же generic pipeline, что и Platega).

Подпись проверяется FAIL-CLOSED (аудит 2026-09, P0-B): нет ключа / нет
библиотеки cryptography / подпись не сошлась → TransientPaymentError →
роут отвечает 500 → WATA повторяет post-payment webhook до 32 часов.
Платёж не теряется, а неподписанный запрос никогда не принимается.

Особенности WATA:
  - Bearer JWT вместо HMAC-подписи запросов
  - Ответ webhook'а — прямо в теле JSON, статус в `transactionStatus`
    (Created / Pending / Paid / Declined). Учитываем только Paid.
  - Sandbox через отдельный host: api-sandbox.wata.pro
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import time
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


def _normalize_pem(value: Optional[str]) -> str:
    """PEM из env/ответа API → каноничный многострочный вид.

    Railway-переменные часто хранят PEM одной строкой с литеральными
    «\\n» и/или в кавычках — принимаем оба варианта.
    """
    if not value:
        return ""
    pem = str(value).strip().strip('"').strip("'").strip()
    pem = pem.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
    return pem.strip()


# Закреплённый (pinned) публичный ключ из env WATA_PUBLIC_KEY_PEM. Если задан —
# используется без сетевого запроса и НЕ перезапрашивается при несовпадении.
_PINNED_PUBLIC_KEY_PEM: str = _normalize_pem(getattr(config, "WATA_PUBLIC_KEY_PEM", ""))
# Кэш публичного ключа, полученного через GET /public-key (только успешные).
_PUBLIC_KEY_PEM: Optional[str] = None
# time.monotonic() последней сетевой попытки получить ключ.
_PUBLIC_KEY_FETCHED_AT: Optional[float] = None
# Лок против thundering herd: одна загрузка ключа на все параллельные webhook'и.
_KEY_LOCK: Optional[asyncio.Lock] = None
# Принудительный перезапрос ключа (при несовпадении подписи) — не чаще раза в N с,
# чтобы поток поддельных запросов не превращался в поток запросов к WATA.
_KEY_REFRESH_MIN_INTERVAL = 60.0

# Cooldown алерта «WATA API 401 — токен истёк» (токен живёт 1–12 месяцев).
_LAST_AUTH_ALERT_AT: Optional[float] = None
_AUTH_ALERT_COOLDOWN = 3600.0

# Покупки магазина (SCOPE.md: «магазин как есть») — зеркало классификации
# confirmation.py::process_confirmed_payment. Для них новые проверки
# суммы/валюты НЕ применяются. `proxy` в магазин не входит → строгий путь.
_SHOP_PURCHASE_TYPES = frozenset({"telegram_premium", "telegram_stars", "steam", "spotify"})
_SHOP_TARIFF_PREFIXES = ("apple_id_", "steam_", "spotify_")


def is_enabled() -> bool:
    return bool(WATA_ACCESS_TOKEN)


def is_visible_to(telegram_id: int) -> bool:
    """Wata раскатана на всех юзеров (2026-08). Кнопка видна при условии
    что сервис настроен (WATA_ACCESS_TOKEN присутствует).

    Rollback до admin-only: раскомментировать проверку ADMIN_TELEGRAM_ID.
    """
    return is_enabled()
    # ── rollback: только админ видит Wata ──
    # if not is_enabled():
    #     return False
    # try:
    #     return int(telegram_id) == int(config.ADMIN_TELEGRAM_ID)
    # except Exception:
    #     return False


logger.info(
    "WATA_CONFIG: token_len=%d sandbox=%s api=%s enabled=%s pinned_public_key=%s",
    len(WATA_ACCESS_TOKEN), WATA_SANDBOX, WATA_API_URL, is_enabled(),
    bool(_PINNED_PUBLIC_KEY_PEM),
)


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {WATA_ACCESS_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _admin_bot():
    """Bot для админ-алертов из мест без bot в сигнатуре (create_invoice,
    check_*). Берём экземпляр, который main.py передаёт в
    app.api.payment_webhook.setup(bot). None → только лог."""
    try:
        from app.api import payment_webhook
        return getattr(payment_webhook, "_bot", None)
    except Exception:  # noqa: BLE001
        return None


async def _alert_unauthorized(operation: str) -> None:
    """WATA API ответил 401 → токен истёк/отозван. Алерт админу с cooldown."""
    global _LAST_AUTH_ALERT_AT
    logger.error(
        "WATA_API_UNAUTHORIZED: op=%s — access token expired or revoked", operation,
    )
    now = time.monotonic()
    if _LAST_AUTH_ALERT_AT is not None and now - _LAST_AUTH_ALERT_AT < _AUTH_ALERT_COOLDOWN:
        return
    bot = _admin_bot()
    if bot is None:
        return
    _LAST_AUTH_ALERT_AT = now  # ставим до await — параллельные 401 не дублируют алерт
    try:
        from app.services.admin_alerts import send_alert
        sent = await send_alert(
            bot, "payment",
            "WATA API ответил 401 Unauthorized.\n"
            f"Операция: {operation}\n"
            "Вероятно, истёк access token (живёт 1–12 месяцев). Оплата через WATA "
            "не работает: выпустить новый токен в кабинете WATA и обновить "
            "PROD_WATA_ACCESS_TOKEN в Railway.",
            force=True,
        )
        if not sent:
            _LAST_AUTH_ALERT_AT = None
    except Exception as e:  # noqa: BLE001
        _LAST_AUTH_ALERT_AT = None
        logger.error("wata 401 admin alert failed: %s", e)


# ── Create payment link ─────────────────────────────────────────────

async def create_invoice(
    amount_rubles: float,
    purchase_id: str,
    comment: str = "",
    expire_minutes: int = 30,
    user_id: Optional[int] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """POST /api/h2h/links — создать одноразовую платёжную ссылку.

    expire_minutes по умолчанию 30 = TTL pending_purchases (устанавливается
    при сохранении provider_invoice_id), чтобы ссылка не жила дольше заказа.
    URL без завершающего «/» — так работает в проде; менять не стали
    (POST-редирект httpx не проходит).

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
                if response.status_code == 401:
                    await _alert_unauthorized("create_invoice")
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

    Возвращает:
      - dict  → payload от Wata
      - None  → любая non-200 (429/404/500/сеть). Вызывающему хочется
                отличать 404 «инвойса больше нет» от «сеть моргнула»,
                поэтому кладём HTTP-статус в dict под ключом `_http`
                когда статус != 200 (кроме 200 — там натуральный dict).
                Reconciler читает `_http == 404` и помечает pending
                строку как expired, чтобы не долбить бесконечно.
    """
    if not is_enabled() or not link_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{WATA_API_URL}/links/{link_id}",
                headers=_headers(),
            )
            if response.status_code == 200:
                return response.json()
            if response.status_code == 429:
                logger.warning("Wata link status: rate limited (30s)")
                return None
            # 404 = инвойс удалён Wata по retention; логируем INFO
            # (не ERROR — это ожидаемое состояние для старых pending'ов).
            if response.status_code == 404:
                logger.info(
                    "Wata link status 404: id=%s — invoice purged by Wata retention",
                    link_id,
                )
                return {"_http": 404, "_link_id": link_id}
            if response.status_code == 401:
                await _alert_unauthorized("check_link_status")
                return None
            logger.error(
                "Wata link status failed: id=%s status=%d",
                link_id, response.status_code,
            )
            return None
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
            if response.status_code == 401:
                await _alert_unauthorized("check_transaction")
                return None
            if response.status_code != 200:
                return None
            return response.json()
    except Exception as e:
        logger.error("Wata check_transaction error: %s", e)
        return None


# ── Webhook signature verification (RSA-SHA512) ─────────────────────

class WataSignatureError(TransientPaymentError):
    """X-Signature отсутствует или не сходится с ключом WATA.

    Наследник TransientPaymentError НАМЕРЕННО: роут
    (app/api/payment_webhook.py) маппит его в HTTP 500. Для настоящего
    webhook'а при ротации ключа это значит «WATA повторит» (до 32 ч),
    для поддельного — просто 500. Отдельного 4xx роут не умеет, а 200
    (как было раньше) терял настоящие платежи без повтора.
    """


def _load_public_key(pem: str):
    """PEM → объект ключа или None, если PEM битый.

    Бросает ImportError, если нет библиотеки cryptography.
    Принимает и PKCS1 («BEGIN RSA PUBLIC KEY»), и SPKI («BEGIN PUBLIC KEY»).
    """
    from cryptography.hazmat.primitives import serialization
    try:
        return serialization.load_pem_public_key(pem.encode("utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.error("Wata public key is malformed: %s", type(e).__name__)
        return None


async def _fetch_public_key_pem() -> Optional[str]:
    """GET /api/h2h/public-key → PEM или None (любая ошибка)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{WATA_API_URL}/public-key")
        if response.status_code != 200:
            logger.error(
                "Wata public-key fetch failed: status=%d", response.status_code,
            )
            return None
        data = response.json() or {}
        pem = _normalize_pem(data.get("value"))
        return pem or None
    except Exception as e:  # noqa: BLE001
        logger.error("Wata public-key error: %s", e)
        return None


def _key_lock() -> asyncio.Lock:
    global _KEY_LOCK
    if _KEY_LOCK is None:
        _KEY_LOCK = asyncio.Lock()
    return _KEY_LOCK


async def _get_public_key(force_refresh: bool = False) -> Optional[str]:
    """Публичный ключ WATA (PEM) для проверки webhook-подписи.

    1. Задан WATA_PUBLIC_KEY_PEM → он, без сети (force_refresh игнорируется).
    2. Иначе — кэш процесса; при пустом кэше ленивая загрузка под локом
       (одна загрузка на все параллельные запросы). Неудачи НЕ кэшируются.
    3. force_refresh=True — перезапрос (ротация ключа), но не чаще раза
       в _KEY_REFRESH_MIN_INTERVAL; при неудаче старый кэш сохраняется.
    """
    global _PUBLIC_KEY_PEM, _PUBLIC_KEY_FETCHED_AT
    if _PINNED_PUBLIC_KEY_PEM:
        return _PINNED_PUBLIC_KEY_PEM
    if _PUBLIC_KEY_PEM and not force_refresh:
        return _PUBLIC_KEY_PEM
    async with _key_lock():
        now = time.monotonic()
        if _PUBLIC_KEY_PEM:
            if not force_refresh:
                return _PUBLIC_KEY_PEM  # загрузил параллельный запрос
            if (
                _PUBLIC_KEY_FETCHED_AT is not None
                and now - _PUBLIC_KEY_FETCHED_AT < _KEY_REFRESH_MIN_INTERVAL
            ):
                return _PUBLIC_KEY_PEM
        _PUBLIC_KEY_FETCHED_AT = now
        pem = await _fetch_public_key_pem()
        if not pem:
            return None if not force_refresh else _PUBLIC_KEY_PEM
        try:
            if _load_public_key(pem) is None:
                return None if not force_refresh else _PUBLIC_KEY_PEM
        except ImportError:
            pass  # проверить нечем; верификация всё равно откажет fail-closed
        _PUBLIC_KEY_PEM = pem
        logger.info("Wata public key fetched (len=%d)", len(pem))
        return pem


async def warmup_public_key() -> bool:
    """Заранее получить/проверить публичный ключ (для вызова при старте).

    Никогда не бросает. True — ключ есть и парсится.
    """
    try:
        pem = await _get_public_key()
        if not pem:
            logger.warning("WATA_PUBLIC_KEY_WARMUP: key unavailable")
            return False
        ok = _load_public_key(pem) is not None
        logger.info("WATA_PUBLIC_KEY_WARMUP: ok=%s pinned=%s", ok, bool(_PINNED_PUBLIC_KEY_PEM))
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning("WATA_PUBLIC_KEY_WARMUP: failed: %s", e)
        return False


def _verify_with_pem(pem: str, raw_body: bytes, sig: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    key = _load_public_key(pem)
    if key is None:
        return False
    try:
        key.verify(sig, raw_body, padding.PKCS1v15(), hashes.SHA512())
        return True
    except InvalidSignature:
        return False
    except Exception as e:  # noqa: BLE001
        logger.error("Wata webhook: signature verification error: %s", type(e).__name__)
        return False


async def _verify_webhook_signature(raw_body: bytes, signature_b64: str) -> bool:
    """Проверить X-Signature (RSA-SHA512, PKCS1v15) по сырому телу. FAIL-CLOSED.

    True  — подпись верна.
    False — подписи нет / она битая / не сошлась (в т.ч. после одного
            перезапроса ключа, если ключ не закреплён через env).
    raise TransientPaymentError — проверить нечем (нет ключа или нет
            библиотеки cryptography). Роут отдаёт 500, WATA повторяет
            webhook до 32 часов — платёж не теряется.
    """
    if not signature_b64:
        logger.warning("Wata webhook: missing X-Signature header")
        return False
    try:
        from cryptography.exceptions import InvalidSignature  # noqa: F401
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import padding  # noqa: F401
    except ImportError as e:
        logger.critical("Wata webhook: cryptography lib missing — cannot verify signature")
        raise TransientPaymentError(
            "cryptography library unavailable — cannot verify Wata signature",
        ) from e
    try:
        sig = base64.b64decode(signature_b64)
    except Exception:  # noqa: BLE001
        logger.warning("Wata webhook: X-Signature is not valid base64")
        return False

    pem = await _get_public_key()
    if not pem:
        raise TransientPaymentError("Wata public key unavailable — cannot verify signature")
    if _verify_with_pem(pem, raw_body, sig):
        return True
    if _PINNED_PUBLIC_KEY_PEM:
        logger.error("Wata webhook: signature mismatch against pinned WATA_PUBLIC_KEY_PEM")
        return False
    # Возможна ротация ключа — перезапросить ОДИН раз и перепроверить.
    fresh = await _get_public_key(force_refresh=True)
    if not fresh or fresh == pem:
        return False
    logger.warning("Wata webhook: public key changed — re-verifying with fresh key")
    return _verify_with_pem(fresh, raw_body, sig)


# ── Webhook processing ─────────────────────────────────────────────

def _is_shop_purchase(pending: Dict[str, Any]) -> bool:
    """Покупка магазина? Зеркало confirmation.py (без `proxy`, см. SCOPE.md)."""
    purchase_type = pending.get("purchase_type") or "subscription"
    tariff = pending.get("tariff") or ""
    return purchase_type in _SHOP_PURCHASE_TYPES or tariff.startswith(_SHOP_TARIFF_PREFIXES)


def _parse_positive_amount(value: Any) -> Optional[float]:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount <= 0:
        return None
    return amount


async def _safe_alert(bot: Bot, category: str, text: str, *, force: bool) -> None:
    try:
        from app.services.admin_alerts import send_alert
        await send_alert(bot, category, text, force=force)
    except Exception as e:  # noqa: BLE001
        logger.error("wata admin alert failed (category=%s): %s", category, e)


async def _alert_refund(bot: Bot, body: dict) -> None:
    """kind=Refund → лог + принудительный алерт. Доступ НЕ отзываем (SCOPE.md)."""
    order_id = body.get("orderId")
    tx_id = body.get("transactionId") or body.get("id")
    telegram_id: Any = "—"
    product = "—"
    if order_id:
        try:
            pending = await database.get_pending_purchase_any_status(str(order_id))
            if pending:
                telegram_id = pending.get("telegram_id") or "—"
                product = f"{pending.get('purchase_type') or '—'} / {pending.get('tariff') or '—'}"
        except Exception as e:  # noqa: BLE001
            logger.warning("Wata refund: pending lookup failed order=%s: %s", order_id, e)
    logger.warning(
        "WATA_REFUND: order=%s tx=%s status=%s amount=%s currency=%s tg=%s",
        order_id, tx_id, body.get("transactionStatus"), body.get("amount"),
        body.get("currency"), telegram_id,
    )
    await _safe_alert(
        bot, "payment",
        "WATA: возврат (kind=Refund)\n"
        f"orderId: {order_id}\n"
        f"transactionId: {tx_id}\n"
        f"status: {body.get('transactionStatus')}\n"
        f"amount: {body.get('amount')} {body.get('currency') or ''}\n"
        f"User TG ID: {telegram_id}\n"
        f"Товар: {product}\n"
        "Доступ автоматически НЕ отозван — решить вручную.",
        force=True,
    )

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
        pool = await database.get_pool()
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

    # 08 #23 / M9: RU/EN, and «Попробовать снова» returns to the flow of THIS order
    # (it always opened the shop, also for a VPN purchase).
    try:
        from app.services.language_service import resolve_user_language
        language = await resolve_user_language(telegram_id)
    except Exception:  # noqa: BLE001
        language = "ru"
    from app.i18n import get_text as _t
    retry_cb = {
        "subscription": "menu_buy_vpn",
        "traffic_pack": "buy_traffic",
        "gift": "gift_subscription",
        "balance_topup": "topup_balance",
    }.get(pending.get("purchase_type") or "subscription", "mini_shop")   # shop orders: the shop
    text = _t(language, "payment.wata_declined", order=order_id[-12:], ticket=ticket)
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_t(language, "main.support_button"), url="https://t.me/atlas_suppbot")],
        [InlineKeyboardButton(text=_t(language, "payment.wata_retry_button"), callback_data=retry_cb)],
    ])
    try:
        await bot.send_message(
            chat_id=telegram_id, text=text,
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Wata declined: user notify failed tg=%s: %s", telegram_id, e)

    # Админ-уведомление по каждой отклонённой оплате раньше слалось
    # сюда — но 95% отказов это банальные «эмитент не пропустил»
    # (TRA_2999 и т.п.), к которым админ ничего сделать не может.
    # Info-лог остаётся для аудита — если юзер напишет с тикетом,
    # можно грепнуть по order_id и найти всё, что нужно.
    logger.info(
        "Wata declined notified: tg=%s order=%s tx=%s code=%s ticket=%s",
        telegram_id, order_id, transaction_id, error_code, ticket,
    )


async def process_webhook_data(
    headers: dict, raw_body: bytes, body: dict, bot: Bot,
) -> dict:
    """Обработать webhook от Wata.

    Успешный платёж → transactionStatus == "Paid" + kind == "Payment".
    Declined → уведомление юзеру + алерт админу (с cooldown).
    Refund → принудительный алерт админу (доступ не отзываем).
    Created / Pending → игнор.
    Для confirm вызываем тот же generic process_confirmed_payment
    что и Platega — единый pipeline финализации.

    Подпись fail-closed: нет ключа/крипто-либы → TransientPaymentError,
    неверная/отсутствующая подпись → WataSignatureError (тоже 500 в роуте).
    """
    if not database.DB_READY:
        raise TransientPaymentError("DB not ready")
    if not is_enabled():
        return {"status": "disabled"}

    signature = headers.get("x-signature") or headers.get("X-Signature") or ""
    try:
        signature_ok = await _verify_webhook_signature(raw_body, signature)
    except TransientPaymentError as e:
        await _safe_alert(
            bot, "wata_signature",
            f"WATA webhook: подпись проверить нечем ({e}). Отвечаем 500, WATA "
            "повторит до 32 ч. Проверить доступ к api.wata.pro / WATA_PUBLIC_KEY_PEM.",
            force=False,
        )
        raise
    if not signature_ok:
        logger.error(
            "Wata webhook: invalid signature (has_header=%s body_len=%d)",
            bool(signature), len(raw_body or b""),
        )
        await _safe_alert(
            bot, "wata_signature",
            "WATA webhook: неверная или отсутствующая X-Signature. Отвечаем 500. "
            "Если это ротация ключа WATA — обновить WATA_PUBLIC_KEY_PEM.",
            force=False,
        )
        raise WataSignatureError("Wata webhook: invalid or missing X-Signature")

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
        await _alert_refund(bot, body)
        return {"status": "refund_alerted"}
    if kind != "Payment":
        logger.info("Wata webhook: ignoring kind=%s", kind)
        return {"status": "ignored"}
    if not order_id:
        logger.error("Wata webhook: missing orderId")
        return {"status": "invalid",
                "_detail": f"WATA Payment callback without orderId; tx={transaction_id} status={tx_status} amount={amount}"}

    # ── Declined → уведомить юзера с error-кодом ──────────────────────
    if tx_status == "Declined":
        await _notify_user_declined(
            bot, order_id, transaction_id, error_code, error_description,
        )
        # Свой bucket (cooldown 300 с по умолчанию в admin_alerts), чтобы
        # массовые отказы эмитента не глушили важные «payment»-алерты.
        await _safe_alert(
            bot, "wata_declined",
            "WATA: оплата отклонена (Declined)\n"
            f"orderId: {order_id}\n"
            f"transactionId: {transaction_id}\n"
            f"errorCode: {error_code}\n"
            f"errorDescription: {str(error_description or '')[:300]}",
            force=False,
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
        if lookup["status"] == "not_found":
            # Wata подтвердил Paid, а pending_purchase row отсутствует —
            # это data loss / orphaned invoice.  Дёргаем админа, пусть
            # руками сверит по ID транзакции.
            try:
                from app.services.admin_alerts import send_alert
                echo_user = body.get("userId") or body.get("customerUserId") or "—"
                details = (
                    f"Wata webhook Paid, но pending_purchase не найден!\n"
                    f"orderId: {order_id}\n"
                    f"transactionId: {transaction_id}\n"
                    f"amount: {amount} RUB\n"
                    f"echo userId (from invoice.userId): {echo_user}\n"
                    f"Действие: свериться в кабинете Wata по tx id, "
                    f"найти юзера и вручную оформить подписку/возврат."
                )
                await send_alert(bot, "payment", details, force=True)
            except Exception as e:  # noqa: BLE001
                logger.error("wata orphan-webhook admin alert failed: %s", e)
        return lookup
    pending_purchase = lookup["purchase"]
    telegram_id = lookup["telegram_id"]
    expected_amount = pending_purchase["price_kopecks"] / 100.0

    if _is_shop_purchase(pending_purchase):
        # МАГАЗИН — прежнее поведение без изменений (SCOPE.md «магазин как есть»).
        # Sanity: сравнить сумму с pending. Если разница > 1 руб — warning.
        raw_amount = float(amount) if amount else 0.0
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
    else:
        # VPN: сумма обязательна и > 0, валюта строго RUB. Иначе — не
        # зачисляем и принудительно алертим админа. Сверку суммы с ценой
        # (недоплата) делает finalize_purchase.
        currency = body.get("currency")
        parsed_amount = _parse_positive_amount(amount)
        reject = None
        if parsed_amount is None:
            reject = "invalid_amount"
        elif str(currency or "").strip().upper() != "RUB":
            reject = "invalid_currency"
        if reject:
            logger.error(
                "WATA_WEBHOOK_REJECTED: reason=%s order=%s tx=%s amount=%r currency=%r "
                "expected=%.2f user=%s",
                reject, order_id, transaction_id, amount, currency,
                expected_amount, telegram_id,
            )
            await _safe_alert(
                bot, "payment",
                f"WATA webhook Paid отклонён: {reject}\n"
                f"orderId: {order_id}\n"
                f"transactionId: {transaction_id}\n"
                f"amount: {amount!r}, currency: {currency!r}\n"
                f"Ожидалось: {expected_amount:.2f} RUB\n"
                f"User TG ID: {telegram_id}\n"
                "Платёж НЕ зачислен. Свериться в кабинете WATA и оформить вручную.",
                force=True,
            )
            return {"status": reject, "purchase_id": order_id}
        amount_rubles = parsed_amount
        if abs(amount_rubles - expected_amount) > 1.0:
            logger.warning(
                "Wata webhook: amount mismatch. order=%s webhook=%s expected=%s",
                order_id, amount_rubles, expected_amount,
            )

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
