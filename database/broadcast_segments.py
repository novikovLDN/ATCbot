"""Подбор получателей рассылки по имени сегмента.

ЧТО ЗДЕСЬ
    Ровно одна функция — get_users_by_segment. Она большая, и это НЕ повод
    её резать: каждый сегмент это самостоятельный SQL со своими границами
    окна, и разложить их по файлам значит потерять возможность сравнить
    соседние сегменты глазами в одном месте.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Сегменты — самая часто правящаяся часть рассылок и единственная, где
    ошибка стоит дорого сразу: неверное условие отправляет сообщение не
    тем людям, и откатить это уже нельзя.

ЧТО ЛЕГКО СЛОМАТЬ
    Сегменты «N дней назад» — это ПОЛУИНТЕРВАЛЫ, а не «раньше чем».
    Например trial_expired_1d берёт [NOW-2d, NOW-1d), а не всё, что старше
    суток. Замена на простое сравнение превращает точечную рассылку в
    рассылку по всей истории.

    Почти каждый сегмент дополнительно требует «и сейчас нет активной
    подписки». Забыть это условие значит написать «ваш триал закончился»
    человеку, который уже заплатил.

    Добавляя сегмент, исключайте пользователей, помеченных недоступными:
    писать в заблокированный чат бессмысленно и портит статистику доставки.
"""
import logging
from datetime import datetime, timezone

from database.core import get_pool, _to_db_utc

logger = logging.getLogger(__name__)


