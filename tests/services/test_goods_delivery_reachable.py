"""Товар мини-магазина должен выдаваться, а не уходить в ручной разбор.

ЧТО БЫЛО СЛОМАНО

    При выносе выдачи из обработчика оплаты в отдельный модуль тела всех
    семи функций остались на лишнем уровне отступа — внутри guard-clause,
    сразу после `return False`:

        if classify_purchase(pending) != "gift":
            return False

            # 40 строк, которые не выполнятся никогда
            gift_result = await database.finalize_purchase(...)

    Каждая функция всегда возвращала False. Цикл выдачи не забирал ни
    одну покупку, и всё уходило в предохранитель: человеку писали «заказ
    у поддержки», админу летел алерт. Деньги не терялись и чужой товар не
    выдавался — но автоматика не работала ни для подарка, ни для Premium,
    ни для звёзд, Steam, Spotify, Apple ID и пакетов ГБ.

    Python на такое не ругается. Тесты молчали: выдача требует базы и
    Telegram, юнит-тестом её не звали.

ЧТО ПРОВЕРЯЕМ

    Что каждая функция выдачи содержит рабочее тело после проверки типа,
    что список в диспетчере полный, и что типы, которые он обслуживает,
    совпадают с теми, ради которых функции написаны.
"""
import ast
import inspect
from pathlib import Path

import pytest

from app.handlers.payments import goods_delivery as goods


DELIVERERS = [
    "deliver_gift",
    "deliver_premium",
    "deliver_stars",
    "deliver_steam",
    "deliver_spotify",
    "deliver_apple_id",
    "deliver_traffic_pack",
]


@pytest.mark.parametrize("name", DELIVERERS)
def test_deliverer_has_a_body_after_the_guard(name):
    """Guard-clause обязан заканчиваться на return, а работа — идти после."""
    fn = getattr(goods, name)
    tree = ast.parse(inspect.getsource(fn))
    node = tree.body[0]

    # Перед проверкой типа идут докстринг и распаковка контекста, поэтому
    # ищем первую проверку, заканчивающуюся выходом, а не первый оператор.
    guards = [
        st for st in node.body
        if isinstance(st, ast.If) and st.body and isinstance(st.body[-1], ast.Return)
    ]
    assert guards, f"{name}: нет проверки «мой ли это тип покупки»"

    guard = guards[0]
    assert len(guard.body) == 1, (
        f"{name}: внутри guard-clause после return осталось "
        f"{len(guard.body) - 1} операторов — они не выполнятся"
    )

    after = node.body[node.body.index(guard) + 1:]
    assert after, f"{name}: после проверки типа пусто, выдавать нечего"


@pytest.mark.parametrize("name", DELIVERERS)
def test_deliverer_can_return_true(name):
    """Функция, которая физически не может вернуть True, ничего не выдаёт."""
    fn = getattr(goods, name)
    tree = ast.parse(inspect.getsource(fn))
    returns = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Constant)
        and n.value.value is True
    ]
    assert returns, f"{name}: нет ни одного пути, где выдача считается выполненной"


def test_dispatcher_lists_every_deliverer():
    """Забытый в списке товар провалится в предохранитель."""
    src = Path("app/handlers/payments/payments_messages.py").read_text(encoding="utf-8")
    block = src[src.index("for deliver in ("):]
    block = block[: block.index("):")]
    for name in DELIVERERS:
        assert f"goods.{name}" in block, f"{name} не подключён к выдаче"


def test_dispatcher_has_no_extra_names():
    """И наоборот: в списке не должно быть того, чего нет в модуле."""
    src = Path("app/handlers/payments/payments_messages.py").read_text(encoding="utf-8")
    block = src[src.index("for deliver in ("):]
    block = block[: block.index("):")]
    listed = {ln.strip().rstrip(",").removeprefix("goods.")
              for ln in block.split("\n") if ln.strip().startswith("goods.")}
    assert listed == set(DELIVERERS), (
        f"список выдачи разошёлся с модулем: лишние {listed - set(DELIVERERS)}, "
        f"недостающие {set(DELIVERERS) - listed}"
    )


def test_safety_net_still_guards_the_tail():
    """Предохранитель после цикла снимать нельзя: он ловит забытый тип."""
    src = Path("app/handlers/payments/payments_messages.py").read_text(encoding="utf-8")
    tail = src[src.index("for deliver in ("):]
    assert "PURCHASE_ROUTE_UNHANDLED" in tail, (
        "исчез предохранитель — забытый товар выдастся как VPN-подписка"
    )
