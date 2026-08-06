"""Выдача ключа человеку оставляет след.

ЧТО БЫЛО СЛОМАНО

    В `app/handlers/callbacks/connect_guide/keys.py`, `.../qr.py` и
    `app/handlers/user/connect.py` не было НИ ОДНОЙ записи в лог. Между тем
    это самое частое обращение в поддержку: человек с активной оплаченной
    подпиской открывает экран подключения и видит «пока нечего подключать».

    Ссылку отдаёт `get_user_primary_subscription_url`, которая возвращает
    None молча в пяти разных случаях (Remnawave выключен, пул недоступен,
    строки подписки нет, remnawave_premium_uuid пуст, панель не отдала
    subscriptionUrl). Экран на всё это отвечал одинаково и молча, поэтому
    «ключа нет в базе», «панель не ответила» и «человек смотрит не туда»
    были неразличимы, а разбор сводился к просьбе повторить действие.

ЧТО ПРОВЕРЯЕТСЯ

    Пустой экран у человека С активной подпиской — ERROR (это инцидент:
    оплачено, не выдано). Пустой экран БЕЗ подписки — INFO (штатный путь
    из меню помощи). Разный уровень здесь принципиален: если оба случая
    писать одинаково, ERROR-поток забьётся людьми, которые просто читают
    инструкцию, и настоящие инциденты в нём утонут.
"""
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.handlers.callbacks.connect_guide import keys as keys_mod
from app.handlers.user import connect as connect_mod


ACTIVE_SUB = {
    "telegram_id": 777,
    "status": "active",
    "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    "subscription_type": "plus",
}


def _callback(data: str, telegram_id: int = 777):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = telegram_id
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.delete = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.bot = MagicMock()
    cb.bot.send_message = AsyncMock()
    cb.bot.send_photo = AsyncMock()
    return cb


@pytest.fixture
def keys_env(monkeypatch):
    """Экран ключей без базы, панели и Telegram."""
    import app.services.user_subscription_links as links_mod

    monkeypatch.setattr(keys_mod, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(keys_mod, "safe_edit_text", AsyncMock())
    monkeypatch.setattr(keys_mod, "_get_photo_id", lambda _k: "")
    # Обход отключаем: он живёт отдельно от подписки и к проверяемым
    # веткам отношения не имеет.
    monkeypatch.setattr(keys_mod.config, "REMNAWAVE_ENABLED", False)
    monkeypatch.setattr(keys_mod.config, "PUBLIC_BASE_URL", "https://x.example")
    return links_mod


async def _run_step2(monkeypatch, keys_env, subscription, sub_url):
    monkeypatch.setattr(
        keys_mod.database, "get_subscription", AsyncMock(return_value=subscription),
    )
    monkeypatch.setattr(
        keys_env, "get_user_primary_subscription_url", AsyncMock(return_value=sub_url),
    )
    await keys_mod.callback_setup_step2(_callback("setup_step2:ios"))


@pytest.mark.asyncio
async def test_empty_screen_for_paying_user_is_an_error(monkeypatch, keys_env, caplog):
    """«Заплатил, а бот говорит нечего подключать» обязано быть в логе."""
    with caplog.at_level(logging.INFO):
        await _run_step2(monkeypatch, keys_env, ACTIVE_SUB, None)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "пустой экран у оплатившего человека не оставил записи"
    msg = errors[0].getMessage()
    assert "CONNECT_KEYS_EMPTY_FOR_ACTIVE" in msg
    assert "777" in msg, "в записи нет telegram_id — связать с человеком нечем"
    assert "ios" in msg, "в записи нет платформы"


@pytest.mark.asyncio
async def test_empty_screen_without_subscription_is_not_an_error(
    monkeypatch, keys_env, caplog,
):
    """Экран достижим из меню помощи без всякой подписки. Если писать это
    ERROR-ом, настоящие инциденты утонут в потоке читателей инструкции."""
    with caplog.at_level(logging.INFO):
        await _run_step2(monkeypatch, keys_env, None, None)

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "штатный экран покупки записан как инцидент"
    )
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("CONNECT_KEYS_EMPTY" in m for m in infos), (
        "открытие экрана без подписки не записано вовсе"
    )


