"""Мёртвый обработчик corporate_access_confirm не должен вернуться.

Дефект: в app/handlers/payments/callbacks.py жил обработчик
callback_corporate_access_confirm — 100 строк, которые не мог выполнить ни
один пользователь. Недостижимость была двойная:

  1) ни одна клавиатура во всём репозитории (включая dashboard/ на
     TypeScript) не создавала кнопку с callback_data
     "corporate_access_confirm";
  2) обработчик стоял под StateFilter(CorporateAccessRequest.waiting_for_confirmation),
     а это состояние нигде не выставлялось.

Почему это важно, хотя пользователь ничего не терял: мёртвый код читается
как живой. Разработчик, правя корпоративный сценарий, тратит время на ветку,
которая не исполняется, и — хуже — может решить, что заявки админу
отправляются, хотя их не бывает.

Живой корпоративный сценарий сейчас другой: "corporate_access_request"
(каталог бизнес-тарифов) → "tariff:biz_*" → выбор страны → обычная оплата.
"""
import re
from pathlib import Path

import pytest

CALLBACKS = Path("app/handlers/payments/callbacks.py")
STATES = Path("app/handlers/common/states.py")

# Строки в коде, а не в комментариях: комментарий-надгробие оставлен намеренно.
_CODE_LINES = [
    line for line in CALLBACKS.read_text(encoding="utf-8").splitlines()
    if not line.lstrip().startswith("#")
]
_CODE = "\n".join(_CODE_LINES)


def test_handler_is_gone():
    assert "callback_corporate_access_confirm" not in _CODE
    assert "corporate_access_confirm" not in _CODE


def test_tombstone_comment_explains_what_was_here():
    """Комментарий на месте удалённого кода — чтобы не восстановили вслепую."""
    src = CALLBACKS.read_text(encoding="utf-8")
    assert "corporate_access_confirm" in src, "надгробие потеряно"
    assert "buy.corporate_request_accepted" in src, (
        "не указано, где лежит текст ответа — восстанавливать будет дороже"
    )


def test_live_corporate_entry_point_survived():
    """Удаляли только мёртвый шаг: вход в бизнес-каталог остаётся живым."""
    assert 'F.data == "corporate_access_request"' in _CODE
    assert 'callback_data="corporate_access_request"' in _CODE


def test_no_button_leads_to_corporate_confirm():
    """Сторож: если кнопку когда-нибудь добавят, обработчика под неё нет.

    Ищем по всему дереву — и по Python, и по фронтенду дашборда.
    """
    roots = [Path("app"), Path("dashboard/src"), Path("database")]
    hits = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".js", ".jsx"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith(("#", "//", "*")):
                    continue
                if "corporate_access_confirm" in line:
                    hits.append(f"{path}:{lineno}")
    assert not hits, (
        f"callback_data corporate_access_confirm снова в коде, а обработчика нет: {hits}"
    )


def test_unused_corporate_state_is_not_imported_by_payments_callbacks():
    """CorporateAccessRequest больше не тянется в платежи.

    Класс в states.py оставлен: удаление состояний — отдельная тема, и
    неиспользуемый класс безвреден. Вредно было держать импорт, из-за
    которого казалось, что сценарий работает.
    """
    assert "CorporateAccessRequest" not in _CODE
    assert "class CorporateAccessRequest" in STATES.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["Bot", "sanitize_display_name"])
def test_imports_left_over_from_removed_handler_are_gone(name):
    """Импорты, которые держал только удалённый обработчик."""
    assert not re.search(rf"^from .*import .*\b{name}\b", _CODE, re.M), (
        f"{name} импортируется, хотя больше не используется"
    )