async def get_users_by_segment(segment: str) -> list:
    """Получить список Telegram ID пользователей по сегменту

    Args:
        segment: Сегмент получателей:
            - all_users            — все
            - active_subscriptions — активная подписка
            - no_subscription      — нет активной подписки (включая истёкшие)
            - no_remnawave         — никогда не имели entity в Remnawave
                                     (ни premium, ни bypass)
            - expired_1d / expired_2d / expired_3d — подписка истекла
                                     ровно N полных суток назад
                                     (и сейчас нет активной)
            - started_7d_cold      — холодные лиды: запустили бот за
                                     последние 7 суток (users.created_at)
                                     и до сих пор без активной подписки
                                     И без bypass-entity.
            - trial_ends_in_1d     — у юзера ИДЁТ триал и закончится
                                     в ближайшие 24 часа
                                     (trial_expires_at ∈ (NOW, NOW+24h])
            - trial_expired_6h / 1d / 2d / 3d
                                   — триал закончился N времени назад
                                     по фиксированному бакету:
                                       6h → [NOW-7h, NOW-6h)
                                       1d → [NOW-2d, NOW-1d)
                                       2d → [NOW-3d, NOW-2d)
                                       3d → [NOW-4d, NOW-3d)
                                     И сейчас нет активной подписки.
            - paid_expired_1d      — платная (subscriptions.source='payment')
                                     истекла ровно 1 сутки назад
                                     (expires_at ∈ [NOW-2d, NOW-1d))
                                     и сейчас нет активной подписки

    Returns:
        Список Telegram ID пользователей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if segment == "all_users":
            rows = await conn.fetch("SELECT telegram_id FROM users")
            return [row["telegram_id"] for row in rows]
        elif segment == "active_subscriptions":
            now = _to_db_utc(datetime.now(timezone.utc))
            rows = await conn.fetch(
                """SELECT DISTINCT u.telegram_id
                   FROM users u
                   INNER JOIN subscriptions s ON u.telegram_id = s.telegram_id
                   WHERE s.expires_at > $1""",
                now
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "no_subscription":
            now = _to_db_utc(datetime.now(timezone.utc))
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE NOT EXISTS (
                       SELECT 1 FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id AND s.expires_at > $1
                   )""",
                now
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "no_remnawave":
            # Users who never had ANY Remnawave entity — neither premium
            # nor bypass. They've never been provisioned on the panel.
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE NOT EXISTS (
                       SELECT 1 FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id
                         AND (s.remnawave_premium_uuid IS NOT NULL
                              OR s.remnawave_uuid IS NOT NULL)
                   )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "started_7d_cold":
            # Холодные лиды для прогрева: запустили бот не позже 7 суток
            # назад и до сих пор ничего не купили — ни подписку, ни
            # bypass-ГБ. Условия:
            #   1) users.created_at >= NOW() - 7 days  → свежий старт
            #   2) NO subscription row с expires_at > NOW()  → нет
            #      активной подписки
            #   3) NO subscription row с remnawave_uuid или
            #      remnawave_premium_uuid → не сидит на bypass-only
            #      ключах, оставшихся от триала / прошлой покупки.
            # 1 + 3 — то самое «никаких ключей вообще».
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE u.created_at >= NOW() - INTERVAL '7 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.telegram_id = u.telegram_id
                           AND (
                               s.expires_at > NOW()
                               OR s.remnawave_uuid IS NOT NULL
                               OR s.remnawave_premium_uuid IS NOT NULL
                           )
                     )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "trial_ends_in_1d":
            # Идёт триал, до конца ≤ 24 часа. Цель — пуш с напоминанием
            # «триал заканчивается, оформи подписку».
            #
            # ВАЖНО про tz: users.trial_expires_at — TIMESTAMP без TZ,
            # в БД хранится naive UTC (см. _to_db_utc). NOW() возвращает
            # TIMESTAMPTZ в session-TZ; implicit cast TIMESTAMP→TIMESTAMPTZ
            # интерпретирует TIMESTAMP в session-TZ и даёт сдвиг, если
            # session-TZ ≠ UTC. Используем `NOW() AT TIME ZONE 'UTC'` —
            # это TIMESTAMP-без-TZ в UTC, сравнение с trial_expires_at
            # надёжно без implicit cast в любой session-TZ.
            #
            # COALESCE: trial_expires_at добавлен в схему users позже,
            # чем trial_used_at. У старых триалов поле могло быть NULL.
            # Fallback на trial_used_at + 3 дня (продолжительность
            # триала — см. app/handlers/callbacks/subscription.py:143).
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE u.trial_used_at IS NOT NULL
                     AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                           >  (NOW() AT TIME ZONE 'UTC')
                     AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                           <= (NOW() AT TIME ZONE 'UTC') + INTERVAL '24 hours'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("trial_expired_6h", "trial_expired_1d", "trial_expired_2d", "trial_expired_3d"):
            # Триал закончился N времени назад (фиксированный бакет).
            # Исключаем только тех, у кого есть активная **платная**
            # подписка — это юзеры, успешно конвертнувшиеся, им пуш
            # «триал истёк, купи подписку» уже не нужен. Активные
            # bypass-only/gift/admin_grant не считаем — у них нет
            # основной подписки, и наш пуш им релевантен.
            #   trial_expired_6h → [NOW-7h, NOW-6h)
            #   trial_expired_1d → [NOW-2d, NOW-1d)
            #   trial_expired_2d → [NOW-3d, NOW-2d)
            #   trial_expired_3d → [NOW-4d, NOW-3d)
            # См. коммент про tz и COALESCE в trial_ends_in_1d.
            if segment == "trial_expired_6h":
                upper_sql = "(NOW() AT TIME ZONE 'UTC') - INTERVAL '6 hours'"
                lower_sql = "(NOW() AT TIME ZONE 'UTC') - INTERVAL '7 hours'"
            else:
                days = int(segment.split("_")[-1].rstrip("d"))
                upper_sql = f"(NOW() AT TIME ZONE 'UTC') - INTERVAL '{days} days'"
                lower_sql = f"(NOW() AT TIME ZONE 'UTC') - INTERVAL '{days + 1} days'"
            rows = await conn.fetch(
                f"""SELECT u.telegram_id FROM users u
                    WHERE u.trial_used_at IS NOT NULL
                      AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                            <= {upper_sql}
                      AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                            >  {lower_sql}
                      AND NOT EXISTS (
                          SELECT 1 FROM subscriptions s
                          WHERE s.telegram_id = u.telegram_id
                            AND s.source = 'payment'
                            AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                      )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "paid_expired_1d":
            # Платная подписка (source='payment') истекла ровно
            # 1 сутки назад (бакет [NOW-2d, NOW-1d)). И сейчас нет
            # активной ПЛАТНОЙ — это churn-окно, классическая точка
            # реактивации. (Активный bypass/gift тут не считаем —
            # юзер всё равно без основной подписки.)
            # См. коммент про tz в trial_ends_in_1d.
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE EXISTS (
                       SELECT 1 FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id
                         AND s.source = 'payment'
                         AND s.expires_at <= (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 day'
                         AND s.expires_at >  (NOW() AT TIME ZONE 'UTC') - INTERVAL '2 days'
                   )
                     AND NOT EXISTS (
                       SELECT 1 FROM subscriptions s2
                       WHERE s2.telegram_id = u.telegram_id
                         AND s2.source = 'payment'
                         AND s2.expires_at > (NOW() AT TIME ZONE 'UTC')
                   )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("paid_expired_30d", "paid_lapsed_any"):
            # Реактивационные сегменты по subscription_history:
            #   paid_expired_30d → последний end_date платной транзакции
            #                      попал в [NOW-30d, NOW-1d], и сейчас
            #                      нет активной подписки в subscriptions.
            #   paid_lapsed_any  → когда-либо платил (purchase / renewal /
            #                      auto_renew) и сейчас неактивен —
            #                      максимальная реактивационная аудитория.
            #
            # Почему через subscription_history, а не subscriptions:
            # в subscriptions хранится ТЕКУЩЕЕ состояние подписки;
            # при renewal expires_at UPDATEится в будущее, а старое
            # значение не сохраняется. История истёкших — только в
            # subscription_history (см. column end_date).
            #
            # action_type для платных: purchase, renewal, auto_renew
            # (не 'payment' — то поле в subscriptions.source).
            window_clause = (
                "AND last_paid_end BETWEEN "
                "(NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days' "
                "AND (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 day'"
                if segment == "paid_expired_30d"
                else ""
            )
            rows = await conn.fetch(
                f"""WITH paid_history AS (
                       SELECT telegram_id, MAX(end_date) AS last_paid_end
                       FROM subscription_history
                       WHERE action_type IN ('purchase', 'renewal', 'auto_renew')
                       GROUP BY telegram_id
                   )
                   SELECT p.telegram_id FROM paid_history p
                   WHERE 1=1 {window_clause}
                     AND NOT EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.telegram_id = p.telegram_id
                           AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("paid_bought_within_7d", "paid_bought_within_14d",
                         "paid_bought_within_30d"):
            # Юзер оформил платную подписку в течение последних N дней.
            # Читаем историю успешных платежей (status IN 'paid','approved').
            # Кумулятивное окно (NOT ровно-N-суток бакет) — все, кто
            # покупал хотя бы раз за N дней. Дубли по telegram_id
            # убираются через DISTINCT.
            days = int(segment.split("_")[-1].rstrip("d"))
            rows = await conn.fetch(
                f"""SELECT DISTINCT p.telegram_id
                    FROM payments p
                    WHERE p.status IN ('paid', 'approved')
                      AND p.created_at >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{days} days'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "trial_active_any":
            # Все юзеры у которых СЕЙЧАС идёт триал (не истёк, платной ещё нет).
            # Целевая аудитория для мидл-триал коммуникаций (день 2 из 3 и т.п.).
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE u.trial_used_at IS NOT NULL
                     AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                           > (NOW() AT TIME ZONE 'UTC')
                     AND NOT EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.telegram_id = u.telegram_id
                           AND s.source = 'payment'
                           AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "trial_activated_today":
            # Активировали триал в течение последних 24 часов. Свежая ЦА
            # для welcome-серии, объяснения features и т.п.
            rows = await conn.fetch(
                """SELECT telegram_id FROM users
                   WHERE trial_used_at IS NOT NULL
                     AND trial_used_at >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '24 hours'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("trial_active_day1", "trial_active_day2",
                         "trial_active_day3"):
            # Триал активен И его активировали N-1..N дней назад.
            # Классические welcome-day2/day3 коммуникации:
            #   day1 → [NOW-24h, NOW]                → «первый день»
            #   day2 → [NOW-48h, NOW-24h)            → «уже 2 дня с нами»
            #   day3 → [NOW-72h, NOW-48h)            → «завтра закончится»
            # Ограничение trial_expires_at > NOW отсеивает истекшие триалы.
            day = int(segment.split("_")[-1].replace("day", ""))
            rows = await conn.fetch(
                f"""SELECT u.telegram_id FROM users u
                    WHERE u.trial_used_at IS NOT NULL
                      AND u.trial_used_at <= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{day - 1} hours' * 24
                      AND u.trial_used_at >  (NOW() AT TIME ZONE 'UTC') - INTERVAL '{day} hours' * 24
                      AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                            > (NOW() AT TIME ZONE 'UTC')
                      AND NOT EXISTS (
                          SELECT 1 FROM subscriptions s
                          WHERE s.telegram_id = u.telegram_id
                            AND s.source = 'payment'
                            AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                      )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("paid_expires_in_1d", "paid_expires_in_3d",
                         "paid_expires_in_7d", "paid_expires_in_14d"):
            # Платная подписка сейчас активна, кончается в течение N суток.
            # Точка renewal-подсказки — юзер ещё внутри, есть время оформить.
            # source='payment' — исключаем trial/admin_grant/gift (у них другой
            # renewal-flow).
            days = int(segment.rsplit("_", 1)[-1].rstrip("d"))
            rows = await conn.fetch(
                f"""SELECT DISTINCT s.telegram_id FROM subscriptions s
                    WHERE s.source = 'payment'
                      AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                      AND s.expires_at <= (NOW() AT TIME ZONE 'UTC') + INTERVAL '{days} days'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "trial_expired_within_6m":
            # КУМУЛЯТИВНОЕ окно: юзер активировал триал, тот истёк В ЛЮБОЙ
            # момент последних 180 дней (не exact-day bucket, а всё окно),
            # и с тех пор так и не купил → сейчас нет активной подписки.
            #
            # Смысл: покрывает всех «отвалившихся после триала за полгода».
            # Обычные trial_expired_Nd таргетируют точечно (N-ый день),
            # а этот — «все, кто когда-либо за полгода не сконвертился».
            #
            # Условия:
            #   trial_used_at IS NOT NULL
            #   AND trial_expires_at ∈ [NOW-180d, NOW]  (истёк за полгода)
            #   AND нет ни одной s.source='payment' (никогда не покупал)
            #   AND нет ни одной активной подписки
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE u.trial_used_at IS NOT NULL
                     AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                           <= (NOW() AT TIME ZONE 'UTC')
                     AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                           >  (NOW() AT TIME ZONE 'UTC') - INTERVAL '180 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.telegram_id = u.telegram_id
                           AND s.source = 'payment'
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.telegram_id = u.telegram_id
                           AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("trial_expired_7d", "trial_expired_14d",
                         "trial_expired_30d", "trial_expired_60d",
                         "trial_expired_90d", "trial_expired_180d",
                         "trial_expired_365d"):
            # Триал истёк N дней назад — И пользователь никогда не покупал
            # (нет ни одной строки в subscriptions с source='payment').
            # Это чистая «холодная реактивация» — прошло много времени,
            # человек не сконвертился, шлём ему повторный оффер.
            # Бакеты 24-часовые, окно вокруг ровно N-дневной точки:
            #   trial_expired_7d  → (NOW-8d,  NOW-7d]
            #   trial_expired_14d → (NOW-15d, NOW-14d]
            #   trial_expired_30d → (NOW-31d, NOW-30d]
            #   trial_expired_90d → (NOW-91d, NOW-90d]
            # См. коммент про tz в trial_ends_in_1d.
            days = int(segment.split("_")[-1].rstrip("d"))
            rows = await conn.fetch(
                f"""SELECT u.telegram_id FROM users u
                    WHERE u.trial_used_at IS NOT NULL
                      AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                            <= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{days} days'
                      AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                            >  (NOW() AT TIME ZONE 'UTC') - INTERVAL '{days + 1} days'
                      AND NOT EXISTS (
                          SELECT 1 FROM subscriptions s
                          WHERE s.telegram_id = u.telegram_id
                            AND s.source = 'payment'
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM subscriptions s
                          WHERE s.telegram_id = u.telegram_id
                            AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                      )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("started_1d_cold", "started_3d_cold",
                         "started_14d_cold", "started_30d_cold"):
            # Холодные лиды — старт был не позднее N суток назад,
            # и до сих пор ноль активности (нет подписки, нет ключей,
            # нет триала). Cumulative-окно: включает всех, кто нажал
            # /start в диапазоне [NOW-N days, NOW]. Смысл — «свежие
            # молчуны» для прогрева. started_1d_cold = сегодняшние.
            days = int(segment.split("_")[1].rstrip("d"))
            rows = await conn.fetch(
                f"""SELECT u.telegram_id FROM users u
                    WHERE u.created_at >= NOW() - INTERVAL '{days} days'
                      AND u.trial_used_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM subscriptions s
                          WHERE s.telegram_id = u.telegram_id
                            AND (
                                s.expires_at > (NOW() AT TIME ZONE 'UTC')
                                OR s.remnawave_uuid IS NOT NULL
                                OR s.remnawave_premium_uuid IS NOT NULL
                            )
                      )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("paid_expired_7d", "paid_expired_14d",
                         "paid_expired_60d", "paid_expired_90d",
                         "paid_expired_180d", "paid_expired_365d",
                         "paid_expired_730d"):
            # Платная (source='payment') истекла ровно N суток назад
            # (24-час бакет [NOW-(N+1)d, NOW-Nd)) — сейчас нет активной
            # ПЛАТНОЙ. Классическая точка реактивации, аналог paid_expired_1d
            # для более далёких окон.
            # См. tz-коммент в trial_ends_in_1d.
            days = int(segment.split("_")[-1].rstrip("d"))
            rows = await conn.fetch(
                f"""SELECT u.telegram_id FROM users u
                    WHERE EXISTS (
                        SELECT 1 FROM subscriptions s
                        WHERE s.telegram_id = u.telegram_id
                          AND s.source = 'payment'
                          AND s.expires_at <= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{days} days'
                          AND s.expires_at >  (NOW() AT TIME ZONE 'UTC') - INTERVAL '{days + 1} days'
                    )
                      AND NOT EXISTS (
                        SELECT 1 FROM subscriptions s2
                        WHERE s2.telegram_id = u.telegram_id
                          AND s2.source = 'payment'
                          AND s2.expires_at > (NOW() AT TIME ZONE 'UTC')
                    )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "vip_active":
            # VIP-пользователи (users.is_vip=TRUE) — для эксклюзивных
            # приглашений/апселлов/фидбека.
            rows = await conn.fetch(
                """SELECT telegram_id FROM users
                   WHERE is_vip = TRUE"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "combo_active":
            # Активные подписки типа combo_basic / combo_plus — целевая
            # для апселла на большие GB-паки обхода / доп. устройств.
            rows = await conn.fetch(
                """SELECT DISTINCT s.telegram_id
                   FROM subscriptions s
                   WHERE s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     AND s.subscription_type IN ('combo_basic','combo_plus')"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "basic_active":
            # Активные Basic — целевая для апселла на Plus/Combo.
            rows = await conn.fetch(
                """SELECT DISTINCT s.telegram_id
                   FROM subscriptions s
                   WHERE s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     AND s.subscription_type = 'basic'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "plus_active":
            # Активные Plus — целевая для upsell на Combo или продление на 1 год.
            rows = await conn.fetch(
                """SELECT DISTINCT s.telegram_id
                   FROM subscriptions s
                   WHERE s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     AND s.subscription_type = 'plus'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "discount_active":
            # У пользователя действует персональная скидка
            # (user_discounts) — стоит напомнить использовать её.
            rows = await conn.fetch(
                """SELECT DISTINCT ud.telegram_id
                   FROM user_discounts ud
                   WHERE (ud.expires_at IS NULL
                          OR ud.expires_at > (NOW() AT TIME ZONE 'UTC'))"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "has_balance_50plus":
            # На балансе > 50₽. Напомнить использовать балансовый чекаут.
            rows = await conn.fetch(
                """SELECT telegram_id FROM users
                   WHERE COALESCE(balance_kopecks, 0) >= 5000"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "expires_in_3d":
            # Активная подписка (любого типа) закончится в ближайшие
            # 72 часа — точка «продли/переоформи». Мощная реактивационная
            # аудитория, пока люди ещё внутри.
            rows = await conn.fetch(
                """SELECT DISTINCT s.telegram_id FROM subscriptions s
                   WHERE s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     AND s.expires_at <= (NOW() AT TIME ZONE 'UTC') + INTERVAL '3 days'
                     AND s.source = 'payment'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("expired_1d", "expired_2d", "expired_3d"):
            # User's MOST RECENT subscription expired exactly N full days
            # ago (24-hour bucket). MAX(expires_at) делает выборку
            # устойчивой к history-rows (renewal flow создаёт несколько
            # subscription_row). Также неявно исключает юзеров с активной
            # подпиской — если их max в прошлом, активной нет.
            #
            # ВАЖНО про tz: см. коммент в trial_ends_in_1d. Используем
            # `(NOW() AT TIME ZONE 'UTC')` чтобы сравнение TIMESTAMP-без-TZ
            # работало стабильно в любой session-TZ.
            days = int(segment.split("_")[1].rstrip("d"))
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE (
                       SELECT MAX(s.expires_at) FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id
                   ) >= (NOW() AT TIME ZONE 'UTC') - $1 * INTERVAL '1 day'
                     AND (
                       SELECT MAX(s.expires_at) FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id
                   ) <  (NOW() AT TIME ZONE 'UTC') - $2 * INTERVAL '1 day'""",
                days + 1, days,
            )
            return [row["telegram_id"] for row in rows]
        else:
            logging.warning(f"Unknown segment: {segment}, returning empty list")
            return []
