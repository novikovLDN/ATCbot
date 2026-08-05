"""Данные для главного экрана дашборда — «Сводки».

ЧТО ЗДЕСЬ
    Три зоны главного экрана, по одной функции на зону:
      • состояние бизнеса — четыре числа (зона B);
      • «требует внимания» — конкретные объекты, с которыми надо что-то
        делать (зона C);
      • лента событий — последние N штук из трёх источников (зона D).
    Деньги (зона A) живут не здесь, а в database/analytics_revenue.py:
    там определение выручки и все запросы, которые ему подчиняются.

ГЛАВНОЕ ПРАВИЛО ЭТОГО МОДУЛЯ: ОШИБКА НЕ ПРЕВРАЩАЕТСЯ В НОЛЬ
    Соседние отчётные модули на любом сбое возвращают 0 или []. Для
    графика это терпимо, для главного экрана — нет: «0 платежей» и «не
    смогли посчитать» человек читает одинаково, а означают они прямо
    противоположное. Поэтому функции ниже ПОДНИМАЮТ исключение, а
    маршрут (app/api/dashboard/routes/summary.py) ловит его по каждому
    числу отдельно и отдаёт наружу пометку об ошибке вместо значения.

    Единственное исключение — отсутствующая таблица payment_errors: её
    заводит миграция 055, и на базе без неё «ошибок платежей не
    зафиксировано» — это правда, а не сбой. Такой случай логируется и
    отдаёт ноль.

ЧТО ЛЕГКО СЛОМАТЬ
    1. Границы времени. subscriptions.expires_at сравнивается с
       параметром, прогнанным через _to_db_utc, а не с выражением
       NOW() внутри SQL: в проекте колонки разъехались по типам
       (TIMESTAMP и TIMESTAMPTZ), и сравнение с NOW() без зоны молча
       сдвигает границу на три часа. Ошибки при этом не будет.

    2. Список тарифов в счётчике активных подписок. combo_basic и
       combo_plus — отдельные продукты, а не разновидность plus.
       Выкинете их из списка — активных подписок станет меньше, и
       никто не заметит, потому что число просто станет другим.

    3. Тексты ошибок наружу. last_error рассылки и details аудита
       пишутся из исключений, а в исключение может попасть URL с
       токеном бота. Всё, что уходит на экран, проходит через
       _scrub_secrets. Уберёте вызов — токен уедет в браузер админа и
       в логи прокси по дороге.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import asyncpg

import config
from app.utils.security import scrub_secrets
from database.core import get_pool, _to_db_utc

logger = logging.getLogger(__name__)


# Тарифы, которые считаются платной подпиской. combo_* здесь обязаны быть:
# это отдельные продукты со своей ценой, а не «plus с добавкой». Второе
# представление комбо — subscription_type='plus' + is_combo=TRUE — попадает
# в счёт само, через 'plus'.
#
# Список берётся из config, а не выписывается здесь. Ровно из-за второй
# копии, выписанной руками, в database/analytics_stats.py потерялось комбо:
# два счётчика активных подписок показывали разные числа, и понять, какое
# из них верное, можно было только чтением обоих запросов.
_PAID_SUBSCRIPTION_TYPES = config.PAID_SUBSCRIPTION_TYPES

# Сколько платёж может висеть в pending, прежде чем это станет поводом
# посмотреть. Меньше — в список полезут живые, ещё не оплаченные счета.
_STUCK_PAYMENT_MINUTES = 30

# Окно, в котором ищем упавшие рассылки. Рассылка старше двух суток — уже
# не «требует внимания», её либо разобрали, либо она никому не нужна.
_BROADCAST_LOOKBACK_HOURS = 48

# Доля недоставленных, при которой рассылку считаем упавшей. Единичные
# отказы — это заблокировавшие бота люди, а не поломка.
#
# В процентах целым числом, а не долей 0.2: доля ушла бы в запрос как
# float, а сравнение с NUMERIC заставило бы asyncpg требовать Decimal и
# падать на ровном месте. Целое сравнивается целочисленной арифметикой.
_BROADCAST_FAILURE_PERCENT = 20

# Похоже на токен бота: 6+ цифр, двоеточие, 30+ символов. Ровно в таком
# виде он попадает в текст исключения aiogram вместе с URL метода.
#
# Без \b в начале сознательно: в тексте он стоит вплотную к «bot» из
# api.telegram.org/bot<токен>/sendMessage, границы слова там нет, и с \b
# выражение не срабатывало вовсе.
# Очистка текстов исключений от секретов — общая с ошибками платежей
# (app/utils/security.py). Здесь только псевдоним: копия этой функции
# разошлась бы с оригиналом на первом же добавленном шаблоне, и разошлась
# бы молча — пропущенный секрет виден только тому, кто открыл экран.
_scrub_secrets = scrub_secrets


async def get_summary_subscription_counts() -> Dict[str, int]:
    """Два числа зоны B: активные платные подписки и истекающие за 7 дней.

    Одним запросом, потому что оба считаются по одной выборке и
    отличаются только границей по expires_at.

    Активной считается строка со status='active' и не истёкшим сроком,
    без триалов и без bypass-only: и то и другое — не платящие люди, а
    в них тонет полезное число.

    Исключение НЕ гасится: пустой ответ вместо ошибки на этом экране
    запрещён (см. шапку модуля).
    """
    pool = await get_pool()
    now = _to_db_utc(datetime.now(timezone.utc))
    horizon = _to_db_utc(datetime.now(timezone.utc) + timedelta(days=7))
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                   COUNT(*) FILTER (WHERE expires_at > $1)::int AS active,
                   COUNT(*) FILTER (
                       WHERE expires_at > $1 AND expires_at <= $2
                   )::int AS expiring
               FROM subscriptions
               WHERE status = 'active'
                 AND COALESCE(is_bypass_only, FALSE) = FALSE
                 AND COALESCE(source, '') <> 'trial'
                 AND subscription_type = ANY($3::text[])""",
            now, horizon, list(_PAID_SUBSCRIPTION_TYPES),
        )
    return {
        "active": int(row["active"]) if row else 0,
        "expiring_7d": int(row["expiring"]) if row else 0,
    }


