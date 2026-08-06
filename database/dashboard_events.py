"""Журнал событий для экрана «События» (/events).

ЧТО ЭТО И ЧЕМ ОТЛИЧАЕТСЯ ОТ ЛЕНТЫ НА СВОДКЕ

    database.dashboard_summary.get_summary_events отвечает на вопрос
    «что происходит прямо сейчас»: двадцать последних строк из трёх
    источников (оплаты, регистрации, действия админов), без фильтров и
    без листания.

    Здесь — сам журнал: только audit_log, зато с фильтрами по типу
    события, по человеку и по времени, со счётчиками и с листанием.
    Экран отвечает на «что произошло» и «кто это сделал» (research §4.7,
    §4.8: модель GitHub Audit Log и Stripe Activity Logs).

ПРАВИЛО «ЧТО СОБЫТИЕ, А ЧТО ФОН» — ОДНО НА ОБА ЭКРАНА

    NOISE_SQL ниже — единственное место, где оно записано. Ленту сводки
    оно фильтрует тоже: dashboard_summary импортирует эту же константу.
    Вторая копия разошлась бы с первой молча, и два экрана начали бы
    показывать разные наборы событий на одних и тех же данных.

    Что считается фоном: admin_view_* и *_viewed пишутся при каждом
    открытии экрана в боте — журнал заполнялся бы хождением владельца по
    меню. reminder_sent — сотни строк в сутки, это не событие, а работа
    воркера.

КАТЕГОРИЯ СЧИТАЕТСЯ В SQL, А НЕ В PYTHON

    _CATEGORY_CASE — тоже единственное место. По этой же CASE и
    фильтруют, и группируют счётчики: если раскладывать действия по
    категориям на стороне Python, фильтр и счётчики немедленно разъедутся
    с тем, что видно в списке.

ИСКЛЮЧЕНИЯ НЕ ГАСЯТСЯ

    Соседняя get_last_audit_logs при отсутствующей таблице возвращает
    пустой список. Здесь так нельзя: пустая лента читается как «ничего не
    происходило», и это самая вредная неправда именно на журнале. Отказ
    обязан долететь до маршрута и стать 500 — экран напишет «журнал не
    ответил».

СЕКРЕТЫ

    В details попадает текст исключения, а в него — URL метода Telegram
    вместе с токеном бота. Наружу details уходит только через
    app.utils.security.scrub_secrets. Уберёте вызов — токен уедет в
    браузер администратора и осядет в логах прокси по дороге.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.utils.security import scrub_secrets
from database.core import get_pool

logger = logging.getLogger(__name__)


# Фон, а не события. Backslash экранирует подчёркивание в LIKE: без него
# `_` совпадёт с любым одним символом и под правило попадёт лишнее.
#
# Имя колонки не квалифицировано алиасом сознательно — константа
# подставляется в запросы с разными алиасами, а таблица в них одна, и
# неоднозначности не возникает.
NOISE_SQL = (
    "action NOT LIKE 'admin\\_view%' "
    "AND action NOT LIKE '%\\_viewed' "
    "AND action <> 'reminder_sent'"
)

# Категория события. Порядок веток значим: побеждает первая подошедшая.
# vpn_add_user попадает в «доступ» раньше, чем в «пользователи», а
# payment_subscription_activation_failed — в «деньги», потому что с
# subscription_ он не начинается.
_CATEGORY_CASE = """
        CASE
            WHEN action LIKE 'broadcast\\_%' THEN 'broadcast'
            WHEN action LIKE 'vpn\\_%'
              OR action LIKE 'subscription\\_%'
              OR action LIKE 'admin\\_grant%'
              OR action LIKE 'admin\\_revoke%'
              OR action LIKE 'admin\\_reissue%'
              OR action IN ('admin_remnawave_mass_provision',
                            'admin_switch_tariff') THEN 'access'
            WHEN action LIKE '%payment%'
              OR action LIKE '%purchase%'
              OR action LIKE '%discount%'
              OR action LIKE '%bonus%'
              OR action LIKE '%promo%'
              OR action LIKE '%referral%'
              OR action LIKE '%balance%'
              OR action LIKE '%withdrawal%'
              OR action LIKE 'vip\\_%' THEN 'money'
            WHEN action LIKE '%user%' THEN 'users'
            ELSE 'other'
        END
