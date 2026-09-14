"""
Wata payment reconciler — защита от потерянных webhook'ов.

Проблема: Wata иногда может не доставить webhook (сеть, downtime, наш сервер
недоступен). Юзер оплатил, деньги дошли, но подписка не активировалась.

Решение: раз в 2 минуты сканируем pending_purchases с непустым invoice_id,
старше 2 мин и меньше суток, и ищем оплату документированным способом
(docs/providers/wata_api.md):

    GET {WATA_API_URL}/v2/transactions/?orderId=<purchase_id>

(orderId = наш purchase_id, его проставляет wata_service.create_invoice).
Объект платёжной ссылки (/links/{id}) имеет статус только Opened|Closed и
транзакций не содержит — по нему оплату не определить.

Если нашли транзакцию status=Paid, kind=Payment с нашим orderId →
финализируем через тот же process_confirmed_payment, что и обычный webhook.

## Какие строки сканируем (provider-scoped)

- payment_provider = 'wata'  → полный цикл: поиск оплаты + истечение по 404
  ссылки (Wata retention purge).
- payment_provider IS NULL   → легаси-строки и покупки магазина (магазин
  provider не проставляет до финализации). Ищем оплату по orderId — Paid-
  транзакция WATA с нашим orderId сама по себе доказывает оплату через WATA.
  НО такие строки НИКОГДА не помечаем expired: провайдер неизвестен, и 404
  от /links может означать «это вообще не WATA-инвойс» (Platega/CryptoBot).
- любой другой provider      → не трогаем вообще (раньше reconciler дёргал
  /links/{id} по чужим id и помечал их expired по 404).

## Сверка суммы и валюты

VPN-покупки: сумма Paid-транзакции должна совпасть с price_kopecks/100
(допуск 1 ₽, как validate_payment_amount) и валюта = RUB. Иначе НЕ
финализируем, шлём алерт админу (один раз на purchase_id в процессе).
В финализацию передаём ОЖИДАЕМУЮ сумму, а не присланную.
Магазин (docs/audit/SCOPE.md: «магазин как есть»): прежнее поведение —
сумма из транзакции либо ожидаемая, без сверки.

## Защита от двойного начисления

1. SQL-фильтр status='pending' — уже paid не трогаем.
2. process_confirmed_payment использует mark_pending_purchase_paid, у которого
   UPDATE ... WHERE status='pending' RETURNING id — атомарный row-level lock.
   Если в момент проверки прилетит настоящий webhook, только один из них
   выиграет (другой получит 'already_processed').
3. finalize_purchase внутри транзакции с SELECT FOR UPDATE — гарантирует что
   двойная финализация невозможна.

## Rate limits

Wata API: 1 GET на объект раз в 30 сек (429 при превышении). Поиск по
orderId переиспользуют fast-poll и кнопка «Проверить» (payments_callbacks),
поэтому lookup_paid_transaction держит in-process троттл 30 с на orderId.
Любой 429 / сеть / 5xx = «неизвестно, повторим позже», НИКОГДА не «не оплачено».
Удалённый 429 прерывает текущий батч reconciler'а.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

RECONCILER_INTERVAL_SEC = 120      # 2 минуты между итерациями (было 5)
STALE_THRESHOLD_MIN = 2            # проверяем pending старше 2 мин (было 5)
MAX_LOOKBACK_HOURS = 24            # не проверяем старше суток (там уже expired)
MAX_BATCH = 20                     # лимит на одну итерацию (rate limit safety)
INTER_ITEM_SLEEP_SEC = 1.0         # пауза между Wata-запросами внутри батча

WATA_GET_MIN_INTERVAL_SEC = 30.0   # документированный лимит: 1 GET / 30 с на объект
AMOUNT_TOLERANCE_RUB = 1.0         # как validate_payment_amount (service.py)
EXPECTED_CURRENCY = "RUB"

# Результаты lookup_paid_transaction / resolve_wata_payment
LOOKUP_PAID = "paid"
LOOKUP_NOT_PAID = "not_paid"
LOOKUP_UNKNOWN = "unknown"            # сеть / 5xx / локальный троттл — повторить позже
LOOKUP_RATE_LIMITED = "rate_limited"  # Wata ответил 429 — повторить позже
LOOKUP_MISMATCH = "mismatch"          # Paid, но сумма/валюта не сходятся (VPN)

# Магазин — те же признаки, что в confirmation.process_confirmed_payment
# (кроме proxy: это не магазин по docs/audit/SCOPE.md).
_SHOP_PURCHASE_TYPES = frozenset(
    {"telegram_stars", "telegram_premium", "steam", "spotify", "apple_id"},
)
_SHOP_TARIFF_PREFIXES = ("apple_id_", "steam_", "spotify_")

# purchase_id, по которым алерт о расхождении уже отправлен (in-process dedupe).
_MISMATCH_ALERTED: set[str] = set()
# orderId → monotonic-время последнего GET (in-process троттл 30 с).
_LAST_LOOKUP_AT: Dict[str, float] = {}
_LAST_LOOKUP_MAX_ENTRIES = 5000


async def wata_reconciler_task(bot):
    """Main loop: каждые RECONCILER_INTERVAL_SEC проверяет pending Wata покупки."""
    logger.info(
        "WATA_RECONCILER started (interval=%ds, stale_threshold=%dmin, batch=%d)",
        RECONCILER_INTERVAL_SEC, STALE_THRESHOLD_MIN, MAX_BATCH,
    )
    from app.core import runtime_health  # dashboard liveness (in-memory)
    runtime_health.register("wata_reconciler", interval_s=RECONCILER_INTERVAL_SEC + 300,
                            initial_delay_s=RECONCILER_INTERVAL_SEC)
    while True:
        try:
            await asyncio.sleep(RECONCILER_INTERVAL_SEC)
        except asyncio.CancelledError:
            logger.info("WATA_RECONCILER stopped (cancelled)")
            return

        try:
            import wata_service
            if not wata_service.is_enabled():
                runtime_health.record("wata_reconciler", "skipped")
                continue
            await _reconcile_iteration(bot)
            runtime_health.beat("wata_reconciler")
        except Exception as e:
            logger.error("WATA_RECONCILER_ITERATION_ERROR: %s", e, exc_info=True)
            runtime_health.fail("wata_reconciler", e)


async def _reconcile_iteration(bot) -> None:
    """Одна итерация: собрать stale pending → проверить каждый через Wata API."""
    import database

    pool = await database.get_pool()
    if pool is None:
        return

    threshold_max = datetime.now(timezone.utc) - timedelta(minutes=STALE_THRESHOLD_MIN)
    threshold_min = datetime.now(timezone.utc) - timedelta(hours=MAX_LOOKBACK_HOURS)

    # БД-соединение отпускаем ДО HTTP-вызовов (железное правило).
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT purchase_id, telegram_id,
                   provider_invoice_id AS invoice_id,
                   price_kopecks, created_at,
                   payment_provider, purchase_type, tariff
              FROM pending_purchases
             WHERE status = 'pending'
               AND provider_invoice_id IS NOT NULL
               AND provider_invoice_id <> ''
               AND (payment_provider = 'wata' OR payment_provider IS NULL)
               AND created_at < $1
               AND created_at > $2
             ORDER BY created_at DESC
             LIMIT $3
            """,
            threshold_max, threshold_min, MAX_BATCH,
        )

    if not rows:
        return

    logger.info(
        "WATA_RECONCILER: scanning %d stale pending purchases",
        len(rows),
    )

    checked, finalized, skipped = 0, 0, 0
    for row in rows:
        outcome = None
        try:
            outcome = await _check_and_finalize(bot, dict(row))
            checked += 1
            if outcome == "finalized":
                finalized += 1
            else:
                skipped += 1
        except Exception as e:
            logger.warning(
                "WATA_RECONCILER_ITEM_FAIL: purchase=%s err=%s",
                row["purchase_id"], e,
            )
        if outcome == LOOKUP_RATE_LIMITED:
            logger.warning(
                "WATA_RECONCILER_RATE_LIMITED: Wata returned 429 — "
                "stopping batch, retry next iteration (checked=%d)",
                checked,
            )
            break
        # Не спамить Wata API — держим паузу между итемами
        await asyncio.sleep(INTER_ITEM_SLEEP_SEC)

    if finalized > 0:
        logger.warning(
            "WATA_RECONCILER_SUMMARY: checked=%d finalized=%d skipped=%d — "
            "%d lost webhook(s) recovered!",
            checked, finalized, skipped, finalized,
        )
    else:
        logger.info(
            "WATA_RECONCILER_SUMMARY: checked=%d skipped=%d (all pending have no Paid tx yet)",
            checked, skipped,
        )


