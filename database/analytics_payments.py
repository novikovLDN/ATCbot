"""Ленты покупок и журнал ошибок оплаты.

ЧТО ЗДЕСЬ
    Построчная выдача для страницы «Платежи» дашборда и карточки
    пользователя: что купили за окно, что купил конкретный человек, какие
    ошибки словил вебхук. Плюс единственная пишущая функция всей
    аналитики — log_payment_error.

ПОЧЕМУ ОТДЕЛЬНО
    Это не метрики, а сырые строки: их правят, когда меняется вид таблицы
    на экране, а не когда меняется формула выручки. Здесь нет фильтра
    внешних поступлений и он не нужен — лента показывает всё, что было,
    включая покупки с баланса.

ЧТО ЛЕГКО СЛОМАТЬ
    1. У покупок Spotify колонки promo_code и country заняты не по
       назначению: там лежат пароль и email клиента. Обе выборки гасят их
       через CASE. Уберёте CASE — учётные данные поедут в дашборд, где их
       видит любой с доступом.

    2. log_payment_error обязана молчать при любой своей поломке: её
       зовут из обработчиков ошибок оплаты, и исключение отсюда затрёт
       исходную ошибку. Отсюда широкий except и возврат None.

    3. Фильтры собираются в f-строку по индексам ($1, $2, ...). Порядок
       append в params и порядок условий в where должны совпадать —
       перепутаете, и запрос отфильтрует не по тому полю, не упав.
"""
import asyncpg
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc

logger = logging.getLogger(__name__)


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
