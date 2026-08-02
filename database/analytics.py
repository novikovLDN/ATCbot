"""Аналитика и денежные метрики для админки и дашборда.

ЧТО ЗДЕСЬ ЕСТЬ
    Только чтение: сводки по выручке, разбивка платежей по провайдерам,
    статистика трафика, LTV, ARPU, журнал ошибок оплаты и лента последних
    покупок. Ни одна функция здесь ничего не меняет.

ПОЧЕМУ ЭТО ВАЖНО ДЕРЖАТЬ ОТДЕЛЬНО
    Это отчётный слой. Он читает те же таблицы, что и денежная логика, но
    его правят при работе над дашбордом, а не над платежами. Ошибка здесь
    портит цифру на экране; ошибка в платежах теряет деньги. Разная цена
    ошибки — разные файлы.

ЧТО ЛЕГКО СЛОМАТЬ НЕЗАМЕТНО
    Все выборки за период опираются на created_at. Помните, что в базе
    время хранится в UTC: сравнение с локальным «сегодня» сдвинет сутки
    и цифры разойдутся с реальностью, не вызвав никакой ошибки.

    Деньги в базе лежат в копейках. Любая метрика, отдающая рубли, обязана
    делить на 100 — иначе выручка вырастет в сто раз и это заметят не сразу.
"""
import asyncpg
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import config
import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc, safe_int

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


