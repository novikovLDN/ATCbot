"""Разбивка фермы ничего не потеряла.

app/handlers/farm.py был на 1386 строк и держал в одном файле четыре вещи,
которые правят по разным поводам: формулы роста и ускорения, вёрстку экрана,
игровые действия над грядками и оплату плёнки внешним провайдерам. Разрезан
на mechanics / screen / plots / storm плюс сборку в __init__.py.

ГЛАВНЫЙ РИСК ТАКОЙ ОПЕРАЦИИ

    Забытый include_router. Обработчик остаётся объявленным, ошибок в логах
    нет — кнопка просто перестаёт отвечать. Поэтому проверяем не только что
    функция объявлена, но и что диспетчер её реально видит.

    Второй риск — потерянный гард подписки. Он висит на роутере пакета, а
    обработчики теперь живут в подроутерах. Если aiogram перестанет собирать
    inner-middleware по цепочке родителей (или кто-то заведёт ферме роутер
    мимо пакета), неплательщик снова сможет майнить баланс — и об этом тоже
    не будет ни одной строки в логах.
"""
import ast
from pathlib import Path

FARM = Path("app/handlers/farm")

# Список взят из файла ДО разрезания. Пропажа любого имени = молчащая кнопка.
FARM_HANDLERS = {
    "callback_game_farm",
    "callback_farm_choose_plant",
    "callback_farm_plant",
    "callback_farm_water",
    "callback_farm_fert",
    "callback_farm_harvest",
    "callback_farm_remove",
    "callback_farm_buy_plot",
    "callback_farm_dig",
    "callback_farm_dig_confirm",
    "callback_farm_noop",
    "callback_farm_shield",
    "callback_farm_shield_lava",
    "callback_farm_shield_sbp",
    "callback_farm_early_harvest",
}

# Все callback_data, на которые ферма откликалась до разрезания. Значения
# берутся из декораторов; расхождение здесь = кнопка, потерявшая адресата.
FARM_CALLBACK_DATA = {
    "game_farm",
    "farm_choose_",
    "farm_plant_",
    "farm_water_",
    "farm_fert_",
    "farm_harvest_",
    "farm_remove_",
    "farm_buy_plot",
    "farm_dig_",
    "farm_dig_confirm_",
    "farm_noop",
    "farm_shield:",
    "farm_shield_lava:",
    "farm_shield_sbp:",
    "farm_early:",
}

# Имена, которые снаружи зовут через `app.handlers.farm.X`: их используют
# тесты и соседний код. Реэкспорт падает не при импорте, а при обращении.
REEXPORTED = {
    "router",
    "require_active_subscription",
    "PLANT_TYPES",
    "STORM_STALE_AFTER_HOURS",
    "SHIELD_INVOICE_MIN_LEAD_MINUTES",
    "FARM_BOOST_MAX_FRACTION",
    "_plant_name",
    "_storm_seconds_left",
    "_invoice_can_arrive_in_time",
    "_apply_growth_boost",
    "_get_imminent_storm",
    "_render_farm",
    "_parse_plot_id",
    "_find_growing_plot",
    "_shield_invoice_allowed",
} | FARM_HANDLERS


def _module_files():
    return sorted(p for p in FARM.glob("*.py"))


def _decorated_functions():
    """{имя функции: [текст декораторов]} по всем модулям пакета."""
    found = {}
    for path in _module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
                found[node.name] = [ast.unparse(d) for d in node.decorator_list]
    return found


def test_old_farm_module_is_gone():
    """Файл разрезан целиком: остаться копией он не может."""
    assert not Path("app/handlers/farm.py").exists()


def test_no_farm_handler_was_lost():
    missing = FARM_HANDLERS - set(_decorated_functions())
    assert not missing, f"обработчики фермы пропали: {sorted(missing)}"


def test_every_farm_handler_is_registered():
    """Кнопка, потерявшая роутер, просто молчит — без ошибки в логах."""
    from app.handlers import router

    registered = set()

    def walk(r):
        for h in list(r.callback_query.handlers) + list(r.message.handlers):
            registered.add(getattr(h.callback, "__name__", ""))
        for sub in r.sub_routers:
            walk(sub)

    walk(router)
    missing = FARM_HANDLERS - registered
    assert not missing, f"объявлены, но не подключены: {sorted(missing)}"


def _is_data_attr(node) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "data"


def test_callback_data_set_did_not_change():
    """Тот же набор адресов, что и до разрезания.

    Берём только строки из фильтров по F.data: StateFilter('*') и прочие
    аргументы декоратора к адресации кнопок отношения не имеют.
    """
    values = set()
    for decorators in _decorated_functions().values():
        for text in decorators:
            if "callback_query(" not in text:
                continue
            for node in ast.walk(ast.parse(text, mode="eval")):
                # F.data == "x"
                if isinstance(node, ast.Compare) and _is_data_attr(node.left):
                    for cmp_node in node.comparators:
                        if isinstance(cmp_node, ast.Constant) and isinstance(cmp_node.value, str):
                            values.add(cmp_node.value)
                # F.data.startswith("x")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "startswith"
                    and _is_data_attr(node.func.value)
                ):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            values.add(arg.value)
    assert values == FARM_CALLBACK_DATA, (
        f"лишние: {sorted(values - FARM_CALLBACK_DATA)}, "
        f"пропавшие: {sorted(FARM_CALLBACK_DATA - values)}"
    )


def test_subscription_guard_covers_every_subrouter():
    """Гард висит на роутере пакета, а обработчики — в подроутерах.

    aiogram собирает inner-middleware по всей цепочке родителей
    (TelegramEventObserver._resolve_middlewares идёт по router.chain_head).
    Проверяем это на живых объектах, а не на вере в документацию: без гарда
    ферма начисляет реальные деньги тому, у кого подписка кончилась.
    """
    from app.handlers.farm import require_active_subscription
    from app.handlers.farm import plots, storm

    for name, sub in (("plots", plots.router), ("storm", storm.router)):
        chain = sub.callback_query._resolve_middlewares()
        assert require_active_subscription in chain, (
            f"подроутер {name} не закрыт гардом подписки"
        )


def test_everything_is_reexported_from_the_package():
    import app.handlers.farm as farm

    missing = [n for n in sorted(REEXPORTED) if not hasattr(farm, n)]
    assert not missing, f"потерян реэкспорт: {missing}"


def _imported_siblings(module: str):
    """Соседи по пакету, которые модуль импортирует.

    Считаем по AST, а не поиском подстроки: «farm.storm» встречается в
    ключах i18n (farm.storm_banner) и дало бы ложное срабатывание.
    """
    tree = ast.parse((FARM / module).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.handlers.farm"):
            tail = (node.module or "").split(".")[-1]
            if tail != "farm":
                names.add(tail)
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app.handlers.farm."):
                    names.add(alias.name.split(".")[-1])
    return names


def test_rules_module_does_not_import_handlers():
    """Правила игры не должны знать про экраны — иначе кольцо импортов,
    и mechanics перестаёт быть нижним уровнем."""
    imported = _imported_siblings("mechanics.py")
    for sibling in ("plots", "storm", "screen"):
        assert sibling not in imported, f"mechanics тянет {sibling}"


def test_screen_module_does_not_import_handlers():
    """Экран зовут ИЗ обработчиков, а не наоборот."""
    imported = _imported_siblings("screen.py")
    for sibling in ("plots", "storm"):
        assert sibling not in imported, f"screen тянет {sibling}"
