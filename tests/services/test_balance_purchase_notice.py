"""Оплата с баланса: одно сообщение и честная статистика.

ДВА ДЕФЕКТА В ОДНОМ ЭКРАНЕ

1. При смене тарифа (Basic → Plus) человек получал два одинаковых
   сообщения подряд. Ветка апгрейда отправляла текст сама и не делала
   return, а ниже стоял общий блок отправки — тот же text, та же
   клавиатура.

2. Запись в статистику автоуведомлений делалась сразу после сборки
   текста, задолго до отправки, и статус брался из того, включил ли
   админ кастомный текст:

       status="sent" if _use_custom else "skipped_disabled"

   Получалось наоборот. Переопределение выключено — пишется
   skipped_disabled, хотя сообщение уходит всегда (это оговорено
   отдельным комментарием прямо там же). Переопределение включено —
   пишется sent, даже если отправка упала с исключением.

   Админ смотрел на статистику и видел не доставку, а настройку.
"""
import ast
import inspect
from pathlib import Path

import pytest

from app.handlers.callbacks import pay_balance


SRC = Path("app/handlers/callbacks/pay_balance.py").read_text(encoding="utf-8")
HANDLER = inspect.getsource(pay_balance.callback_pay_balance)


def test_success_message_is_sent_once():
    """Сколько бы веток ни собирало текст, отправка одна.

    Считаем именно отправки переменной `text` — той, что собирают ветки
    апгрейда, продления и первой покупки. Соседние отправки в этом же
    обработчике (ошибка списания, «активация в процессе») к делу не
    относятся: у них свои тексты и свои ранние return.
    """
    tree = ast.parse(SRC)
    handler = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "callback_pay_balance"
    )
    sends = [
        n for n in ast.walk(handler)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "answer"
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == "message"
        and n.args
        and isinstance(n.args[0], ast.Name)
        and n.args[0].id == "text"
    ]
    assert len(sends) == 1, (
        f"экран успеха отправляется {len(sends)} раз — при апгрейде тарифа "
        f"человек снова получит дубль"
    )


def test_stats_are_written_after_the_send():
    """Иначе в статистике «отправлено» у сообщения, которое не ушло."""
    send = HANDLER.index("await callback.message.answer(")
    log = HANDLER.index("await _autonotif_log(")
    assert send < log, "статистика пишется до отправки"


def test_status_reflects_delivery_not_settings():
    """Статус доставки не должен зависеть от того, включён ли кастомный текст."""
    assert 'status="sent" if _use_custom else "skipped_disabled"' not in SRC, (
        "статус снова берётся из настройки переопределения"
    )
    assert 'status="sent" if delivered else "failed"' in SRC, (
        "статус не строится по результату отправки"
    )


def test_upgrade_and_business_log_nothing():
    """У них свои тексты, не из реестра — логировать нечего."""
    assert "_autonotif_key_to_log = None" in HANDLER, (
        "ключ не инициализирован — ветка апгрейда упадёт на NameError"
    )
    log_block = HANDLER[HANDLER.index("if _autonotif_key_to_log:"):]
    assert "await _autonotif_log(" in log_block, "запись вне охраняющего условия"


@pytest.mark.parametrize("status", ["sent", "failed"])
def test_status_values_are_accepted_by_the_writer(status):
    """Оба значения должны быть в списке допустимых у log_notification_send."""
    from app.services.automated_notifications import helper

    doc = inspect.getsource(helper.log_notification_send)
    assert f"'{status}'" in doc or f'"{status}"' in doc, (
        f"статус {status} не описан у писателя статистики"
    )
