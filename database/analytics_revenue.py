"""Деньги: выручка, средние чеки, LTV и ARPU.

ЧТО ЗДЕСЬ
    Только чтение и только по таблицам оплат. Сводки за период, разрезы по
    провайдеру / типу покупки / тарифу, покупки трафика и три средних —
    LTV на платящего, ARPU на зарегистрированного, средний чек.

ПОЧЕМУ ОТДЕЛЬНО ОТ ОСТАЛЬНОЙ АНАЛИТИКИ
    Здесь живёт определение выручки (REVENUE_EXTERNAL_ONLY_SQL) и все
    запросы, которые обязаны ему подчиняться. Когда эти запросы лежат в
    одном файле, добавляя новый, трудно не заметить фильтр в соседних.
    В общей куче со счётчиками пользователей его как раз и забывали.

ЧТО ЛЕГКО СЛОМАТЬ
    1. Забыть фильтр `COALESCE(payment_provider, '') <> 'balance'` в новом
       запросе. Ошибки не будет — просто выручка вырастет: пополнение
       баланса, покупка с баланса и автопродление с него же посчитаются
       как три разных прихода вместо одного.

    2. Отдать копейки вместо рублей. В базе деньги в копейках, наружу
       уходят рубли — каждое возвращаемое денежное поле делится на 100.
       Пропущенное деление даёт цифру в сто раз больше, и на дашборде это
       выглядит правдоподобно ровно до конца месяца.

    3. Сравнить created_at с локальным «сегодня». Время в базе в UTC;
       границу суток по Москве считает Postgres (см. get_extended_bot_stats
       в database/analytics_stats.py), а не Python.
"""
import asyncpg
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from database.core import get_pool, _to_db_utc

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
#  ЧТО СЧИТАЕТСЯ ВЫРУЧКОЙ — единое определение на весь проект
# ──────────────────────────────────────────────────────────────────────
#
# Выручка = ВНЕШНИЕ ПОСТУПЛЕНИЯ. Деньги считаются один раз — в момент, когда
# они пришли извне (карта, СБП, крипта, Telegram Stars).
#
# Почему это пришлось проговорить. Внутри бота одни и те же рубли делают
# несколько шагов: пополнение баланса → покупка подписки с этого баланса →
# автопродление с него же. Каждый шаг создаёт свою строку, а отчёты
# суммировали всё подряд: одни и те же деньги попадали в выручку два-три
# раза, а реферальный кешбэк, потраченный с баланса, превращался в
# «выручку» из воздуха. Порог milestone-пуша (5k/10k/…) срабатывал на
# завышенных числах.
#
# Правило: строки с payment_provider='balance' — внутреннее движение, в
# выручку не входят. Пополнение баланса входит: это и есть приход извне.
#
# Фильтр стоит в КАЖДОМ денежном запросе этого модуля и в database/admin.py.
# Где его сознательно НЕТ — история подписок пользователя
# (get_user_paid_subscription_history и её батч-версия): там вопрос не
# «сколько мы заработали», а «что человеку выдано», и покупка с баланса —
# такая же полноценная покупка.
#
# NULL в payment_provider = строки до миграции 072. Считаем их выручкой:
# до появления покупок с баланса других вариантов не было.
REVENUE_EXTERNAL_ONLY_SQL = "COALESCE(payment_provider, '') <> 'balance'"


async def get_revenue_for_period(
    hours: int,
    since: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Money in over the window from paid pending_purchases.

    If `since` is given, the lower bound is that exact moment (used for
    "today MSK" tile on the dashboard). Otherwise — trailing N hours.

    Returns totals (rubles) + counts split by purchase_type so the
    UI can render a single KPI for the period plus a small breakdown.
    """
    pool = await get_pool()
    if pool is None:
        return {
            "revenue_rubles": 0.0,
            "payments_count": 0,
            "avg_check_rubles": 0.0,
            "by_type": {},
        }
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
    since = _to_db_utc(since)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                   COALESCE(SUM(price_kopecks), 0)::BIGINT AS total_kopecks,
                   COUNT(*)::BIGINT AS count
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1""",
            since,
        )
        by_type_rows = await conn.fetch(
            """SELECT
                   COALESCE(purchase_type, 'subscription') AS purchase_type,
                   COUNT(*)::BIGINT AS count,
                   COALESCE(SUM(price_kopecks), 0)::BIGINT AS revenue_kopecks
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1
               GROUP BY purchase_type
               ORDER BY revenue_kopecks DESC""",
            since,
        )
    total = int(row["total_kopecks"]) if row else 0
    count = int(row["count"]) if row else 0
    return {
        "revenue_rubles": total / 100,
        "payments_count": count,
        "avg_check_rubles": (total / 100 / count) if count else 0.0,
        "by_type": {
            r["purchase_type"]: {
                "count": int(r["count"]),
                "revenue_rubles": int(r["revenue_kopecks"]) / 100,
            }
            for r in by_type_rows
        },
    }


