"""Замаскированный пароль Spotify не должен ломать HTML-разметку.

Дефект: _mask_password подставлял первый и последний символы пароля в
<code> как есть. Пароль, начинающийся с «<» или содержащий «&» на краю,
делал сообщение невалидным HTML — Telegram отвечал ошибкой парсинга, и
человек вместо экрана подтверждения получал молчание бота. Такие символы в
паролях встречаются: их специально советуют добавлять для стойкости.

Email в этих же экранах безопасен по конструкции: _EMAIL_RE не пропускает
< > & вовсе.
"""
import pytest

from app.handlers.payments.spotify_purchase import _mask_password


@pytest.mark.parametrize("pwd, expected", [
    ("<script>abc", "&lt;" + "•" * 9 + "c"),
    ("a&bcdefgh&", "a" + "•" * 8 + "&amp;"),
    ('"quoted"x', "&quot;" + "•" * 7 + "x"),
])
def test_edges_are_escaped(pwd, expected):
    assert _mask_password(pwd) == expected


def test_ordinary_password_unchanged_in_shape():
    """Маскировка не должна менять смысл: первый и последний видны."""
    assert _mask_password("SuperSecret1") == "S" + "•" * 10 + "1"


def test_short_and_empty_passwords():
    assert _mask_password("") == "—"
    assert _mask_password("<") == "•"
    assert _mask_password("<>") == "••"


def test_masked_length_matches_password_length():
    """Длина маски — часть смысла экрана: человек сверяет число символов."""
    pwd = "abcdefghij"
    masked = _mask_password(pwd)
    assert masked.count("•") == len(pwd) - 2
