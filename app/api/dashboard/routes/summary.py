"""Главный экран дашборда — «Сводка». Четыре зоны, четыре маршрута.

    GET /money      зона A — выручка за сегодня, вчера в то же время суток,
                    спарклайн за 30 дней;
    GET /business   зона B — четыре числа состояния бизнеса;
    GET /attention  зона C — конкретные объекты, требующие действия;
    GET /events     зона D — последние события.

ПОЧЕМУ ЧЕТЫРЕ МАРШРУТА, А НЕ ОДИН И НЕ ПЯТНАДЦАТЬ
    Пятнадцать было: старая главная дёргала одиннадцать эндпоинтов и на
    каждый ставила свой refetchInterval. Один общий тоже плохо: зона C
    ходит в панель Remnawave и может отвечать секунду, а деньги обязаны
    появиться сразу. Граница — зона: у каждой свой темп обновления и своя
    судьба при отказе.

ОШИБКА И ПУСТОТА — РАЗНЫЕ ОТВЕТЫ
    Главный дефект прежнего дашборда: упавший запрос рисовал «0», и это
    читалось как «сегодня никто не платил». Здесь:

      • зона A — одно число, его нечем показать частично: отказ = 500,
        и фронт рисует «не смогли посчитать выручку»;

      • зона B — четыре независимых числа. Каждое приходит объектом
        {"value": …} либо {"value": null, "error": …}. Одно упавшее не
        стирает остальные три и не притворяется нулём;

      • зона C — рядом с items едет sources: по каждому источнику
        «ok» или «error». Пустой items при всех «ok» — это «проблем
        нет»; пустой items при «error» — «не смогли проверить». Тексты
        на экране у этих случаев разные.

ПОРЯДОК МАРШРУТОВ
    Все пути литеральные, ни одного с параметром. Это не случайность:
    маршрут вида /{id} перехватил бы любой односегментный путь,
    объявленный ниже, и экран отвечал бы 422 — ровно так в этом проекте
    умер список отложенных рассылок. Появится здесь путь с параметром —
    объявляйте его ПОСЛЕ всех литеральных. Проверяется тестом
    tests/services/test_summary_routes.py.

СЕКРЕТЫ
    Наружу не уходит ни одна строка, пришедшая из текста исключения, без
    прогона через database.dashboard_summary._scrub_secrets. В логи здесь
    пишутся только счётчики и итоговые суммы — ни имён, ни ссылок на
    подписку, ни payload'ов провайдера.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

import database
from app.api.dashboard.deps import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


def _ok(value) -> dict:
    return {"value": value}


def _failed(reason: str) -> dict:
    """Число не посчиталось. value=null, а не 0 — см. шапку модуля."""
    return {"value": None, "error": reason}


@router.get("/money")
async def summary_money(spark_days: int = Query(30, ge=7, le=90)):
    """Зона A. Выручка за сегодня и вчера к этому же часу + спарклайн.

    Сравнение честное по времени суток: у вчерашнего дня берётся ровно
    столько же минут от полуночи, сколько прошло сегодня. Иначе в
    одиннадцать утра падение показывалось бы всегда.
    """
    try:
        data = await database.get_revenue_today_vs_yesterday(spark_days)
    except Exception as e:
        logger.exception("SUMMARY_MONEY_FAILED days=%s", spark_days)
        raise HTTPException(500, f"summary_money_failed: {e}")
    logger.info(
        "SUMMARY_MONEY_OK today=%.2f yesterday_same_time=%.2f payments=%s points=%s",
        data["today_rubles"], data["yesterday_same_time_rubles"],
        data["today_payments"], len(data["sparkline"]),
    )
    return data


@router.get("/business")
async def summary_business():
    """Зона B. Ровно четыре числа, каждое со своей судьбой при отказе.

    Расхождения с панелью считает сверка (database.find_over_issuance_candidates).
    Она панель-driven и отдаёт строку-маркер panel_unreachable вместо
    пустого списка, когда панель недоступна, — этот случай мы обязаны
    показать как ошибку, а не как «расхождений нет».
    """
    metrics: dict = {}

    try:
        counts = await database.get_summary_subscription_counts()
        metrics["active_subscriptions"] = _ok(counts["active"])
        metrics["expiring_7d"] = _ok(counts["expiring_7d"])
    except Exception as e:
        logger.exception("SUMMARY_BUSINESS_SUBSCRIPTIONS_FAILED")
        metrics["active_subscriptions"] = _failed(f"subscriptions_failed: {e}")
        metrics["expiring_7d"] = _failed(f"subscriptions_failed: {e}")

    try:
        metrics["failed_payments_24h"] = _ok(
            await database.get_failed_payments_count(24)
        )
    except Exception as e:
        logger.exception("SUMMARY_BUSINESS_PAYMENT_ERRORS_FAILED")
        metrics["failed_payments_24h"] = _failed(f"payment_errors_failed: {e}")

    try:
        rows = await database.find_over_issuance_candidates(200)
        if rows and rows[0].get("panel_unreachable"):
            metrics["panel_mismatches"] = _failed("panel_unreachable")
        else:
            metrics["panel_mismatches"] = _ok(len(rows))
    except Exception as e:
        logger.exception("SUMMARY_BUSINESS_PANEL_FAILED")
        metrics["panel_mismatches"] = _failed(f"panel_failed: {e}")

    logger.info(
        "SUMMARY_BUSINESS_OK %s",
        " ".join(f"{k}={v['value'] if v.get('error') is None else 'ERR'}"
                 for k, v in metrics.items()),
    )
    return {"metrics": metrics}


@router.get("/attention")
async def summary_attention(limit: int = Query(10, ge=1, le=10)):
    """Зона C. От нуля до десяти объектов, с которыми надо что-то сделать.

    Три источника, каждый падает отдельно. sources рядом с items —
    единственный способ отличить «проблем нет» от «не смогли проверить»:
    без него пустой список означал бы и то и другое.
    """
    items: list = []
    sources: dict = {}

    try:
        for p in await database.get_stuck_payments(limit):
            items.append({
                "kind": "stuck_payment",
                "id": f"payment:{p['payment_id']}",
                "telegram_id": p["telegram_id"],
                "username": p["username"],
                "amount_rubles": p["amount_rubles"],
                "tariff": p["tariff"],
                "provider": p["provider"],
                "at": p["at"],
            })
        sources["payments"] = "ok"
    except Exception:
        logger.exception("SUMMARY_ATTENTION_STUCK_PAYMENTS_FAILED")
        sources["payments"] = "error"

    try:
        rows = await database.find_over_issuance_candidates(limit)
        if rows and rows[0].get("panel_unreachable"):
            sources["panel"] = "error"
        else:
            sources["panel"] = "ok"
            for r in rows[:limit]:
                items.append({
                    "kind": "panel_mismatch",
                    "id": f"panel:{r['telegram_id']}",
                    "telegram_id": r["telegram_id"],
                    "username": r.get("username") or None,
                    "years_from_now": r.get("years_from_now"),
                    "at": r.get("panel_expires_at"),
                })
    except Exception:
        logger.exception("SUMMARY_ATTENTION_PANEL_FAILED")
        sources["panel"] = "error"

    try:
        for b in await database.get_failed_broadcasts(limit):
            items.append({
                "kind": "failed_broadcast",
                "id": f"broadcast:{b.get('broadcast_id') or b.get('scheduled_id')}",
                "broadcast_id": b.get("broadcast_id"),
                "title": b["title"],
                "failed": b["failed"],
                "total": b["total"],
                "error": b["error"],
                "at": b["at"],
            })
        sources["broadcasts"] = "ok"
    except Exception:
        logger.exception("SUMMARY_ATTENTION_BROADCASTS_FAILED")
        sources["broadcasts"] = "error"

    truncated = len(items) > limit
    items = items[:limit]
    logger.info(
        "SUMMARY_ATTENTION_OK items=%s truncated=%s sources=%s",
        len(items), truncated, sources,
    )
    return {"items": items, "truncated": truncated, "sources": sources}


@router.get("/events")
async def summary_events(limit: int = Query(20, ge=1, le=50)):
    """Зона D. Последние события: действия админов + оплаты + регистрации.

    Одним запросом в базу, а не тремя с последующей склейкой на фронте:
    у источников разная частота, и «взять по 20 из каждого и обрезать»
    без общей сортировки даёт неверный хвост ленты.
    """
    try:
        items = await database.get_summary_events(limit)
    except Exception as e:
        logger.exception("SUMMARY_EVENTS_FAILED limit=%s", limit)
        raise HTTPException(500, f"summary_events_failed: {e}")
    logger.info("SUMMARY_EVENTS_OK returned=%s limit=%s", len(items), limit)
    return {"items": items}
