"""Выдача доступа из дашборда обязана уведомлять человека.

ЧТО СЛОМАЛОСЬ И ПОЧЕМУ ЭТО ВАЖНО

    Пока выдача жила в боте (app/handlers/admin/access_grant.py),
    уведомление отправлял тот же экран: «Вам выдан доступ на N дней»,
    ключ и дата окончания, на языке получателя.

    Экран удалён — выдача идёт через веб-дашборд. Вместе с экраном
    уведомление исчезло: доступ появлялся в БД, а человек об этом не
    узнавал и не получал ключ. Тихая потеря: ни ошибки, ни лога — просто
    клиент, которому «выдали», сидит без доступа.

ЧЕГО ЗДЕСЬ ПРОВЕРЯЕМ ТРИ ВЕЩИ

    1. уведомление вообще отправляется;
    2. отчёт notify_sent строится по ФАКТУ отправки, а не по намерению —
       иначе админ читает «уведомлён» про человека, который заблокировал
       бота, и не связывается с ним другим способом;
    3. неудачная отправка не роняет запрос: доступ уже в БД, откатывать
       его из-за молчания Telegram нельзя.
"""
import pytest

from app.api.dashboard.routes import users as users_route


class _FakeBot:
    """Бот, который либо принимает отправку, либо отказывает."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent = []

    async def send_message(self, telegram_id, text, **kwargs):
        if self.fail:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.sent.append((telegram_id, text))
        return "message"


@pytest.fixture
def patched(monkeypatch):
    """Подменяем всё внешнее: бота, язык и сам отправитель."""
    from app.api import telegram_webhook

    bot = _FakeBot()
    monkeypatch.setattr(telegram_webhook, "_bot", bot, raising=False)

    async def _language(_tg):
        return "ru"

    from app.services import language_service
    monkeypatch.setattr(language_service, "resolve_user_language", _language)

    from app.utils import telegram_safe

    async def _send(b, tg, text, **kwargs):
        return await b.send_message(tg, text, **kwargs)

    monkeypatch.setattr(telegram_safe, "safe_send_message", _send)
    return bot


@pytest.mark.asyncio
async def test_user_is_told_about_the_grant(patched):
    from datetime import datetime

    ok = await users_route._notify_granted(
        555, 30, "units.days", "vless://key", datetime(2026, 9, 1),
    )
    assert ok is True, "уведомление о выдаче не отправлено"

    telegram_id, text = patched.sent[-1]
    assert telegram_id == 555
    assert "30" in text, "не сказано, на сколько выдан доступ"
    assert "vless://key" in text, "ключ не дошёл — доступ бесполезен"
    assert "01.09.2026" in text, "не сказано, до какого числа"
    assert "{" not in text, "остались неподставленные плейсхолдеры"


@pytest.mark.asyncio
async def test_report_reflects_actual_delivery(monkeypatch):
    """«Уведомлён» должно означать, что отправка состоялась."""
    from datetime import datetime
    from app.api import telegram_webhook

    monkeypatch.setattr(telegram_webhook, "_bot", _FakeBot(fail=True), raising=False)

    ok = await users_route._notify_granted(
        555, 30, "units.days", "vless://key", datetime(2026, 9, 1),
    )
    assert ok is False, "отчёт строится по намерению, а не по факту"


@pytest.mark.asyncio
async def test_missing_bot_does_not_raise(monkeypatch):
    """Бот может быть ещё не поднят. Выдача уже в БД — падать нельзя."""
    from datetime import datetime
    from app.api import telegram_webhook

    monkeypatch.setattr(telegram_webhook, "_bot", None, raising=False)

    assert await users_route._notify_granted(
        555, 30, "units.days", "k", datetime(2026, 9, 1),
    ) is False


def test_grant_endpoints_report_notify_sent():
    """Флаг обязан быть в ответе — иначе админ не узнает о неудаче."""
    import inspect

    src = inspect.getsource(users_route)
    for endpoint in ("async def user_grant(", "async def user_grant_minutes("):
        block = src[src.index(endpoint):]
        block = block[: block.index("\n\n\n")]
        assert "_notify_granted" in block, f"{endpoint}: уведомление не отправляется"
        assert '"notify_sent"' in block, f"{endpoint}: результат не виден админу"


def test_notification_is_localized():
    """Раньше текст собирался русской f-строкой независимо от языка."""
    import inspect

    src = inspect.getsource(users_route._notify_granted)
    assert "resolve_user_language(telegram_id)" in src, "язык получателя не запрашивается"
    assert "admin.user_granted_access" in src


@pytest.mark.parametrize("lang", ["ru", "en", "de", "ar", "kk", "tj", "uz"])
def test_unit_keys_exist_in_every_locale(lang):
    """Единицы времени подставляются отдельным ключом — пустой перевод
    дал бы «Вам выдан доступ на 30 units.days»."""
    from app.i18n import get_text

    for key in ("units.days", "units.minutes"):
        value = get_text(lang, key)
        assert value and value != key, f"{lang}: нет перевода {key}"