async def get_payments_by_provider(hours: int) -> list:
    """Breakdown of paid purchases by payment_provider.

    Uses the payment_provider column (migration 054) when present.
    NULL rows are bucketed as 'unknown' so old data is still visible.
    """
    pool = await get_pool()
    if pool is None:
        return []
    since = _to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours))
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """SELECT
                       COALESCE(payment_provider, 'unknown') AS provider,
                       COUNT(*)::BIGINT AS count,
                       COALESCE(SUM(price_kopecks), 0)::BIGINT AS revenue_kopecks
                   FROM pending_purchases
                   WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1
                   GROUP BY provider
                   ORDER BY revenue_kopecks DESC""",
                since,
            )
        except asyncpg.UndefinedColumnError:
            # Migration 054 not applied yet — return only what we can
            # infer from payments table.
            rows = []
    return [
        {
            "provider": r["provider"],
            "count": int(r["count"]),
            "revenue_rubles": int(r["revenue_kopecks"]) / 100,
        }
        for r in rows
    ]


async def get_revenue_today_vs_yesterday(spark_days: int = 30) -> Dict[str, Any]:
    """Главное число сводки: выручка за сегодня против вчера В ТО ЖЕ ВРЕМЯ.

    ПОЧЕМУ НЕ «ЗА ВЕСЬ ВЧЕРАШНИЙ ДЕНЬ»
        В одиннадцать утра сравнивать четыре часа продаж с полными
        предыдущими сутками бессмысленно: падение будет всегда, и число
        перестают читать. Поэтому у вчерашнего дня берётся ровно столько
        же времени от полуночи, сколько прошло сегодня.

    Границу суток режет Postgres по Москве — тем же выражением
    `AT TIME ZONE 'Europe/Moscow'`, что и суточный ряд в
    database/admin_reports.py::get_daily_timeseries. Разъедутся эти два
    места — тайл и график снова будут показывать разные цифры про один
    день, как было до выравнивания.

    Выручка — только внешние поступления (REVENUE_EXTERNAL_ONLY_SQL в
    шапке модуля). Покупка с баланса уже посчитана в момент пополнения.

    Возвращает рубли, спарклайн за `spark_days` суток и elapsed_minutes —
    сколько минут прошло с полуночи; фронту это нужно, чтобы подписать
    сравнение честно («вчера к 11:12»).

    Исключения НЕ гасятся: на главном экране «0 ₽» и «не смогли
    посчитать» обязаны выглядеть по-разному, а решает это маршрут.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH b AS (
                SELECT
                    (DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow'))
                        AT TIME ZONE 'Europe/Moscow') AS today_start,
                    ((DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow'))
                        - INTERVAL '1 day') AT TIME ZONE 'Europe/Moscow') AS yday_start,
                    ((NOW() AT TIME ZONE 'Europe/Moscow')
                        - DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow'))) AS elapsed
            )
            SELECT
                (EXTRACT(EPOCH FROM b.elapsed)::bigint / 60) AS elapsed_minutes,
                COALESCE(SUM(pp.price_kopecks) FILTER (
                    WHERE pp.created_at >= b.today_start), 0)::bigint AS today_kopecks,
                COUNT(pp.id) FILTER (WHERE pp.created_at >= b.today_start)::int AS today_count,
                COALESCE(SUM(pp.price_kopecks) FILTER (
                    WHERE pp.created_at >= b.yday_start
                      AND pp.created_at < b.yday_start + b.elapsed), 0)::bigint AS yday_kopecks,
                COUNT(pp.id) FILTER (
                    WHERE pp.created_at >= b.yday_start
                      AND pp.created_at < b.yday_start + b.elapsed)::int AS yday_count
            FROM b
            LEFT JOIN pending_purchases pp
                   ON pp.status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
                  AND pp.created_at >= b.yday_start
            GROUP BY b.elapsed, b.today_start, b.yday_start
            """,
        )
        spark = await conn.fetch(
            """
            WITH days AS (
                SELECT generate_series(
                    DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow'))
                        - ($1::int - 1) * INTERVAL '1 day',
                    DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow')),
                    INTERVAL '1 day'
                )::date AS day
            ),
            win AS (
                SELECT ((DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow'))
                    - ($1::int - 1) * INTERVAL '1 day') AT TIME ZONE 'Europe/Moscow') AS since
            ),
            pay AS (
                SELECT DATE_TRUNC('day', created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                       COALESCE(SUM(price_kopecks), 0)::bigint AS revenue_kopecks
                FROM pending_purchases
                WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
                  AND created_at >= (SELECT since FROM win)
                GROUP BY 1
            )
            SELECT d.day, COALESCE(pay.revenue_kopecks, 0) AS revenue_kopecks
            FROM days d LEFT JOIN pay ON pay.day = d.day
            ORDER BY d.day
            """,
            spark_days,
        )
    # LEFT JOIN гарантирует строку даже на пустой таблице, но GROUP BY по
    # elapsed на всякий случай проверяем: пустой ответ здесь означал бы
    # сломанный запрос, а не отсутствие продаж.
    today_kopecks = int(row["today_kopecks"]) if row else 0
    yday_kopecks = int(row["yday_kopecks"]) if row else 0
    return {
        "tz": "Europe/Moscow",
        "elapsed_minutes": int(row["elapsed_minutes"]) if row else 0,
        "today_rubles": today_kopecks / 100,
        "today_payments": int(row["today_count"]) if row else 0,
        "yesterday_same_time_rubles": yday_kopecks / 100,
        "yesterday_same_time_payments": int(row["yday_count"]) if row else 0,
        "sparkline": [
            {
                "date": r["day"].isoformat(),
                "rubles": int(r["revenue_kopecks"]) / 100,
            }
            for r in spark
        ],
    }


