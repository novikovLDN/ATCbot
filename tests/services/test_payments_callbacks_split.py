"""Разрезание экранов покупки ничего не потеряло.

app/handlers/payments/callbacks.py был на 976 строк и держал четыре разные
ветки: покупку с нуля, смену тарифа действующим подписчиком, промокод и
бизнес-сценарий с выбором страны. Разрезан на пакет subscription_menu /
purchase_flow / promo / business / tariff_meta.

ГЛАВНЫЕ РИСКИ ТАКОЙ ОПЕРАЦИИ

    1. Забытый include_router. Ошибок нет — кнопка молчит. Здесь это
       самая дорогая тишина в боте: молчит кнопка «Купить».

    2. Потерянный фильтр состояния. Кнопка из старого сообщения живёт в
       чате вечно; без StateFilter человек прыгает в середину покупки со
       старой ценой.

    3. Потерянный реэкспорт `_period_badge` — его берут подарки и
       навигация. Падает не при импорте, а в момент отрисовки экрана.
"""
import ast
from pathlib import Path

import pytest

PKG = Path("app/handlers/payments/callbacks")

HANDLERS = {
    "callback_buy_vpn",
    "callback_switch_tariff_menu",
    "callback_switch_tariff",
    "callback_tariff_type",
    "callback_tariff_period",
    "callback_downgrade_confirm_basic",
    "callback_enter_promo",
    "callback_promo_back",
    "callback_corporate_access_request",
    "callback_biz_country_selected",
}

CALLBACK_DATA = {
    "menu_buy_vpn",
    "switch_tariff_menu",
    "switch_tariff:",
    "tariff:",
    "period:",
    "downgrade_confirm_basic",
    "enter_promo",
    "promo_back",
    "corporate_access_request",
    "biz_country:",
}

MODULES = [
    "tariff_meta.py",
    "subscription_menu.py",
    "purchase_flow.py",
    "promo.py",
    "business.py",
]


def _decorated_functions():
    found = {}
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
                found[node.name] = [ast.unparse(d) for d in node.decorator_list]
    return found


@pytest.mark.parametrize("module", MODULES)
def test_module_exists_with_a_docstring(module):
    path = PKG / module
    assert path.exists(), f"{module} потерян"
    assert ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))), (
        f"{module} без докстринга: непонятно, что здесь и почему выделено"
    )


def test_old_single_file_module_is_gone():
    assert not Path("app/handlers/payments/callbacks.py").exists()


def test_no_purchase_handler_was_lost():
    missing = HANDLERS - set(_decorated_functions())
    assert not missing, f"экраны покупки пропали: {sorted(missing)}"


def test_every_purchase_handler_is_registered():
    from app.handlers import router

    registered = set()

    def walk(r):
        for h in list(r.callback_query.handlers) + list(r.message.handlers):
            registered.add(getattr(h.callback, "__name__", ""))
        for sub in r.sub_routers:
            walk(sub)

    walk(router)
    missing = HANDLERS - registered
    assert not missing, f"объявлены, но не подключены: {sorted(missing)}"


def _is_data_attr(node) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "data"


def test_callback_data_set_did_not_change():
    values = set()
    for decorators in _decorated_functions().values():
        for text in decorators:
            if "callback_query(" not in text:
                continue
            for node in ast.walk(ast.parse(text, mode="eval")):
                if isinstance(node, ast.Compare) and _is_data_attr(node.left):
                    for cmp_node in node.comparators:
                        if isinstance(cmp_node, ast.Constant) and isinstance(cmp_node.value, str):
                            values.add(cmp_node.value)
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "startswith"
                    and _is_data_attr(node.func.value)
                ):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            values.add(arg.value)
    assert values == CALLBACK_DATA, (
        f"лишние: {sorted(values - CALLBACK_DATA)}, "
        f"пропавшие: {sorted(CALLBACK_DATA - values)}"
    )


@pytest.mark.parametrize("handler,state_name", [
    ("callback_switch_tariff_menu", "PurchaseState.choose_tariff"),
    ("callback_switch_tariff", "PurchaseState.choose_tariff"),
    ("callback_tariff_type", "PurchaseState.choose_tariff"),
    ("callback_tariff_period", "PurchaseState.choose_period"),
    ("callback_downgrade_confirm_basic", "PurchaseState.choose_period"),
    ("callback_biz_country_selected", "PurchaseState.choose_country"),
])
def test_state_filters_survived(handler, state_name):
    """Кнопка из старого сообщения без фильтра состояния — прыжок в
    середину покупки со старой ценой."""
    text = " ".join(_decorated_functions()[handler])
    assert "StateFilter" in text, f"{handler} потерял StateFilter"
    assert state_name in text, f"{handler} потерял состояние {state_name}"


def test_package_reexports_survived():
    import app.handlers.payments.callbacks as callbacks

    for name in HANDLERS | {
        "payments_callbacks_router",
        "_period_badge",
        "_TARIFF_META",
        "_current_tariff_key",
    }:
        assert hasattr(callbacks, name), f"потерян реэкспорт: {name}"


def test_period_badge_is_still_importable_by_its_consumers():
    """Подарки и навигация берут значок периода по пути пакета."""
    for path in (
        Path("app/handlers/callbacks/gift/wizard.py"),
        Path("app/handlers/callbacks/navigation.py"),
    ):
        src = path.read_text(encoding="utf-8")
        assert "app.handlers.payments.callbacks import _period_badge" in src, (
            f"{path} больше не берёт _period_badge из пакета"
        )

    from app.handlers.payments.callbacks import _period_badge
    from app.handlers.payments.callbacks.tariff_meta import (
        _period_badge as original,
    )

    assert _period_badge is original, "фасад отдаёт копию, а не ту же функцию"


def test_purchase_flow_creates_no_pending_purchase():
    """Покупка создаётся на экране способа оплаты, а не здесь.

    Создание раньше даст висящую неоплаченную запись на каждый клик по
    периоду — и испорченную статистику конверсии.
    """
    src = (PKG / "purchase_flow.py").read_text(encoding="utf-8")
    assert "create_pending_purchase" not in src
    assert "create_subscription_purchase" not in src


def test_tariff_meta_stays_at_the_bottom():
    """Справочник тарифов не должен знать про экраны — иначе кольцо."""
    tree = ast.parse((PKG / "tariff_meta.py").read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not imports, "tariff_meta.py обзавёлся зависимостями"
