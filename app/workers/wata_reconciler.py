"""
Wata payment reconciler — защита от потерянных webhook'ов.

Проблема: Wata иногда может не доставить webhook (сеть, downtime, наш сервер
недоступен). Юзер оплатил, деньги дошли, но подписка не активировалась.

Решение: раз в 5 минут сканируем pending_purchases с непустым invoice_id,
старше 5 мин и меньше суток. Для каждого дёргаем wata_service.check_link_status
для сверки. Если status=Paid → финализируем через тот же process_confirmed_payment,
что и обычный webhook.

## Защита от двойного начисления

1. SQL-фильтр status='pending' — уже paid не трогаем.
2. process_confirmed_payment использует mark_pending_purchase_paid, у которого
   UPDATE ... WHERE status='pending' RETURNING id — атомарный row-level lock.
   Если в момент проверки прилетит настоящий webhook, только один из них
   выиграет (другой получит 'already_processed').
3. finalize_purchase внутри транзакции с SELECT FOR UPDATE — гарантирует что
   двойная финализация невозможна.
4. Wata check_link_status возвращает None на 404 (не наш invoice) — просто
   пропускаем.

## Rate limits

Wata API: 1 GET на invoice_id раз в 30 сек. Наш интервал 5 мин + sleep 1сек
между запросами внутри батча → в лимите с большим запасом.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)

RECONCILER_INTERVAL_SEC = 120      # 2 минуты между итерациями (было 5)
STALE_THRESHOLD_MIN = 2            # проверяем pending старше 2 мин (было 5)
MAX_LOOKBACK_HOURS = 24            # не проверяем старше суток (там уже expired)
MAX_BATCH = 20                     # лимит на одну итерацию (rate limit safety)
INTER_ITEM_SLEEP_SEC = 1.0         # пауза между Wata-запросами внутри батча


async def wata_reconciler_task(bot):
    """Main loop: каждые 5 мин проверяет pending Wata покупки."""
    logger.info(
        "WATA_RECONCILER started (interval=%ds, stale_threshold=%dmin, batch=%d)",
        RECONCILER_INTERVAL_SEC, STALE_THRESHOLD_MIN, MAX_BATCH,
    )
    while True:
        try:
            await asyncio.sleep(RECONCILER_INTERVAL_SEC)
        except asyncio.CancelledError:
            logger.info("WATA_RECONCILER stopped (cancelled)")
            return

        try:
            import wata_service
            if not wata_service.is_enabled():
                continue
            await _reconcile_iteration(bot)
        except Exception as e:
            logger.error("WATA_RECONCILER_ITERATION_ERROR: %s", e, exc_info=True)


async def _reconcile_iteration(bot) -> None:
    """Одна итерация: собрать stale pending → проверить каждый через Wata API."""
    import database

    pool = await database.get_pool()
    if pool is None:
        return

    threshold_max = datetime.now(timezone.utc) - timedelta(minutes=STALE_THRESHOLD_MIN)
    threshold_min = datetime.now(timezone.utc) - timedelta(hours=MAX_LOOKBACK_HOURS)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT purchase_id, telegram_id,
                   provider_invoice_id AS invoice_id,
                   price_kopecks, created_at
              FROM pending_purchases
             WHERE status = 'pending'
               AND provider_invoice_id IS NOT NULL
               AND provider_invoice_id <> ''
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
    """Проверить один invoice через Wata; если Paid → финализировать.

    Возвращает: "finalized" | "skipped" | "not_paid" | "not_wata".
    """
    import wata_service
    from app.services.payments.confirmation import process_confirmed_payment

    purchase_id = row["purchase_id"]
    invoice_id = row["invoice_id"]
    telegram_id = row["telegram_id"]

    status_data = await wata_service.check_link_status(invoice_id)
    if not status_data:
        # rate-limit / сеть — попробуем в следующей итерации.
        return "not_wata"

    # 404 → инвойс удалён Wata по retention. Помечаем pending как
    # expired, чтобы reconciler больше не долбил Wata по нему каждые
    # 2 минуты и не спамил логи. Оплата уже никогда не придёт.
    if isinstance(status_data, dict) and status_data.get("_http") == 404:
        try:
            import database
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE pending_purchases SET status = 'expired' "
                    "WHERE purchase_id = $1 AND status = 'pending'",
                    purchase_id,
                )
            logger.info(
                "WATA_RECONCILER_EXPIRED_STALE: purchase=%s invoice=%s "
                "(Wata retention purge — link больше не существует)",
                purchase_id, invoice_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "WATA_RECONCILER_EXPIRE_FAIL: purchase=%s err=%s",
                purchase_id, e,
            )
        return "expired_stale"

    # Wata возвращает объект payment link. Смотрим есть ли Paid транзакция.
    # Возможные поля: transactions[] или transaction (в зависимости от API-версии)
    paid_tx = _find_paid_transaction(status_data)
    if not paid_tx:
        return "not_paid"

    amount = _extract_amount(paid_tx) or (row["price_kopecks"] / 100.0)
    tx_id = paid_tx.get("id") or paid_tx.get("transactionId") or invoice_id

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


def _find_paid_transaction(link_data: Dict[str, Any]) -> Dict[str, Any] | None:
    """Ищем Paid транзакцию в ответе /api/h2h/links/{id}."""
    # Wata возвращает список transactions с status
    txs = link_data.get("transactions") or []
    if isinstance(txs, list):
        for tx in txs:
            if isinstance(tx, dict):
                status = str(tx.get("status") or "").strip()
                kind = str(tx.get("kind") or "Payment").strip()
                if status == "Paid" and kind == "Payment":
                    return tx
    # Некоторые ответы могут содержать сам статус в корне (legacy)
    root_status = str(link_data.get("status") or "").strip()
    if root_status == "Paid":
        return link_data
    return None


def _extract_amount(tx: Dict[str, Any]) -> float | None:
    """Аккуратно достать amount из транзакции (int/float/str)."""
    val = tx.get("amount") or tx.get("paidAmount")
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
