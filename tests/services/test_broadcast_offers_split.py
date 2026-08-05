"""Разрезание экранов спецпредложений ничего не потеряло.

app/handlers/callbacks/broadcast_offers.py был на 1025 строк и держал пять
независимых акций: две скидки, пишущиеся в базу, и три подарка, живущих
одноразовой подменой цены в FSM. Разрезан на пакет promo_discounts /
gift_1m / gift_3m / gift_1y40 / gift_reveal со сборкой в __init__.py.

ГЛАВНЫЕ РИСКИ ТАКОЙ ОПЕРАЦИИ

    1. Забытый include_router. Кнопка живёт в уже разосланном сообщении,
       которое остаётся в чате навсегда. Потерянный роутер = человек жмёт
       и не получает ничего, а в логах пусто.

    2. Раздел не админский. Эти экраны — пользовательские; однажды они
       уже лежали в админском разделе, попали под middleware «только
       админ» и молча перестали работать у всех. Проверка на админа не
       должна появиться ни в одном модуле пакета.

    3. Подарки не должны начать писать скидку в user_discounts. Сейчас
       они подменяют цену в FSM: закрыл экран — предложение сгорело.
       Запись в базу сделала бы скидку переживающей экран и утекающей на
       другие периоды.
"""
import ast
from pathlib import Path

import pytest

PKG = Path("app/handlers/callbacks/broadcast_offers")

# Список обработчиков взят из файла ДО разрезания.
HANDLERS = {
    "callback_broadcast_promo_buy",
    "callback_broadcast_promo_traffic",
    "callback_broadcast_gift_1m",
    "callback_broadcast_gift_1m_buy",
    "callback_broadcast_gift_3m",
    "callback_broadcast_gift_3m_menu",
    "callback_broadcast_gift_3m_info",
    "callback_broadcast_gift_3m_buy",
    "callback_broadcast_gift_1y_40",
    "callback_broadcast_gift_1y_40_menu",
    "callback_broadcast_gift_1y_40_info",
    "callback_broadcast_gift_1y_40_tariff",
    "callback_broadcast_gift_1y_40_buy",
    "callback_broadcast_gift_reveal",
    "callback_broadcast_back_to_tariffs",
}

# Все callback_data, на которые предложения откликались до разрезания.
CALLBACK_DATA = {
    "broadcast_promo_buy:",
    "broadcast_promo_traffic:",
    "broadcast_gift_1m",
    "bcg1m:buy:",
    "broadcast_gift_3m",
    "bcg3m:menu",
    "bcg3m:info",
    "bcg3m:buy:",
    "broadcast_gift_1y_40",
    "bcg1y40:menu",
    "bcg1y40:info",
    "bcg1y40:tariff:",
    "bcg1y40:buy:",
    "broadcast_gift_reveal:",
    "broadcast_back_to_tariffs",
}

MODULES = [
    "promo_discounts.py",
    "gift_1m.py",
    "gift_3m.py",
    "gift_1y40.py",
    "gift_reveal.py",
]

# Подарки живут подменой цены в FSM и не пишут ничего в базу.
FSM_ONLY_OFFERS = ["gift_1m.py", "gift_3m.py", "gift_1y40.py"]


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
    assert not Path("app/handlers/callbacks/broadcast_offers.py").exists()


def test_no_offer_handler_was_lost():
    missing = HANDLERS - set(_decorated_functions())
    assert not missing, f"экраны предложений пропали: {sorted(missing)}"


def test_every_offer_handler_is_registered():
    """Кнопка из уже разосланного сообщения обязана находить адресата."""
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


def test_package_reexports_survived():
    import app.handlers.callbacks.broadcast_offers as offers

    for name in HANDLERS | {"broadcast_offers_router"}:
        assert hasattr(offers, name), f"потерян реэкспорт: {name}"


@pytest.mark.parametrize("module", FSM_ONLY_OFFERS)
def test_gift_offers_do_not_write_discounts_to_the_database(module):
    """Подарок обязан оставаться одноразовым.

    Запись в user_discounts переживает экран и применяется ко всем
    периодам — ровно то, чего эти акции избегают by design.
    """
    src = (PKG / module).read_text(encoding="utf-8")
    assert "create_user_discount" not in src, (
        f"{module} начал писать скидку в базу — она перестанет сгорать"
    )


@pytest.mark.parametrize("module", MODULES)
def test_offer_modules_are_not_admin_only(module):
    """Экраны пользовательские: проверке на админа здесь не место."""
    src = (PKG / module).read_text(encoding="utf-8")
    assert "app.handlers.admin" not in src
    assert "is_admin(" not in src


def test_offer_modules_do_not_import_each_other():
    """Акции независимы: связь между ними — это общий отказ на релизе."""
    names = {m[:-3] for m in MODULES}
    for module in MODULES:
        tree = ast.parse((PKG / module).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "app.handlers.callbacks.broadcast_offers"
            ):
                imported.add((node.module or "").split(".")[-1])
                imported.update(a.name for a in node.names)
        clash = (imported & names) - {module[:-3]}
        assert not clash, f"{module} тянет соседнюю акцию: {sorted(clash)}"
