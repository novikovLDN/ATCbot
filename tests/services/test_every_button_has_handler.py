"""Каждая кнопка бота обязана иметь живой обработчик.

ЧТО ЗА ДЕФЕКТ

    В aiogram нажатие на инлайн-кнопку, у которой нет обработчика под её
    callback_data, не даёт НИЧЕГО: ни исключения, ни ответа пользователю,
    ни строчки в логе. Диспетчер перебирает обработчики, ни один не
    подходит, апдейт молча отбрасывается. Человек жмёт кнопку и решает,
    что бот сломался; в мониторинге при этом чисто.

    В этом проекте так уже теряли целые экраны: раздел спецпредложений
    накрыло админской middleware (см. test_broadcast_buttons_reachable), а
    после переноса админки в веб-дашборд в главном меню /admin осталось
    тринадцать кнопок, чьи экраны удалили вместе с 23 модулями. Раздел
    ручной выдачи ГБ обхода при этом потерял единственный вход и стал
    недостижим целиком.

    Отдельно: в боте НЕТ catch-all обработчика callback_query — это
    проверяется ниже. Именно его отсутствие делает промах бесшумным.

ЧТО ПРОВЕРЯЕТСЯ

    Собираем все callback_data, которые код где-либо ставит на кнопку
    (константы, f-строки, переменные, тернарники), и прогоняем каждое
    значение через СОБРАННОЕ дерево роутеров — то самое, что уходит в
    диспетчер. Фильтры вычисляются по-настоящему: MagicFilter.resolve на
    подставном объекте с нужным .data. Это ловит и опечатку в префиксе, и
    забытый include_router, и удалённый вместе с разделом обработчик.

ПОЧЕМУ НЕ ГРЕПОМ ПО ИСХОДНИКАМ

    Греп видит объявление, но не видит, подключён ли роутер. Забытый
    include_router — ровно такая же молчащая кнопка, и грепом она
    выглядит живой.

ГДЕ ИЩЕМ КНОПКИ

    Не только в app/. Напоминания об окончании подписки (reminders.py) и
    уведомления о триале (trial_notifications.py) лежат в корне и тоже
    ставят кнопки — например, paid_discount_15 и trial_discount_15,
    которые больше нигде не встречаются. Сузить обход до app/ значит
    перестать сторожить именно те кнопки, что уходят людям сами.
"""
import ast
from pathlib import Path

import pytest
from aiogram.filters import StateFilter
from magic_filter import MagicFilter

from app.handlers import router


ROOT = Path(".")
SCAN_DIRS = (Path("app"), Path("database"))
SKIP = ("__pycache__",)

# Маркеры того, НАСКОЛЬКО точно разобрано значение. Разница между ними —
# не косметика: по первому тест имеет право объявить дефект, по второму не
# имеет, и путать их опасно.
#
# CUT — известно начало, подстановка стоит в САМОМ КОНЦЕ и после неё
#     ничего нет: f"admin:traffic:{user_id}". Настоящее значение
#     гарантированно начинается с этого начала, поэтому проверка
#     «есть ли обработчик, ловящий такой префикс» честная.
#
# PARTIAL — известно начало, но дальше идёт ещё литеральный текст после
#     подстановки: f"setup_qr_{kind}:{platform}". Чем заполнена дырка,
#     статически неизвестно (здесь — standard/bypass, и оба обработчика
#     существуют). Подставить правдоподобное значение неоткуда, значит
#     ОБЪЯВЛЯТЬ ТУТ ДЕФЕКТ НЕЛЬЗЯ: тест, сообщающий о несуществующей
#     мёртвой кнопке, приводит к удалению рабочего экрана. Такие места
#     проверяются мягко, а непрошедшие уходят в список для просмотра
#     глазами (test_unresolvable_buttons_are_declared).
CUT = "…"
PARTIAL = "‥"


def _sources():
    for directory in SCAN_DIRS:
        for path in sorted(directory.rglob("*.py")):
            if not any(d in str(path) for d in SKIP):
                yield path
    # Воркеры и рассыльщики в корне: они тоже строят клавиатуры.
    for path in sorted(ROOT.glob("*.py")):
        yield path