async def _check_and_finalize(bot, row: Dict[str, Any]) -> str:
    """Проверить одну pending-покупку через Wata; если Paid → финализировать.

    Возвращает: "finalized" | "skipped" | "not_paid" | "not_wata" |
    "expired_stale" | "unknown" | "rate_limited" | "mismatch".
    """
    from app.services.payments.confirmation import process_confirmed_payment

    purchase_id = row["purchase_id"]
    invoice_id = row.get("invoice_id")
    telegram_id = row["telegram_id"]
    provider = (row.get("payment_provider") or "").strip() or None

    # Защита в коде поверх SQL-фильтра: чужих провайдеров не трогаем.
    if provider is not None and provider != "wata":
        return "not_wata"

    res = await resolve_wata_payment(purchase_id, row, bot=bot)
    outcome = res["outcome"]

    if outcome == LOOKUP_NOT_PAID and provider == "wata":
        # Только для строк, про которые ТОЧНО известно, что это WATA:
        # 404 по ссылке → инвойс удалён Wata по retention. Помечаем
        # expired, чтобы не долбить Wata каждые 2 минуты. Поздний webhook
        # всё равно пройдёт: lookup_pending_purchase принимает 'expired'.
        if await _expire_if_link_purged(purchase_id, invoice_id):
            return "expired_stale"
        return LOOKUP_NOT_PAID

    if outcome != LOOKUP_PAID:
        return outcome

    amount = res["amount"]
    tx_id = res["tx_id"]
    logger.warning(
        "WATA_RECONCILER_MISSED_WEBHOOK: purchase=%s tx=%s amount=%.2f — "
        "будет финализировано через process_confirmed_payment",
        purchase_id, tx_id, amount,
    )

    # Идемпотентно: если pending уже обработан (webhook пришёл параллельно),
    # process_confirmed_payment вернёт 'already_processed' и НЕ создаст
    # дублирующего начисления. Row-level lock в mark_pending_purchase_paid
    # + SELECT FOR UPDATE в finalize_purchase гарантируют single-writer.
    result = await process_confirmed_payment(
        provider="wata",
        purchase_id=purchase_id,
        amount_rubles=float(amount),
        invoice_id=str(tx_id),
        telegram_id=int(telegram_id),
        bot=bot,
    )
    outcome_status = (result or {}).get("status", "unknown")
    logger.info(
        "WATA_RECONCILER_FINALIZE_RESULT: purchase=%s status=%s",
        purchase_id, outcome_status,
    )
    return "finalized" if outcome_status in ("ok", "already_processed") else "skipped"


