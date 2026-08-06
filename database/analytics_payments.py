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
from app.utils.security import scrub_secrets
from database.core import get_pool, _to_db_utc, _from_db_utc

logger = logging.getLogger(__name__)


async def get_recent_payments_feed(
    limit: int = 100,
    hours: Optional[int] = None,
    status: Optional[str] = None,
    provider: Optional[str] = None,
) -> list:
    """Recent paid (and optionally pending/expired) purchases for the
    Payments page feed. Joins users so we render @username with no
    second round-trip.

    provider — фильтр по способу оплаты ('platega', 'cryptobot',
    'telegram_stars', 'lava', 'balance'). Отдельная величина 'balance'
    здесь показательна: такие строки в выручку не входят (это движение уже
    учтённых денег), и экран обязан их помечать, а не суммировать.
    """
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
    if provider:
        params.append(provider)
        # COALESCE, а не голая колонка: строки без провайдера отдаются как
        # 'unknown', и фильтр «неизвестно» обязан их находить.
        where.append(f"COALESCE(pp.payment_provider, 'unknown') = ${len(params)}")
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
            # Migration not applied — fall back without payment_provider.
            # Условие фильтра подменяется вместе с колонкой в SELECT: иначе
            # запрос с ?provider= падал бы тем же UndefinedColumnError уже
            # внутри обработчика отката.
            sql_fallback = sql.replace(
                "COALESCE(pp.payment_provider, 'unknown') AS payment_provider,",
                "'unknown' AS payment_provider,",
            ).replace(
                "COALESCE(pp.payment_provider, 'unknown') = $",
                "'unknown' = $",
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
    """Несостоявшиеся платежи, свежие сверху. [] — если таблицы ещё нет.

    ЧТО НЕ УХОДИТ НАРУЖУ

        raw_payload — тело вебхука провайдера целиком, до 8000 символов
        JSON. Там подписи запроса и служебные поля провайдера. Раньше
        запрос был `SELECT pe.*`, то есть колонка ехала в браузер
        администратора и оседала в логах прокси по дороге; на экране её
        при этом никто не показывал. Сейчас колонок нет ни одного лишнего
        — перечислены явно, и добавление новой колонки в таблицу больше не
        начинает молча её отдавать.

        error_message пишется из текста исключения, а туда попадает URL
        метода Telegram вместе с токеном бота. Прогоняем через
        scrub_secrets — ту же функцию, что и тексты ошибок на сводке.

    ЧТО УХОДИТ ДОПОЛНИТЕЛЬНО И ЗАЧЕМ

        Красная строка «оплата не прошла» сама по себе не говорит, надо ли
        что-то делать: половина записей — это сорвавшаяся попытка, после
        которой человек оплатил со второго раза, а половина — деньги
        ушли, доступа нет. Различает их состояние самой покупки и
        подписки на момент просмотра, поэтому рядом с каждой ошибкой едут:

            purchase_status      — чем кончилась покупка с этим purchase_id
                                   (paid / pending / expired / None, если
                                   строки нет вовсе);
            purchase_price_rubles, purchase_tariff, purchase_type;
            subscription_expires_at, subscription_status — есть ли у
                                   человека доступ прямо сейчас.

        Оба блока берутся LATERAL-подзапросом с LIMIT 1, а не обычным
        JOIN: purchase_id в pending_purchases не уникален по построению, и
        JOIN размножил бы строки ошибок, тихо завысив их количество.
    """
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
    # Колонки перечислены явно. raw_payload здесь нет намеренно — см.
    # докстринг. `SELECT pe.*` вернул бы и его, и любую колонку, которую
    # добавят в таблицу завтра.
    sql = f"""
        SELECT pe.id, pe.telegram_id, pe.purchase_id, pe.payment_provider,
               pe.amount_rubles, pe.stage, pe.error_code, pe.error_message,
               pe.created_at, u.username,
               pur.status        AS purchase_status,
               pur.price_kopecks AS purchase_price_kopecks,
               pur.tariff        AS purchase_tariff,
               pur.purchase_type AS purchase_type,
               sub.expires_at    AS subscription_expires_at,
               sub.status        AS subscription_status
        FROM payment_errors pe
        LEFT JOIN users u ON u.telegram_id = pe.telegram_id
        LEFT JOIN LATERAL (
            SELECT pp.status, pp.price_kopecks, pp.tariff, pp.purchase_type
            FROM pending_purchases pp
            WHERE pe.purchase_id IS NOT NULL
              AND pp.purchase_id = pe.purchase_id
            ORDER BY pp.created_at DESC NULLS LAST
            LIMIT 1
        ) pur ON TRUE
        LEFT JOIN LATERAL (
            SELECT s.expires_at, s.status
            FROM subscriptions s
            WHERE s.telegram_id = pe.telegram_id
            ORDER BY (s.status = 'active') DESC, s.expires_at DESC NULLS LAST
            LIMIT 1
        ) sub ON TRUE
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
        if d.get("subscription_expires_at"):
            d["subscription_expires_at"] = _from_db_utc(d["subscription_expires_at"])
        # Цена покупки в базе в копейках. Отдаём рублями и убираем копейки
        # из ответа: две величины одного и того же в одном объекте — прямой
        # путь к тому, что где-то на экране поделят на сто второй раз.
        kopecks = d.pop("purchase_price_kopecks", None)
        d["purchase_price_rubles"] = (kopecks / 100.0) if kopecks is not None else None
        if d.get("amount_rubles") is not None:
            try:
                d["amount_rubles"] = float(d["amount_rubles"])
            except Exception:
                d["amount_rubles"] = None
        # Текст исключения уходит прямо на экран администратора.
        # Ограничение длиннее, чем на сводке: здесь это основной способ
        # понять, почему платёж не прошёл, и обрезать его до строчки
        # значит оставить разбор без причины.
        if d.get("error_message"):
            d["error_message"] = scrub_secrets(d["error_message"], limit=1000)
        out.append(d)
    return out


async def get_purchase_detail(row_id: int) -> Optional[Dict[str, Any]]:
    """Разбор одной покупки: сама строка, человек, доступ, ошибки по ней.

    ЗАЧЕМ ОТДЕЛЬНАЯ ФУНКЦИЯ, А НЕ get_payment

        get_payment читает таблицу `payments` — она устарела и не покрывает
        большую часть потоков. Лента платежей построена на
        pending_purchases, и её id в `payments` означает другую строку либо
        не означает ничего. Разбор, открытый из ленты, обязан смотреть в ту
        же таблицу, из которой пришла строка.

    ЧТО ВНУТРИ

        purchase       — покупка: тариф, период, сумма в рублях, провайдер,
                         статус, даты. Учётные данные Spotify гасятся тем
                         же CASE, что и в ленте (см. шапку модуля).
        user           — username и баланс в рублях.
        subscription   — что с доступом сейчас: тип, срок, статус.
        errors         — записи payment_errors с тем же purchase_id, свежие
                         сверху. Тексты прогнаны через scrub_secrets;
                         raw_payload не отдаётся здесь ровно так же, как в
                         get_recent_payment_errors.

    None — покупки с таким id нет.
    """
    pool = await get_pool()
    if pool is None:
        return None

    purchase_sql = """
        SELECT pp.id, pp.purchase_id, pp.telegram_id, pp.tariff,
               pp.purchase_type, pp.period_days, pp.price_kopecks,
               pp.status, pp.created_at, pp.expires_at, pp.is_combo,
               pp.farm_plot_id, pp.provider_invoice_id,
               CASE WHEN pp.purchase_type = 'spotify' THEN NULL ELSE pp.promo_code END AS promo_code,
               CASE WHEN pp.purchase_type = 'spotify' THEN NULL ELSE pp.country END AS country,
               COALESCE(pp.payment_provider, 'unknown') AS payment_provider,
               u.username,
               COALESCE(u.balance, 0) AS balance_kopecks
        FROM pending_purchases pp
        LEFT JOIN users u ON u.telegram_id = pp.telegram_id
        WHERE pp.id = $1
    """
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(purchase_sql, row_id)
        except asyncpg.UndefinedColumnError:
            row = await conn.fetchrow(
                purchase_sql.replace(
                    "COALESCE(pp.payment_provider, 'unknown') AS payment_provider,",
                    "'unknown' AS payment_provider,",
                ),
                row_id,
            )
        if row is None:
            return None

        telegram_id = row["telegram_id"]
        sub = None
        if telegram_id:
            sub = await conn.fetchrow(
                """SELECT subscription_type, expires_at, status, source,
                          COALESCE(is_combo, FALSE) AS is_combo,
                          COALESCE(auto_renew, FALSE) AS auto_renew
                   FROM subscriptions
                   WHERE telegram_id = $1
                   ORDER BY (status = 'active') DESC, expires_at DESC NULLS LAST
                   LIMIT 1""",
                telegram_id,
            )

        errors: list = []
        if row["purchase_id"]:
            try:
                # Колонки перечислены явно и здесь: raw_payload наружу не
                # уходит ни из одного запроса этого модуля.
                error_rows = await conn.fetch(
                    """SELECT id, stage, payment_provider, error_code,
                              error_message, amount_rubles, created_at
                       FROM payment_errors
                       WHERE purchase_id = $1
                       ORDER BY created_at DESC
                       LIMIT 20""",
                    row["purchase_id"],
                )
            except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
                error_rows = []
            for e in error_rows:
                item = dict(e)
                if item.get("created_at"):
                    item["created_at"] = _from_db_utc(item["created_at"]).isoformat()
                if item.get("error_message"):
                    item["error_message"] = scrub_secrets(item["error_message"], limit=1000)
                if item.get("amount_rubles") is not None:
                    try:
                        item["amount_rubles"] = float(item["amount_rubles"])
                    except Exception:
                        item["amount_rubles"] = None
                errors.append(item)

    d = dict(row)
    created_at = _from_db_utc(d["created_at"]) if d["created_at"] else None
    expires_at = _from_db_utc(d["expires_at"]) if d["expires_at"] else None
    purchase = {
        "id": int(d["id"]),
        "purchase_id": d["purchase_id"],
        "telegram_id": int(telegram_id) if telegram_id else None,
        "tariff": d["tariff"],
        "purchase_type": d["purchase_type"],
        "period_days": d["period_days"],
        "price_rubles": (d["price_kopecks"] or 0) / 100.0,
        "status": d["status"],
        "payment_provider": d["payment_provider"],
        "provider_invoice_id": d["provider_invoice_id"],
        "promo_code": d["promo_code"],
        "country": d["country"],
        "is_combo": bool(d["is_combo"]),
        "farm_plot_id": d["farm_plot_id"],
        "created_at": created_at.isoformat() if created_at else None,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }

    subscription = None
    if sub is not None:
        sub_expires = _from_db_utc(sub["expires_at"]) if sub["expires_at"] else None
        subscription = {
            "subscription_type": sub["subscription_type"],
            "expires_at": sub_expires.isoformat() if sub_expires else None,
            "status": sub["status"],
            "source": sub["source"],
            "is_combo": bool(sub["is_combo"]),
            "auto_renew": bool(sub["auto_renew"]),
        }

    return {
        "purchase": purchase,
        "user": {
            "telegram_id": int(telegram_id) if telegram_id else None,
            "username": d["username"] or None,
            "balance_rubles": float(d["balance_kopecks"] or 0) / 100.0,
        },
        "subscription": subscription,
        "errors": errors,
    }


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