async def get_payments_breakdown(hours: int) -> Dict[str, Any]:
    """Полный разрез оплат за окно N часов:
      - total: {count, revenue_rubles}
      - by_provider:  [(provider, count, revenue_rubles), ...] сорт. по revenue
      - by_type:      [(purchase_type, count, revenue_rubles), ...]
      - by_tariff:    [(tariff, count, revenue_rubles), ...] топ-15
      - by_apple_nominal: [(region, nominal, count, revenue_rubles), ...]

    Используется в дашборде для «что купили сегодня/за N часов»:
    админ видит, кто платит, чем и за что.
    """
    pool = await get_pool()
    if pool is None:
        return {}
    since = _to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours))
    out: Dict[str, Any] = {
        "hours": hours,
        "total": {"count": 0, "revenue_rubles": 0.0},
        "by_provider": [],
        "by_type": [],
        "by_tariff": [],
        "by_apple_nominal": [],
    }
    async with pool.acquire() as conn:
        try:
            total = await conn.fetchrow(
                """SELECT COUNT(*)::BIGINT AS count,
                          COALESCE(SUM(price_kopecks), 0)::BIGINT AS revenue_kop
                   FROM pending_purchases
                   WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1""",
                since,
            )
            out["total"] = {
                "count": int(total["count"] or 0),
                "revenue_rubles": int(total["revenue_kop"] or 0) / 100,
            }
        except Exception as e:
            logger.warning("breakdown total_failed: %s", e)

        # by_provider
        try:
            rows = await conn.fetch(
                """SELECT COALESCE(payment_provider, 'unknown') AS k,
                          COUNT(*)::BIGINT AS c,
                          COALESCE(SUM(price_kopecks), 0)::BIGINT AS rev
                   FROM pending_purchases
                   WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1
                   GROUP BY k
                   ORDER BY rev DESC""",
                since,
            )
            out["by_provider"] = [
                {"provider": r["k"], "count": int(r["c"]),
                 "revenue_rubles": int(r["rev"]) / 100}
                for r in rows
            ]
        except Exception as e:
            logger.warning("breakdown by_provider failed: %s", e)

        # by_type (subscription / apple_id / steam / spotify / ...)
        try:
            rows = await conn.fetch(
                """SELECT COALESCE(purchase_type, 'unknown') AS k,
                          COUNT(*)::BIGINT AS c,
                          COALESCE(SUM(price_kopecks), 0)::BIGINT AS rev
                   FROM pending_purchases
                   WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1
                   GROUP BY k
                   ORDER BY rev DESC""",
                since,
            )
            out["by_type"] = [
                {"purchase_type": r["k"], "count": int(r["c"]),
                 "revenue_rubles": int(r["rev"]) / 100}
                for r in rows
            ]
        except Exception as e:
            logger.warning("breakdown by_type failed: %s", e)

        # by_tariff (top-15 по revenue)
        try:
            rows = await conn.fetch(
                """SELECT COALESCE(tariff, 'unknown') AS k,
                          COUNT(*)::BIGINT AS c,
                          COALESCE(SUM(price_kopecks), 0)::BIGINT AS rev
                   FROM pending_purchases
                   WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1
                   GROUP BY k
                   ORDER BY rev DESC
                   LIMIT 15""",
                since,
            )
            out["by_tariff"] = [
                {"tariff": r["k"], "count": int(r["c"]),
                 "revenue_rubles": int(r["rev"]) / 100}
                for r in rows
            ]
        except Exception as e:
            logger.warning("breakdown by_tariff failed: %s", e)

        # by_apple_nominal — только apple_id_ строки, распарсим tariff
        # apple_id_{region}_{nominal} → region + nominal.
        #
        # ESCAPE обязателен. В LIKE символ `_` — одиночный wildcard, то есть
        # шаблон 'apple_id_%' совпадает и с 'appleXidY...'. Сейчас таких
        # тарифов нет, но появится любой — и он молча попадёт в разбивку
        # Apple с мусорным регионом и номиналом. Экранируем `_`, чтобы
        # шаблон означал ровно префикс 'apple_id_'.
        #
        # Строка сырая (r"""), иначе Python сам съест `\_` как неизвестную
        # escape-последовательность и в SQL уедет непонятно что.
        try:
            rows = await conn.fetch(
                r"""SELECT tariff, COUNT(*)::BIGINT AS c,
                          COALESCE(SUM(price_kopecks), 0)::BIGINT AS rev
                   FROM pending_purchases
                   WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1
                     AND tariff LIKE 'apple\_id\_%' ESCAPE '\'
                   GROUP BY tariff
                   ORDER BY rev DESC""",
                since,
            )
            apple = []
            for r in rows:
                t = str(r["tariff"] or "")
                # Разбор позиционный, поэтому проверяем форму ДО обращения
                # по индексам: ждём ровно apple / id / регион / номинал.
                # Всё, что не легло в эту форму, не выбрасываем (это
                # оплаченные деньги, они должны быть видны), а показываем
                # как есть — чтобы админ увидел странный тариф, а не тихо
                # приписанный чужому региону доход.
                parts = t.split("_")
                if len(parts) == 4 and parts[3].isdigit():
                    region = parts[2]
                    nominal = int(parts[3])
                else:
                    logger.warning(
                        "breakdown by_apple_nominal: неожиданный формат тарифа %r", t,
                    )
                    region = t or "?"
                    nominal = 0
                apple.append({
                    "region": region,
                    "nominal": nominal,
                    "count": int(r["c"]),
                    "revenue_rubles": int(r["rev"]) / 100,
                })
            out["by_apple_nominal"] = apple
        except Exception as e:
            logger.warning("breakdown by_apple_nominal failed: %s", e)
    return out


