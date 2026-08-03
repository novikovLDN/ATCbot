"""Разбивка платёжных экранов ничего не потеряла.

app/handlers/callbacks/payments_callbacks.py был на 1686 строк и держал в
одном файле три разные вещи: списание с внутреннего баланса (единственное
место, где деньги уходят прямо здесь), выставление инвойсов у пяти внешних
провайдеров и пополнение баланса. Разрезан на pay_balance / pay_external /
topup плюс общий _invoice_cleanup.

Главный риск такой операции — потерять обработчик: забытый роутер не даёт
никакой ошибки, кнопка просто перестаёт отвечать. Поэтому проверяем не
только что функция объявлена, но и что её роутер реально подключён.

ЧЕГО ЗДЕСЬ БОЛЬШЕ НЕТ

    Раньше файл проверял ещё две разбивки — админского раздела «Доступ»
    (access*.py) и рассылок (broadcast*.py). Оба раздела удалены целиком:
    выдача доступа, смена тарифа, отзыв и рассылки живут в веб-дашборде.
    Проверять сохранность обработчиков, которых нет, незачем.

    От рассылок остался нижний уровень доставки — он переехал в
    app/services/broadcast_delivery.py, потому что нужен и промо-рассылке
    триальщикам, и broadcast_sender. Его проверки внизу файла.
"""
import ast
from pathlib import Path

CALLBACKS = Path("app/handlers/callbacks")

PAYMENT_MODULES = [
    "pay_balance.py",
    "pay_external.py",
    "topup.py",
    "_invoice_cleanup.py",
]

# Список взят из файла до разрезания: если какой-то обработчик исчезнет,
# соответствующая кнопка оплаты в боте начнёт молчать.
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
        tree = ast.parse((CALLBACKS / name).read_text(encoding="utf-8"))
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
    src = (CALLBACKS / "_invoice_cleanup.py").read_text(encoding="utf-8")
    for sibling in ("pay_balance", "pay_external", "topup", "balance_callbacks"):
        assert f"import {sibling}" not in src and f"{sibling} import" not in src


def test_old_payments_module_is_gone():
    """Файл удалён целиком: всё его содержимое переехало."""
    assert not (CALLBACKS / "payments_callbacks.py").exists()


# ──────────────────────────────────────────────────────────────────────
#  Слой доставки рассылок
# ──────────────────────────────────────────────────────────────────────

DELIVERY = Path("app/services/broadcast_delivery.py")


def test_delivery_layer_does_not_import_handlers():
    """Нижний уровень доставки не должен знать про экраны — иначе кольцо,
    и уровень перестаёт быть нижним. Ровно поэтому он и переехал в
    services: мастер создания рассылки удалён, доставка нужна дальше."""
    src = DELIVERY.read_text(encoding="utf-8")
    assert "app.handlers.admin" not in src


def test_retry_after_is_handled_in_the_delivery_layer():
    """Telegram отвечает TelegramRetryAfter с точным временем ожидания.
    Проигнорировать его — получить временную блокировку и встать целиком."""
    src = DELIVERY.read_text(encoding="utf-8")
    assert "TelegramRetryAfter" in src


def test_delivery_layer_has_its_consumers():
    """Модуль оставлен не «на всякий случай» — у него есть потребители."""
    consumers = [
        Path("app/services/broadcast_sender.py"),
        Path("app/handlers/admin/promo_trial.py"),
    ]
    for path in consumers:
        src = path.read_text(encoding="utf-8")
        assert "app.services.broadcast_delivery import" in src, path
