"""Устойчивость отправки: лимиты Telegram, битая разметка, видимость отказов.

Три дефекта, все про то, что сообщение молча не доходит:

1. safe_send_message не знал про TelegramRetryAfter. Массовая рассылка идёт
   быстрее лимита (~30 сообщений в секунду), и без паузы бот получает
   временную блокировку — встаёт РАССЫЛКА ЦЕЛИКОМ, а не одна отправка.

2. При ошибке разметки (битый HTML, чужой premium-эмодзи) сообщение просто
   терялось. Содержание важнее оформления: человек должен получить хотя бы
   текст.

3. В админских рассылках ошибки перехватывались пустым `except Exception:`
   без логирования, а в рассылке про x2-кешбэк счётчик неудач даже не
   увеличивался — админ видел «Отправлено N/M» и не понимал, куда делись
   остальные.
"""
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from app.utils.telegram_safe import _strip_markup, safe_send_message


class FakeBot:
    """Бот, который отдаёт заранее заданные ошибки по очереди."""

    def __init__(self, errors=()):
        self.errors = list(errors)
        self.calls = []

    async def send_message(self, telegram_id, text, **kwargs):
        self.calls.append({"text": text, "parse_mode": kwargs.get("parse_mode")})
        if self.errors:
            raise self.errors.pop(0)
        return "sent"


@pytest.mark.asyncio
async def test_rate_limit_is_waited_out_and_retried():
    bot = FakeBot([TelegramRetryAfter(method=None, message="Flood", retry_after=0)])
    result = await safe_send_message(bot, 1, "привет")
    assert result == "sent"
    assert len(bot.calls) == 2, "после ожидания отправка не повторилась"


@pytest.mark.asyncio
async def test_rate_limit_twice_gives_up_instead_of_looping():
    """Бесконечные ретраи прячут неверный темп рассылки."""
    bot = FakeBot([
        TelegramRetryAfter(method=None, message="Flood", retry_after=0),
        TelegramRetryAfter(method=None, message="Flood", retry_after=0),
    ])
    assert await safe_send_message(bot, 1, "привет") is None
    assert len(bot.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "Bad Request: can't parse entities",
    "Bad Request: unsupported start tag",
    "Bad Request: invalid custom emoji identifier",
])
async def test_markup_error_falls_back_to_plain_text(message):
    bot = FakeBot([TelegramBadRequest(method=None, message=message)])
    result = await safe_send_message(bot, 1, "<b>жирный</b> текст")
    assert result == "sent"
    assert len(bot.calls) == 2
    second = bot.calls[-1]
    assert second["parse_mode"] is None
    assert "<b>" not in second["text"], "теги остались бы видны человеку"
    assert "жирный текст" == second["text"]


@pytest.mark.asyncio
async def test_successful_send_does_not_retry():
    bot = FakeBot()
    assert await safe_send_message(bot, 1, "ок") == "sent"
    assert len(bot.calls) == 1


@pytest.mark.asyncio
async def test_blocked_user_is_not_retried():
    """Заблокировавший бота не станет доступнее от повтора."""
    bot = FakeBot([TelegramForbiddenError(method=None, message="Forbidden: bot was blocked")])
    assert await safe_send_message(bot, 1, "текст") is None
    assert len(bot.calls) == 1


def test_strip_markup_keeps_the_content():
    assert _strip_markup("<b>жирный</b> <i>курсив</i>") == "жирный курсив"
    assert _strip_markup("без тегов") == "без тегов"


def test_broadcast_delivery_logs_its_failures():
    """Пустой except прячет причину: заблокирован бот или битый HTML —
    разные вещи, вторая ломает рассылку всем.

    Дефект 3 жил в app/handlers/admin/notifications.py — экран удалён,
    рассылки идут через дашборд. Но слой доставки под ними тот же, и
    молчаливый перехват в нём стоит ровно столько же: счётчик покажет
    «отправлено N из M», а куда делись остальные — не узнает никто.
    """
    src = Path("app/services/broadcast_delivery.py").read_text(encoding="utf-8")
    assert "BROADCAST_SEND_FAILED" in src, "неудача отправки нигде не логируется"
    assert "except Exception:\n" not in src, "остался перехват без причины"