async def get_purchase_breakdown() -> Dict[str, Any]:
    """Per-category purchase counts and revenue across time windows.

    Source: pending_purchases with status='paid' — the single table that
    covers both subscription purchases (finalize_purchase marks it paid) and
    notification-only products like the proxy (mark_pending_purchase_paid).

    Categories: basic, plus, basic_combo, plus_combo, proxy.
    Windows: 24h, 7d, 30d, 180d, 365d, all.

    created_at is the checkout-start time (no separate paid timestamp is
    stored), but payment completes within the ~15-min pending TTL, so it is
    an accurate proxy for windows of a day or more.

    Returns:
        { category: { window: {"count": int, "revenue": int_kopecks} } }
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    s24 = _to_db_utc(now - timedelta(hours=24))
    s7 = _to_db_utc(now - timedelta(days=7))
    s30 = _to_db_utc(now - timedelta(days=30))
    s180 = _to_db_utc(now - timedelta(days=180))
    s365 = _to_db_utc(now - timedelta(days=365))

    query = """
        WITH classified AS (
            SELECT
                CASE
                    WHEN purchase_type = 'proxy' THEN 'proxy'
                    WHEN purchase_type = 'subscription' AND tariff = 'basic'
                         AND COALESCE(is_combo, false) THEN 'basic_combo'
                    WHEN purchase_type = 'subscription' AND tariff = 'plus'
                         AND COALESCE(is_combo, false) THEN 'plus_combo'
                    WHEN purchase_type = 'subscription' AND tariff = 'basic' THEN 'basic'
                    WHEN purchase_type = 'subscription' AND tariff = 'plus'  THEN 'plus'
                    ELSE NULL
                END AS category,
                price_kopecks,
                created_at
            FROM pending_purchases
            WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
        )
        SELECT
            category,
            COUNT(*) FILTER (WHERE created_at >= $1) AS c_24h,
            COUNT(*) FILTER (WHERE created_at >= $2) AS c_7d,
            COUNT(*) FILTER (WHERE created_at >= $3) AS c_30d,
            COUNT(*) FILTER (WHERE created_at >= $4) AS c_180d,
            COUNT(*) FILTER (WHERE created_at >= $5) AS c_365d,
            COUNT(*) AS c_all,
            COALESCE(SUM(price_kopecks) FILTER (WHERE created_at >= $1), 0) AS r_24h,
            COALESCE(SUM(price_kopecks) FILTER (WHERE created_at >= $2), 0) AS r_7d,
            COALESCE(SUM(price_kopecks) FILTER (WHERE created_at >= $3), 0) AS r_30d,
            COALESCE(SUM(price_kopecks) FILTER (WHERE created_at >= $4), 0) AS r_180d,
            COALESCE(SUM(price_kopecks) FILTER (WHERE created_at >= $5), 0) AS r_365d,
            COALESCE(SUM(price_kopecks), 0) AS r_all
        FROM classified
        WHERE category IS NOT NULL
        GROUP BY category
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, s24, s7, s30, s180, s365)

    windows = ["24h", "7d", "30d", "180d", "365d", "all"]
    result = {
        cat: {w: {"count": 0, "revenue": 0} for w in windows}
        for cat in ("basic", "plus", "basic_combo", "plus_combo", "proxy")
    }
    for row in rows:
        cat = row["category"]
        if cat not in result:
            continue
        for w in windows:
            result[cat][w] = {
                "count": row[f"c_{w}"] or 0,
                "revenue": row[f"r_{w}"] or 0,
            }
    return result