async def list_paid_subscriptions(kind: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Тот же набор строк, что стоит за числом зоны B, но списком.

    Нужен, чтобы плитки сводки вели В ОТФИЛЬТРОВАННЫЙ СПИСОК, а не просто
    на экран пользователей. Число, по которому нельзя провалиться, — это
    число, из которого не следует действие.

    kind:
        'active'      — все активные платные;
        'expiring_7d' — из них те, что кончатся в ближайшую неделю.

    Условия отбора обязаны совпадать с get_summary_subscription_counts.
    Разъедутся — плитка покажет одно число, а список другой длины, и
    доверие к экрану кончится быстрее, чем найдётся причина.
    """
    if kind not in ("active", "expiring_7d"):
        raise ValueError(f"unknown subscription filter: {kind}")

    pool = await get_pool()
    now = _to_db_utc(datetime.now(timezone.utc))
    params: list = [now, list(_PAID_SUBSCRIPTION_TYPES)]

    # Верхнюю границу дописываем параметром, а не выражением
    # `($2::timestamptz IS NULL OR …)`: явный каст в запросе заставил бы
    # asyncpg кодировать naive-время иначе, чем в соседнем сравнении с $1,
    # и граница «истекает через 7 дней» могла бы уехать на три часа. Такое
    # расхождение не падает — его находят по неверному списку.
    upper_clause = ""
    if kind == "expiring_7d":
        params.append(_to_db_utc(datetime.now(timezone.utc) + timedelta(days=7)))
        upper_clause = f"AND s.expires_at <= ${len(params)}"
    params.append(limit)
    limit_idx = len(params)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT s.telegram_id, s.subscription_type, s.expires_at,
                       s.activated_at, s.source, s.auto_renew,
                       COALESCE(s.is_combo, FALSE) AS is_combo,
                       COALESCE(u.username, '') AS username
                FROM subscriptions s
                LEFT JOIN users u ON u.telegram_id = s.telegram_id
                WHERE s.status = 'active'
                  AND COALESCE(s.is_bypass_only, FALSE) = FALSE
                  AND COALESCE(s.source, '') <> 'trial'
                  AND s.subscription_type = ANY($2::text[])
                  AND s.expires_at > $1
                  {upper_clause}
                ORDER BY s.expires_at ASC
                LIMIT ${limit_idx}""",
            *params,
        )
    return [
        {
            "telegram_id": int(r["telegram_id"]),
            "username": r["username"] or None,
            "subscription_type": r["subscription_type"],
            "is_combo": bool(r["is_combo"]),
            "source": r["source"],
            "auto_renew": bool(r["auto_renew"]),
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "activated_at": r["activated_at"].isoformat() if r["activated_at"] else None,
        }
        for r in rows
    ]