async def _expire_if_link_purged(purchase_id: str, invoice_id: Optional[str]) -> bool:
    """GET /links/{id}; при 404 пометить pending как expired. True если помечено."""
    import wata_service

    if not invoice_id:
        return False
    status_data = await wata_service.check_link_status(invoice_id)
    if not (isinstance(status_data, dict) and status_data.get("_http") == 404):
        return False
    try:
        import database
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE pending_purchases SET status = 'expired' "
                "WHERE purchase_id = $1 AND status = 'pending' "
                "AND payment_provider = 'wata'",
                purchase_id,
            )
        logger.info(
            "WATA_RECONCILER_EXPIRED_STALE: purchase=%s invoice=%s "
            "(Wata retention purge — link больше не существует)",
            purchase_id, invoice_id,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "WATA_RECONCILER_EXPIRE_FAIL: purchase=%s err=%s",
            purchase_id, e,
        )
        return False


# ── Documented payment lookup (shared with fast-poll / «Проверить») ──────

def _throttle_allows(order_id: str) -> bool:
    """In-process троттл: не чаще 1 GET / 30 с на orderId."""
    now = time.monotonic()
    last = _LAST_LOOKUP_AT.get(order_id)
    if last is not None and now - last < WATA_GET_MIN_INTERVAL_SEC:
        return False
    if len(_LAST_LOOKUP_AT) >= _LAST_LOOKUP_MAX_ENTRIES:
        cutoff = now - WATA_GET_MIN_INTERVAL_SEC
        for key in [k for k, t in _LAST_LOOKUP_AT.items() if t < cutoff]:
            _LAST_LOOKUP_AT.pop(key, None)
    _LAST_LOOKUP_AT[order_id] = now
    return True