async def get_traffic_stats(hours: int) -> Dict[str, Any]:
    """Traffic-purchase stats — separate revenue/count + breakdown by
    payment_method (column may be optional on older deploys)."""
    pool = await get_pool()
    if pool is None:
        return {"count": 0, "revenue_rubles": 0.0, "total_gb": 0, "by_method": []}
    since = _to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours))
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """SELECT
                       COUNT(*)::BIGINT AS count,
                       COALESCE(SUM(price_rub), 0)::BIGINT AS revenue_rubles,
                       COALESCE(SUM(gb_amount), 0)::BIGINT AS total_gb
                   FROM traffic_purchases
                   WHERE created_at >= $1""",
                since,
            )
        except asyncpg.UndefinedTableError:
            return {"count": 0, "revenue_rubles": 0.0, "total_gb": 0, "by_method": []}

        by_method = []
        try:
            method_rows = await conn.fetch(
                """SELECT
                       COALESCE(payment_method, 'unknown') AS method,
                       COUNT(*)::BIGINT AS count,
                       COALESCE(SUM(price_rub), 0)::BIGINT AS revenue_rubles,
                       COALESCE(SUM(gb_amount), 0)::BIGINT AS total_gb
                   FROM traffic_purchases
                   WHERE created_at >= $1
                   GROUP BY method
                   ORDER BY revenue_rubles DESC""",
                since,
            )
            by_method = [
                {
                    "method": r["method"],
                    "count": int(r["count"]),
                    "revenue_rubles": int(r["revenue_rubles"]),
                    "total_gb": int(r["total_gb"]),
                }
                for r in method_rows
            ]
        except (asyncpg.UndefinedColumnError, asyncpg.UndefinedTableError):
            by_method = []

    return {
        "count": int(row["count"]) if row else 0,
        "revenue_rubles": int(row["revenue_rubles"]) if row else 0,
        "total_gb": int(row["total_gb"]) if row else 0,
        "by_method": by_method,
    }