async def get_failed_payments_count(hours: int = 24) -> int:
    """Сколько попыток оплаты сорвалось за окно.

    Источник — payment_errors: в payments провалов нет по построению,
    там строка появляется уже подтверждённой (см. разбор в
    database/analytics_stats.py::get_business_metrics).

    Отсутствие таблицы — не ошибка: её заводит миграция 055, и до неё
    ошибок действительно не записано. Любой другой сбой поднимается
    наверх.
    """
    pool = await get_pool()
    since = _to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours))
    try:
        async with pool.acquire() as conn:
            n = await conn.fetchval(
                "SELECT COUNT(*)::int FROM payment_errors WHERE created_at >= $1",
                since,
            )
    except asyncpg.UndefinedTableError:
        logger.warning(
            "SUMMARY_PAYMENT_ERRORS_NO_TABLE — миграция 055 не применена, "
            "считаем, что сорвавшихся оплат нет",
        )
        return 0
    return int(n or 0)


async def get_stuck_payments(limit: int = 5) -> List[Dict[str, Any]]:
    """Платежи, зависшие в pending дольше получаса.

    Такая строка означает ровно одно: счёт выставлен, а подтверждение от
    провайдера не доехало — потерянный вебхук. Самые старые сверху: чем
    дольше висит, тем вероятнее, что человек уже заплатил и ждёт.

    amount в payments лежит в копейках — наружу отдаём рубли.
    """
    pool = await get_pool()
    cutoff = _to_db_utc(
        datetime.now(timezone.utc) - timedelta(minutes=_STUCK_PAYMENT_MINUTES)
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.id, p.telegram_id, p.tariff, p.amount,
                      p.payment_provider, p.created_at,
                      COALESCE(u.username, '') AS username
               FROM payments p
               LEFT JOIN users u ON u.telegram_id = p.telegram_id
               WHERE p.status = 'pending' AND p.created_at < $1
               ORDER BY p.created_at ASC
               LIMIT $2""",
            cutoff, limit,
        )
    return [
        {
            "payment_id": int(r["id"]),
            "telegram_id": int(r["telegram_id"]),
            "username": r["username"] or None,
            "tariff": r["tariff"],
            "amount_rubles": (int(r["amount"]) / 100) if r["amount"] is not None else None,
            "provider": r["payment_provider"],
            "at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def get_failed_broadcasts(limit: int = 5) -> List[Dict[str, Any]]:
    """Рассылки, которые не доехали.

    Два разных повода, оба про «отправили и не сработало»:

      • обычная рассылка, у которой доля недоставленных перевалила за
        порог. Единичные отказы — это заблокировавшие бота люди, и
        показывать их как проблему значит приучить не смотреть в раздел;

      • отложенная рассылка, у которой планировщик записал last_error.
        Там ошибка одна на всё задание, доли считать не из чего.

    Текст ошибки проходит через _scrub_secrets: в него попадает
    сообщение исключения, а туда — URL метода Telegram с токеном.
    """
    pool = await get_pool()
    since = _to_db_utc(
        datetime.now(timezone.utc) - timedelta(hours=_BROADCAST_LOOKBACK_HOURS)
    )
    out: List[Dict[str, Any]] = []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT b.id, b.title, b.created_at,
                      COUNT(*)::int AS total,
                      COUNT(*) FILTER (WHERE bl.status = 'failed')::int AS failed
               FROM broadcasts b
               JOIN broadcast_log bl ON bl.broadcast_id = b.id
               WHERE b.created_at >= $1
               GROUP BY b.id, b.title, b.created_at
               HAVING COUNT(*) FILTER (WHERE bl.status = 'failed') * 100
                      >= $2 * COUNT(*)
               ORDER BY b.created_at DESC
               LIMIT $3""",
            since, _BROADCAST_FAILURE_PERCENT, limit,
        )
        for r in rows:
            out.append({
                "broadcast_id": int(r["id"]),
                "title": r["title"],
                "total": int(r["total"]),
                "failed": int(r["failed"]),
                "error": None,
                "at": r["created_at"].isoformat() if r["created_at"] else None,
            })

        try:
            sched = await conn.fetch(
                """SELECT id, title, last_error, last_run_at
                   FROM scheduled_broadcasts
                   WHERE last_error IS NOT NULL AND last_run_at >= $1
                   ORDER BY last_run_at DESC
                   LIMIT $2""",
                since, limit,
            )
        except asyncpg.UndefinedTableError:
            # Таблицу заводит миграция 067. До неё отложенных рассылок
            # не существует — падать тут не из-за чего.
            sched = []

    for r in sched:
        out.append({
            "broadcast_id": None,
            "scheduled_id": int(r["id"]),
            "title": r["title"],
            "total": None,
            "failed": None,
            "error": _scrub_secrets(r["last_error"]),
            "at": r["last_run_at"].isoformat() if r["last_run_at"] else None,
        })
    return out[:limit]