async def lookup_paid_transaction(order_id: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """GET {WATA_API_URL}/v2/transactions/?orderId=<order_id>.

    Returns (outcome, tx):
      (LOOKUP_PAID, tx)          — есть Paid Payment-транзакция с этим orderId
      (LOOKUP_NOT_PAID, None)    — ответ 200, Paid-транзакции нет
      (LOOKUP_RATE_LIMITED, None)— Wata ответил 429
      (LOOKUP_UNKNOWN, None)     — сеть / non-200 / битый JSON / локальный троттл
    """
    import wata_service

    if not order_id or not wata_service.is_enabled():
        return LOOKUP_UNKNOWN, None
    if not _throttle_allows(str(order_id)):
        return LOOKUP_UNKNOWN, None

    url = f"{wata_service.WATA_API_URL}/v2/transactions/"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url,
                headers=wata_service._headers(),
                params={"orderId": str(order_id)},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("WATA_TX_LOOKUP_ERROR: order=%s err=%s", order_id, e)
        return LOOKUP_UNKNOWN, None

    if response.status_code == 429:
        logger.warning("WATA_TX_LOOKUP_RATE_LIMITED: order=%s", order_id)
        return LOOKUP_RATE_LIMITED, None
    if response.status_code == 401:
        logger.error("WATA_TX_LOOKUP_UNAUTHORIZED: access token rejected (401)")
        return LOOKUP_UNKNOWN, None
    if response.status_code != 200:
        logger.warning(
            "WATA_TX_LOOKUP_FAILED: order=%s status=%d",
            order_id, response.status_code,
        )
        return LOOKUP_UNKNOWN, None
    try:
        data = response.json()
    except ValueError as e:
        logger.warning("WATA_TX_LOOKUP_BAD_JSON: order=%s err=%s", order_id, e)
        return LOOKUP_UNKNOWN, None

    tx = _find_paid_transaction(data, order_id=str(order_id))
    if tx:
        return LOOKUP_PAID, tx
    return LOOKUP_NOT_PAID, None


async def resolve_wata_payment(
    purchase_id: str,
    purchase: Dict[str, Any],
    *,
    bot=None,
) -> Dict[str, Any]:
    """Найти и проверить оплату WATA для pending-покупки.

    Общая точка для reconciler'а, fast-poll и кнопки «Проверить».
    Returns {"outcome": ..., "amount": float, "tx_id": str} — amount/tx_id
    только при outcome == LOOKUP_PAID; при LOOKUP_MISMATCH есть "reason".
    """
    outcome, tx = await lookup_paid_transaction(purchase_id)
    if outcome != LOOKUP_PAID or not tx:
        return {"outcome": outcome}

    expected = int(purchase.get("price_kopecks") or 0) / 100.0
    tx_id = str(
        tx.get("id")
        or tx.get("transactionId")
        or purchase.get("provider_invoice_id")
        or purchase.get("invoice_id")
        or purchase_id
    )

    if _is_shop_purchase(purchase):
        # Магазин как есть (SCOPE.md): прежняя логика суммы, без сверки.
        amount = _extract_amount(tx) or expected
        return {"outcome": LOOKUP_PAID, "amount": float(amount), "tx_id": tx_id}

    reason = _verify_vpn_transaction(tx, expected)
    if reason:
        await _alert_mismatch(bot, purchase_id, purchase, tx, reason, expected)
        return {"outcome": LOOKUP_MISMATCH, "reason": reason}
    # Зачисляем ОЖИДАЕМУЮ сумму (сверена в пределах допуска).
    return {"outcome": LOOKUP_PAID, "amount": expected, "tx_id": tx_id}


def _is_shop_purchase(purchase: Dict[str, Any]) -> bool:
    purchase_type = str(purchase.get("purchase_type") or "")
    tariff = str(purchase.get("tariff") or "")
    return purchase_type in _SHOP_PURCHASE_TYPES or tariff.startswith(_SHOP_TARIFF_PREFIXES)


def _verify_vpn_transaction(tx: Dict[str, Any], expected_rubles: float) -> Optional[str]:
    """None если сумма и валюта совпадают, иначе причина."""
    amount = _extract_amount(tx)
    if amount is None:
        return "amount_missing"
    if abs(amount - expected_rubles) > AMOUNT_TOLERANCE_RUB:
        return "amount_mismatch"
    currency = str(tx.get("currency") or "").strip().upper()
    if currency != EXPECTED_CURRENCY:
        return "currency_mismatch"
    return None


async def _alert_mismatch(
    bot,
    purchase_id: str,
    purchase: Dict[str, Any],
    tx: Dict[str, Any],
    reason: str,
    expected_rubles: float,
) -> None:
    if purchase_id in _MISMATCH_ALERTED:
        return
    _MISMATCH_ALERTED.add(purchase_id)
    logger.error(
        "WATA_PAYMENT_MISMATCH: purchase=%s tx=%s reason=%s amount=%s currency=%s "
        "expected=%.2f %s — NOT finalized",
        purchase_id, tx.get("id") or tx.get("transactionId"), reason,
        tx.get("amount"), tx.get("currency"), expected_rubles, EXPECTED_CURRENCY,
    )
    if bot is None:
        return
    try:
        from app.services.admin_alerts import send_alert
        details = (
            f"WATA: Paid-транзакция не совпадает с покупкой — НЕ финализировано.\n"
            f"purchase_id: {purchase_id}\n"
            f"telegram_id: {purchase.get('telegram_id')}\n"
            f"transactionId: {tx.get('id') or tx.get('transactionId')}\n"
            f"причина: {reason}\n"
            f"пришло: {tx.get('amount')} {tx.get('currency')}\n"
            f"ожидалось: {expected_rubles:.2f} {EXPECTED_CURRENCY}\n"
            f"Действие: сверить в кабинете Wata и оформить вручную/вернуть."
        )
        await send_alert(bot, "payment", details, force=True)
    except Exception as e:  # noqa: BLE001
        logger.error("WATA_PAYMENT_MISMATCH alert failed: purchase=%s err=%s", purchase_id, e)


def _find_paid_transaction(
    data: Any,
    order_id: Optional[str] = None,
) -> Dict[str, Any] | None:
    """Найти Paid Payment-транзакцию в ответе Wata.

    Понимает:
      - ответ поиска /v2/transactions/ — {"items": [...]} или список;
      - одиночный объект транзакции (/transactions/{id}) — статус в корне;
      - легаси-форму {"transactions": [...]}.
    Если задан order_id — учитываются только транзакции с этим orderId
    (защита от чужой транзакции, если фильтр API не сработал).
    Объект ссылки (status Opened|Closed) никогда не считается оплатой.
    """
    candidates: list = []
    if isinstance(data, list):
        candidates.extend(data)
    elif isinstance(data, dict):
        for key in ("items", "transactions"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        candidates.append(data)

    for tx in candidates:
        if not isinstance(tx, dict):
            continue
        status = str(tx.get("status") or tx.get("transactionStatus") or "").strip()
        kind = str(tx.get("kind") or "Payment").strip()
        if status != "Paid" or kind != "Payment":
            continue
        if order_id is not None and str(tx.get("orderId") or "") != str(order_id):
            continue
        return tx
    return None


def _extract_amount(tx: Dict[str, Any]) -> float | None:
    """Аккуратно достать amount из транзакции (int/float/str)."""
    val = tx.get("amount")
    if val is None:
        val = tx.get("paidAmount")
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