async def get_total_revenue() -> float:
    """
    Получить общий доход от всех успешных платежей
    
    Returns:
        Общий доход в рублях (только утвержденные платежи)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Суммируем все утвержденные платежи
        # ИСТОЧНИК ИСТИНЫ ПО ВЫРУЧКЕ — pending_purchases, а не payments.
        #
        # В payments строка создаётся только внутри finalize_purchase, то есть
        # для подписок. Товары мини-магазина (Stars, Telegram Premium, Steam,
        # Spotify, Apple ID, прокси) помечаются оплаченными через
        # mark_pending_purchase_paid и в payments не попадают вовсе — их выручка
        # просто отсутствовала в отчётах.
        #
        # Вдобавок payments двоил деньги: пополнение баланса писало строку, и
        # покупка с этого же баланса писала вторую — один рубль считался дважды.
        #
        # pending_purchases покрывает оба случая: и подписки, и товары.
        total_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(price_kopecks), 0)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'"""
        ) or 0
        
        return total_kopecks / 100.0  # Конвертируем из копеек в рубли


async def get_paying_users_count() -> int:
    """
    Получить количество платящих пользователей
    
    Returns:
        Количество уникальных пользователей с хотя бы одним утвержденным платежом
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'"""
        ) or 0
        
        return count


async def get_user_ltv(telegram_id: int) -> float:
    """
    Получить LTV (Lifetime Value) пользователя
    
    LTV = общая сумма платежей за подписки (исключая кешбэк)
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        LTV в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Суммируем все утвержденные платежи за подписки
        total_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(price_kopecks), 0)
               FROM pending_purchases
               WHERE telegram_id = $1 AND status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'""",
            telegram_id
        ) or 0
        
        return total_kopecks / 100.0  # Конвертируем из копеек в рубли


async def get_average_ltv() -> float:
    """LTV — средняя выручка на одного ПЛАТЯЩЕГО пользователя.

    Считается как среднее суммы покупок по каждому, кто хоть раз заплатил.
    Пользователи без покупок в знаменатель не входят — этим LTV и отличается
    от ARPU (см. get_arpu): та же выручка, но делённая на всю базу.

    Returns:
        Средний LTV в рублях.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем LTV для каждого пользователя
        ltv_data = await conn.fetch(
            """SELECT telegram_id, COALESCE(SUM(price_kopecks), 0) as total_payments
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
               GROUP BY telegram_id"""
        )
        
        if not ltv_data:
            return 0.0
        
        total_ltv = sum(row["total_payments"] for row in ltv_data)
        avg_ltv = total_ltv / len(ltv_data)
        
        return avg_ltv / 100.0  # Конвертируем из копеек в рубли


async def get_arpu() -> float:
    """ARPU — средняя выручка на ОДНОГО ЗАРЕГИСТРИРОВАННОГО пользователя.

    ARPU = выручка / все пользователи бота.

    Почему именно так. Раньше здесь стояло «выручка / платящие», и это
    алгебраически ровно то же самое, что считает get_average_ltv:
    SUM(всё) / COUNT(платящих) против AVG(SUM по каждому платящему). На
    дашборде рисовались две карточки — «ARPU · на юзера» и «LTV · средний»,
    — которые ВСЕГДА показывали одно и то же число. Две одинаковые цифры
    под разными названиями хуже, чем одна: по ним принимают решения,
    считая, что видят разные срезы.

    Теперь метрики отвечают на разные вопросы:
        ARPU (здесь)      сколько приносит средний зарегистрированный —
                          показывает, насколько хорошо мы конвертируем базу;
        LTV (get_average_ltv) сколько приносит средний ПЛАТЯЩИЙ —
                          показывает ценность клиента.
    Отношение ARPU/LTV и есть конверсия базы в платящих.

    Returns:
        ARPU в рублях.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Общий доход (только утвержденные платежи)
        # ИСТОЧНИК ИСТИНЫ ПО ВЫРУЧКЕ — pending_purchases, а не payments.
        #
        # В payments строка создаётся только внутри finalize_purchase, то есть
        # для подписок. Товары мини-магазина (Stars, Telegram Premium, Steam,
        # Spotify, Apple ID, прокси) помечаются оплаченными через
        # mark_pending_purchase_paid и в payments не попадают вовсе — их выручка
        # просто отсутствовала в отчётах.
        #
        # Вдобавок payments двоил деньги: пополнение баланса писало строку, и
        # покупка с этого же баланса писала вторую — один рубль считался дважды.
        #
        # pending_purchases покрывает оба случая: и подписки, и товары.
        total_revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(price_kopecks), 0)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'"""
        ) or 0
        
        total_revenue = total_revenue_kopecks / 100.0
        
        # Знаменатель — ВСЕ зарегистрированные, а не платящие: иначе
        # получается то же число, что у LTV (см. докстринг).
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users") or 0

        return total_revenue / total_users if total_users > 0 else 0.0