async def get_business_metrics() -> Dict[str, Any]:
    """Получить бизнес-метрики сервиса
    
    Returns:
        Словарь с метриками:
        - avg_payment_approval_time_seconds: среднее время подтверждения оплаты (в секундах)
        - avg_subscription_lifetime_days: среднее время жизни подписки (в днях)
        - avg_renewals_per_user: среднее количество продлений на пользователя
        - approval_rate_percent: процент подтвержденных платежей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. Среднее время подтверждения оплаты
        # Используем audit_log для получения времени подтверждения
        # Парсим Payment ID из details поля через CTE
        avg_approval_time = await conn.fetchval(
            """WITH payment_approvals AS (
                SELECT 
                    al.created_at as approved_at,
                    CAST(SUBSTRING(al.details FROM 'Payment ID: ([0-9]+)') AS INTEGER) as payment_id
                FROM audit_log al
                WHERE al.action IN ('payment_approved', 'subscription_renewed')
                AND al.details LIKE 'Payment ID: %'
            )
            SELECT AVG(EXTRACT(EPOCH FROM (pa.approved_at - p.created_at))) 
            FROM payment_approvals pa
            JOIN payments p ON p.id = pa.payment_id
            WHERE p.status = 'approved'"""
        )
        
        # 2. Среднее время жизни подписки (из subscription_history)
        # Используем только завершенные подписки (end_date < now)
        avg_lifetime = await conn.fetchval(
            """SELECT AVG(EXTRACT(EPOCH FROM (end_date - start_date)) / 86400.0)
               FROM subscription_history
               WHERE end_date IS NOT NULL
               AND end_date < NOW()"""
        )
        
        # 3. Среднее количество продлений на пользователя
        total_renewals = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history WHERE action_type = 'renewal'"""
        )
        total_users_with_subscriptions = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id) FROM subscription_history"""
        )
        avg_renewals = 0.0
        if total_users_with_subscriptions and total_users_with_subscriptions > 0:
            avg_renewals = (total_renewals or 0) / total_users_with_subscriptions
        
        # 4. Процент подтвержденных платежей
        total_payments = await conn.fetchval("SELECT COUNT(*) FROM payments")
        approved_payments = await conn.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status = 'approved'"
        )
        approval_rate = 0.0
        if total_payments and total_payments > 0:
            approval_rate = ((approved_payments or 0) / total_payments) * 100
        
        return {
            "avg_payment_approval_time_seconds": float(avg_approval_time) if avg_approval_time else None,
            "avg_subscription_lifetime_days": float(avg_lifetime) if avg_lifetime else None,
            "avg_renewals_per_user": float(avg_renewals) if avg_renewals else 0.0,
            "approval_rate_percent": float(approval_rate) if approval_rate else 0.0,
        }


async def get_last_audit_logs(limit: int = 10) -> list:
    """Получить последние записи из audit_log
    
    Args:
        limit: Количество записей для получения (по умолчанию 10)
    
    Returns:
        Список словарей с записями audit_log, отсортированных по created_at DESC
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_last_audit_logs skipped")
        return []
    
    pool = await get_pool()
    if pool is None:
        return []
    
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM audit_log 
                   ORDER BY created_at DESC 
                   LIMIT $1""",
                limit
            )
            return [dict(row) for row in rows]
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"audit_log table missing or inaccessible — skipping: {e}")
        return []
    except Exception as e:
        logger.warning(f"Error getting audit logs: {e}")
        return []


async def get_analytics_by_period(
    hours: int,
    since: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Получить аналитику за указанный период.

    Если `since` задан — окно [since, now). Иначе trailing `hours` часов
    от текущего момента (старое поведение). `since` нужен дашборду,
    чтобы считать «сегодня по МСК» (UTC+3) — окно с 00:00 МСК.

    Returns:
        Словарь с ключами:
        - new_users: новые пользователи за период
        - trial_activated: активировали пробный период за период
        - new_subscriptions: новые платные подписки за период
        - total_users: общее количество пользователей
        - total_trial_used: всего активировали trial
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(hours=hours)
        since_db = _to_db_utc(since)

        new_users = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= $1",
            since_db
        )

        trial_activated = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE trial_used_at IS NOT NULL AND trial_used_at >= $1",
            since_db
        )

        new_subscriptions = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE activated_at >= $1",
            since_db
        )

        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")

        total_trial_used = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE trial_used_at IS NOT NULL"
        )

        return {
            "new_users": new_users or 0,
            "trial_activated": trial_activated or 0,
            "new_subscriptions": new_subscriptions or 0,
            "total_users": total_users or 0,
            "total_trial_used": total_trial_used or 0,
        }


async def get_active_paid_subscriptions_count() -> int:
    """Count of subscriptions that are paid, not bypass-only, not trial,
    with expires_at in the future. This is the number an admin actually
    cares about — get_extended_bot_stats's active_subscriptions also
    includes trial rows and bypass-only entries, which inflates it."""
    pool = await get_pool()
    if pool is None:
        return 0
    now = _to_db_utc(datetime.now(timezone.utc))
    try:
        async with pool.acquire() as conn:
            n = await conn.fetchval(
                """SELECT COUNT(*) FROM subscriptions
                   WHERE status = 'active'
                     AND expires_at > $1
                     AND COALESCE(is_bypass_only, FALSE) = FALSE
                     AND COALESCE(source, '') != 'trial'
                     AND subscription_type IN (
                         'basic', 'plus', 'biz_starter', 'biz_team',
                         'biz_business', 'biz_pro', 'biz_enterprise',
                         'biz_ultimate'
                     )""",
                now,
            )
            return int(n or 0)
    except Exception as e:
        logger.warning("get_active_paid_subscriptions_count failed: %s", e)
        return 0


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
        try:
            rows = await conn.fetch(
                """SELECT tariff, COUNT(*)::BIGINT AS c,
                          COALESCE(SUM(price_kopecks), 0)::BIGINT AS rev
                   FROM pending_purchases
                   WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1
                     AND tariff LIKE 'apple_id_%'
                   GROUP BY tariff
                   ORDER BY rev DESC""",
                since,
            )
            apple = []
            for r in rows:
                t = str(r["tariff"] or "")
                parts = t.split("_")
                region = parts[2] if len(parts) >= 3 else "?"
                nominal_raw = parts[3] if len(parts) >= 4 else "0"
                try:
                    nominal = int(nominal_raw)
                except ValueError:
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


async def get_recent_payments_feed(
    limit: int = 100,
    hours: Optional[int] = None,
    status: Optional[str] = None,
) -> list:
    """Recent paid (and optionally pending/expired) purchases for the
    Payments page feed. Joins users so we render @username with no
    second round-trip."""
    pool = await get_pool()
    if pool is None:
        return []
    where = ["pp.created_at IS NOT NULL"]
    params: list = []
    if hours is not None:
        params.append(_to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours)))
        where.append(f"pp.created_at >= ${len(params)}")
    if status:
        params.append(status)
        where.append(f"pp.status = ${len(params)}")
    params.append(limit)
    limit_idx = len(params)
    sql = f"""
        SELECT
            pp.id, pp.purchase_id, pp.telegram_id, pp.tariff,
            pp.purchase_type, pp.period_days, pp.price_kopecks,
            pp.status, pp.created_at, pp.is_combo, pp.farm_plot_id,
            -- У покупок Spotify поля promo_code и country заняты не по
            -- назначению: там лежат пароль и email от аккаунта клиента.
            -- Это исторический хак ради экономии колонок, но в отчётной
            -- выдаче учётные данные светить нельзя — они попадали в ленту
            -- платежей дашборда, откуда их видит любой, у кого есть доступ.
            -- Отдаём заглушку; админ берёт данные в карточке заказа, куда
            -- они приходят отдельным сообщением.
            CASE WHEN pp.purchase_type = 'spotify' THEN NULL ELSE pp.promo_code END AS promo_code,
            CASE WHEN pp.purchase_type = 'spotify' THEN NULL ELSE pp.country END AS country,
            COALESCE(pp.payment_provider, 'unknown') AS payment_provider,
            u.username
        FROM pending_purchases pp
        LEFT JOIN users u ON u.telegram_id = pp.telegram_id
        WHERE {' AND '.join(where)}
        ORDER BY pp.created_at DESC
        LIMIT ${limit_idx}
    """
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(sql, *params)
        except asyncpg.UndefinedColumnError:
            # Migration not applied — fall back without payment_provider
            sql_fallback = sql.replace(
                "COALESCE(pp.payment_provider, 'unknown') AS payment_provider,",
                "'unknown' AS payment_provider,",
            )
            rows = await conn.fetch(sql_fallback, *params)
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = _from_db_utc(d["created_at"])
        # convert kopecks to rubles for UI
        d["price_rubles"] = (d.get("price_kopecks") or 0) / 100
        out.append(d)
    return out


async def get_user_purchases(telegram_id: int, limit: int = 100) -> list:
    """Все покупки одного пользователя из pending_purchases.

    Этот стол — источник правды для всего, что юзер покупал в боте:
    подписки (basic / plus / биз-тарифы), trafic-паки, балансовые
    пополнения, telegram premium, steam, прокси, фарм-участки.
    Старая таблица payments тоже была, но она устарела и не покрывает
    весь поток — поэтому в карточке юзера показываем именно
    pending_purchases.

    Возвращает все строки (paid + pending + expired) свежие первые.
    """
    pool = await get_pool()
    if pool is None:
        return []
    sql = """
        SELECT
            pp.id, pp.purchase_id, pp.tariff,
            pp.purchase_type, pp.period_days, pp.price_kopecks,
            pp.status, pp.created_at, pp.expires_at,
            pp.is_combo, pp.farm_plot_id,
            -- У покупок Spotify поля promo_code и country заняты не по
            -- назначению: там лежат пароль и email от аккаунта клиента.
            -- Это исторический хак ради экономии колонок, но в отчётной
            -- выдаче учётные данные светить нельзя — они попадали в ленту
            -- платежей дашборда, откуда их видит любой, у кого есть доступ.
            -- Отдаём заглушку; админ берёт данные в карточке заказа, куда
            -- они приходят отдельным сообщением.
            CASE WHEN pp.purchase_type = 'spotify' THEN NULL ELSE pp.promo_code END AS promo_code,
            CASE WHEN pp.purchase_type = 'spotify' THEN NULL ELSE pp.country END AS country,
            COALESCE(pp.payment_provider, 'unknown') AS payment_provider,
            pp.provider_invoice_id
        FROM pending_purchases pp
        WHERE pp.telegram_id = $1
        ORDER BY pp.created_at DESC NULLS LAST
        LIMIT $2
    """
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(sql, telegram_id, limit)
        except asyncpg.UndefinedColumnError:
            sql_fb = sql.replace(
                "COALESCE(pp.payment_provider, 'unknown') AS payment_provider,",
                "'unknown' AS payment_provider,",
            )
            rows = await conn.fetch(sql_fb, telegram_id, limit)
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = _from_db_utc(d["created_at"])
        if d.get("expires_at"):
            d["expires_at"] = _from_db_utc(d["expires_at"])
        d["price_rubles"] = (d.get("price_kopecks") or 0) / 100
        out.append(d)
    return out


async def log_payment_error(
    *,
    stage: str,
    telegram_id: Optional[int] = None,
    purchase_id: Optional[str] = None,
    payment_provider: Optional[str] = None,
    amount_rubles: Optional[float] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Append a payment-error row. Never raises — payment-error logging
    must not break the caller's own error handling. Returns the inserted
    row's id, or None on failure (e.g. table not migrated yet).

    `stage` is a short label like 'webhook_validation', 'amount_mismatch',
    'provider_callback_invalid', 'provision_failed', 'idempotency_rejected'.
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None

    import json
    payload_json = None
    if raw_payload is not None:
        try:
            payload_json = json.dumps(raw_payload, default=str)[:8000]
        except Exception:
            payload_json = None

    try:
        async with pool.acquire() as conn:
            row_id = await conn.fetchval(
                """INSERT INTO payment_errors
                       (telegram_id, purchase_id, payment_provider,
                        amount_rubles, stage, error_code, error_message,
                        raw_payload)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                   RETURNING id""",
                telegram_id, purchase_id, payment_provider,
                amount_rubles, stage,
                (error_code or "")[:120] if error_code else None,
                (error_message or "")[:2000] if error_message else None,
                payload_json,
            )
            try:
                from app.events import bus
                bus.publish({
                    "type": "payment:error",
                    "id": int(row_id) if row_id else None,
                    "telegram_id": telegram_id,
                    "stage": stage,
                    "provider": payment_provider,
                })
            except Exception:
                pass
            return int(row_id) if row_id else None
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning("log_payment_error: table missing — %s", e)
        return None
    except Exception as e:
        logger.warning("log_payment_error: %s", e)
        return None


async def get_recent_payment_errors(
    limit: int = 100,
    hours: Optional[int] = None,
    provider: Optional[str] = None,
    stage: Optional[str] = None,
) -> list:
    """Recent payment_errors rows, newest first. Returns [] if the
    table doesn't exist yet."""
    pool = await get_pool()
    if pool is None:
        return []
    where = ["TRUE"]
    params: list = []
    if hours is not None:
        params.append(_to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours)))
        where.append(f"created_at >= ${len(params)}")
    if provider:
        params.append(provider)
        where.append(f"payment_provider = ${len(params)}")
    if stage:
        params.append(stage)
        where.append(f"stage = ${len(params)}")
    params.append(limit)
    limit_idx = len(params)
    sql = f"""
        SELECT pe.*, u.username
        FROM payment_errors pe
        LEFT JOIN users u ON u.telegram_id = pe.telegram_id
        WHERE {' AND '.join(where)}
        ORDER BY pe.created_at DESC
        LIMIT ${limit_idx}
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
        return []

    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = _from_db_utc(d["created_at"])
        if d.get("amount_rubles") is not None:
            try:
                d["amount_rubles"] = float(d["amount_rubles"])
            except Exception:
                d["amount_rubles"] = None
        out.append(d)
    return out


async def get_payment_errors_summary(hours: int = 24) -> Dict[str, Any]:
    """Counters for the Payments page header — total errors in window,
    plus by stage and by provider."""
    pool = await get_pool()
    if pool is None:
        return {"total": 0, "by_stage": [], "by_provider": []}
    since = _to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours))
    try:
        async with pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM payment_errors WHERE created_at >= $1",
                since,
            ) or 0
            by_stage = await conn.fetch(
                """SELECT stage, COUNT(*)::BIGINT AS count
                   FROM payment_errors
                   WHERE created_at >= $1
                   GROUP BY stage
                   ORDER BY count DESC
                   LIMIT 10""",
                since,
            )
            by_provider = await conn.fetch(
                """SELECT COALESCE(payment_provider, 'unknown') AS provider,
                          COUNT(*)::BIGINT AS count
                   FROM payment_errors
                   WHERE created_at >= $1
                   GROUP BY provider
                   ORDER BY count DESC""",
                since,
            )
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
        return {"total": 0, "by_stage": [], "by_provider": []}
    return {
        "total": int(total),
        "by_stage": [{"stage": r["stage"], "count": int(r["count"])} for r in by_stage],
        "by_provider": [
            {"provider": r["provider"], "count": int(r["count"])}
            for r in by_provider
        ],
    }


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


async def get_extended_bot_stats() -> Dict[str, Any]:
    """Расширенная статистика бота для мониторинга."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        now_db = _to_db_utc(now)

        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")

        # Active subscriptions
        active_subs = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE expires_at > $1", now_db
        )

        # Expired and not renewed (churn)
        expired_subs = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE expires_at <= $1", now_db
        )

        # Trial stats
        total_trial = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE trial_used_at IS NOT NULL"
        )

        # Conversion: users who have at least one subscription
        users_with_sub = await conn.fetchval(
            "SELECT COUNT(DISTINCT telegram_id) FROM subscriptions"
        )

        # Выручка — из pending_purchases: payments не содержит товары
        # мини-магазина и двоит деньги при покупке с баланса.
        total_revenue = await conn.fetchval(
            "SELECT COALESCE(SUM(price_kopecks), 0) FROM pending_purchases WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'"
        ) or 0

        # Выручка за 30 дней (оценка MRR).
        # created_at в pending_purchases — момент начала оплаты, а не её
        # подтверждения. Счёт живёт 15-30 минут, поэтому для месячного окна
        # это корректный ориентир.
        mrr_since = _to_db_utc(now - timedelta(days=30))
        mrr = await conn.fetchval(
            "SELECT COALESCE(SUM(price_kopecks), 0) FROM pending_purchases "
            "WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1",
            mrr_since
        ) or 0

        # New users today
        today_start = _to_db_utc(now.replace(hour=0, minute=0, second=0, microsecond=0))
        new_today = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= $1", today_start
        )

        # Broadcasts sent
        total_broadcasts = await conn.fetchval("SELECT COUNT(*) FROM broadcasts")

        # Average subscriptions per paying user
        avg_subs = await conn.fetchval(
            "SELECT ROUND(AVG(cnt), 1) FROM (SELECT COUNT(*) as cnt FROM subscriptions GROUP BY telegram_id) sub"
        )

        conversion_rate = round((users_with_sub / total_users * 100), 1) if total_users > 0 else 0
        trial_rate = round((total_trial / total_users * 100), 1) if total_users > 0 else 0
        churn_rate = round((expired_subs / (active_subs + expired_subs) * 100), 1) if (active_subs + expired_subs) > 0 else 0

        return {
            "total_users": total_users or 0,
            "active_subs": active_subs or 0,
            "expired_subs": expired_subs or 0,
            "total_trial": total_trial or 0,
            "trial_rate": trial_rate,
            "users_with_sub": users_with_sub or 0,
            "conversion_rate": conversion_rate,
            "churn_rate": churn_rate,
            "total_revenue": total_revenue,
            "mrr": mrr,
            "new_today": new_today or 0,
            "total_broadcasts": total_broadcasts or 0,
            "avg_subs_per_user": float(avg_subs) if avg_subs else 0,
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
    """
    Получить средний LTV по всем пользователям
    
    Returns:
        Средний LTV в рублях
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
    """
    Получить ARPU (Average Revenue Per User)
    
    ARPU = общий доход / количество платящих пользователей
    
    Returns:
        ARPU в рублях
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
        
        # Количество платящих пользователей
        paying_users_count = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'"""
        ) or 0
        
        # ARPU = общий доход / платящие пользователи
        arpu = total_revenue / paying_users_count if paying_users_count > 0 else 0.0
        
        return arpu


# ── Bypass-overwrite audit & recovery ──────────────────────────────
#
# Ловит юзеров, пострадавших от старого бага `ensure_bypass_only_*`:
# у них стоит `is_bypass_only=TRUE` и `expires_at` > NOW+3 года, но
# в истории есть платные subscription_history-записи (purchase /
# renewal / auto_renew) — значит реальная подписка была premium,
# а функция её переписала.
