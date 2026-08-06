"""
Analytics / business-metrics endpoints.

All routes proxy directly to existing functions in `database.admin`
and `database.subscriptions`. We don't compute anything new here —
the bot already has full coverage, we just expose it over HTTP.

Time-range params accept a trailing-window in hours: `?hours=24`,
`?hours=720` etc. Routes that need a calendar-day window also accept
`?since=<ISO datetime>` which overrides `hours` and uses that as an
absolute lower bound (used for the "Сегодня (МСК)" dashboard tile).

СЕКРЕТЫ
    Текст исключения наружу — только через scrub_secrets. Экраны «Метрики и
    доход» и «Статистика» показывают detail целиком в блоке отказа, то есть
    строка уходит прямо в браузер администратора и оседает в логах прокси.

ЛОГИ
    Каждый обработчик пишет и удачу, и отказ. Числа этих восьми ручек стоят
    на витрине денег, и «экран показал ноль» надо уметь отличить от «запрос
    не дошёл» по логу, а не по памяти.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

import database
from app.api.dashboard.deps import require_admin
from app.utils.security import scrub_secrets

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


def _parse_since(since: str | None) -> datetime | None:
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "invalid_since")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/overview")
async def stats_overview():
    """Один запрос со всеми числами для главной страницы дашборда.

    Основа — get_extended_bot_stats(): пользователи, подписки, выручка,
    конверсия, отток.

    Плюс два блока, которых там нет:

    • active_paid_subscriptions — как active_subscriptions, но без триалов,
      bypass-only и biz-тарифов: только те, кто прямо сейчас платит за VPN.

    • business_metrics — avg_subscription_lifetime_days и
      avg_renewals_per_user. Фронт читает
      их из overview в нескольких местах (Dashboard.tsx), а сюда они не клались
      вовсе: get_extended_bot_stats такого ключа не возвращает, и KPI
      на главной всегда показывали «—». Отдельный /stats/business существует,
      но дашборд его не вызывал.

    Оба блока необязательные: их сбой не должен ронять всю главную страницу,
    поэтому каждый обёрнут своим try и при ошибке отдаёт безопасное значение.
    """
    try:
        data = await database.get_extended_bot_stats()
        try:
            data["active_paid_subscriptions"] = (
                await database.get_active_paid_subscriptions_count()
            )
        except Exception:
            data["active_paid_subscriptions"] = data.get("active_subscriptions")
        try:
            data["business_metrics"] = await database.get_business_metrics()
        except Exception as e:
            logger.warning(
                "stats.overview: business_metrics failed: %s", scrub_secrets(e)
            )
            data["business_metrics"] = {}
        logger.info("stats.overview ok")
        return data
    except Exception as e:
        logger.error("stats.overview failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"stats_overview_failed: {scrub_secrets(e)}")


@router.get("/business")
async def stats_business():
    """avg_subscription_lifetime_days, avg_renewals_per_user.

    «Время апрува» и «процент подтверждённых платежей» отсюда убраны:
    считать их не из чего — подробности в докстринге
    database.analytics.get_business_metrics."""
    try:
        data = await database.get_business_metrics()
    except Exception as e:
        logger.error("stats.business failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"business_metrics_failed: {scrub_secrets(e)}")
    logger.info("stats.business ok")
    return data


@router.get("/revenue")
async def stats_revenue():
    """Aggregate revenue / LTV / ARPU."""
    try:
        total = await database.get_total_revenue()
        paying = await database.get_paying_users_count()
        arpu = await database.get_arpu()
        ltv = await database.get_ltv()
    except Exception as e:
        logger.error("stats.revenue failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"revenue_failed: {scrub_secrets(e)}")
    logger.info("stats.revenue ok")
    return {
        "total_revenue_rubles": total,
        "paying_users": paying,
        "arpu_rubles": arpu,
        "avg_ltv_rubles": ltv,
    }


@router.get("/period")
async def stats_period(
    hours: int = Query(24, gt=0, le=8760),
    since: str | None = Query(None),
):
    """Aggregates over [since, now) or trailing `hours` window.
    `hours` capped at one year. `since` is an ISO datetime — when
    supplied it overrides `hours`."""
    try:
        data = await database.get_analytics_by_period(
            hours, since=_parse_since(since),
        )
    except HTTPException:
        # _parse_since уже отдал 400 invalid_since — не превращаем его в 500.
        raise
    except Exception as e:
        logger.error("stats.period failed: hours=%s %s", hours, scrub_secrets(e))
        raise HTTPException(500, f"period_failed: {scrub_secrets(e)}")
    logger.info("stats.period ok: hours=%s", hours)
    return data


@router.get("/purchase-breakdown")
async def stats_purchase_breakdown():
    """Counts + revenue by tariff and time window."""
    try:
        data = await database.get_purchase_breakdown()
    except Exception as e:
        logger.error("stats.purchase_breakdown failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"breakdown_failed: {scrub_secrets(e)}")
    logger.info("stats.purchase_breakdown ok")
    return data


@router.get("/promo")
async def stats_promo():
    """Promo-code usage stats."""
    try:
        data = await database.get_promo_stats()
    except Exception as e:
        logger.error("stats.promo failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"promo_failed: {scrub_secrets(e)}")
    logger.info("stats.promo ok")
    return data


@router.get("/daily")
async def stats_daily(days: int = Query(30, gt=0, le=180)):
    """Daily time-series for the dashboard charts.

    Returns one row per UTC day in window [NOW-days, NOW], including
    empty days (zeros). Single query — frontend builds revenue / new
    users / new subs charts off this one payload.
    """
    try:
        data = await database.get_daily_timeseries(days)
    except Exception as e:
        logger.error("stats.daily failed: days=%s %s", days, scrub_secrets(e))
        raise HTTPException(500, f"daily_failed: {scrub_secrets(e)}")
    logger.info("stats.daily ok: days=%s", days)
    return data


@router.get("/hourly")
async def stats_hourly(days: int = Query(7, gt=0, le=90)):
    """Hour-of-day breakdown за последние `days` суток.

    Возвращает массив из 24 строк (hour 0..23, Europe/Moscow) с
    суммарными revenue / payments / new_users / new_subs за окно.
    Полезно понять, в какие часы юзеры покупают и когда пик.
    """
    try:
        data = await database.get_hourly_timeseries(days)
    except Exception as e:
        logger.error("stats.hourly failed: days=%s %s", days, scrub_secrets(e))
        raise HTTPException(500, f"hourly_failed: {scrub_secrets(e)}")
    logger.info("stats.hourly ok: days=%s", days)
    return data
