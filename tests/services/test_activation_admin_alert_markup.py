"""Админский алерт об окончательном провале активации обязан дойти.

Дефект: сообщение собиралось из ключей admin.activation_error_* и уходило с
parse_mode="Markdown", а текст исключения подставлялся внутрь обратных кавычек
как есть. Сообщения HTTP-клиентов сплошь и рядом содержат '_', '*' и backtick
('connect_timeout', 'field *uuid* invalid'). Telegram не разбирал такую
разметку, отвечал BadRequest, safe_send_message молча возвращал None — и админ
НЕ узнавал, что оплаченная подписка окончательно помечена failed. Деньги
получены, доступ не выдан, сигнала нет.
"""
import inspect
from html.parser import HTMLParser

import pytest

import activation_worker as aw


class _Balance(HTMLParser):
    """Считает теги: непарный тег — это ровно то, на чём падал Telegram."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.broken = False

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.broken = True


def _tags_balanced(text: str) -> bool:
    parser = _Balance()
    parser.feed(text)
    return not parser.broken and not parser.stack


HOSTILE = [
    "connect_timeout while calling panel",
    "field *uuid* invalid",
    "unexpected ` in response",
    "<script>alert(1)</script>",
    "**bold** and _italic_ and `code`",
]


@pytest.mark.parametrize("error_msg", HOSTILE)
def test_error_text_never_breaks_markup(error_msg):
    """Любой текст ошибки даёт валидный HTML с парными тегами."""
    line = aw._admin_line("ru", "admin.activation_error_error", error_msg=error_msg)
    assert _tags_balanced(line), f"разметка сломана текстом ошибки: {error_msg!r}"


@pytest.mark.parametrize("error_msg", HOSTILE)
def test_error_text_is_escaped_not_interpreted(error_msg):
    """Спецсимволы HTML экранируются, а не уезжают в Telegram как разметка."""
    line = aw._admin_line("ru", "admin.activation_error_error", error_msg=error_msg)
    assert "<script>" not in line
    if "<" in error_msg:
        assert "&lt;" in line


def test_markdown_markup_converted_to_html():
    """Ключи писались под Markdown — в сообщении не должно остаться его следов."""
    title = aw._admin_line("ru", "admin.activation_error_title")
    assert "**" not in title, "звёздочки Markdown уедут в HTML как текст"
    assert "<b>" in title and "</b>" in title

    status = aw._admin_line("ru", "admin.activation_error_status")
    assert "`" not in status
    assert "<code>" in status


def test_conversion_happens_before_substitution():
    """Разметку разбираем на шаблоне, а не на готовой строке.

    Если конвертировать после подстановки, обратная кавычка внутри текста
    ошибки снова породит непарный <code> — то есть исходный дефект.
    """
    line = aw._admin_line(
        "ru", "admin.activation_error_error", error_msg="broken ` backtick",
    )
    assert line.count("<code>") == 1 and line.count("</code>") == 1


def test_broken_translation_does_not_swallow_the_alert():
    """Опечатка в плейсхолдере перевода не должна оставлять админа без сигнала."""
    line = aw._admin_line("ru", "admin.activation_error_error", wrong_kwarg="x")
    assert line, "алерт схлопнулся из-за перевода"


def test_admin_alert_is_sent_as_html():
    """Сам вызов отправки переведён на HTML — Markdown больше не используется."""
    src = inspect.getsource(aw.process_pending_activations)
    admin_block = src[src.index("admin_message = ("):]
    admin_block = admin_block[: admin_block.index("except Exception as admin_error")]
    assert 'parse_mode="HTML"' in admin_block
    assert 'parse_mode="Markdown"' not in admin_block