@pytest.mark.asyncio
async def test_successful_issue_is_recorded_without_the_key_itself(
    monkeypatch, keys_env, caplog,
):
    """Успех нужен, чтобы отличить «ключ не показали» от «показали, а человек
    не нашёл». Сама ссылка — секрет и в запись попадать не должна."""
    secret = "https://panel.example.com/api/sub/TOP_SECRET_TOKEN"
    with caplog.at_level(logging.INFO):
        await _run_step2(monkeypatch, keys_env, ACTIVE_SUB, secret)

    shown = [r.getMessage() for r in caplog.records if "CONNECT_KEYS_SHOWN" in r.getMessage()]
    assert shown, "успешная выдача ключа не записана"
    assert "TOP_SECRET_TOKEN" not in shown[0], (
        "подписочная ссылка утекла в запись об успехе"
    )
    assert "premium=True" in shown[0]


@pytest.mark.asyncio
async def test_success_is_recorded_only_after_the_message_leaves(
    monkeypatch, keys_env, caplog,
):
    """Запись утверждает «человек увидел ключи». Если отправка упала,
    записи быть не должно — иначе она врёт."""
    cb = _callback("setup_step2:ios")
    cb.bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    monkeypatch.setattr(
        keys_mod.database, "get_subscription", AsyncMock(return_value=ACTIVE_SUB),
    )
    monkeypatch.setattr(
        keys_env, "get_user_primary_subscription_url",
        AsyncMock(return_value="https://panel.example.com/api/sub/T"),
    )

    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError):
            await keys_mod.callback_setup_step2(cb)

    assert not [r for r in caplog.records if "CONNECT_KEYS_SHOWN" in r.getMessage()], (
        "записан показ ключей, которых человек не получил"
    )


@pytest.mark.asyncio
async def test_manual_screen_without_keys_is_recorded(monkeypatch, keys_env, caplog):
    """У ручной установки ветки «нечего подключать» нет: экран молча
    вырождается в голую инструкцию. Для оплатившего это тот же инцидент."""
    monkeypatch.setattr(
        keys_mod.database, "get_subscription", AsyncMock(return_value=ACTIVE_SUB),
    )
    monkeypatch.setattr(
        keys_env, "get_user_primary_subscription_url", AsyncMock(return_value=None),
    )

    with caplog.at_level(logging.INFO):
        await keys_mod.callback_setup_manual(_callback("setup_manual:ios"))

    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("CONNECT_MANUAL_KEYS_EMPTY_FOR_ACTIVE" in m for m in errors), (
        "инструкция без единого ключа у оплатившего человека не записана"
    )


@pytest.mark.asyncio
async def test_hwadd_without_subscription_is_recorded(monkeypatch, caplog):
    """/hwadd — вторая дверь к ключу. Отказ на ней тоже был бесследным."""
    monkeypatch.setattr(connect_mod, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(
        connect_mod.database, "get_subscription", AsyncMock(return_value=None),
    )
    message = MagicMock()
    message.chat.type = "private"
    message.from_user.id = 777
    message.answer = AsyncMock()

    with caplog.at_level(logging.INFO):
        await connect_mod.cmd_hwadd(message)

    assert any(
        "HWADD_NO_SUBSCRIPTION" in r.getMessage() for r in caplog.records
    ), "отказ /hwadd не оставил записи"


def test_qr_screen_also_names_the_incident():
    """QR рисуется из той же ссылки и той же веткой отказа. Пропустить его —
    значит оставить дыру ровно того же класса на соседнем экране."""
    import inspect

    from app.handlers.callbacks.connect_guide import qr as qr_mod

    src = inspect.getsource(qr_mod.callback_setup_qr_app)
    assert "CONNECT_QR_EMPTY_FOR_ACTIVE" in src
    assert "CONNECT_QR_EMPTY" in src
