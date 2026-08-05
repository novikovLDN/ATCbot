"""Разрезание экранов оплаты по провайдерам ничего не потеряло.

app/handlers/callbacks/pay_external.py был на 1031 строку и держал в одном
файле пять провайдеров: карту и Stars в Telegram, три метода Platega,
CryptoBot и Lava. Разрезан на пакет telegram_invoice / platega / cryptobot /
lava со сборкой роутера в __init__.py.

ГЛАВНЫЙ РИСК ТАКОЙ ОПЕРАЦИИ

    Забытый include_router. Обработчик остаётся объявленным, ошибок в
    логах нет — кнопка оплаты просто молчит, и узнаете вы об этом от
    человека, который не смог заплатить. Поэтому проверяем не только что
    функция объявлена, но и что диспетчер её реально видит.

    Второй риск — потерянная callback_data: кнопка в старом сообщении
    остаётся в чате навсегда, и её адрес обязан находить адресата.
"""
import ast
from pathlib import Path

import pytest

PKG = Path("app/handlers/callbacks/pay_external")

# Список взят из файла ДО разрезания.
HANDLERS = {
    "callback_pay_card",
    "callback_pay_stars",
    "callback_pay_card_pl",
    "callback_pay_intl_pl",
    "callback_pay_sbp",
    "callback_pay_crypto",
    "callback_pay_lava",
    "callback_pay_tariff_card",
}

# Все callback_data, на которые экраны откликались до разрезания.
CALLBACK_DATA = {
    "pay:card",
    "pay:stars",
    "pay:card_pl",
    "pay:intl_pl",
    "pay:sbp",
    "pay:crypto",
    "pay:lava",
    "pay_tariff_card:",
}

MODULES = [
    "telegram_invoice.py",
    "platega.py",
    "cryptobot.py",
    "lava.py",
]


def _decorated_functions():
    """{имя функции: [текст декораторов]} по всем модулям пакета."""
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
    assert not Path("app/handlers/callbacks/pay_external.py").exists()


def test_no_payment_handler_was_lost():
    missing = HANDLERS - set(_decorated_functions())
    assert not missing, f"экраны оплаты пропали: {sorted(missing)}"


def test_every_handler_is_registered():
    """Кнопка оплаты, потерявшая роутер, молчит — без ошибки в логах."""
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
    """Тот же набор адресов, что и до разрезания."""
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
    """Через `pay_external.X` к экранам обращаются снаружи."""
    import app.handlers.callbacks.pay_external as pay_external

    for name in HANDLERS | {"pay_external_router", "_start_platega_payment"}:
        assert hasattr(pay_external, name), f"потерян реэкспорт: {name}"


def test_purchase_is_created_before_the_invoice():
    """Порядок «сначала покупка, потом счёт» — не стилистика.

    Вебхук провайдера приходит по purchase_id. Выставив счёт раньше, чем
    создана запись, получаем оплату, для которой некуда положить товар:
    деньги пришли, подписки нет, и чинить это придётся руками.
    """
    for module in MODULES:
        src = (PKG / module).read_text(encoding="utf-8")
        create_at = src.find("create_subscription_purchase(")
        if create_at == -1:
            continue
        for invoice_call in ("send_invoice(", "create_transaction(", "create_invoice("):
            at = src.find(invoice_call)
            if at == -1:
                continue
            assert create_at < at, (
                f"{module}: {invoice_call} стоит раньше create_subscription_purchase"
            )


def test_provider_modules_do_not_import_each_other():
    """Провайдеры независимы: связь между ними = общий отказ на релизе."""
    names = {m[:-3] for m in MODULES}
    for module in MODULES:
        tree = ast.parse((PKG / module).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "app.handlers.callbacks.pay_external"
            ):
                imported.add((node.module or "").split(".")[-1])
                imported.update(a.name for a in node.names)
        clash = (imported & names) - {module[:-3]}
        assert not clash, f"{module} тянет соседний провайдер: {sorted(clash)}"