async def get_summary_events(limit: int = 20) -> List[Dict[str, Any]]:
    """Лента событий: кто что сделал плюс что случилось само.

    Три источника в одном запросе — иначе фронт склеивал бы их сам и
    ошибался бы на границе: у каждого источника своя частота, и «взять
    по 20 из каждого и обрезать» без общей сортировки в базе даёт
    неверный хвост.

    ИЗ АУДИТА БЕРЁМ ТОЛЬКО ДЕЙСТВИЯ, А НЕ ПРОСМОТРЫ. В audit_log пишутся
    и admin_view_stats, admin_test_menu_viewed и им подобные — они
    появляются каждый раз, когда владелец открывает экран в боте, и лента
    из двадцати строк заполнялась бы его собственным хождением по меню.
    Туда же reminder_sent: их сотни в сутки и это не событие, а фон.
    payment_received и telegram_payment_successful не берём потому, что
    ту же оплату уже отдаёт ветка выше — и отдаёт с суммой.

    ПРО ФИЛЬТР ВЫРУЧКИ. Его здесь СОЗНАТЕЛЬНО нет. Лента отвечает на
    вопрос «что произошло», а покупка с баланса — это произошедшее
    событие, даже если в выручку она не входит (определение выручки —
    REVENUE_EXTERNAL_ONLY_SQL в database/analytics_revenue.py). Не
    копируйте эту строку в денежный запрос: там отсутствие фильтра
    удваивает выручку.

    promo_code из pending_purchases наружу НЕ отдаётся: для покупок
    Spotify в этой колонке лежит пароль клиента.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            (SELECT 'payment'::text        AS kind,
                    pp.created_at          AS happened_at,
                    pp.telegram_id::bigint AS actor_id,
                    NULL::bigint           AS target_id,
                    COALESCE(u.username, '')::text AS username,
                    COALESCE(pp.purchase_type, 'subscription')::text AS title_key,
                    pp.tariff::text        AS tariff,
                    pp.price_kopecks::int  AS amount_kopecks,
                    NULL::text             AS detail
             FROM pending_purchases pp
             LEFT JOIN users u ON u.telegram_id = pp.telegram_id
             WHERE pp.status = 'paid'
             ORDER BY pp.created_at DESC
             LIMIT $1)
            UNION ALL
            (SELECT 'signup'::text, u.created_at, u.telegram_id::bigint,
                    NULL::bigint, COALESCE(u.username, '')::text,
                    'signup'::text, NULL::text, NULL::int, NULL::text
             FROM users u
             ORDER BY u.created_at DESC
             LIMIT $1)
            UNION ALL
            (SELECT 'admin'::text, a.created_at, a.telegram_id::bigint,
                    a.target_user::bigint, ''::text,
                    a.action::text, NULL::text, NULL::int, a.details::text
             FROM audit_log a
             WHERE (a.action LIKE 'admin\\_%'
                    OR a.action IN ('vip_granted', 'vip_revoked',
                                    'broadcast_sent', 'broadcast_deleted',
                                    'broadcast_delete_cancelled'))
               AND a.action NOT LIKE 'admin\\_view%'
               AND a.action NOT LIKE '%\\_viewed'
             ORDER BY a.created_at DESC
             LIMIT $1)
            ORDER BY happened_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [
        {
            "kind": r["kind"],
            "at": r["happened_at"].isoformat() if r["happened_at"] else None,
            "actor_id": int(r["actor_id"]) if r["actor_id"] is not None else None,
            "target_id": int(r["target_id"]) if r["target_id"] is not None else None,
            "username": r["username"] or None,
            "title_key": r["title_key"],
            "tariff": r["tariff"],
            "amount_rubles": (
                int(r["amount_kopecks"]) / 100
                if r["amount_kopecks"] is not None
                else None
            ),
            "detail": _scrub_secrets(r["detail"]),
        }
        for r in rows
    ]
