"""Разбивка database/core.py и database/analytics.py ничего не потеряла.

ЧТО БЫЛО. Два файла по ~1260 строк. В core.py фундамент (пул, флаг
готовности, init_db) лежал вперемешку с 640 строками легаси-DDL и мелкими
хелперами времени. В analytics.py — деньги, ленты покупок и счётчики
пользователей, то есть три вещи с разной ценой ошибки.

ЧТО СТАЛО. core.py → db_helpers + legacy_schema, analytics.py → фасад плюс
analytics_revenue / analytics_payments / analytics_stats.

ГЛАВНЫЕ РИСКИ ТАКОЙ ОПЕРАЦИИ

    1. Кольцевой импорт из core. Его тянут тридцать с лишним модулей: если
       модуль-реализация потянет core обратно, падает `import database`,
       то есть старт бота целиком, до первой строчки логики.

    2. Потерянный реэкспорт. К этим именам обращаются двумя путями —
       `database.X` и `database.<модуль>.X`. Пропавшее имя не ломает
       импорт: оно падает AttributeError в момент вызова, на живом
       пользователе или в ночном воркере.

    3. Расползание DB_READY. Флаг изменяемый и обязан существовать в одном
       экземпляре. Копия в соседнем модуле навсегда останется False, и бот
       тихо уйдёт в деградированный режим, ничем это не показав.
"""
import ast
from pathlib import Path

DB = Path("database")

# Модули, на которые разрезан analytics.py.
ANALYTICS_MODULES = [
    "analytics_revenue.py",
    "analytics_payments.py",
    "analytics_stats.py",
]

# Модули, выделенные из core.py. Сам core остался с пулом и init_db.
CORE_MODULES = [
    "db_helpers.py",
    "legacy_schema.py",
]

# Публичные имена analytics.py ДО разрезания.
ANALYTICS_NAMES = {
    # деньги
    "REVENUE_EXTERNAL_ONLY_SQL",
    "get_revenue_for_period",
    "get_payments_by_provider",
    "get_payments_breakdown",
    "get_purchase_breakdown",
    "get_traffic_stats",
    "get_total_revenue",
    "get_paying_users_count",
    "get_user_ltv",
    "get_average_ltv",
    "get_arpu",
    # ленты покупок и ошибки оплат
    "get_recent_payments_feed",
    "get_user_purchases",
    "log_payment_error",
    "get_recent_payment_errors",
    "get_payment_errors_summary",
    # счётчики
    "get_business_metrics",
    "get_last_audit_logs",
    "get_analytics_by_period",
    "get_active_paid_subscriptions_count",
    "get_extended_bot_stats",
}

# Публичные имена core.py ДО разрезания. Приватные (с подчёркиванием) сюда
# входят намеренно: их по именам импортируют database/__init__.py и добрая
# половина модулей пакета, так что «приватность» тут только на словах.
CORE_NAMES = {
    "get_pool",
    "close_pool",
    "init_db",
    "ensure_db_ready",
    "check_critical_tables",
    "safe_int",
    "safe_float",
    "safe_get",
    "mark_payment_notification_sent",
    "is_payment_notification_sent",
    "_to_db_utc",
    "_from_db_utc",
    "_ensure_utc",
    "_generate_subscription_uuid",
    "_normalize_subscription_row",
    "_get_pool_config",
    "_get_pool_safe",
    "_init_promo_codes",
}


