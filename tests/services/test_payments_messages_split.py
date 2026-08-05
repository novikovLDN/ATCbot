"""Разбивка приёма оплаты Telegram ничего не потеряла.

app/handlers/payments/payments_messages.py был на 1148 строк, из них тысяча —
один обработчик successful_payment: входные проверки, пополнение баланса,
семь товаров, подписка, вёрстка экрана и начисления делили полтора десятка
локальных переменных. Разрезан на precheckout / photo_log / payment_preflight
/ balance_topup / subscription_finalize / subscription_success / combo_bypass
плюс фасад-маршрутизатор.

ГЛАВНЫЕ РИСКИ ТАКОЙ ОПЕРАЦИИ

    Забытый include_router: обработчик остаётся объявленным, ошибок в логах
    нет — событие оплаты просто перестаёт обрабатываться. Деньги списаны,
    товар не выдан.

    Сбитый порядок шагов. Предохранитель PURCHASE_ROUTE_UNHANDLED обязан
    стоять между выдачей товаров и финализацией подписки. Начисления и
    уборка — строго после экрана успеха: он единственный, кто ловит
    повторное событие оплаты по флагу уведомления.
"""
import ast
from pathlib import Path

PKG = Path("app/handlers/payments")

# Модули, на которые разложен приём оплаты.
SPLIT_MODULES = [
    "precheckout.py",
    "photo_log.py",
    "payment_preflight.py",
    "balance_topup.py",
    "subscription_finalize.py",
    "subscription_success.py",
    "combo_bypass.py",
]

# Обработчики, взятые из файла ДО разрезания. Пропажа любого = молчащий
# платёж: Telegram присылает событие, а бот на него не отвечает.
HANDLERS = {
    "process_pre_checkout_query",
    "log_incoming_photo_file_id",
    "process_successful_payment",
}

# Имена, которые снаружи зовут через app.handlers.payments.payments_messages:
# на них ссылаются тесты и соседний код. Реэкспорт падает не при импорте,
# а в момент обращения.
REEXPORTED = {
    "payments_router",
    "classify_purchase",
    "resolve_payment_amount_rubles",
    "_ROUTED_PURCHASE_TYPES",
    "_TARIFF_PREFIX_ROUTES",
    "PaidPurchase",
    "process_successful_payment",
}


def _facade_source() -> str:
    return (PKG / "payments_messages.py").read_text(encoding="utf-8")


def test_every_split_module_exists():
    missing = [m for m in SPLIT_MODULES if not (PKG / m).exists()]
    assert not missing, f"модули разбивки пропали: {missing}"


def test_no_payment_handler_was_lost():
    found = set()
    for name in ["payments_messages.py"] + SPLIT_MODULES:
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
                found.add(node.name)
    missing = HANDLERS - found
    assert not missing, f"обработчики оплаты пропали: {sorted(missing)}"


def test_every_handler_is_registered_in_the_dispatcher():
    """Забытый include_router не даёт ошибки — платёж просто не обработается."""
    from app.handlers import router

    registered = set()

    def walk(r):
        for observer in (r.message, r.pre_checkout_query, r.callback_query):
            for h in observer.handlers:
                registered.add(getattr(h.callback, "__name__", ""))
        for sub in r.sub_routers:
            walk(sub)

    walk(router)
    missing = HANDLERS - registered
    assert not missing, f"объявлены, но не подключены: {sorted(missing)}"


def test_everything_is_reexported_from_the_facade():
    import app.handlers.payments.payments_messages as pm

    missing = [n for n in sorted(REEXPORTED) if not hasattr(pm, n)]
    assert not missing, f"потерян реэкспорт: {missing}"


def test_pre_checkout_answers_are_still_reachable():
    """Ответ на pre_checkout обязан жить в одном модуле и отвечать оба раза:
    ok=True и ok=False. Потеря одного из них означает либо оплату по
    просроченному счёту, либо таймаут у Telegram и сорванный платёж."""
    src = (PKG / "precheckout.py").read_text(encoding="utf-8")
    assert "answer(ok=True)" in src
    assert "answer(ok=False" in src


def test_success_flow_keeps_its_order():
    """Порядок четырёх шагов в фасаде: финализация → экран → трафик → уборка.

    Перестановка стоит дорого: экран успеха — единственное место, которое
    отсекает повторное событие оплаты от Telegram по флагу уведомления.
    Начисление комбо-трафика раньше него = вторые гигабайты бесплатно.
    """
    src = _facade_source()
    steps = [
        "await finalize_subscription(",
        "await announce_success(",
        "await grant_combo_and_bypass_traffic(",
        "await finish_payment(",
    ]
    positions = []
    for step in steps:
        assert step in src, f"шаг оплаты пропал из обработчика: {step}"
        positions.append(src.index(step))
    assert positions == sorted(positions), f"порядок шагов оплаты сбит: {steps}"


def test_balance_topup_is_called_inside_the_payload_try():
    """Пополнение баланса зовётся ВНУТРИ try, разбирающего payload.

    Его исключения обязаны попадать в те же две ветки (InvalidPaymentPayload
    и PaymentServiceError), что и до разрезания. Вынесете вызов из try —
    человек при сбое увидит не «ошибка обработки платежа», а молчание.
    """
    tree = ast.parse(_facade_source())
    handler = next(
        n for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "process_successful_payment"
    )
    inside_try = set()
    for node in ast.walk(handler):
        if isinstance(node, ast.Try):
            for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    inside_try.add(child.func.id)
    assert "deliver_balance_topup" in inside_try, (
        "вызов пополнения баланса вышел из-под try с разбором payload"
    )


def test_split_modules_do_not_import_the_facade():
    """Фасад зовёт модули, а не наоборот: обратный импорт замкнёт кольцо
    и уронит бота на старте — но только на живом импорте, не в тестах."""
    for name in SPLIT_MODULES:
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "payments_messages" not in (node.module or ""), (
                    f"{name} импортирует фасад — кольцо импортов"
                )
