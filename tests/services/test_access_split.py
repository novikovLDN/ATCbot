"""Разбивка админского раздела «Доступ» ничего не потеряла.

app/handlers/admin/access.py был на 2283 строки и смешивал четыре разные
обязанности: найти человека, выдать доступ, поменять тариф, отнять. Правку
одной приходилось делать посреди трёх других, а самая рискованная часть —
выдача — тонула среди поиска и истории.

Разрезан на:
    access.py         поиск, карточка, история, ключи
    access_grant.py   выдача доступа
    access_switch.py  смена тарифа Basic ↔ Plus
    access_revoke.py  отзыв, VIP, перевыпуск, удаление
    _user_card.py     сама карточка (её показывают из трёх мест)

Главный риск такой операции — потерять обработчик: забытый роутер не даёт
никакой ошибки, кнопка просто перестаёт отвечать. Тест проверяет, что все
callback'и на месте и что кольца импортов не возникло.
"""
import ast
from pathlib import Path

import pytest

ADMIN = Path("app/handlers/admin")

MODULES = [
    "access.py",
    "access_grant.py",
    "access_switch.py",
    "access_revoke.py",
    "_user_card.py",
]

# Обработчики, которые обязаны существовать после разбивки. Список взят из
# файла до операции: если какой-то исчезнет, соответствующая кнопка в боте
# начнёт молчать.
EXPECTED_HANDLERS = {
    "callback_admin_keys", "callback_admin_keys_reissue_all", "callback_admin_keys_legacy",
    "callback_admin_user", "process_admin_user_id", "callback_admin_show_user",
    "callback_admin_user_history", "callback_admin_user_back",
    "callback_admin_grant_basic", "callback_admin_grant_plus",
    "process_admin_grant_flex_amount", "callback_admin_grant_flex_unit",
    "callback_admin_grant_flex_confirm", "callback_admin_grant_flex_notify",
    "callback_admin_grant_flex_cancel", "callback_admin_grant",
    "callback_admin_grant_days", "callback_admin_grant_minutes",
    "callback_admin_grant_1_year", "callback_admin_grant_1_year_fallback",
    "callback_admin_grant_custom_from_days", "callback_admin_grant_custom",
    "callback_admin_grant_unit", "process_admin_grant_value",
    "callback_admin_grant_notify", "callback_admin_grant_minutes_notify",
    "callback_admin_grant_quick_notify_fsm", "callback_admin_grant_notify_fallback",
    "callback_admin_switch_plus", "callback_admin_switch_basic",
    "callback_admin_switch_confirm", "callback_admin_switch_notify",
    "callback_admin_revoke", "callback_admin_revoke_notify",
    "callback_admin_vip_grant", "callback_admin_vip_revoke",
    "callback_admin_user_reissue", "callback_admin_delete_user",
    "callback_admin_delete_user_confirm",
}


