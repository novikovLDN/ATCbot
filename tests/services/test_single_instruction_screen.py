"""Инструкция в боте должна быть одна и достижимая.

ЧТО БЫЛО

    Экранов инструкции было два.

    Первый (menu_instruction / instruction) — страница из одной строки
    текста и кнопки в мини-приложение. В меню кнопки на неё не было:
    попасть можно было только командой /instruction, которую надо знать,
    да из админского уведомления о перевыпуске ключа. Алиас instruction
    не выставлялся вообще ни одной кнопкой.

    Второй (connect_instruction) — настоящая пошаговая установка: выбор
    устройства, ссылки на приложения, ключи, QR-код, диплинки. Он-то и
    стоит в меню.

    Человек, нажавший «Инструкции по сервису» и человек, набравший
    /instruction, попадали в разные места и видели разное.

ЧТО СТАЛО

    Остался пошаговый экран. Кнопка в мини-приложение переехала в него —
    визуальная инструкция никуда не делась. Команда /instruction и
    кнопка после перевыпуска ключа ведут туда же.
"""
import inspect
from pathlib import Path

import pytest


def test_only_one_instruction_screen_is_registered():
    """Две инструкции — два разных ответа на один вопрос."""
    from app.handlers import router as root

    sources = set()
    for path in Path("app/handlers").rglob("*.py"):
        sources.add(path.read_text(encoding="utf-8"))
    joined = "\n".join(sources)

    assert 'F.data == "menu_instruction"' not in joined, (
        "вернулся второй экран инструкции"
    )
    assert 'F.data == "instruction"' not in joined, (
        "вернулся алиас, на который не ведёт ни одна кнопка"
    )
    assert 'F.data == "connect_instruction"' in joined, (
        "пропал канонический экран инструкции"
    )
    assert root is not None


def test_command_and_button_open_the_same_screen():
    """Иначе /instruction снова показывает не то, что кнопка в меню."""
    from app.handlers.user import support
    from app.handlers.callbacks import connect_guide

    cmd = inspect.getsource(support.cmd_instruction)
    assert "_open_connect_screen" in cmd, "команда ведёт на другой экран"

    btn = inspect.getsource(connect_guide.callback_connect_instruction)
    assert "_open_connect_screen" in btn, "кнопка ведёт мимо общей функции"


def test_mini_app_guide_is_still_reachable():
    """Удаляя промежуточный экран, нельзя было потерять мини-приложение."""
    src = inspect.getsource(
        __import__("app.handlers.callbacks.connect_guide", fromlist=["x"])._open_connect_screen
    )
    assert "WebAppInfo" in src, "кнопка в мини-приложение потерялась"
    assert "startapp=guide" in src, "мини-приложение открывается не на инструкции"


def test_instruction_is_reachable_from_the_menu():
    """Смысл правки — чтобы экран нашли, а не знали команду наизусть."""
    keyboards = Path("app/handlers/common/keyboards.py").read_text(encoding="utf-8")
    assert keyboards.count('callback_data="connect_instruction"') >= 2, (
        "кнопка инструкции пропала из меню"
    )


def test_reissue_notification_points_at_the_live_screen():
    """После перевыпуска ключ надо добавить заново — нужен пошаговый экран."""
    src = Path("app/handlers/common/keyboards.py").read_text(encoding="utf-8")
    block = src[src.index("def get_reissue_notification_keyboard"):]
    block = block[: block.index("])")]
    # Смотрим только на сами кнопки: в докстринге функции menu_instruction
    # упоминается как раз для объяснения, почему его там больше нет.
    buttons = [ln for ln in block.split("\n") if "callback_data=" in ln]
    joined = "\n".join(buttons)
    assert "connect_instruction" in joined
    assert "menu_instruction" not in joined, "кнопка ведёт на удалённый экран"


def test_dead_screen_helpers_are_gone():
    """Экран удалён — вместе с ним функция и клавиатура."""
    from app.handlers.common import screens, keyboards

    assert not hasattr(screens, "_open_instruction_screen")
    assert not hasattr(keyboards, "get_instruction_keyboard")


def test_connect_screen_accepts_both_event_kinds():
    """Команда даёт Message, кнопка — CallbackQuery. Удалять нечего только
    в первом случае: сообщение принадлежит человеку, не боту."""
    from app.handlers.callbacks import connect_guide

    src = inspect.getsource(connect_guide._open_connect_screen)
    assert 'getattr(event, "message", None)' in src, (
        "экран снова требует CallbackQuery — команда упадёт"
    )


@pytest.mark.parametrize("lang", ["ru", "en", "de", "ar", "kk", "tj", "uz"])
def test_guide_button_label_exists_in_every_locale(lang):
    from app.i18n import get_text

    text = get_text(lang, "instruction._open_guide")
    assert text and text != "instruction._open_guide", (
        f"{lang}: подпись кнопки мини-приложения не переведена"
    )
