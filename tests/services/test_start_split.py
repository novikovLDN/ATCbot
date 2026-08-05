"""Разрезание /start ничего не потеряло.

app/handlers/user/start.py был на 994 строки: вход в бота, выдача наград по
промо-ссылкам, отдельная механика скидки «подари другу» и stage-only
развилка, которая в проде не выполняется никогда. Разрезан на пакет
command / share_discount / marketing_links / stage_gate.

ГЛАВНЫЙ РИСК ТАКОЙ ОПЕРАЦИИ

    Потерять регистрацию /start. Ошибок при этом не будет: команда просто
    перестанет отвечать. Это самый заметный отказ в боте — и при этом в
    логах он выглядит как тишина.

    Второй риск — порядок разбора диплинков внутри cmd_start. Он остался
    в одной функции намеренно (ветки делят состояние и по-разному решают,
    прерывать ли поток), но проверить его наличие всё равно стоит: ссылка
    из рассылки, попавшая не в ту ветку, отдаёт человеку не то, за чем он
    пришёл.
"""
import ast
from pathlib import Path

import pytest

PKG = Path("app/handlers/user/start")

MODULES = [
    "command.py",
    "share_discount.py",
    "marketing_links.py",
    "stage_gate.py",
]

# Имена, доступные снаружи до разрезания.
PUBLIC_NAMES = {
    "user_router",
    "cmd_start",
    "callback_stage_gate_dev",
    "_show_stage_gate",
    "_handle_share_discount_start",
    "_handle_stats_link_click",
    "_handle_promo_link_start",
    "_apply_promo_reward",
    "_SHARE_DISCOUNT_PERCENT",
    "_SHARE_DISCOUNT_HOURS",
}

# Префиксы диплинков, которые cmd_start обязан различать.
DEEPLINK_PREFIXES = ["bgift_", "gift_", "s-", "p-", "refd_", "ref_"]


@pytest.mark.parametrize("module", MODULES)
def test_module_exists_with_a_docstring(module):
    path = PKG / module
    assert path.exists(), f"{module} потерян"
    assert ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))), (
        f"{module} без докстринга: непонятно, что здесь и почему выделено"
    )


def test_old_single_file_module_is_gone():
    assert not Path("app/handlers/user/start.py").exists()


def test_start_command_is_registered():
    """Без этого бот молча не отвечает на самую первую команду."""
    from app.handlers import router

    registered = set()

    def walk(r):
        for h in list(r.callback_query.handlers) + list(r.message.handlers):
            registered.add(getattr(h.callback, "__name__", ""))
        for sub in r.sub_routers:
            walk(sub)

    walk(router)
    for name in ("cmd_start", "callback_stage_gate_dev"):
        assert name in registered, f"{name} объявлен, но не подключён"


def test_package_reexports_survived():
    import app.handlers.user.start as start

    missing = sorted(n for n in PUBLIC_NAMES if not hasattr(start, n))
    assert not missing, f"потерян реэкспорт: {missing}"


def test_user_package_still_exposes_the_router():
    """app/handlers/user/__init__.py тянет user_router по старому пути."""
    from app.handlers.user import start_router

    assert start_router is not None


def _cmd_start_source() -> str:
    """Код самой функции, без докстринга модуля.

    Читать файл целиком нельзя: в шапке перечислены те же префиксы
    диплинков, и проверки ниже проходили бы по комментарию, а не по коду.
    """
    text = (PKG / "command.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "cmd_start":
            return ast.get_source_segment(text, node) or ""
    raise AssertionError("cmd_start пропал из command.py")


@pytest.mark.parametrize("prefix", DEEPLINK_PREFIXES)
def test_every_deeplink_prefix_is_still_handled(prefix):
    src = _cmd_start_source()
    assert f'"{prefix}"' in src, (
        f"cmd_start перестал различать диплинк {prefix!r} — человек по ссылке "
        f"получит обычное приветствие вместо того, за чем пришёл"
    )


def test_share_discount_branch_runs_before_plain_referral_registration():
    """`refd_` разбирается ДО реферальной регистрации.

    Префиксы не пересекаются по startswith, но поменяв блоки местами,
    получим ссылку «подари другу», отработавшую как обычная регистрация:
    человек придёт за скидкой и не получит её.
    """
    src = _cmd_start_source()
    refd_at = src.index('startswith("refd_")')
    registration_at = src.index("process_referral_on_first_interaction")
    assert refd_at < registration_at
    assert "_handle_share_discount_start" in src, (
        "ветка refd_ больше не зовёт обработчик скидки"
    )


def test_bypass_gift_does_not_clobber_an_active_subscription():
    """ensure_bypass_only_subscription ставит срок +10 лет.

    Звать её при живой подписке — значит затереть настоящий срок. В коде
    вызов обязан стоять под проверкой «активной подписки нет».
    """
    src = _cmd_start_source()
    # Ищем именно ВЫЗОВ: выше по тексту есть комментарий с тем же именем.
    at = src.index("database.ensure_bypass_only_subscription(")
    before = src[max(0, at - 300):at]
    assert "if not existing_active" in before, (
        "вызов ensure_bypass_only_subscription вышел из-под проверки"
    )


def test_helper_modules_do_not_import_the_command():
    """Ветки зовут ИЗ cmd_start, а не наоборот — иначе кольцо импортов."""
    for module in ("share_discount.py", "marketing_links.py", "stage_gate.py"):
        tree = ast.parse((PKG / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("start.command"), (
                    f"{module} тянет cmd_start обратно"
                )
