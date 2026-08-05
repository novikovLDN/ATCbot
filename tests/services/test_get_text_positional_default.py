"""Третий позиционный аргумент get_text — это запасной ТЕКСТ, а не флаг.

История дефекта. Раньше сигнатура была get_text(language, key, strict=False),
и сотня вызовов вида get_text(lang, key, "Запасной текст") молча теряла свой
запасной текст: строка попадала в strict, приводилась к True и никак не
влияла на результат. Пользователь при пропавшем ключе видел сырой ключ.

Сигнатуру починили (третий параметр стал default), но осталось наследство:
в некоторых вызовах третьим аргументом лежат не тексты, а бывшие имена
ключей — 'buy_renew_button', 'profile', 'support_button'. Раньше их
игнорировали, теперь они стали настоящим запасным текстом. Если ключ
когда-нибудь пропадёт, на кнопке появится 'buy_renew_button'. Это хуже
пустого места: выглядит как утечка внутренностей.

Тест сторожит платёжные экраны: там таких огрызков быть не должно.
Полный список остальных мест собран отдельно и чинится владельцем.
"""
import ast
from pathlib import Path

import pytest

from app.i18n import get_text

# Файлы, приведённые в порядок. Список намеренно узкий: расширять по мере
# того, как остальные вызовы разберут.
CLEANED = [
    Path("app/handlers/payments/payments_messages.py"),
    # Экраны покупки — пакет; проверяем каждый его модуль.
    *sorted(Path("app/handlers/payments/callbacks").glob("*.py")),
    Path("app/handlers/payments/telegram_stars_purchase.py"),
]

_NAMES = {"get_text", "i18n_get_text"}


def _calls_with_positional_default(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
        if name in _NAMES and len(node.args) >= 3:
            hits.append((node.lineno, ast.unparse(node)[:120]))
    return hits


@pytest.mark.parametrize("path", CLEANED, ids=lambda p: p.name)
def test_payment_screens_do_not_pass_positional_default(path):
    hits = _calls_with_positional_default(path)
    assert not hits, (
        "третий позиционный аргумент get_text в платёжном экране "
        f"(станет видимым текстом при пропаже ключа): {hits}"
    )


def test_positional_default_is_really_used_as_text():
    """Проверяем сам контракт, а не только вызовы.

    Если кто-то вернёт strict третьим параметром, тесты выше пройдут, а
    поведение сломается — поэтому фиксируем и контракт.
    """
    assert get_text("ru", "нет.такого.ключа", "Запасной текст") == "Запасной текст"
    assert get_text("ru", "нет.такого.ключа") == "нет.такого.ключа"


def test_keys_used_on_payment_screens_exist_everywhere():
    """Огрызки убрали — значит ключи обязаны быть во всех языках."""
    from app.i18n import LANGUAGES

    for key in ("buy.renew_button", "main.support_button", "main.profile"):
        missing = [lang for lang, d in LANGUAGES.items() if key not in d]
        assert not missing, f"{key} отсутствует в языках: {missing}"