def _candidates(node: ast.AST) -> set:
    """Возможные значения выражения с маркером точности разбора."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        # ar_data = "toggle_auto_renew:off" if auto_renew else "...:on"
        # Тернарник разворачивается в обе ветки — иначе кнопка
        # автопродления выглядела бы как несуществующая.
        return _candidates(node.body) | _candidates(node.orelse)
    if isinstance(node, ast.JoinedStr):
        head, seen_hole, tail_literal = [], False, False
        for piece in node.values:
            if isinstance(piece, ast.Constant):
                if seen_hole:
                    # Литеральный текст ПОСЛЕ подстановки: собрать значение
                    # целиком уже нельзя.
                    if piece.value:
                        tail_literal = True
                else:
                    head.append(piece.value)
            else:
                seen_hole = True
        prefix = "".join(head)
        if not prefix:
            return set()
        if not seen_hole:
            # f-строка без подстановок (f"premium_period_back") — обычная
            # строка, а не префикс. Пометить её обрезанной значит проверять
            # несуществующее значение и молча пропустить настоящую кнопку.
            return {prefix}
        return {prefix + (PARTIAL if tail_literal else CUT)}
    return set()


def _assigned_names(tree: ast.AST) -> dict:
    """Строки, присвоенные именам: MY_CB = "x", back_cb = f"y:{id}".

    Кнопки часто собирают через переменную (`callback_data=back_cb`), и без
    этого шага такие кнопки не попадут в проверку — то есть сторож молча
    перестанет сторожить самое интересное.
    """
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                values = _candidates(node.value)
                if values:
                    out.setdefault(target.id, set()).update(values)
    return out


def produced_callback_data() -> dict:
    """{значение или начало+CUT: [файл:строка]} — что бот ставит на кнопки."""
    out = {}
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — синтаксис ловит ruff
            continue
        names = _assigned_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg != "callback_data":
                continue
            where = f"{path}:{node.value.lineno}"
            values = _candidates(node.value)
            if not values and isinstance(node.value, ast.Name):
                values = names.get(node.value.id, set())
            for value in values:
                out.setdefault(value, []).append(where)
    return out


def registered_filter_strings() -> set:
    """Строки из F.data == / .startswith() / .in_() по исходникам.

    Нужны только как запасной вариант для обрезанных префиксов, у которых
    подстановка стоит В СЕРЕДИНЕ: f"setup_qr_{kind}:{platform}" даёт начало
    'setup_qr_', и подставить вместо {kind} правдоподобное значение
    неоткуда. Для таких случаев достаточно убедиться, что хоть один фильтр
    в дереве начинается с этого начала.
    """
    found = set()
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Compare)
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)
                and isinstance(node.left, ast.Attribute)
                and node.left.attr == "data"
                and isinstance(node.comparators[0], ast.Constant)
            ):
                found.add(node.comparators[0].value)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                inner = node.func.value
                is_data = isinstance(inner, ast.Attribute) and inner.attr == "data"
                if node.func.attr == "startswith" and is_data and node.args:
                    if isinstance(node.args[0], ast.Constant):
                        found.add(node.args[0].value)
                if node.func.attr == "in_" and node.args:
                    for element in ast.walk(node.args[0]):
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            found.add(element.value)
    return found


def _callback_handlers():
    """Обработчики callback_query в порядке подключения роутеров."""
    def walk(r):
        yield from r.observers["callback_query"].handlers
        for sub in r.sub_routers:
            yield from walk(sub)
    return list(walk(router))


HANDLERS = _callback_handlers()
PRODUCED = produced_callback_data()
FILTER_STRINGS = registered_filter_strings()


class _FakeQuery:
    """Минимальный объект: фильтрам F.data нужен только атрибут data."""

    def __init__(self, data: str):
        self.data = data


def _magic_filters(handler):
    """MagicFilter'ы обработчика. aiogram хранит их как bound-метод resolve."""
    out = []
    for f in (handler.filters or []):
        owner = getattr(f.callback, "__self__", None)
        if isinstance(owner, MagicFilter):
            out.append(owner)
    return out


