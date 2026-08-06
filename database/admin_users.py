"""Пользователь глазами админа: выгрузки, история, финансовый профиль.

ЧТО ЗДЕСЬ
    Только чтение. Данные, которые админка показывает в карточке
    пользователя и отдаёт в выгрузки: список всех пользователей, активные
    подписки, история подписок, сводный финансовый и реферальный профиль.

ПОЧЕМУ ОТДЕЛЬНО ОТ ОТЧЁТОВ
    Отчёты (database/admin_reports.py) агрегируют по всей базе и живут в
    часовом поясе Москвы. Здесь всё считается по одному telegram_id и часовой
    пояс не важен. Правят их по разным поводам: тут — когда меняется карточка
    пользователя, там — когда спорят о цифрах на дашборде.

ЧТО ЛЕГКО СЛОМАТЬ
    Имена колонок в get_user_extended_stats. В pending_purchases НЕТ ни
    amount_kopecks, ни paid_at; запрос, который их спрашивал, падал
    UndefinedColumnError на живой базе — и карточка пользователя не
    открывалась ни в боте, ни в дашборде.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import database.core as _core
from database.core import get_pool, _from_db_utc, _to_db_utc

logger = logging.getLogger(__name__)


async def get_all_users_for_export() -> list:
    """Получить всех пользователей для экспорта
    
    Returns:
        Список словарей с данными пользователей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC")
        return [dict(row) for row in rows]


async def get_active_subscriptions_for_export() -> list:
    """Получить все активные подписки для экспорта
    
    Returns:
        Список словарей с данными активных подписок
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        rows = await conn.fetch(
            "SELECT * FROM subscriptions WHERE expires_at > $1 ORDER BY expires_at DESC",
            _to_db_utc(now)
        )
        return [dict(row) for row in rows]


async def get_subscription_history(telegram_id: int, limit: int = 5) -> list:
    """Получить историю подписок пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        limit: Максимальное количество записей (по умолчанию 5)
    
    Returns:
        Список словарей с записями истории, отсортированные по created_at DESC
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscription_history skipped")
        return []
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscription_history skipped")
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM subscription_history 
               WHERE telegram_id = $1 
               ORDER BY created_at DESC 
               LIMIT $2""",
            telegram_id, limit
        )
        return [dict(row) for row in rows]


async def get_user_extended_stats(telegram_id: int) -> Dict[str, Any]:
    """Full financial + referral profile of a user for the admin card.

    Returns:
        renewals_count            — продлений подписки (subscription_history)
        reissues_count            — перевыпусков ключа
        total_spent_rubles        — сумма ВСЕХ approved-платежей в ₽
        total_payments_count      — общее число approved-платежей
        first_paid_at / last_paid_at — граничные даты платежей
        referrer_telegram_id      — кто пригласил (или NULL)
        referrer_username         — username пригласившего (для UI)
        referrals_invited_count   — сколько пригласил сам
        referrals_rewarded_count  — из них сколько «сработали» (bonus paid)
        traffic_gb_purchased_total — суммарно ГБ купил (bypass-паки)
        traffic_purchases_count   — количество GB-покупок
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Продления
        renewals_count = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history
               WHERE telegram_id = $1 AND action_type = 'renewal'""",
            telegram_id,
        )
        # Перевыпуски
        reissues_count = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history
               WHERE telegram_id = $1
                 AND action_type IN ('reissue','manual_reissue')""",
            telegram_id,
        )
        # Финансы — оплаченные покупки по всем типам (подписки/трафик/etc).
        # price_kopecks / 100 = рубли; NULLs исключаем.
        #
        # Колонки называются именно так, а не иначе, и это важно: в
        # pending_purchases НЕТ ни amount_kopecks, ни paid_at. Запрос,
        # который их запрашивал, падал UndefinedColumnError на живой базе —
        # карточка пользователя в боте и в дашборде не открывалась вовсе.
        #
        # Момент оплаты берём из created_at — это старт чекаута, а не
        # приход денег. Расхождение до срока жизни счёта (минуты). Честная
        # колонка paid_at потребует миграции + записи в двух местах, где
        # статус переводится в 'paid' (database/subscriptions.py,
        # database/pending_purchases.py); до тех пор created_at — лучшее,
        # что есть, и им же считаются все оконные метрики выручки.
        pay_row = await conn.fetchrow(
            """SELECT
                   COUNT(*) AS n,
                   COALESCE(SUM(price_kopecks), 0)::BIGINT AS total_kopecks,
                   MIN(created_at) AS first_paid_at,
                   MAX(created_at) AS last_paid_at
               FROM pending_purchases
               WHERE telegram_id = $1
                 AND status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'""",
            telegram_id,
        )
        total_payments = int(pay_row["n"] or 0) if pay_row else 0
        total_kopecks = int(pay_row["total_kopecks"] or 0) if pay_row else 0
        first_paid_at = pay_row["first_paid_at"] if pay_row else None
        last_paid_at = pay_row["last_paid_at"] if pay_row else None

        # Пригласивший
        ref_row = await conn.fetchrow(
            """SELECT u.referrer_id, ru.username AS referrer_username
               FROM users u
               LEFT JOIN users ru ON ru.telegram_id = u.referrer_id
               WHERE u.telegram_id = $1""",
            telegram_id,
        )
        referrer_id = ref_row["referrer_id"] if ref_row else None
        referrer_username = ref_row["referrer_username"] if ref_row else None

        # Сколько сам пригласил
        inv_row = await conn.fetchrow(
            """SELECT COUNT(*) AS n,
                      COUNT(*) FILTER (WHERE is_rewarded) AS rewarded
               FROM referrals
               WHERE referrer_user_id = $1""",
            telegram_id,
        )
        invited = int(inv_row["n"] or 0) if inv_row else 0
        rewarded = int(inv_row["rewarded"] or 0) if inv_row else 0

        # Купил ГБ (bypass). Столбец gb_purchased.gb_amount, count и sum.
        gb_row = None
        try:
            gb_row = await conn.fetchrow(
                """SELECT COUNT(*) AS n,
                          COALESCE(SUM(gb_amount), 0)::INTEGER AS gb_total
                   FROM gb_purchased
                   WHERE telegram_id = $1""",
                telegram_id,
            )
        except Exception as e:
            logger.debug("gb_purchased query failed (табл. может отсутствовать): %s", e)
        gb_total = int(gb_row["gb_total"] or 0) if gb_row else 0
        gb_count = int(gb_row["n"] or 0) if gb_row else 0

        return {
            "renewals_count": renewals_count or 0,
            "reissues_count": reissues_count or 0,
            "total_spent_rubles": total_kopecks / 100.0,
            "total_payments_count": total_payments,
            "first_paid_at": first_paid_at.isoformat() if first_paid_at else None,
            "last_paid_at": last_paid_at.isoformat() if last_paid_at else None,
            "referrer_telegram_id": referrer_id,
            "referrer_username": referrer_username,
            "referrals_invited_count": invited,
            "referrals_rewarded_count": rewarded,
            "traffic_gb_purchased_total": gb_total,
            "traffic_purchases_count": gb_count,
        }


