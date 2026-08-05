"""Разрезание подарочных подписок ничего не потеряло.

app/handlers/callbacks/gift.py был на 982 строки и держал четыре разные
вещи: мастер покупки, пять способов оплаты, просмотр купленных подарков и
посторонний CTA «скидка 20%» из админского уведомления. Разрезан на пакет
wizard / payment / my_gifts / offer_claim / formatting.

ГЛАВНЫЕ РИСКИ ТАКОЙ ОПЕРАЦИИ

    1. Забытый include_router — кнопка молчит, в логах пусто.

    2. Потерянный реэкспорт `_send_gift_success`. Её зовут по пути
       `app.handlers.callbacks.gift` из доставки товара после вебхука.
       Падает не при импорте, а в момент, когда человек уже заплатил: он
       не получит ссылку на подарок.

    3. Своя копия автоудаления счёта. Копий уже было шесть с расходящимися
       телами; правка таймаута попадала в один-два файла, и счета одних
       товаров исчезали, а других висели просроченными.
"""
import ast
from pathlib import Path

import pytest

PKG = Path("app/handlers/callbacks/gift")

HANDLERS = {
    "callback_gift_start",
    "callback_gift_tariff",
    "callback_gift_period",
    "callback_gift_pay_balance",
    "callback_gift_pay_card",
    "callback_gift_pay_stars",
    "callback_gift_pay_crypto",
    "callback_gift_pay_lava",
    "callback_my_gifts",
    "callback_gift_detail",
    "callback_gift_offer_claim",
}

CALLBACK_DATA = {
    "gift_subscription",
    "gift_tariff:",
    "gift_period:",
    "gift_pay:balance",
    "gift_pay:card",
    "gift_pay:stars",
    "gift_pay:crypto",
    "gift_pay:lava",
    "my_gifts:",
    "gift_detail:",
    "gift_offer:claim",
}

MODULES = [
    "formatting.py",
    "wizard.py",
    "payment.py",
    "my_gifts.py",
    "offer_claim.py",
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
    assert not Path("app/handlers/callbacks/gift.py").exists()


def test_no_gift_handler_was_lost():
    missing = HANDLERS - set(_decorated_functions())
    assert not missing, f"экраны подарков пропали: {sorted(missing)}"


def test_every_gift_handler_is_registered():
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


def test_wizard_steps_keep_their_fsm_filters():
    """Шаги мастера фильтруются состоянием.

    Без состояния кнопка из старого сообщения откроет середину мастера с
    пустыми данными — человек увидит «сессия истекла» вместо экрана.
    """
    decorated = _decorated_functions()
    for name, expected_state in (
        ("callback_gift_tariff", "GiftState.choose_tariff"),
        ("callback_gift_period", "GiftState.choose_period"),
        ("callback_gift_pay_balance", "GiftState.choose_payment_method"),
        ("callback_gift_pay_card", "GiftState.choose_payment_method"),
        ("callback_gift_pay_stars", "GiftState.choose_payment_method"),
        ("callback_gift_pay_crypto", "GiftState.choose_payment_method"),
        ("callback_gift_pay_lava", "GiftState.choose_payment_method"),
    ):
        text = " ".join(decorated[name])
        assert expected_state in text, f"{name} потерял фильтр {expected_state}"


def test_delivery_path_still_finds_send_gift_success():
    """Доставка после вебхука импортирует функцию по пути пакета."""
    import app.handlers.callbacks.gift as gift

    assert hasattr(gift, "_send_gift_success")
    for path in (
        Path("app/handlers/payments/goods_delivery.py"),
        Path("app/services/payments/confirmation.py"),
    ):
        src = path.read_text(encoding="utf-8")
        assert "app.handlers.callbacks.gift import _send_gift_success" in src, (
            f"{path} больше не берёт _send_gift_success из пакета подарков"
        )


def test_package_reexports_survived():
    import app.handlers.callbacks.gift as gift

    for name in HANDLERS | {
        "gift_router",
        "_send_gift_success",
        "_schedule_invoice_deletion",
        "_tariff_display_name",
        "_period_display",
    }:
        assert hasattr(gift, name), f"потерян реэкспорт: {name}"


def test_formatting_module_stays_at_the_bottom():
    """Подписи не должны знать про экраны — иначе кольцо импортов."""
    tree = ast.parse((PKG / "formatting.py").read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not imports, "formatting.py обзавёлся зависимостями"
