"""Экран несостоявшихся платежей не отдаёт наружу лишнего.

ЧТО БЫЛО

    `SELECT pe.*` в get_recent_payment_errors возвращал ВСЕ колонки
    payment_errors, включая raw_payload — тело вебхука провайдера целиком,
    до 8000 символов JSON с подписями запроса и служебными полями.

    На экране эту колонку никто не показывал, то есть польза от неё была
    нулевая, а ехала она в браузер администратора и оседала в логах
    прокси по дороге.

    Рядом та же беда с error_message: он пишется из текста исключения, а в
    исключение попадает URL метода Telegram вместе с токеном бота.

ПОЧЕМУ ТЕСТ, А НЕ ПРОСТО ПРАВКА

    `SELECT *` опасен не тем, что отдаёт лишнее сегодня, а тем, что начнёт
    отдавать колонку, которую добавят в таблицу завтра. Никто при этом не
    вспомнит, что она уезжает на экран.
"""
import inspect
import re

import pytest

import database.analytics_payments as payments


def _source() -> str:
    """Только исполняемый код: без докстринга и без комментариев.

    Иначе тест ловит сам себя — в объяснении рядом с запросом написано
    «раньше здесь стоял SELECT pe.*», и проверка на эту строку падает на
    правильном коде.
    """
    src = inspect.getsource(payments.get_recent_payment_errors)
    # Докстринг функции — первый тройной блок кавычек.
    parts = src.split('"""')
    if len(parts) >= 3:
        src = parts[0] + '"""'.join(parts[2:])
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )


def test_the_webhook_body_is_not_selected():
    assert "raw_payload" not in _source(), (
        "raw_payload снова уезжает на экран: это тело вебхука провайдера "
        "целиком, с подписями запроса"
    )


def test_columns_are_listed_explicitly():
    """Иначе новая колонка начнёт отдаваться сама, молча."""
    assert not re.search(r"SELECT\s+pe\.\*", _source()), (
        "вернулся SELECT pe.* — любая колонка, добавленная в таблицу, "
        "поедет в браузер вместе с ней"
    )


def test_the_columns_the_screen_needs_are_still_there():
    """Страховка от обратной ошибки: вырезали лишнее вместе с нужным."""
    src = _source()
    for column in (
        "pe.id", "pe.telegram_id", "pe.purchase_id", "pe.payment_provider",
        "pe.amount_rubles", "pe.stage", "pe.error_code", "pe.error_message",
        "pe.created_at",
    ):
        assert column in src, f"из выборки пропала {column}"


def test_the_error_text_is_scrubbed():
    src = _source()
    assert "scrub_secrets(" in src, (
        "текст исключения уходит на экран как есть — вместе с URL метода "
        "Telegram и токеном бота в нём"
    )


@pytest.mark.parametrize("raw,leak", [
    ("https://api.telegram.org/bot123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/sendMessage",
     "AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
])
def test_the_scrubber_actually_removes_the_token(raw, leak):
    """Вызов на месте — ещё не защита, если функция ничего не делает."""
    from app.utils.security import scrub_secrets

    out = scrub_secrets(raw, limit=1000)
    assert leak not in out, f"токен бота остался в тексте: {out}"


def test_the_scrubber_is_shared_with_the_summary_screen():
    """Две копии разошлись бы на первом же добавленном шаблоне.

    Разошлись бы молча: пропущенный секрет виден только тому, кто открыл
    экран.
    """
    from app.utils.security import scrub_secrets
    from database.dashboard_summary import _scrub_secrets

    assert _scrub_secrets is scrub_secrets


def test_the_payments_error_text_keeps_enough_to_diagnose():
    """Обрезать причину до строчки — оставить разбор без причины.

    На сводке предел 200 символов: там текст ошибки лишь помечает, что
    что-то не так. Здесь он единственный способ понять, почему платёж не
    прошёл.
    """
    src = _source()
    assert "limit=1000" in src, (
        "предел текста ошибки на экране платежей снова короткий"
    )
