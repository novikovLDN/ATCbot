"""Ссылка подключения экранируется перед подстановкой в <code>.

ДЕФЕКТ

    Экраны выдачи ключа собирают сообщение вручную:

        f"<code>{vpn_key}</code>"

    и отправляют с parse_mode="HTML". Один символ & или < в ссылке — и
    Telegram отвергает СООБЩЕНИЕ ЦЕЛИКОМ: человек после оплаты не
    получает ключ, а в логах видна ошибка разбора, а не «плохая ссылка».

    Сейчас Remnawave отдаёт base64-путь без спецсимволов, поэтому дефект
    не стреляет. Стрельнёт при смене схемы подписки или добавлении
    query-параметров — то есть при изменении на стороне панели, а не в
    боте, и найти причину будет тяжело.

    В пяти сопоставимых местах правило уже применено:
    callbacks/subscription.py, user/bypass_gift_setup.py,
    admin/broadcast.py, services/broadcast_sender.py — везде
    html.escape(..., quote=False).

ЧТО ПРОВЕРЯЕМ

    Все значения, которые navigation.py кладёт внутрь <code>…</code>,
    проходят через html_escape. Проверка по ast: искать текстом
    бессмысленно — рядом лежат комментарии с примерами.
"""
import ast
from pathlib import Path

import pytest

NAVIGATION = Path("app/handlers/callbacks/navigation.py")


def _code_block_fstrings(path: Path):
    """[(строка, [выражения внутри <code>…</code>])] для f-строк файла."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        rendered = "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "\0"
            for v in node.values
        )
        if "<code>" not in rendered:
            continue
        exprs = [v for v in node.values if isinstance(v, ast.FormattedValue)]
        if exprs:
            out.append((node.lineno, exprs))
    return out


def test_navigation_has_such_a_screen():
    """Страховка от теста, который проходит, потому что искать нечего."""
    assert _code_block_fstrings(NAVIGATION), (
        "в navigation.py больше нет подстановки в <code> — проверка стала пустой"
    )


@pytest.mark.parametrize("path", [NAVIGATION], ids=lambda p: str(p))
def test_every_value_in_code_block_is_escaped(path):
    unescaped = []
    for lineno, exprs in _code_block_fstrings(path):
        for expr in exprs:
            call = expr.value
            escaped = (
                isinstance(call, ast.Call)
                and (
                    getattr(call.func, "id", None) == "html_escape"
                    or getattr(call.func, "attr", None) in {"escape", "quote"}
                )
            )
            if not escaped:
                unescaped.append(f"{path}:{lineno} — {ast.unparse(expr.value)}")
    assert not unescaped, (
        f"значение уходит в <code> с parse_mode=HTML без экранирования, "
        f"один & в нём положит всё сообщение: {unescaped}"
    )