async def get_all_users_telegram_ids() -> list:
    """Получить список всех Telegram ID пользователей"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id FROM users")
        return [row["telegram_id"] for row in rows]


# ──────────────────────────────────────────────────────────────────────
# Плотная таблица экрана «Пользователи»
#
# Одна строка = один человек со всем, что нужно решить прямо в списке:
# кто это, что у него за подписка, когда кончится, есть ли чем продлить
# (баланс). Открывать карточку ради этих четырёх вопросов не надо.
#
# ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. Суммы потраченного на человека в списке нет:
# она считается по pending_purchases и на сотне строк дала бы сотню
# агрегатов. Её место — карточка (get_user_extended_stats), где она
# считается один раз для одного telegram_id.
# ──────────────────────────────────────────────────────────────────────

# Подписка берётся ОДНА, самая живая: сперва действующая, потом самая
# свежая по сроку. Без ORDER BY человек с историей из пяти подписок
# показывал бы в списке случайную из них.
_LATEST_SUBSCRIPTION_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT s.subscription_type, s.expires_at, s.activated_at, s.source,
               s.status, s.auto_renew,
               COALESCE(s.is_combo, FALSE) AS is_combo,
               COALESCE(s.is_bypass_only, FALSE) AS is_bypass_only
        FROM subscriptions s
        WHERE s.telegram_id = u.telegram_id
        ORDER BY (s.status = 'active' AND s.expires_at > $1) DESC,
                 s.expires_at DESC NULLS LAST
        LIMIT 1
    ) sub ON TRUE
"""


def _user_row(record, now: datetime) -> Dict[str, Any]:
    """Строка списка в том виде, в каком её рисует таблица.

    Баланс приводится к рублям здесь: в базе он в копейках, и деление на
    100 в трёх разных местах интерфейса рано или поздно разъедется.

    Каждая дата проходит через _from_db_utc: колонки в проекте разъехались
    по типам (TIMESTAMP и TIMESTAMPTZ), и сравнение naive-времени с aware
    падает TypeError прямо на выдаче списка.
    """
    expires_at = _from_db_utc(record["expires_at"]) if record["expires_at"] else None
    activated_at = _from_db_utc(record["activated_at"]) if record["activated_at"] else None
    created_at = _from_db_utc(record["created_at"]) if record["created_at"] else None
    has_active = bool(
        record["status"] == "active"
        and expires_at is not None
        and expires_at > now
    )
    return {
        "telegram_id": int(record["telegram_id"]),
        "username": record["username"] or None,
        "language": record["language"],
        "created_at": created_at.isoformat() if created_at else None,
        "balance_rubles": float(record["balance_kopecks"] or 0) / 100.0,
        "is_vip": bool(record["is_vip"]),
        "subscription_type": record["subscription_type"],
        "is_combo": bool(record["is_combo"]) if record["subscription_type"] else False,
        "is_bypass_only": bool(record["is_bypass_only"]) if record["subscription_type"] else False,
        "source": record["source"],
        "status": record["status"],
        "auto_renew": bool(record["auto_renew"]) if record["subscription_type"] else False,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "activated_at": activated_at.isoformat() if activated_at else None,
        "has_active_sub": has_active,
    }


