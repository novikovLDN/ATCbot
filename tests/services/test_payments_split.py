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

# pay_external с тех пор разрезан ещё раз — на пакет по провайдерам
# (карта/Stars в Telegram, Platega, CryptoBot, Lava), поэтому здесь путь к
# каталогу, а не к файлу. Свой набор проверок у него в
# tests/services/test_pay_external_split.py.
PAYMENT_MODULES = [
    "pay_balance.py",
    "pay_external/telegram_invoice.py",
    "pay_external/platega.py",
    "pay_external/cryptobot.py",
    "pay_external/lava.py",
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


def test_gift_screen_reuses_the_shared_invoice_cleanup():
    """Экран подарка не должен держать свою копию автоудаления инвойса.

    Копий было пять с расходящимися сигнатурами: четыре брали message_id:
    int, версия в gift.py — Message. Пока они жили порознь, смена
    INVOICE_TIMEOUT или добавление лога попадали в один-два файла: инвойсы
    одних товаров исчезали, других висели просроченными.
    """
    # Подарки разрезаны на пакет; платёжные экраны — в gift/payment.py.
    src = (CALLBACKS / "gift" / "payment.py").read_text(encoding="utf-8")
    assert "_invoice_cleanup import _schedule_invoice_deletion" in src
    assert "async def _schedule_invoice_deletion" not in src, (
        "в gift/payment.py снова своя копия автоудаления инвойса"
    )


def test_shared_and_gift_cleanup_are_the_same_object():
    """Импорт, а не одноимённая функция: расхождению взяться неоткуда."""
    from app.handlers.callbacks import gift
    from app.handlers.callbacks import _invoice_cleanup

    assert gift._schedule_invoice_deletion is _invoice_cleanup._schedule_invoice_deletion


def _sleeps_then_deletes(func: ast.AST) -> bool:
    """Признак самодельного автоудаления: в одном теле и sleep, и delete_message.

    Копию узнаём не по имени, а по повадке: подождать и удалить сообщение.
    Имя ненадёжно — те же самые семь копий звались _schedule_invoice_deletion,
    _auto_delete_lava_msg, _auto_delete, _del и _del_invoice, а две последние
    были вообще вложенными функциями внутри обработчика. Поэтому ищем не
    объявление с известным именем, а поведение, и ищем через ast.walk —
    вложенные функции он тоже обходит.
    """
    sleeps = deletes = False
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if attr == "sleep":
            sleeps = True
        elif attr == "delete_message":
            deletes = True
    return sleeps and deletes


# Единственная реализация отложенного удаления счёта плюс те места, где
# «поспать и удалить сообщение» означает совсем другое. Whitelist именно
# явный: строка сюда добавляется только вместе с причиной, и причина
# «мне так удобнее» не годится — восьмая копия автоудаления счёта должна
# упираться в красный тест, а не тихо появляться.
AUTO_DELETE_WHITELIST = {
    # ТА САМАЯ реализация. Всё остальное в боте зовёт её.
    ("app/handlers/callbacks/_invoice_cleanup.py", "_schedule_invoice_deletion"):
        "единственная реализация автоудаления счёта",
    # Ниже — не таймеры счёта: пауза измеряется секундами и меньше,
    # сообщение удаляется тут же, в том же обработчике, и никакой
    # create_task его не переживает.
    ("app/handlers/callbacks/broadcast_offers/gift_1y40.py", "callback_broadcast_gift_1y_40"):
        "пауза 2 с ради эффекта: показать 👀 и убрать перед экраном тарифов",
    ("app/handlers/callbacks/broadcast_offers/gift_reveal.py", "callback_broadcast_gift_reveal"):
        "та же интрига с 👀 в рассылке-подарке",
    ("app/services/broadcast_deleter.py", "delete_broadcast_from_users"):
        "пауза между пачками при массовом удалении рассылки — защита от rate limit",
}


def test_app_has_one_auto_delete_implementation():
    """Во всём app/ ровно одно место, которое ждёт и удаляет сообщение счёта.

    Копий было семь: общая в _invoice_cleanup плюс шесть самодельных — в
    traffic/_shared.py, proxy.py, steam_purchase.py, telegram_premium.py,
    telegram_stars_purchase.py, spotify_purchase.py и apple_id.py (в
    последнем сразу две). Часть держала свою константу 15 минут вместо
    config.INVOICE_TIMEOUT_SECONDS, часть — голый asyncio.sleep(15 * 60)
    без имени вовсе, и ни одна не писала INVOICE_EXPIRED в лог.

    Чем это било. Правка срока жизни счёта доезжала до одних способов
    оплаты и не доезжала до других, хотя тот же срок lava_service шлёт
    провайдеру в поле expire — сообщение и счёт протухали в разное время.
    А по жалобе «счёт исчез, я не успел оплатить» половину случаев нельзя
    было поднять в логах: копии молчали.

    Если провайдеру нужен ДРУГОЙ срок — это аргумент timeout у общей
    функции, а не повод завести вторую реализацию.
    """
    homegrown = {}
    for path in sorted(Path("app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _sleeps_then_deletes(node):
                continue
            if (path.as_posix(), node.name) in AUTO_DELETE_WHITELIST:
                continue
            homegrown[f"{path.as_posix()}:{node.lineno}"] = node.name

    assert not homegrown, (
        f"снова своя реализация автоудаления сообщения: {homegrown}. "
        "Нужен общий app.handlers.callbacks._invoice_cleanup._schedule_invoice_deletion; "
        "если провайдеру нужен другой срок — передайте его параметром timeout."
    )


def test_auto_delete_whitelist_has_no_stale_entries():
    """Whitelist не должен переживать код, ради которого его завели.

    Иначе он тихо разрастается: функцию переименовали или удалили, строка
    осталась — и однажды прикроет собой настоящую копию с тем же именем.
    """
    stale = []
    for (path, name), reason in AUTO_DELETE_WHITELIST.items():
        p = Path(path)
        if not p.exists():
            stale.append(f"{path} (файла нет)")
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        found = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name and _sleeps_then_deletes(node)
        ]
        if not found:
            stale.append(f"{path}::{name} ({reason})")
    assert not stale, f"мёртвые исключения в AUTO_DELETE_WHITELIST: {stale}"


def test_deletion_is_scheduled_by_message_id_not_by_message_object():
    """Вызывающий передаёт message_id, а не сам Message.

    Задача уходит в фон и живёт ещё 15 минут после выхода из обработчика.
    Message тянет за собой bot, chat и from_user — состояние, устаревшее
    сразу; обращение к нему через четверть часа — гонка. Ровно из-за
    расхождения сигнатур (Message против message_id: int) копии в своё
    время и не свели.

    Проверка нужна ещё и потому, что ошибка здесь молчит: передать Message
    туда, где ждут int, — это TypeError внутри try/except общей функции
    через 15 минут, в фоне. Счёт просто останется висеть в чате.
    """
    bad = []
    for path in sorted(Path("app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "_schedule_invoice_deletion":
                continue
            # (bot, chat_id, message_id) — третий позиционный и проверяем.
            if len(node.args) < 3:
                bad.append(f"{path.as_posix()}:{node.lineno} (аргументов меньше трёх)")
                continue
            target = node.args[2]
            ok = (
                (isinstance(target, ast.Attribute) and target.attr == "message_id")
                or (isinstance(target, ast.Name) and target.id.endswith("message_id"))
            )
            if not ok:
                bad.append(f"{path.as_posix()}:{node.lineno} ({ast.unparse(target)})")

    assert not bad, (
        f"автоудалению счёта передают не message_id: {bad}. "
        "Нужен именно идентификатор (msg.message_id): фоновая задача живёт "
        "дольше обработчика и не должна держать устаревший объект Message."
    )


def test_gift_lava_branch_schedules_deletion_through_the_shared_helper():
    """Счёт Lava в подарках удаляет общая функция — и с её таймаутом.

    Проверяем именно вызов: удалить копию, но забыть переключить ветку
    Lava — значит оставить просроченный счёт висеть в чате навсегда.
    """
    tree = ast.parse((CALLBACKS / "gift" / "payment.py").read_text(encoding="utf-8"))
    lava = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "callback_gift_pay_lava"
    )
    scheduled = [
        arg.func.id
        for node in ast.walk(lava)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "create_task"
        for arg in node.args
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
    ]
    assert scheduled == ["_schedule_invoice_deletion"], (
        f"ветка Lava в подарках планирует удаление счёта не общей функцией: {scheduled}"
    )


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