"""

CATEGORIES: Tuple[str, ...] = ("access", "money", "broadcast", "users", "other")

# Общая выборка для списка и для счётчиков. Считай их разными запросами —
# в списке было бы одно число, на фильтре другое.
#
# Окно по времени считается в базе (NOW() - интервал), а не приходит
# готовой меткой от клиента: audit_log.created_at после миграции 025
# timestamptz, и naive-метка из Python сравнивалась бы с ним через
# часовой пояс сессии — то есть по-разному на разных стендах.
_FILTERED_CTE = f"""
    WITH filtered AS (
        SELECT id,
               action,
               telegram_id,
               target_user,
               details,
               created_at,
               source,
               result,
               {_CATEGORY_CASE} AS category
        FROM audit_log
        WHERE {NOISE_SQL}
          AND ($1::int IS NULL
               OR created_at >= NOW() - make_interval(hours => $1::int))
          AND ($2::bigint IS NULL
               OR telegram_id = $2
               OR target_user = $2)
          AND ($3::text IS NULL
               OR action ILIKE '%' || $3 || '%'
               OR details ILIKE '%' || $3 || '%')
    )
"""


def _clean_categories(categories: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Незнакомая категория выбрасывается, но не вместе с фильтром.

    Пустой список после чистки означает «выбрали только несуществующее» —
    возвращаем его как есть, и человек увидит пустой результат фильтра, а
    не всю ленту. Тихо показать всё было бы хуже: фильтр выглядел бы
    применённым.
    """
    if categories is None:
        return None
    return [c for c in categories if c in CATEGORIES]


async def get_audit_events(
    limit: int = 50,
    offset: int = 0,
    hours: Optional[int] = None,
    who: Optional[int] = None,
    query: Optional[str] = None,
    categories: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Страница журнала, новое сверху.

    who ищет человека и как автора действия, и как пострадавшего: на
    экране это один вопрос «что было с этим человеком», а не два.

    Имена подтягиваются джойном ПОСЛЕ среза страницы: джойн до LIMIT
    заставил бы базу тащить users на всю выборку ради полусотни строк.
    """
    cats = _clean_categories(categories)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _FILTERED_CTE
            + """
            SELECT f.*,
                   ua.username AS actor_username,
                   ut.username AS target_username
            FROM (
                SELECT * FROM filtered
                WHERE ($4::text[] IS NULL OR category = ANY($4))
                ORDER BY created_at DESC, id DESC
                LIMIT $5 OFFSET $6
            ) f
            LEFT JOIN users ua ON ua.telegram_id = f.telegram_id
            LEFT JOIN users ut ON ut.telegram_id = f.target_user
            ORDER BY f.created_at DESC, f.id DESC
            """,
            hours or None,
            who,
            query or None,
            cats,
            limit,
            offset,
        )
    return [
        {
            "id": int(r["id"]),
            "at": r["created_at"].isoformat() if r["created_at"] else None,
            "action": r["action"],
            "category": r["category"],
            "actor_id": int(r["telegram_id"]) if r["telegram_id"] is not None else None,
            "actor_username": r["actor_username"] or None,
            "target_id": int(r["target_user"]) if r["target_user"] is not None else None,
            "target_username": r["target_username"] or None,
            "source": r["source"],
            # 'success' | 'error' | None. Колонка заполняется только у
            # событий жизненного цикла VPN; пусто — это «не сообщалось», а
            # не «успешно».
            "result": r["result"],
            "details": scrub_secrets(r["details"]),
        }
        for r in rows
    ]


async def get_audit_category_counts(
    hours: Optional[int] = None,
    who: Optional[int] = None,
    query: Optional[str] = None,
) -> Dict[str, int]:
    """Сколько записей в каждой категории при тех же прочих фильтрах.

    Счётчики считаются БЕЗ фильтра по категории — иначе на выбранной
    категории все остальные показали бы ноль, и человек решил бы, что
    других событий нет вовсе.

    Категории без записей возвращаются нулями явно: отсутствие ключа
    фронту пришлось бы трактовать самому, а «ноль» и «не считали» —
    разные вещи.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _FILTERED_CTE
            + """
            SELECT category, COUNT(*)::int AS n
            FROM filtered
            GROUP BY category
            """,
            hours or None,
            who,
            query or None,
        )
    counts = {c: 0 for c in CATEGORIES}
    for r in rows:
        counts[r["category"]] = int(r["n"])
    return counts