def _top_level_defs(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _top_level_assigns(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def test_analytics_nothing_was_lost_in_the_move():
    """Каждое имя нашлось ровно в одном модуле-реализации."""
    homes = {}
    for name in ANALYTICS_MODULES:
        for fn in _top_level_defs(DB / name) | _top_level_assigns(DB / name):
            homes.setdefault(fn, []).append(name)

    missing = sorted(n for n in ANALYTICS_NAMES if n not in homes)
    assert not missing, f"функции пропали при разрезании: {missing}"

    duplicated = sorted(n for n in ANALYTICS_NAMES if len(homes[n]) > 1)
    assert not duplicated, f"копии одной функции в разных модулях: {duplicated}"


def test_core_nothing_was_lost_in_the_move():
    homes = {}
    for name in CORE_MODULES + ["core.py"]:
        for fn in _top_level_defs(DB / name):
            homes.setdefault(fn, []).append(name)

    missing = sorted(n for n in CORE_NAMES if n not in homes)
    assert not missing, f"функции пропали при разрезании: {missing}"

    duplicated = sorted(n for n in CORE_NAMES if len(homes[n]) > 1)
    assert not duplicated, f"копии одной функции в разных модулях: {duplicated}"


def test_both_import_paths_still_work():
    """`database.X` и `database.<фасад>.X` — оба пути живые и ведут к одному
    объекту."""
    import database
    import database.analytics as analytics
    import database.core as core

    for mod, names in ((analytics, ANALYTICS_NAMES), (core, CORE_NAMES)):
        missing = sorted(n for n in names if not hasattr(mod, n))
        assert not missing, f"нет в {mod.__name__}: {missing}"

    # REVENUE_EXTERNAL_ONLY_SQL в пакет database никогда не экспортировался —
    # его берут из модуля. Остальное обязано быть видно и как database.X.
    pkg_names = (ANALYTICS_NAMES - {"REVENUE_EXTERNAL_ONLY_SQL"}) | CORE_NAMES
    missing_pkg = sorted(n for n in pkg_names if not hasattr(database, n))
    assert not missing_pkg, f"нет в пакете database: {missing_pkg}"

    diverged = sorted(
        n for n in ANALYTICS_NAMES - {"REVENUE_EXTERNAL_ONLY_SQL"}
        if getattr(database, n) is not getattr(analytics, n)
    )
    assert not diverged, f"database.X и database.analytics.X разошлись: {diverged}"


def test_analytics_facade_holds_nothing():
    """database/analytics.py — точка входа, а не место для логики."""
    left = _top_level_defs(DB / "analytics.py")
    assert not left, f"в фасаде осталась логика: {sorted(left)}"


def test_nobody_imports_the_analytics_facade_from_inside():
    """Кольцо database.analytics → модуль → database.analytics."""
    for name in ANALYTICS_MODULES:
        src = (DB / name).read_text(encoding="utf-8")
        assert "database.analytics import" not in src, f"{name} тянет фасад"
        assert "import database.analytics" not in src, f"{name} тянет фасад"


def test_modules_split_out_of_core_do_not_import_core():
    """Самое опасное кольцо в проекте.

    core импортирует db_helpers и legacy_schema на верхнем уровне. Обратная
    стрелка замкнёт круг, и `import database` упадёт при старте бота —
    не в проде через неделю, а сразу и целиком.
    """
    for name in CORE_MODULES:
        src = (DB / name).read_text(encoding="utf-8")
        for line in src.split("\n"):
            code = line.split("#")[0]
            assert "from database.core import" not in code, f"{name} тянет core"
            assert "import database.core" not in code, f"{name} тянет core"


def test_db_ready_lives_in_exactly_one_module():
    """Флаг готовности — изменяемое состояние в одном экземпляре.

    Копия в соседнем модуле навсегда останется False: писать будут в одну,
    читать из другой. Бот при этом не падает — он молча отказывает
    пользователям, считая базу недоступной.
    """
    owners = [p.name for p in sorted(DB.glob("*.py"))
              if "DB_READY" in _top_level_assigns(p)]
    assert owners == ["core.py"], f"DB_READY объявлен не только в core: {owners}"

    # Присваивать флаг тоже можно только там, где он объявлен: `global
    # DB_READY` в чужом модуле создаст ровно такую копию.
    for p in sorted(DB.glob("*.py")):
        if p.name == "core.py":
            continue
        src = p.read_text(encoding="utf-8")
        assert "global DB_READY" not in src, f"{p.name} присваивает чужой флаг"


def test_pool_state_stays_with_the_functions_that_use_it():
    """_pool, get_pool, close_pool и init_db — одно связное состояние.

    init_db создаёт пул и пересоздаёт его после миграций, close_pool
    обнуляет, get_pool читает. Разъедешь по файлам — получишь два разных
    `_pool`: один создан, другой раздаётся.
    """
    core_defs = _top_level_defs(DB / "core.py")
    assert {"get_pool", "close_pool", "init_db"} <= core_defs
    assert "_pool" in _top_level_assigns(DB / "core.py")


def test_legacy_ddl_is_reachable_only_through_core():
    """DDL вызывается из init_db под флагом — и больше ниоткуда.

    Прямой вызов в обход init_db означал бы DDL на соединении без
    lock_timeout: на боевой базе такой ALTER может встать намертво и
    утащить за собой все читающие запросы.
    """
    callers = [
        p for p in Path(".").rglob("*.py")
        if ".venv" not in p.parts
        and "apply_legacy_schema_bootstrap" in p.read_text(encoding="utf-8")
    ]
    names = sorted(p.as_posix() for p in callers)
    assert names == [
        "database/core.py",
        "database/legacy_schema.py",
        "tests/services/test_database_core_analytics_split.py",
    ], f"легаси-DDL зовут откуда-то ещё: {names}"
