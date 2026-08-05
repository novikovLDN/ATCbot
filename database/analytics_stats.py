"""Счётчики бота: пользователи, подписки, конверсия, журнал действий.

ЧТО ЗДЕСЬ
    Всё, что считает не деньги, а людей и подписки: новые пользователи за
    период, активированные триалы, доля просроченных подписок, среднее
    время жизни подписки, последние записи audit_log. Деньги внутри
    get_extended_bot_stats остались только потому, что экран бота читает
    из неё же и total_revenue — вырезать ключ нельзя.

ПОЧЕМУ ОТДЕЛЬНО
    Эти цифры правят, когда меняются экраны /admin → Статистика, а не
    когда меняется определение выручки. Разные поводы — разные файлы.

ЧТО ЛЕГКО СЛОМАТЬ
    1. Ключи ответа get_extended_bot_stats. app/handlers/admin/stats.py
       читает их по именам и падает целиком, если ключ пропал. Поэтому
       здесь живут пары синонимов (mrr / revenue_last_30d_rubles,
       churn_rate / expired_subscription_share_percent): новое честное имя
       и старое — ради экранов бота.

    2. Границу суток. new_today режется по Europe/Moscow средствами
       Postgres — как и весь остальной дашборд. Вернёте UTC-полночь, и три
       часа регистраций (00:00–03:00 МСК) уедут во вчера, причём только у
       этой цифры: соседние останутся московскими.

    3. Деньги здесь тоже в рублях. total_revenue и mrr когда-то отдавались
       в копейках, а бот дописывал «₽» — на экране висела цифра в сто раз
       больше настоящей.
"""
import asyncpg
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import config
import database.core as _core
from database.core import get_pool, _to_db_utc

logger = logging.getLogger(__name__)