def _state_filter(handler):
    for f in (handler.filters or []):
        owner = getattr(f.callback, "__self__", f.callback)
        if isinstance(owner, StateFilter):
            return owner
    return None


def handlers_for(data: str) -> list:
    """Кто откликнется на такой callback_data (без учёта состояния FSM)."""
    matched = []
    for handler in HANDLERS:
        filters = _magic_filters(handler)
        if not filters:
            continue
        for mf in filters:
            try:
                if not mf.resolve(_FakeQuery(data)):
                    break
            except Exception:
                break
        else:
            matched.append(handler)
    return matched


def _probe(value: str) -> str:
    """Из начала строки делаем правдоподобное значение целиком."""
    if value.endswith((CUT, PARTIAL)):
        return value[:-1] + "PROBE123"
    return value


def _is_reachable(value: str) -> bool:
    """Есть ли обработчик. Для обрезанных значений — с запасным вариантом."""
    if handlers_for(_probe(value)):
        return True
    if value.endswith((CUT, PARTIAL)):
        # Обработчик может ловить не префикс, а конкретное значение
        # (F.data == "setup_qr_standard:ios"). Тогда подстановка PROBE123
        # не подойдёт, но литерал фильтра начнётся с известного начала.
        prefix = value[:-1]
        return any(s.startswith(prefix) for s in FILTER_STRINGS)
    return False


# Значения, разобранные ТОЧНО или до надёжного префикса, — по ним тест
# имеет право объявить мёртвую кнопку.
CHECKABLE = sorted(v for v in PRODUCED if not v.endswith(PARTIAL))

# Значения с подстановкой в середине, которые не прошли мягкую проверку.
# Про них тест НЕ утверждает, что кнопка мёртвая: он утверждает только,
# что разобрать их статически не смог.
UNRESOLVABLE = sorted(
    v for v in PRODUCED if v.endswith(PARTIAL) and not _is_reachable(v)
)


def test_the_scan_actually_finds_buttons():
    """Страховка от пустого разбора: пустой словарь сделал бы тест ниже
    вечнозелёным и бесполезным."""
    assert len(PRODUCED) > 250, (
        f"разбор нашёл всего {len(PRODUCED)} значений callback_data — "
        f"скорее всего сломался обход AST, а не исчезли кнопки"
    )


def test_there_is_no_catch_all_callback_handler():
    """Почему промах молчит: перехватить его некому.

    Если такой обработчик когда-нибудь появится, тест ниже потеряет смысл —
    любая кнопка станет «живой». Тогда проверку надо переписывать, а не
    вычёркивать.
    """
    without_data_filter = [
        f"{h.callback.__module__}.{h.callback.__name__}"
        for h in HANDLERS if not _magic_filters(h)
    ]
    assert not without_data_filter, (
        f"появился обработчик callback_query без фильтра по data: "
        f"{without_data_filter}. Он перехватит всё подряд"
    )


@pytest.mark.parametrize("value", CHECKABLE)
def test_every_button_has_a_live_handler(value):
    assert _is_reachable(value), (
        f"кнопка с callback_data {value!r} не имеет обработчика — человек "
        f"нажмёт, и не произойдёт ничего: ни ответа, ни ошибки, ни лога.\n"
        f"Где ставится: {', '.join(PRODUCED[value][:5])}"
    )


# Кнопки, чей callback_data собирается с подстановкой в середине и не
# опознаётся по префиксу. Пусто — и хорошо: значит все такие места
# опознались. Пополнять список можно ТОЛЬКО после проверки глазами, что
# обработчик существует, — с указанием, чем заполняется дырка.
UNRESOLVABLE_BUT_CHECKED_BY_HAND: set = set()


