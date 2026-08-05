"""Разбивка админской части базы ничего не потеряла.

database/admin.py был на 2318 строк и держал шесть вещей, которые правят по
разным поводам: двухфазную выдачу подписки, выгрузки и карточку
пользователя, подбор аудитории рассылок, SQL для графиков дашборда,
подарочные подписки и одноразовые инструменты починки данных. Разрезан на
admin_access / admin_users / admin_audience / admin_reports / admin_recovery
/ gift_subscriptions, а сам admin.py остался фасадом.

ГЛАВНЫЙ РИСК ТАКОЙ ОПЕРАЦИИ

    Потерянный реэкспорт. К этим функциям годами обращаются двумя путями —
    `database.X` и `database.admin.X`. Пропавшее имя не ломает импорт: оно
    падает AttributeError в момент вызова, то есть на живом пользователе или
    в ночном воркере.

    Второй риск — кольцо импортов: модуль-реализация, потянувший фасад,
    роняет старт бота целиком.
"""
import ast
from pathlib import Path

DB = Path("database")

# Модули, на которые разрезан admin.py.
SPLIT_MODULES = [
    "admin_access.py",
    "admin_users.py",
    "admin_audience.py",
    "admin_reports.py",
    "admin_recovery.py",
    "gift_subscriptions.py",
]

# Полный список публичных имён из файла ДО разрезания. Пропажа любого = вызов,
# падающий AttributeError уже в проде.
PUBLIC_NAMES = {
    # доступ
    "admin_grant_access_atomic",
    "admin_grant_access_minutes_atomic",
    "admin_revoke_access_atomic",
    "admin_delete_user_complete",
    # пользователь глазами админа
    "get_all_users_for_export",
    "get_active_subscriptions_for_export",
    "get_subscription_history",
    "get_user_extended_stats",
    "get_all_users_telegram_ids",
    # аудитория рассылок
    "get_eligible_no_subscription_broadcast_users",
    "check_user_still_eligible_for_no_sub_broadcast",
    "get_active_trial_telegram_ids",
    # отчёты
    "get_daily_timeseries",
    "get_hourly_timeseries",
    "get_ltv",
    "get_referral_analytics",
    "get_daily_summary",
    "get_monthly_summary",
    # починка данных
    "get_bypass_overwrite_victims",
    "fix_bypass_overwrite_victim",
    "get_premium_recovery_candidates",
    "get_user_paid_subscription_history",
    "get_paid_subscription_history_bulk",
    "get_activated_gifts_bulk",
    "get_max_subscription_end_bulk",
    "get_paid_payments_via_purchases_bulk",
    "get_active_premium_subscribers",
    "get_subscriptions_with_far_future_expires",
    "update_subscription_expires_at_bulk",
    # подарки
    "generate_gift_code",
    "create_gift_subscription",
    "get_gift_subscription",
    "activate_gift_subscription",
    "get_user_gifts",
    # осталось в фасаде
    "expire_old_pending_purchases",
}


def _top_level_defs(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_nothing_was_lost_in_the_move():
    """Каждое имя нашлось ровно в одном модуле-реализации (или в фасаде)."""
    homes = {}
    for name in SPLIT_MODULES + ["admin.py"]:
        for fn in _top_level_defs(DB / name):
            homes.setdefault(fn, []).append(name)

    missing = sorted(n for n in PUBLIC_NAMES if n not in homes)
    assert not missing, f"функции пропали при разрезании: {missing}"

    duplicated = sorted(n for n in PUBLIC_NAMES if len(homes[n]) > 1)
    assert not duplicated, f"копии одной функции в разных модулях: {duplicated}"


def test_both_import_paths_still_work():
    """`database.X` и `database.admin.X` — оба пути живые и ведут к одному
    объекту. Второй остался ради кода, который годами звал ферму через
    database.admin."""
    import database
    import database.admin as admin

    missing_facade = sorted(n for n in PUBLIC_NAMES if not hasattr(admin, n))
    assert not missing_facade, f"нет в database.admin: {missing_facade}"

    missing_pkg = sorted(n for n in PUBLIC_NAMES if not hasattr(database, n))
    assert not missing_pkg, f"нет в пакете database: {missing_pkg}"

    diverged = sorted(
        n for n in PUBLIC_NAMES
        if getattr(database, n) is not getattr(admin, n)
    )
    assert not diverged, f"database.X и database.admin.X разошлись: {diverged}"


def test_facade_holds_almost_nothing():
    """admin.py — точка входа, а не место для логики.

    Единственное исключение — expire_old_pending_purchases: её настоящее
    место в database/pending_purchases.py, переезд туда отдельным шагом.
    """
    left = _top_level_defs(DB / "admin.py")
    assert left <= {"expire_old_pending_purchases"}, (
        f"в фасаде осталась логика: {sorted(left)}"
    )


def test_implementation_modules_do_not_import_the_facade():
    """Кольцо database.admin → модуль → database.admin роняет старт бота."""
    for name in SPLIT_MODULES:
        src = (DB / name).read_text(encoding="utf-8")
        assert "database.admin import" not in src, f"{name} тянет фасад"
        assert "import database.admin" not in src, f"{name} тянет фасад"


def test_access_module_keeps_the_two_phase_order():
    """Внешний вызов к панели обязан быть ВНЕ транзакции.

    Обратный порядок держит транзакцию открытой на время сети и при откате
    оставляет живую сущность в панели — человека с работающим VPN, которого
    нет в базе.
    """
    tree = ast.parse((DB / "admin_access.py").read_text(encoding="utf-8"))
    grants = [
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name.startswith("admin_grant_access")
    ]
    assert len(grants) == 2, "обе выдачи (дни и минуты) должны быть здесь"

    for fn in grants:
        provisions = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "provision_subscription"
        ]
        assert provisions, f"{fn.name}: провизия в панели исчезла"

        inside_tx = []
        for node in ast.walk(fn):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            if not any("transaction()" in ast.unparse(i.context_expr) for i in node.items):
                continue
            inside_tx += [n for n in ast.walk(node) if n in provisions]
        assert not inside_tx, (
            f"{fn.name}: вызов к панели затащен внутрь транзакции — "
            f"откат оставит сироту в панели"
        )


def test_user_delete_still_keeps_financial_history():
    """Страховка на случай, если переезд «потеряет» комментарий-предупреждение
    и кто-то вернёт DELETE FROM payments."""
    src = (DB / "admin_access.py").read_text(encoding="utf-8")
    for table in ("payments", "pending_purchases", "balance_transactions"):
        assert f"DELETE FROM {table}" not in src, (
            f"удаление пользователя снова стирает {table} — выручка "
            f"перепишется задним числом"
        )
