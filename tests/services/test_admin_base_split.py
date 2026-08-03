"""Разбивка app/handlers/admin/base.py ничего не потеряла.

Файл был на 1167 строк и держал пять несвязанных разделов: вход в админку,
перевыпуск VPN-ключей, мастер промокода, диагностику системы и переписку с
пользователем. Правка входа шла посреди массового перевыпуска ключей.
Разрезан на base / chat / system / reissue / promocodes.

Главный риск — забытый include_router: админская кнопка при этом не даёт ни
одной ошибки в логах, она просто перестаёт отвечать. Поэтому проверяем не
объявление функции, а то, что диспетчер её видит.

Второй риск важнее обычного: весь раздел закрыт одной middleware на
родительском роутере. Новый роутер, подключённый мимо неё, открыл бы
админские операции кому угодно. Ниже проверяется, что все новые роутеры
подключены именно к закрытому родителю.
"""
import ast
from pathlib import Path

ADMIN = Path("app/handlers/admin")

# Взято из base.py до разрезания.
ADMIN_HANDLERS = {
    # вход
    "cmd_admin", "callback_reset_password", "callback_reset_password_cancel",
    "callback_reset_password_confirm", "callback_admin_dashboard", "callback_admin_main",
    # перевыпуск ключей
    "callback_admin_reissue_key", "callback_admin_reissue_all_active_confirm",
    "callback_admin_reissue_all_active",
    # промокоды
    "callback_admin_create_promocode", "callback_admin_promocode_unit",
    "callback_admin_promocode_confirm", "callback_admin_promocode_cancel",
    # системные экраны
    "callback_admin_system", "callback_remnawave_mass_provision",
    "callback_admin_test_menu", "callback_admin_test", "callback_admin_qodev",
    # переписка с пользователем
    "callback_admin_chat_start", "process_admin_chat_user_id", "process_admin_chat_message",
}

SPLIT_MODULES = ["base.py", "chat.py", "system.py", "reissue.py", "promocodes.py"]


def _decorated(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.decorator_list
    }


def test_no_admin_handler_was_lost():
    found = set()
    for name in SPLIT_MODULES:
        found |= _decorated(ADMIN / name)
    missing = ADMIN_HANDLERS - found
    assert not missing, f"админские обработчики пропали: {sorted(missing)}"


def test_every_admin_handler_is_registered():
    """Забытый include_router не даёт ошибки — кнопка просто молчит."""
    from app.handlers import router

    registered = set()

    def walk(r):
        for h in list(r.callback_query.handlers) + list(r.message.handlers):
            registered.add(getattr(h.callback, "__name__", ""))
        for sub in r.sub_routers:
            walk(sub)

    walk(router)
    missing = ADMIN_HANDLERS - registered
    assert not missing, f"объявлены, но не подключены: {sorted(missing)}"


def test_new_routers_hang_under_the_guarded_parent():
    """Раздел закрыт одной middleware на родительском роутере. Роутер,
    подключённый мимо него, откроет админские операции кому угодно."""
    from app.handlers.admin import router
    from app.handlers.admin.chat import admin_chat_router
    from app.handlers.admin.system import admin_system_router
    from app.handlers.admin.reissue import admin_reissue_router
    from app.handlers.admin.promocodes import admin_promocodes_router

    children = set(id(r) for r in router.sub_routers)
    for sub in (admin_chat_router, admin_system_router,
                admin_reissue_router, admin_promocodes_router):
        assert id(sub) in children, f"{sub} подключён мимо проверки админа"

    for observer in (router.message, router.callback_query):
        names = [getattr(m, "__name__", "") for m in observer.middleware]
        assert "_require_admin" in names, "проверка админа слетела с родителя"


def test_entry_module_stayed_small():
    """base.py — парадная дверь. Если он снова начнёт расти, разбивка была
    зря: именно его размер и мешал править что угодно рядом."""
    src = (ADMIN / "base.py").read_text(encoding="utf-8")
    assert len(src.split("\n")) < 400


def test_bulk_reissue_stays_sequential():
    """Массовый перевыпуск идёт по одному ключу с паузой — это защита от
    лимитов Remnawave. Параллельный прогон кладёт панель и оставляет часть
    подписок с новым ключом, а часть со старым."""
    import inspect

    from app.handlers.admin import reissue

    code = inspect.getsource(reissue.callback_admin_reissue_all_active)
    assert "await asyncio.sleep(1.5)" in code
    assert "asyncio.gather" not in code


def test_chat_module_does_not_edit_messages():
    """Кнопка «Написать пользователю» висит под карточками заказов с
    логином, паролем и кнопкой «Выполнено». Правка сообщения стирает их."""
    src = (ADMIN / "chat.py").read_text(encoding="utf-8")
    assert "safe_edit_text" not in src


def test_broken_wizards_are_documented_not_hidden():
    """Мастер промокода и перевыпуск ключей сейчас недостижимы или
    недоведены. Это должно быть написано в файле, а не выясняться заново:
    экраны выглядят рабочими, и на них легко потратить день."""
    for name in ("promocodes.py", "reissue.py"):
        head = (ADMIN / name).read_text(encoding="utf-8")[:2500]
        assert "ВНИМАНИЕ" in head, f"{name}: не предупреждает о состоянии раздела"