async def get_business_metrics() -> Dict[str, Any]:
    """Получить бизнес-метрики сервиса

    Returns:
        Словарь с метриками:
        - avg_subscription_lifetime_days: среднее время жизни подписки (в днях)
        - avg_renewals_per_user: среднее количество продлений на пользователя

    ПОЧЕМУ ЗДЕСЬ БОЛЬШЕ НЕТ approval_rate_percent
    ("Процент подтверждённых платежей").

    Считалась она как COUNT(status='approved') / COUNT(*) по payments и
    всегда давала 100%. Не потому, что у нас идеальные платежи, а потому,
    что строка в payments по построению не может остаться неподтверждённой:
    пять из шести мест INSERT'а сразу пишут status='approved'
    (database/subscriptions.py: balance_topup, gift, traffic_pack,
    farm_effect, apple_id), а шестое — ветка подписки в finalize_purchase —
    вставляет 'pending' и переводит в 'approved' в ТОЙ ЖЕ транзакции. Если
    выдача упала, транзакция откатывается и строки не остаётся вовсе.
    Знаменатель и числитель — одно и то же множество.

    Почему не переопределили как «долю успешных попыток оплаты». Для этого
    нужен знаменатель — попытки. Обоих кандидатов пришлось забраковать:

    1) payment_errors. Это лог НАШИХ сбоев на вебхуке, а не отказов
       плательщику: stage там — setup_missing, webhook_invalid_json,
       transient, timeout, unhandled_exception (app/api/payment_webhook.py).
       'transient' вообще означает «повторим», и после успешного повтора в
       базе будут и строка ошибки, и одобренный платёж. Такой знаменатель
       считал бы успешные оплаты неудачными.

    2) pending_purchases. Соблазнительно взять paid / (paid + expired), но
       'expired' там не равно «человек не заплатил»: создание любого нового
       счёта принудительно гасит все прежние pending этого пользователя
       (database/pending_purchases.py: 51, 120, 258). Пользователь, который
       потыкал три тарифа и купил один, даст 1 paid и 2 expired — метрика
       мерила бы нажатия на кнопки.

    Отказ на стороне провайдера (карта не прошла) до нас просто не доходит:
    вебхук приходит только по успеху. Мерить нечего, поэтому метрика убрана
    целиком, а не заменена правдоподобным числом.

    ПОЧЕМУ ЗДЕСЬ БОЛЬШЕ НЕТ avg_payment_approval_time_seconds
    ("Время апрува" в дашборде и в /admin → Метрики).

    Считалась она так: из audit_log брались строки с action
    'payment_approved'/'subscription_renewed', из свободного текста
    details регуляркой выдирался «Payment ID: N», кастовался в INTEGER и
    джойнился с payments. Две беды.

    1) Строк таких нет. Единственным писателем 'payment_approved' была
       ручная модерация (approve_payment_atomic), её удалили вместе с
       ветвью ручного подтверждения — сейчас платежи подтверждает вебхук
       провайдера. Формата «Payment ID: N» не пишет вообще никто:
       метрика возвращала NULL всегда, а фронт рисовал прочерк.

    2) Считать её честно не из чего. Очевидная замена —
       payments.paid_at - payments.created_at — даст ноль: строка в
       payments вставляется уже со статусом 'approved' и paid_at = NOW()
       в том же INSERT. Никакого промежутка «оплатили → подтвердили» в
       системе не существует, измерять нечего.

    Поэтому метрика убрана целиком (расчёт, ключ ответа API, плитки в
    дашборде), а не заменена на правдоподобное число. Пустой прочерк на
    экране админ трактует как «данных пока нет» и ждёт, что они появятся;
    отсутствие плитки честнее.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. Среднее время жизни подписки (из subscription_history)
        # Используем только завершенные подписки (end_date < now)
        avg_lifetime = await conn.fetchval(
            """SELECT AVG(EXTRACT(EPOCH FROM (end_date - start_date)) / 86400.0)
               FROM subscription_history
               WHERE end_date IS NOT NULL
               AND end_date < NOW()"""
        )
        
        # 2. Среднее количество продлений на пользователя
        total_renewals = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history WHERE action_type = 'renewal'"""
        )
        total_users_with_subscriptions = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id) FROM subscription_history"""
        )
        avg_renewals = 0.0
        if total_users_with_subscriptions and total_users_with_subscriptions > 0:
            avg_renewals = (total_renewals or 0) / total_users_with_subscriptions

        return {
            "avg_subscription_lifetime_days": float(avg_lifetime) if avg_lifetime else None,
            "avg_renewals_per_user": float(avg_renewals) if avg_renewals else 0.0,
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
    """Сколько людей прямо сейчас платят за VPN.

    Не то же самое, что active_subscriptions из get_extended_bot_stats: там
    в счёт попадают триалы и bypass-only строки, и число получается больше
    реального.

    СПИСОК ТАРИФОВ — ИЗ config, А НЕ ЗДЕСЬ
        Он был выписан прямо в запросе, и в нём не было combo_basic и
        combo_plus. Комбо — отдельные продукты со своей ценой, а не
        «plus с добавкой»; у них два представления в колонке
        subscription_type, и явное в счёт не попадало. То есть
        комбо-подписчиков просто не считали. Заметить это по числу нельзя:
        оно не ломается, оно становится другим.

    ОШИБКУ НЕ ГЛУШИМ
        Раньше здесь стоял `except: return 0`. Ноль неотличим от честного
        «никто не платит», а вызывающий (/stats/overview) как раз умеет
        обработать отказ — он подставляет active_subscriptions. Свой
        перехват отбирал у него эту возможность: до запасного варианта
        дело не доходило, на экран уезжал ноль.
    """
    pool = await get_pool()
    if pool is None:
        return 0
    now = _to_db_utc(datetime.now(timezone.utc))
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            """SELECT COUNT(*) FROM subscriptions
               WHERE status = 'active'
                 AND expires_at > $1
                 AND COALESCE(is_bypass_only, FALSE) = FALSE
                 AND COALESCE(source, '') != 'trial'
                 AND subscription_type = ANY($2::text[])""",
            now,
            list(config.PAID_SUBSCRIPTION_TYPES),
        )
        return int(n or 0)


async def get_extended_bot_stats() -> Dict[str, Any]:
    """Расширенная статистика бота для мониторинга.

    КТО ЭТО ЧИТАЕТ. Дашборд берёт отсюда только total_users и
    active_subscriptions (см. /stats/overview). Всё остальное рисует бот:
    /admin → Статистика и /admin → Расширенная статистика
    (app/handlers/admin/stats.py). Поэтому «удалить неиспользуемое»
    не вариант — экраны бота обращаются к ключам по индексу и упадут
    целиком. Вместо удаления — честные имена и честный расчёт.

    ЧТО БЫЛО НЕ ТАК И ЧТО ИСПРАВЛЕНО

    1. new_today считался от полуночи UTC, хотя весь остальной дашборд
       (get_daily_timeseries, get_hourly_timeseries, тайл «Сегодня») режет
       сутки по Europe/Moscow. Три часа регистраций — с 00:00 до 03:00 МСК —
       у этой цифры попадали во вчера, у соседних — в сегодня. Теперь МСК.

    2. mrr никогда не был MRR: это просто сумма оплат за последние 30 дней,
       включая разовые покупки мини-магазина. Честное имя —
       revenue_last_30d_rubles; ключ mrr остался синонимом для экрана бота.

    3. total_revenue и mrr отдавались в копейках, а бот печатал их с «₽» —
       на экране висела цифра в сто раз больше настоящей. Теперь оба поля
       (и их новые имена) в рублях.

    4. churn_rate — это не отток. Это доля пользователей, у которых
       подписка сейчас просрочена, за всё время и без привязки к периоду;
       в знаменатель входят триалы и bypass-строки. Честное имя —
       expired_subscription_share_percent, churn_rate оставлен синонимом.
       (Замечание аудита «считается по строкам, а не по пользователям»
       не подтвердилось: subscriptions.telegram_id UNIQUE, строка = юзер.)

    5. avg_subs_per_user считался как AVG(COUNT(*) GROUP BY telegram_id)
       по subscriptions. Из-за того же UNIQUE это тождественно 1.0 —
       константа, занимающая строку на экране. Считаем то, что обещает
       название: сколько оплаченных периодов подписки приходится на
       пользователя, по subscription_history (purchase + renewal).
    """
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
        total_revenue_kop = await conn.fetchval(
            "SELECT COALESCE(SUM(price_kopecks), 0) FROM pending_purchases WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'"
        ) or 0

        # Выручка за последние 30 дней. Это НЕ MRR: сюда входят разовые
        # покупки мини-магазина и подписки любой длины, никакой
        # нормировки на месяц нет.
        # created_at в pending_purchases — момент начала оплаты, а не её
        # подтверждения. Счёт живёт 15-30 минут, поэтому для месячного окна
        # это корректный ориентир.
        revenue_30d_since = _to_db_utc(now - timedelta(days=30))
        revenue_30d_kop = await conn.fetchval(
            "SELECT COALESCE(SUM(price_kopecks), 0) FROM pending_purchases "
            "WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance' AND created_at >= $1",
            revenue_30d_since
        ) or 0

        # Новые пользователи за сегодня — сутки по Москве, как и весь
        # остальной дашборд. Границу считает сам Postgres: NOW() в МСК,
        # обрезаем до полуночи и переводим обратно в timestamptz, чтобы
        # сравнение с users.created_at (TIMESTAMPTZ с миграции 025) шло
        # в одной зоне.
        new_today = await conn.fetchval(
            """SELECT COUNT(*) FROM users
               WHERE created_at >= (
                   DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow'))
                       AT TIME ZONE 'Europe/Moscow'
               )"""
        )

        # Broadcasts sent
        total_broadcasts = await conn.fetchval("SELECT COUNT(*) FROM broadcasts")

        # Сколько оплаченных периодов подписки приходится на пользователя.
        # Берём subscription_history, а не subscriptions: в subscriptions
        # telegram_id UNIQUE, поэтому среднее по строкам там тождественно
        # равно 1.0. reissue/manual_reissue отбрасываем — перевыпуск ключа
        # не новая подписка.
        avg_periods = await conn.fetchval(
            """SELECT ROUND(AVG(cnt), 1) FROM (
                   SELECT COUNT(*) AS cnt
                   FROM subscription_history
                   WHERE action_type IN ('purchase', 'renewal')
                   GROUP BY telegram_id
               ) h"""
        )

        conversion_rate = round((users_with_sub / total_users * 100), 1) if total_users > 0 else 0
        trial_rate = round((total_trial / total_users * 100), 1) if total_users > 0 else 0
        expired_share = round((expired_subs / (active_subs + expired_subs) * 100), 1) if (active_subs + expired_subs) > 0 else 0

        total_revenue_rubles = int(total_revenue_kop) / 100
        revenue_last_30d_rubles = int(revenue_30d_kop) / 100
        avg_periods_per_user = float(avg_periods) if avg_periods else 0.0

        return {
            "total_users": total_users or 0,
            "active_subs": active_subs or 0,
            # Дашборд читает active_subscriptions в трёх местах
            # (Dashboard.tsx: карточка «Активных с триалами» и подсказка к
            # ней). Такого ключа здесь не было, поэтому карточка всегда
            # оставалась пустой, а fallback в stats.py превращался в None.
            # Отдаём оба имени: старое остаётся для существующих
            # потребителей, новое — то, что реально запрашивает фронт.
            "active_subscriptions": active_subs or 0,
            "expired_subs": expired_subs or 0,
            "total_trial": total_trial or 0,
            "trial_rate": trial_rate,
            "users_with_sub": users_with_sub or 0,
            "conversion_rate": conversion_rate,
            "new_today": new_today or 0,
            "total_broadcasts": total_broadcasts or 0,
            # Честные имена — их и надо использовать в новом коде.
            "total_revenue_rubles": total_revenue_rubles,
            "revenue_last_30d_rubles": revenue_last_30d_rubles,
            "expired_subscription_share_percent": expired_share,
            "avg_subscription_periods_per_user": avg_periods_per_user,
            # Старые имена — синонимы, живут ради экранов бота
            # (app/handlers/admin/stats.py читает их по индексу). Значения
            # те же самые, включая перевод денег в рубли: раньше здесь
            # лежали копейки, а бот дописывал к ним «₽».
            "total_revenue": total_revenue_rubles,
            "mrr": revenue_last_30d_rubles,
            "churn_rate": expired_share,
            "avg_subs_per_user": avg_periods_per_user,
        }
