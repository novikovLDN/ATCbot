"""Экранирование имени пользователя для сообщений с parse_mode=HTML.

Дефект: sanitize_display_name чистила опасный Unicode, но не экранировала
HTML. Имя вида "<b>test" подставлялось в <b>{display_name}</b>, Telegram
отклонял сообщение целиком, и экран профиля не приходил вовсе.
"""
import pytest

from app.handlers.common.utils import sanitize_display_name


class TestSanitizeDisplayNameEscaping:
    def test_angle_brackets_escaped(self):
        assert "<" not in sanitize_display_name("<b>жирный")

    def test_ampersand_escaped(self):
        assert sanitize_display_name("Rock & Roll") == "Rock &amp; Roll"

    def test_closing_tag_escaped(self):
        out = sanitize_display_name("</b>")
        assert "<" not in out and ">" not in out

    def test_plain_name_unchanged(self):
        assert sanitize_display_name("Иван Петров") == "Иван Петров"

    def test_emoji_preserved(self):
        assert sanitize_display_name("Иван 🎮") == "Иван 🎮"

    def test_empty_stays_empty(self):
        assert sanitize_display_name("") == ""

    def test_whitespace_trimmed(self):
        assert sanitize_display_name("  Иван  ") == "Иван"

    @pytest.mark.parametrize("payload", [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<a href='http://evil'>клик</a>",
    ])
    def test_markup_never_survives(self, payload):
        out = sanitize_display_name(payload)
        assert "<" not in out, f"разметка прошла: {out}"