def test_unresolvable_buttons_are_declared():
    """Что детектор не смог разобрать — не выдаём за дефект.

    ПОЧЕМУ ОТДЕЛЬНЫМ ТЕСТОМ

        f"setup_qr_{kind}:{platform}" даёт только начало 'setup_qr_'.
        Чем заполнена дырка, из AST не видно: здесь это standard и bypass,
        и оба обработчика существуют. Если такое место объявить мёртвой
        кнопкой, следующий читатель удалит рабочий экран QR-кодов —
        именно так тест приносит вреда больше, чем пользы.

        Поэтому: сначала мягкая проверка (префикс против литералов
        фильтров), и только непрошедшие попадают сюда — как «посмотрите
        глазами», а не как «сломано».
    """
    unexpected = set(UNRESOLVABLE) - UNRESOLVABLE_BUT_CHECKED_BY_HAND
    assert not unexpected, (
        "эти callback_data собираются с подстановкой в середине, и по ним "
        "нельзя ни подтвердить, ни опровергнуть наличие обработчика. Это НЕ "
        "готовый дефект: проверьте руками, какими значениями заполняется "
        "подстановка, и есть ли под них обработчики. Подтвердив — внесите в "
        f"UNRESOLVABLE_BUT_CHECKED_BY_HAND с пояснением.\n"
        + "\n".join(
            f"  {v!r} <- {', '.join(PRODUCED[v][:3])}" for v in sorted(unexpected)
        )
    )


def test_admin_main_menu_has_no_dead_buttons():
    """Главное меню /admin — экран, где кнопки умирали пачками.

    После переноса админки в дашборд здесь остались тринадцать адресов
    удалённых разделов. Отдельная проверка нужна потому, что это
    единственное меню, которое видит владелец, и молчащая кнопка в нём
    выглядит как сломавшийся бот.
    """
    from app.handlers.admin.keyboards import get_admin_dashboard_keyboard

    dead = []
    for row in get_admin_dashboard_keyboard("ru").inline_keyboard:
        for button in row:
            if button.callback_data and not handlers_for(button.callback_data):
                dead.append(f"{button.text} -> {button.callback_data}")
    assert not dead, f"в меню /admin кнопки без обработчика: {dead}"


def test_bypass_traffic_admin_section_is_reachable():
    """Ручная выдача ГБ обхода: единственный вход в раздел.

    Аналога в веб-дашборде нет, а вход в раздел жил на карточке
    пользователя, которая в дашборд уехала. Без admin:traffic_user все
    шесть экранов раздела недостижимы — они адресуются по
    admin:traffic:{user_id}, и этот адрес больше никто не выставляет.
    """
    assert handlers_for("admin:traffic_user"), "вход в раздел ГБ обхода потерян"
    assert handlers_for("admin:traffic:123"), "экран трафика недостижим"

    from app.handlers.admin.keyboards import get_admin_dashboard_keyboard

    addresses = {
        button.callback_data
        for row in get_admin_dashboard_keyboard("ru").inline_keyboard
        for button in row
    }
    assert "admin:traffic_user" in addresses, (
        "кнопка входа пропала из меню /admin — раздел снова недостижим"
    )


def test_broadcast_traffic_promo_offers_extended_packs():
    """«📦 Больше объёма →» в промо-рассылке на трафик.

    Кнопка вела на broadcast_promo_traffic_ext:{id}, под который не было ни
    одного обработчика: человек с уже применённой скидкой жал её и не
    получал ничего.
    """
    source = Path("app/handlers/callbacks/broadcast_offers/promo_discounts.py")
    text = source.read_text(encoding="utf-8")

    # Комментарий-надгробие про старый адрес оставлен намеренно, поэтому
    # смотрим только строки кода.
    code = [
        line for line in text.splitlines()
        if not line.strip().startswith("#")
    ]
    revived = [line.strip() for line in code if "broadcast_promo_traffic_ext" in line]
    assert not revived, f"вернулся адрес без обработчика: {revived}"

    assert 'callback_data="buy_traffic_extended"' in "\n".join(code)
    assert handlers_for("buy_traffic_extended"), (
        "экран расширенных пакетов пропал — кнопка снова ведёт в никуда"
    )


