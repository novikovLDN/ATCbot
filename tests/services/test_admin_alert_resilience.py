"""send_alert — последний рубеж, он не имеет права падать сам.

Два дефекта:

1. NameError на пути повторной попытки. full_message собиралcя внутри try.
   Если падало само формирование (например, category приходила не строкой и
   category.upper() бросал AttributeError), в ветке повтора имени
   full_message просто не существовало. Второй вызов падал с NameError, тот
   гасился внешним except, и в логе оставалось ADMIN_ALERT_RETRY_FAILED с
   причиной, не имеющей отношения к делу. Настоящая ошибка терялась —
   а это последнее, что должно теряться в аварийном пути.

2. Кулдаун прятал масштаб. При аварии первый алерт уходил, следующие сто
   молча возвращали False. Админ видел одну строку про один сбойный платёж
   и не понимал, что упало всё. Теперь подавленные считаются и попадают
   в следующее сообщение.
"""
import asyncio

import pytest

from app.services import admin_alerts


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Кулдаун и счётчики — модульное состояние, между тестами его чистим."""
    monkeypatch.setattr(admin_alerts, "_last_alert_at", {})
    monkeypatch.setattr(admin_alerts, "_suppressed_since_last", {})
    # Иначе каждая проверка повтора стоила бы две секунды реального времени.
    monkeypatch.setattr(admin_alerts, "_RETRY_DELAY_SECONDS", 0)


class _Bot:
    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.sent = []

    async def send_message(self, chat_id, text):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("Telegram 502")
        self.sent.append(text)


@pytest.mark.asyncio
async def test_alert_sent_on_first_try():
    bot = _Bot()
    assert await admin_alerts.send_alert(bot, "payment", "упал платёж") is True
    assert "упал платёж" in bot.sent[0]


@pytest.mark.asyncio
async def test_retry_sends_the_same_message_not_nameerror():
    """Главный регресс: повтор использует уже собранный текст."""
    bot = _Bot(fail_times=1)
    assert await admin_alerts.send_alert(bot, "payment", "упал платёж") is True
    assert len(bot.sent) == 1
    assert "упал платёж" in bot.sent[0]


@pytest.mark.asyncio
async def test_bad_category_does_not_raise_and_does_not_hide_the_error():
    """Нестроковая категория раньше давала NameError на повторе.

    Сейчас заголовок собирается до try, сообщение уходит, и в логе видна
    настоящая причина, если что-то пойдёт не так.
    """
    bot = _Bot()
    result = await admin_alerts.send_alert(bot, 12345, "странный вызов", force=True)
    assert result is True
    assert "странный вызов" in bot.sent[0]


@pytest.mark.asyncio
async def test_permanent_failure_returns_false_without_raising():
    bot = _Bot(fail_times=99)
    assert await admin_alerts.send_alert(bot, "payment", "упал платёж") is False
    assert bot.sent == []


@pytest.mark.asyncio
async def test_missing_bot_is_not_a_crash_and_costs_no_retry_delay(monkeypatch):
    """bot=None раньше уходил в общий except, спал и падал повторно тем же."""
    monkeypatch.setattr(admin_alerts, "_RETRY_DELAY_SECONDS", 60)
    assert await admin_alerts.send_alert(None, "payment", "нет бота") is False


@pytest.mark.asyncio
async def test_cooldown_counts_suppressed_and_reports_them_next_time():
    """Масштаб аварии виден: следующий алерт несёт число проглоченных."""
    bot = _Bot()
    assert await admin_alerts.send_alert(bot, "payment", "сбой 1") is True

    for _ in range(5):
        assert await admin_alerts.send_alert(bot, "payment", "ещё сбой") is False
    assert admin_alerts._suppressed_since_last["payment"] == 5

    # Кулдаун истёк — следующий алерт обязан рассказать про подавленные.
    admin_alerts._last_alert_at["payment"] = 0.0
    assert await admin_alerts.send_alert(bot, "payment", "сбой 7") is True
    assert "5" in bot.sent[1]
    assert "payment" not in admin_alerts._suppressed_since_last


@pytest.mark.asyncio
async def test_suppressed_counter_survives_failed_delivery():
    """Недоставленный алерт тоже считается подавленным.

    Иначе авария на N событий отразилась бы числом меньше N — и админ
    недооценил бы масштаб ровно на потерянные сообщения.
    """
    bot = _Bot(fail_times=99)
    assert await admin_alerts.send_alert(bot, "payment", "сбой") is False
    assert admin_alerts._suppressed_since_last["payment"] == 1


@pytest.mark.asyncio
async def test_security_alerts_never_rate_limited():
    """У security кулдаун нулевой: каждое событие должно дойти."""
    bot = _Bot()
    for i in range(3):
        assert await admin_alerts.send_alert(bot, "security", f"событие {i}") is True
    assert len(bot.sent) == 3


@pytest.mark.asyncio
async def test_long_message_is_truncated_before_send():
    """Telegram рвёт связь на длинном сообщении — режем сами."""
    bot = _Bot()
    assert await admin_alerts.send_alert(bot, "payment", "я" * 9000) is True
    assert len(bot.sent[0]) <= 4000


@pytest.mark.asyncio
async def test_cancellation_during_retry_is_not_swallowed():
    """Остановку воркера не глушим: иначе shutdown зависает."""
    class _CancelBot:
        async def send_message(self, chat_id, text):
            raise RuntimeError("Telegram 502")

    async def _cancel(_delay):
        raise asyncio.CancelledError()

    original = asyncio.sleep
    asyncio.sleep = _cancel
    try:
        with pytest.raises(asyncio.CancelledError):
            await admin_alerts.send_alert(_CancelBot(), "payment", "сбой")
    finally:
        asyncio.sleep = original
