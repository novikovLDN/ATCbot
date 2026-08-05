"""Кнопка в рассылке должна работать у того, кому рассылку прислали.

ЧТО СЛОМАЛОСЬ

    Экраны спецпредложений (подарок на месяц, на три месяца, год со
    скидкой 40%, «вскрытие» скидки, покупка ГБ со скидкой) лежали в
    app/handlers/admin/broadcast_gifts.py — рядом с мастером создания
    рассылки, но по сути это ПОЛЬЗОВАТЕЛЬСКИЕ экраны: кнопки приходят
    человеку в рассылке.

    Когда на админский раздел повесили middleware «пускать только
    администратора», она накрыла и их. Рассылка уходила, человек жал
    «🎁 Купить со скидкой» — и ничего не происходило. Ни ошибки, ни
    ответа: middleware молча возвращает None.

    Потом админский раздел вырезали целиком, и обработчики исчезли
    вместе с ним.

ЧТО ПРОВЕРЯЕМ

    Каждая callback_data, которую дашборд ставит на кнопку рассылки,
    имеет живой обработчик, и он НЕ под админской проверкой.
"""
import ast
import re
from pathlib import Path

import pytest


# Сборка клавиатуры переехала сюда, когда роут рассылок разрезали на пакет
# (был файл на 1033 строки). Путь читается напрямую: тест сознательно не
# импортирует FastAPI, чтобы работать на голом дереве исходников.
DASHBOARD_ROUTE = Path("app/api/dashboard/routes/broadcasts/keyboard.py")


def _dashboard_broadcast_buttons() -> set:
    """callback_data, которые дашборд ставит на кнопки рассылки."""
    src = DASHBOARD_ROUTE.read_text(encoding="utf-8")
    builder = src[src.index("def _build_reply_markup("):]
    found = set()
    for m in re.finditer(r'callback_data=f?"([^"{]*)(\{[^}]*\})?"', builder):
        base = m.group(1)
        if base:
            found.add(base)
    return found


def test_dashboard_actually_offers_buttons():
    """Страховка от пустого разбора: если тут ноль, тест ниже бесполезен."""
    assert len(_dashboard_broadcast_buttons()) >= 8


def _registered_filters() -> tuple:
    """Что ловят обработчики: точные значения и префиксы.

    Читаем из исходников: repr самого фильтра aiogram ничего не
    показывает — MagicFilter не раскрывает, с чем сравнивает.
    """
    exact, prefixes = set(), set()
    for path in Path("app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        exact |= set(re.findall(r'F\.data\s*==\s*["\']([^"\']+)["\']', text))
        prefixes |= set(re.findall(r'F\.data\.startswith\(["\']([^"\']+)["\']\)', text))
        for block in re.findall(r"F\.data\.in_\(\{([^}]*)\}\)", text):
            exact |= set(re.findall(r'["\']([^"\']+)["\']', block))
    return exact, prefixes


@pytest.mark.parametrize("callback_data", sorted(_dashboard_broadcast_buttons()))
def test_every_broadcast_button_has_a_live_handler(callback_data):
    exact, prefixes = _registered_filters()
    handled = callback_data in exact or any(
        callback_data.startswith(p) or p.startswith(callback_data) for p in prefixes
    )
    assert handled, (
        f"кнопка {callback_data!r} из рассылки не имеет обработчика — "
        f"человек нажмёт и ничего не произойдёт"
    )


def test_offer_screens_are_not_behind_the_admin_guard():
    """Раздел с этими экранами не должен быть админским."""
    from app.handlers.callbacks import broadcast_offers

    path = Path(broadcast_offers.__file__)
    assert "handlers/callbacks" in str(path), (
        f"экраны предложений снова в админском разделе: {path}"
    )

    # Предложения разрезаны на пакет (акция на модуль), поэтому проверяем
    # каждый модуль: проверка, заехавшая в один из них, накрыла бы свою
    # акцию так же тихо, как раньше middleware накрывала весь раздел.
    for module in sorted(path.parent.glob("*.py")):
        src = module.read_text(encoding="utf-8")
        assert "app.handlers.admin" not in src, (
            f"{module.name} тянет админский раздел"
        )

        # Именно ПРОВЕРКА доступа, а не упоминание админа: created_by=
        # config.ADMIN_TELEGRAM_ID в записи о скидке — это атрибуция «кем
        # выдана», она к правам нажавшего отношения не имеет.
        guards = re.findall(
            r"if\s+[^\n]*from_user\.id\s*!=\s*config\.ADMIN_TELEGRAM_ID", src,
        )
        guards += re.findall(r"is_admin\(", src)
        assert not guards, f"в {module.name} осталась проверка на админа: {guards}"


def test_offer_router_is_included_in_the_package():
    """Забытый include_router не даёт ошибки — кнопки просто молчат."""
    from app.handlers.callbacks import router
    from app.handlers.callbacks.broadcast_offers import broadcast_offers_router

    def contains(r):
        return r is broadcast_offers_router or any(contains(s) for s in r.sub_routers)

    assert contains(router)


def test_admin_only_wizard_handlers_did_not_come_back():
    """Выбор процента при СОЗДАНИИ рассылки — часть мастера, который
    переехал в дашборд. У дашборда свой список процентов."""
    names = set()
    for module in sorted(Path("app/handlers/callbacks/broadcast_offers").glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        names |= {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    assert "callback_gift_reveal_percent_select" not in names