# ──────────────────────────────────────────────────────────────────────
#  Обратная сторона: обработчик, до которого не ведёт ни одна кнопка
# ──────────────────────────────────────────────────────────────────────
#
# Такой обработчик не ломает бота, но читается как рабочий сценарий:
# правя его, разработчик уверен, что чинит то, что видят люди.
#
# Список ниже — исключения, каждое с причиной. Пополнять его можно только
# вместе с объяснением, ПОЧЕМУ обработчик обязан жить без кнопки.
HANDLERS_WITHOUT_A_BUTTON = {
    # Совместимость со старыми клавиатурами: сообщения в чатах живут вечно,
    # и кнопка из вчерашней рассылки обязана ответить внятным алертом, а не
    # промолчать. В новом коде такие кнопки не ставятся намеренно.
    "callback_setup_key",            # старый экран ключа (connect_guide)
    "callback_pay_tariff_card",      # DEPRECATED, счёт создаётся автоматически
    "callback_traffic_pay_balance",  # оплата пакетов ГБ с баланса выключена
    "callback_bypass_pay_balance",   # то же для обхода
    "callback_stars_pay_balance",    # оплата Stars с баланса запрещена политикой
    "callback_steam_pay_balance",    # то же для Steam
    # Разделы, оставленные в дереве по решению владельца: недостижимость
    # описана в докстрингах модулей.
    "callback_admin_reissue_key",                 # admin/reissue.py
    "callback_admin_reissue_all_active_confirm",  # admin/reissue.py
    "callback_admin_promocode_unit",              # мастер промокода не доведён
    "callback_admin_promocode_confirm",           # admin/promocodes.py
    # Экраны, потерявшие вход. Решение — за владельцем: вернуть кнопку или
    # удалить экран. Каждый описан в отчёте по аудиту связности.
    "callback_service_status",       # «Статус сервиса»: в меню кнопки нет
    "callback_setup_device",         # i18n-ключ connect.setup_device_button осиротел
    "callback_renewal_pay",          # экран продления заменён обычной покупкой
    "callback_withdraw_start",       # вывод средств: вход не выставляет никто
    "callback_copy_referral_link",   # лояльность перешла на нативный share-url
    "callback_proxy_open",           # кнопку «MT Прокси» дашборд не собирает
}


def test_no_new_handler_lost_its_button():
    """Новый обработчик без кнопки — почти всегда забытая кнопка."""
    reachable = set()
    for value in PRODUCED:
        for handler in handlers_for(_probe(value)):
            reachable.add(handler.callback.__name__)

    orphans = {
        h.callback.__name__ for h in HANDLERS
        if _magic_filters(h) and h.callback.__name__ not in reachable
    }

    unexpected = orphans - HANDLERS_WITHOUT_A_BUTTON
    assert not unexpected, (
        f"обработчики есть, а кнопок на них нет: {sorted(unexpected)}. "
        f"Либо кнопку забыли поставить, либо обработчик надо удалить — "
        f"молча оставлять нельзя"
    )

    stale = HANDLERS_WITHOUT_A_BUTTON - orphans
    assert not stale, (
        f"эти обработчики снова достижимы, уберите их из списка "
        f"исключений: {sorted(stale)}"
    )


# ──────────────────────────────────────────────────────────────────────
#  Один callback_data — один обработчик
# ──────────────────────────────────────────────────────────────────────

def test_no_callback_data_is_served_by_two_handlers():
    """Второй обработчик того же значения не выполнится никогда.

    aiogram отдаёт апдейт первому подошедшему и останавливается. Дубль не
    падает и ничего не пишет в лог — он просто мёртв, и правка, внесённая
    в него, молча не сработает. Так было с двумя регистрациями
    callback_profile: нижний декоратор без StateFilter побеждал всегда.
    """
    clashes = {}
    for value in sorted(PRODUCED):
        matched = handlers_for(_probe(value))
        if len(matched) > 1:
            clashes[value] = [
                f"{h.callback.__module__}.{h.callback.__name__}" for h in matched
            ]
    assert not clashes, (
        f"на один callback_data откликается несколько обработчиков, "
        f"выполнится только первый: {clashes}"
    )


def test_profile_screen_has_exactly_one_registration():
    """Отдельно про menu_profile — здесь дубль уже был."""
    matched = handlers_for("menu_profile")
    assert len(matched) == 1, (
        f"menu_profile зарегистрирован {len(matched)} раз; вторая "
        f"регистрация недостижима"
    )
    assert _state_filter(matched[0]) is None, (
        "menu_profile обязан открываться из любого состояния FSM"
    )