def _defined_handlers() -> set:
    """Функции с декоратором-регистрацией во всех модулях раздела."""
    found = set()
    for name in MODULES:
        tree = ast.parse((ADMIN / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
                found.add(node.name)
    return found


def test_no_handler_was_lost():
    missing = EXPECTED_HANDLERS - _defined_handlers()
    assert not missing, f"обработчики пропали при разбивке: {sorted(missing)}"


def test_every_handler_is_registered_on_the_dispatcher():
    """Мало объявить функцию — её роутер должен быть подключён в пакете.
    Забытый include_router не даёт ошибки, кнопка просто молчит."""
    from app.handlers.admin import router

    registered = set()

    def walk(r):
        for h in list(r.callback_query.handlers) + list(r.message.handlers):
            registered.add(getattr(h.callback, "__name__", ""))
        for sub in r.sub_routers:
            walk(sub)

    walk(router)
    missing = EXPECTED_HANDLERS - registered
    assert not missing, f"объявлены, но не подключены к роутеру: {sorted(missing)}"


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_standalone(module):
    """Каждый модуль обязан импортироваться сам по себе: при разрезании
    легко оставить имя, которое приходило из соседнего блока."""
    import importlib

    importlib.import_module(f"app.handlers.admin.{module[:-3]}")


def test_no_import_cycle_between_split_modules():
    """Карточка пользователя вынесена именно для этого: три модуля её
    показывают, и без отдельного файла они тянули бы друг друга по кругу."""
    card = (ADMIN / "_user_card.py").read_text(encoding="utf-8")
    for sibling in ("access_grant", "access_switch", "access_revoke", "access"):
        assert f"admin.{sibling} import" not in card, (
            f"_user_card тянет {sibling} — появилось кольцо импортов"
        )


def test_split_actually_reduced_the_file():
    """Смысл операции — чтобы каждый файл можно было держать в голове."""
    for name in MODULES:
        n = len((ADMIN / name).read_text(encoding="utf-8").split("\n"))
        assert n < 1100, f"{name}: {n} строк — разбивка не достигла цели"


# ──────────────────────────────────────────────────────────────────────
#  Разбивка платёжных экранов
# ──────────────────────────────────────────────────────────────────────
#
# app/handlers/callbacks/payments_callbacks.py был на 1686 строк и держал
# в одном файле три разные вещи: списание с внутреннего баланса (единственное
# место, где деньги уходят прямо здесь), выставление инвойсов у пяти внешних
# провайдеров и пополнение баланса. Разрезан на pay_balance / pay_external /
# topup плюс общий _invoice_cleanup.

PAYMENT_MODULES = [
    "pay_balance.py",
    "pay_external.py",
    "topup.py",
    "_invoice_cleanup.py",
]

PAYMENT_HANDLERS = {
    "callback_pay_balance",
    "callback_pay_card", "callback_pay_stars", "callback_pay_card_pl",
    "callback_pay_intl_pl", "callback_pay_sbp", "callback_pay_crypto",
    "callback_pay_lava", "callback_pay_tariff_card",
    "callback_topup_sbp", "callback_topup_lava", "callback_topup_card",
}


def test_no_payment_handler_was_lost():
    found = set()
    for name in PAYMENT_MODULES:
        tree = ast.parse((Path("app/handlers/callbacks") / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
                found.add(node.name)
    missing = PAYMENT_HANDLERS - found
    assert not missing, f"платёжные обработчики пропали: {sorted(missing)}"


def test_every_payment_handler_is_registered():
    """Кнопка оплаты, потерявшая роутер, просто молчит — без ошибки."""
    from app.handlers.callbacks import router

    registered = set()

    def walk(r):
        for h in list(r.callback_query.handlers) + list(r.message.handlers):
            registered.add(getattr(h.callback, "__name__", ""))
        for sub in r.sub_routers:
            walk(sub)

    walk(router)
    missing = PAYMENT_HANDLERS - registered
    assert not missing, f"объявлены, но не подключены: {sorted(missing)}"


def test_invoice_cleanup_has_no_siblings_imports():
    """Общий хелпер не должен тянуть платёжные экраны — иначе кольцо."""
    src = (Path("app/handlers/callbacks") / "_invoice_cleanup.py").read_text(encoding="utf-8")
    for sibling in ("pay_balance", "pay_external", "topup", "balance_callbacks"):
        assert f"import {sibling}" not in src and f"{sibling} import" not in src


def test_old_payments_module_is_gone():
    """Файл удалён целиком: всё его содержимое переехало."""
    assert not Path("app/handlers/callbacks/payments_callbacks.py").exists()


# ──────────────────────────────────────────────────────────────────────
#  Разбивка рассылок
# ──────────────────────────────────────────────────────────────────────
#
# app/handlers/admin/broadcast.py был на 1455 строк и держал вместе три
# уровня: как отправить сообщение (ретраи, темп, клавиатура), пошаговый
# мастер создания и управление уже отправленным (удаление, статистика A/B).

BROADCAST_MODULES = ["broadcast.py", "broadcast_manage.py", "_broadcast_send.py"]

BROADCAST_HANDLERS = {
    "callback_admin_bcast_preset_maintenance", "callback_broadcast_promo_traffic_ext",
    "cmd_notify_no_subscription", "process_no_sub_broadcast_text",
    "callback_no_sub_broadcast_confirm", "callback_admin_broadcast",
    "callback_broadcast_create", "process_broadcast_title",
    "callback_broadcast_test_type", "process_broadcast_message_a",
    "process_broadcast_message_b", "process_broadcast_message",
    "process_broadcast_emoji", "callback_broadcast_buttons",
    "process_broadcast_discount", "callback_promo_duration",
    "callback_broadcast_segment", "callback_broadcast_confirm_send",
    "callback_broadcast_delete_list", "callback_broadcast_delete_confirm",
    "callback_broadcast_delete_exec", "callback_broadcast_ab_stats",
    "callback_broadcast_ab_stat_detail",
}


def test_no_broadcast_handler_was_lost():
    found = set()
    for name in BROADCAST_MODULES:
        tree = ast.parse((ADMIN / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
                found.add(node.name)
    missing = BROADCAST_HANDLERS - found
    assert not missing, f"обработчики рассылок пропали: {sorted(missing)}"


def test_every_broadcast_handler_is_registered():
    from app.handlers.admin import router

    registered = set()

    def walk(r):
        for h in list(r.callback_query.handlers) + list(r.message.handlers):
            registered.add(getattr(h.callback, "__name__", ""))
        for sub in r.sub_routers:
            walk(sub)

    walk(router)
    missing = BROADCAST_HANDLERS - registered
    assert not missing, f"объявлены, но не подключены: {sorted(missing)}"


def test_send_layer_does_not_import_the_wizard():
    """Нижний уровень доставки не должен знать про мастер создания —
    иначе кольцо, и уровень перестаёт быть нижним."""
    src = (ADMIN / "_broadcast_send.py").read_text(encoding="utf-8")
    for sibling in ("broadcast_manage", "broadcast_gifts"):
        assert f"admin.{sibling} import" not in src
    assert "admin.broadcast import" not in src


def test_retry_after_is_handled_in_the_send_layer():
    """Telegram отвечает TelegramRetryAfter с точным временем ожидания.
    Проигнорировать его — получить временную блокировку и встать целиком."""
    src = (ADMIN / "_broadcast_send.py").read_text(encoding="utf-8")
    assert "TelegramRetryAfter" in src
