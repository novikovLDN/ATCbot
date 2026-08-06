"""Платежи: показатели, разбивки, лента, разбор одной покупки, отказы.

ЧТО ЗДЕСЬ ВАЖНО ЗНАТЬ

    ВЫРУЧКА — ЭТО ВНЕШНИЕ ПОСТУПЛЕНИЯ. `/revenue` считает только покупки
    с COALESCE(payment_provider,'') <> 'balance' (см.
    database/analytics_revenue.py). Покупка с баланса и автопродление с
    баланса — движение уже учтённых денег, и в выручку они не входят.
    Лента `/recent`, наоборот, показывает всё, включая балансовые: там это
    события, а не деньги. Складывать суммы из ленты и сравнивать их с
    `/revenue` нельзя — числа обязаны разойтись.

    ПОРЯДОК МАРШРУТОВ. Все литеральные пути объявлены ВЫШЕ
    `GET /{payment_id}`. Путь с параметром перехватывает любой
    односегментный путь, объявленный ниже: так в этом проекте умер экран
    отложенных рассылок — `/scheduled` стоял под `/{id}` и отвечал 422.

    ДВА ИСТОЧНИКА ПОКУПОК. Лента и разбор берут pending_purchases —
    единственную таблицу, где есть все потоки. Старая `payments` осталась
    только под `GET /{payment_id}`; её id и id из ленты — разные вещи,
    поэтому лента открывает разбор через `/purchases/{id}`.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

import database
from app.api.dashboard.deps import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


def _parse_since(since: Optional[str]) -> Optional[datetime]:
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "invalid_since")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _serialize(value):
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return None
    return value


@router.get("/pending")
async def payments_pending():
    """All payments stuck in pending — useful for catching webhook
    drops and manual reconciliation."""
    try:
        rows = await database.get_pending_payments()
    except Exception as e:
        logger.exception("DASHBOARD_PAYMENTS_PENDING_FAILED")
        raise HTTPException(500, f"pending_failed: {e}")
    logger.info("DASHBOARD_PAYMENTS_PENDING_OK returned=%s", len(rows or []))
    return _serialize(rows or [])


@router.get("/revenue")
async def payments_revenue(
    hours: int = Query(24, gt=0, le=8760),
    since: Optional[str] = Query(None),
):
    """Total + per-type revenue over the window. `since` (ISO datetime)
    overrides `hours` when present — used by the dashboard "Сегодня (МСК)"
    tile that resets every day at 00:00 Europe/Moscow.
    Source of truth: pending_purchases (status='paid'), без покупок с
    внутреннего баланса — это движение уже учтённых денег."""
    try:
        data = await database.get_revenue_for_period(
            hours, since=_parse_since(since),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "DASHBOARD_PAYMENTS_REVENUE_FAILED hours=%s since=%s", hours, since,
        )
        raise HTTPException(500, f"revenue_failed: {e}")
    logger.info(
        "DASHBOARD_PAYMENTS_REVENUE_OK hours=%s since=%s revenue=%.2f payments=%s",
        hours, since, data.get("revenue_rubles", 0.0), data.get("payments_count"),
    )
    return _serialize(data)


@router.get("/by-provider")
async def payments_by_provider(hours: int = Query(24, gt=0, le=8760)):
    """Breakdown of paid purchases by payment provider (platega /
    cryptobot / telegram_stars / lava / balance / unknown)."""
    try:
        rows = await database.get_payments_by_provider(hours)
    except Exception as e:
        logger.exception("DASHBOARD_PAYMENTS_BY_PROVIDER_FAILED hours=%s", hours)
        raise HTTPException(500, f"by_provider_failed: {e}")
    logger.info(
        "DASHBOARD_PAYMENTS_BY_PROVIDER_OK hours=%s providers=%s",
        hours, len(rows or []),
    )
    return _serialize(rows)


@router.get("/breakdown")
async def payments_breakdown(hours: int = Query(24, gt=0, le=8760)):
    """Full multi-axis breakdown of paid purchases for the last N hours:
    total / by_provider / by_type / by_tariff / by_apple_nominal.
    Одним запросом дашборд получает всё, что нужно для «что купили за
    сегодня/неделю/месяц»."""
    try:
        data = await database.get_payments_breakdown(hours)
    except Exception as e:
        logger.exception("DASHBOARD_PAYMENTS_BREAKDOWN_FAILED hours=%s", hours)
        raise HTTPException(500, f"breakdown_failed: {e}")
    logger.info("DASHBOARD_PAYMENTS_BREAKDOWN_OK hours=%s", hours)
    return _serialize(data)


@router.get("/recent")
async def payments_recent(
    limit: int = Query(100, gt=0, le=500),
    hours: Optional[int] = Query(None, gt=0, le=8760),
    status: Optional[str] = Query(None, regex="^(pending|paid|expired)$"),
    provider: Optional[str] = Query(None, max_length=40),
):
    """Лента покупок для плотной таблицы экрана «Платежи».

    `status` сужает до одного состояния; без него в окне видны все, и
    зависший pending рядом с оплаченным — это и есть повод открыть экран.
    `provider` — способ оплаты; 'balance' здесь означает покупку с
    внутреннего баланса, то есть НЕ выручку. `hours=None` — без границы
    по времени.
    """
    try:
        rows = await database.get_recent_payments_feed(
            limit=limit, hours=hours, status=status, provider=provider,
        )
    except Exception as e:
        logger.exception(
            "DASHBOARD_PAYMENTS_FEED_FAILED hours=%s status=%s provider=%s",
            hours, status, provider,
        )
        raise HTTPException(500, f"recent_failed: {e}")
    logger.info(
        "DASHBOARD_PAYMENTS_FEED_OK returned=%s hours=%s status=%s provider=%s",
        len(rows or []), hours, status, provider,
    )
    return _serialize(rows or [])


@router.get("/traffic")
async def payments_traffic(hours: int = Query(24, gt=0, le=8760)):
    """Stats for GB-traffic purchases (separate flow from subscription)."""
    try:
        data = await database.get_traffic_stats(hours)
    except Exception as e:
        logger.exception("DASHBOARD_PAYMENTS_TRAFFIC_FAILED hours=%s", hours)
        raise HTTPException(500, f"traffic_failed: {e}")
    logger.info("DASHBOARD_PAYMENTS_TRAFFIC_OK hours=%s", hours)
    return _serialize(data)


@router.get("/errors/summary")
async def payments_errors_summary(hours: int = Query(24, gt=0, le=8760)):
    """Счётчики отказов за окно: всего, по стадиям, по провайдерам."""
    try:
        data = await database.get_payment_errors_summary(hours)
    except Exception as e:
        logger.exception("DASHBOARD_PAYMENT_ERRORS_SUMMARY_FAILED hours=%s", hours)
        raise HTTPException(500, f"errors_summary_failed: {e}")
    logger.info(
        "DASHBOARD_PAYMENT_ERRORS_SUMMARY_OK hours=%s total=%s stages=%s",
        hours, data.get("total"), len(data.get("by_stage") or []),
    )
    return _serialize(data)


@router.get("/errors")
async def payments_errors(
    limit: int = Query(100, gt=0, le=500),
    hours: Optional[int] = Query(168, gt=0, le=8760),
    provider: Optional[str] = Query(None, max_length=40),
    stage: Optional[str] = Query(None, max_length=60),
):
    """Несостоявшиеся оплаты построчно — главный инструмент разбора.

    Рядом с каждой ошибкой едет исход: чем кончилась покупка с этим
    purchase_id и есть ли у человека доступ сейчас. Без этого красная
    строка не отвечает на единственный вопрос, ради которого её открыли, —
    надо ли что-то делать руками.

    Тело вебхука провайдера (raw_payload) наружу не уходит; текст
    исключения прогнан через scrub_secrets. Сторожит
    tests/services/test_payment_errors_exposure.py.
    """
    try:
        rows = await database.get_recent_payment_errors(
            limit=limit, hours=hours, provider=provider, stage=stage,
        )
    except Exception as e:
        logger.exception(
            "DASHBOARD_PAYMENT_ERRORS_FAILED hours=%s provider=%s stage=%s",
            hours, provider, stage,
        )
        raise HTTPException(500, f"errors_failed: {e}")
    logger.info(
        "DASHBOARD_PAYMENT_ERRORS_OK returned=%s hours=%s provider=%s stage=%s",
        len(rows or []), hours, provider, stage,
    )
    return _serialize(rows or [])


@router.get("/purchases/{row_id}")
async def purchase_detail(row_id: int = Path(..., gt=0)):
    """Разбор одной покупки: строка, человек, доступ, ошибки по ней.

    Смотрит в pending_purchases — ту же таблицу, откуда пришла строка
    ленты. Соседний `GET /{payment_id}` читает устаревшую `payments`, и
    подставлять туда id из ленты нельзя: это разные нумерации.

    404 — покупки с таким id нет. Пустой объект вместо 404 выглядел бы
    как «покупка без данных».
    """
    try:
        data = await database.get_purchase_detail(row_id)
    except Exception as e:
        logger.exception("DASHBOARD_PURCHASE_DETAIL_FAILED id=%s", row_id)
        raise HTTPException(500, f"purchase_detail_failed: {e}")
    if not data:
        logger.info("DASHBOARD_PURCHASE_DETAIL_NOT_FOUND id=%s", row_id)
        raise HTTPException(404, "Purchase not found")
    logger.info(
        "DASHBOARD_PURCHASE_DETAIL_OK id=%s status=%s errors=%s",
        row_id, data["purchase"]["status"], len(data["errors"]),
    )
    return _serialize(data)


@router.get("/{payment_id}")
async def payment_detail(payment_id: int = Path(..., gt=0)):
    """Строка устаревшей таблицы `payments` по её собственному id.

    Оставлен для старых ссылок. Экран платежей сюда не ходит: его строки
    приходят из pending_purchases, и их id здесь означал бы чужую запись.
    Объявлен последним — путь с параметром съел бы любой литеральный,
    объявленный ниже.
    """
    try:
        row = await database.get_payment(payment_id)
    except Exception as e:
        logger.exception("DASHBOARD_PAYMENT_DETAIL_FAILED id=%s", payment_id)
        raise HTTPException(500, f"payment_detail_failed: {e}")
    if not row:
        logger.info("DASHBOARD_PAYMENT_DETAIL_NOT_FOUND id=%s", payment_id)
        raise HTTPException(404, "Payment not found")
    logger.info("DASHBOARD_PAYMENT_DETAIL_OK id=%s", payment_id)
    return _serialize(row)