async def list_users_dashboard(
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Страница списка пользователей с подпиской и балансом.

    q — та же подстрока, что в search_users_dashboard: цифры ищутся по
    telegram_id как по тексту (префикс «123» находит 1234567890), буквы —
    по username без учёта регистра, ведущая @ отбрасывается. Порядок при
    поиске ранжированный: точное совпадение, потом начало, потом середина.
    Без q — свежезарегистрированные сверху.

    Возвращает {'items': [...], 'total': N}: total — сколько подходит под
    условие ВСЕГО, а не сколько отдано. Без него «показаны 50 из 50»
    неотличимо от «всего 50», и человек не знает, есть ли дальше строки.

    Исключения НЕ гасятся: пустой список вместо отказа на этом экране
    читался бы как «пользователей нет».
    """
    pool = await get_pool()
    if pool is None:
        raise RuntimeError("db pool is not initialised")

    now = datetime.now(timezone.utc)
    now_db = _to_db_utc(now)
    term = (q or "").strip().lstrip("@")

    # Порядок append в params обязан совпадать с порядком $N в тексте
    # запроса: $1 занят временем для LATERAL выше, дальше идут условия
    # поиска. Перепутаете — запрос отфильтрует не по тому полю и не упадёт.
    params: List[Any] = [now_db]
    where = "TRUE"
    order = "u.created_at DESC NULLS LAST"

    if term:
        params.append(f"%{term}%")      # $2 — подстрока
        params.append(term)             # $3 — точное совпадение
        params.append(f"{term}%")       # $4 — начало строки
        where = "(CAST(u.telegram_id AS TEXT) ILIKE $2 OR u.username ILIKE $2)"
        order = """
            CASE
                WHEN CAST(u.telegram_id AS TEXT) = $3 THEN 0
                WHEN LOWER(u.username) = LOWER($3) THEN 1
                WHEN CAST(u.telegram_id AS TEXT) ILIKE $4 THEN 2
                WHEN u.username ILIKE $4 THEN 3
                ELSE 4
            END,
            u.created_at DESC NULLS LAST
        """

    params.append(limit)
    limit_idx = len(params)
    params.append(offset)
    offset_idx = len(params)

    sql = f"""
        SELECT u.telegram_id, u.username, u.language, u.created_at,
               COALESCE(u.balance, 0) AS balance_kopecks,
               EXISTS (
                   SELECT 1 FROM vip_users v WHERE v.telegram_id = u.telegram_id
               ) AS is_vip,
               sub.subscription_type, sub.expires_at, sub.activated_at,
               sub.source, sub.status, sub.auto_renew, sub.is_combo,
               sub.is_bypass_only
        FROM users u
        {_LATEST_SUBSCRIPTION_LATERAL}
        WHERE {where}
        ORDER BY {order}
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """
    # У счётчика собственная нумерация параметров: подстрока в нём $1, а не
    # $2. Переиспользовать текст условия из выборки нельзя — там номера
    # сдвинуты временем для LATERAL, и запрос искал бы по времени.
    if term:
        count_sql = (
            "SELECT COUNT(*)::BIGINT FROM users u "
            "WHERE (CAST(u.telegram_id AS TEXT) ILIKE $1 OR u.username ILIKE $1)"
        )
        count_params: List[Any] = [f"%{term}%"]
    else:
        count_sql = "SELECT COUNT(*)::BIGINT FROM users"
        count_params = []

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
        total = await conn.fetchval(count_sql, *count_params)

    return {
        "items": [_user_row(r, now) for r in rows],
        "total": int(total or 0),
    }


async def get_balances_bulk(telegram_ids: List[int]) -> Dict[int, float]:
    """Балансы пачкой, в рублях. Пустой вход — пустой ответ.

    Нужен списку подписок: его строки приходят из dashboard_summary (там
    отбор совпадает с плитками сводки), а баланса в них нет. Отдельный
    запрос на строку дал бы двести запросов на страницу.
    """
    ids = [int(t) for t in telegram_ids if t]
    if not ids:
        return {}
    pool = await get_pool()
    if pool is None:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT telegram_id, COALESCE(balance, 0) AS balance_kopecks "
            "FROM users WHERE telegram_id = ANY($1::bigint[])",
            ids,
        )
    return {
        int(r["telegram_id"]): float(r["balance_kopecks"] or 0) / 100.0
        for r in rows
    }
