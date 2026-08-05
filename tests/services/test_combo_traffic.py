"""Комбо: человек платит за подписку ВМЕСТЕ с пакетом ГБ обхода.

БОЕВОЙ ДЕФЕКТ
    В продакшене покупатель комбо получал месяц подписки и ноль гигабайтов.
    Начисление было размножено на четыре независимые копии — Telegram-инвойс
    и Stars, вебхуки, оплата с баланса, админская выдача — и каждая теряла
    ГБ по-своему:

    A. Оплата с баланса не сохраняла признак комбо в базу. Единственным
       следом покупки был ключ в FSM: бот перезапустился или человек открыл
       другое меню — восстановить объём было неоткуда, и в логах при этом
       ничего не появлялось.
    B. Отложенная активация теряла ГБ целиком: обработчик выходил до
       начисления, а activation_worker про комбо не знал вовсе.
    C. Идемпотентный выход по «уведомление уже отправлено» в балансовом
       пути стоял ЗАДОЛГО до блока начисления и уносил его с собой.
    D. Копии расходились: правка в одной не доезжала до трёх остальных.

ЧТО ПРОВЕРЯЕТСЯ ЗДЕСЬ
    Что начисление осталось одно (app/services/combo_traffic), что объём
    берётся только из COMBO_TARIFFS, что признак комбо доезжает до базы на
    всех путях и что ни один ранний выход не проносит начисление мимо.
"""

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import database
from app.services import combo_traffic

REPO_ROOT = Path(__file__).resolve().parents[2]

# Единственный модуль, которому позволено считать объём пакета и звать
# начисление. Всё остальное обязано звать grant_combo_traffic.
GRANT_MODULE = "app/services/combo_traffic.py"

# provision_subscription ставит bypass-сущности стартовый лимит трафика при
# выдаче доступа — это не начисление комбо-пакета, а другая операция и другая
# функция (remnawave_bypass.add_bypass_traffic). Поэтому исключение явное.
PROVISIONING_MODULE = "app/services/purchase_flow.py"


# ─────────────────────────────────────────────────────────────────────
# D. Копий начисления снова не появилось
# ─────────────────────────────────────────────────────────────────────

def _identifiers(src: str) -> set:
    """Имена, которые модуль реально называет в КОДЕ.

    Считаем по токенам, а не поиском подстроки: иначе детектор ловил бы
    объяснения в комментариях («в COMBO_TARIFFS этого пакета нет»), и цена
    такого теста была бы в том, что комментарии перестают писать.
    """
    import io
    import tokenize

    names = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.NAME:
                names.add(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):  # pragma: no cover
        pass
    return names


def _python_sources():
    for path in sorted(REPO_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith((".venv/", "tests/", "load_tests/", "scripts/", "migrations/")):
            continue
        if "__pycache__" in rel:
            continue
        yield rel, _identifiers(path.read_text(encoding="utf-8"))


def test_only_one_module_computes_and_grants_combo_traffic():
    """Расчёт объёма и начисление живут вместе только в одном месте.

    Повадка копии узнаваема: модуль читает пакет ГБ из COMBO_TARIFFS (прямо
    или через combo_bypass_gb) и тут же зовёт add_bypass_traffic. Пока таких
    модулей было четыре, они разъезжались молча.
    """
    offenders = []
    for rel, names in _python_sources():
        if rel in (GRANT_MODULE, PROVISIONING_MODULE):
            continue
        reads_volume = bool(names & {"COMBO_TARIFFS", "combo_bypass_gb"})
        grants = "add_bypass_traffic" in names
        if reads_volume and grants:
            offenders.append(rel)

    assert not offenders, (
        "появилась вторая копия начисления комбо-трафика: "
        + ", ".join(offenders)
        + f" — объём и начисление должны жить только в {GRANT_MODULE}"
    )


@pytest.mark.parametrize(
    "rel",
    [
        "app/handlers/payments/combo_bypass.py",
        "app/handlers/callbacks/pay_balance.py",
        "app/services/payments/confirmation.py",
        "activation_worker.py",
        "auto_renewal.py",
        "database/admin_access.py",
    ],
)
def test_every_payment_path_calls_the_single_grant(rel):
    """Каждый путь оплаты зовёт общую функцию, а не свою копию."""
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "grant_combo_traffic(" in src, f"{rel} не зовёт общее начисление"


# ─────────────────────────────────────────────────────────────────────
# Объём — только из конфига, результат — явный
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def panel(monkeypatch):
    """Панель и учёт: запоминаем вызовы, наружу ничего не ходит."""
    calls = SimpleNamespace(bytes=None, ok=True, recorded=[], flags=[])

    async def _add(telegram_id, extra_bytes, **kwargs):
        calls.bytes = extra_bytes
        calls.kwargs = kwargs
        return calls.ok

    async def _record(telegram_id, gb, price):
        calls.recorded.append((telegram_id, gb))

    async def _flag(telegram_id, value=True):
        calls.flags.append((telegram_id, value))

    monkeypatch.setattr(
        "app.services.remnawave_service.add_bypass_traffic", _add, raising=False
    )
    monkeypatch.setattr(database, "record_traffic_purchase", _record)
    monkeypatch.setattr(database, "set_combo_flag", _flag)
    return calls


async def test_volume_comes_from_config_not_from_the_caller(panel):
    """Объём считает сама функция по COMBO_TARIFFS.

    Раньше он приезжал из FSM. FSM живёт в памяти: между выставлением счёта
    и оплатой человек открывает другое меню или бот перезапускается — и
    объёма нет, а покупка комбо уже оплачена.
    """
    expected_gb = config.COMBO_TARIFFS["combo_plus"][90]["gb"]

    outcome = await combo_traffic.grant_combo_traffic(
        777, "plus", 90, is_combo=True, purchase_id="p-1",
    )

    assert outcome.granted is True
    assert outcome.gb == expected_gb
    assert panel.bytes == expected_gb * 1024 ** 3, "гигабайты считаются не в 1024^3"
    assert panel.recorded == [(777, expected_gb)]
    assert panel.flags == [(777, True)], "подписка не помечена комбо"


async def test_full_combo_name_is_accepted_too(panel):
    """Админская выдача называет тариф 'combo_plus', покупка — 'plus'.
    Один продукт приходит под двумя именами, объём обязан совпасть."""
    plain = await combo_traffic.grant_combo_traffic(1, "plus", 30, is_combo=True)
    full = await combo_traffic.grant_combo_traffic(1, "combo_plus", 30, is_combo=True)
    assert plain.gb == full.gb == config.COMBO_TARIFFS["combo_plus"][30]["gb"]


async def test_panel_refusal_is_reported_not_swallowed(panel):
    """Отказ панели обязан вернуться вызывающему явно: на вебхуке по нему
    поднимается TransientPaymentError, и провайдер повторит платёж."""
    panel.ok = False
    outcome = await combo_traffic.grant_combo_traffic(5, "basic", 30, is_combo=True)

    assert outcome.granted is False
    assert outcome.reason == "panel_rejected"
    assert outcome.gb > 0, "объём нужен админу для ручного доначисления"
    assert panel.recorded == [], "покупка трафика записана, хотя ГБ не начислены"


async def test_unknown_period_does_not_invent_a_package(panel):
    outcome = await combo_traffic.grant_combo_traffic(5, "plus", 45, is_combo=True)
    assert outcome.granted is False
    assert outcome.reason == "no_gb_in_config"
    assert panel.bytes is None, "панель дёрнули без пакета"


async def test_plain_purchase_grants_nothing(panel):
    outcome = await combo_traffic.grant_combo_traffic(5, "plus", 30, is_combo=False)
    assert outcome.granted is False
    assert outcome.reason == "not_combo"
    assert panel.bytes is None


async def test_grant_never_raises_into_the_payment_flow(monkeypatch, panel):
    """Деньги взяты и подписка выдана — исключение отсюда сорвало бы экран
    успеха. Отказ возвращается значением, а не броском."""
    async def _boom(*args, **kwargs):
        raise RuntimeError("panel down")

    monkeypatch.setattr(
        "app.services.remnawave_service.add_bypass_traffic", _boom, raising=False
    )
    outcome = await combo_traffic.grant_combo_traffic(5, "plus", 30, is_combo=True)
    assert outcome.granted is False
    assert outcome.reason == "error"


def test_success_is_logged_only_after_the_panel_accepted():
    """«Начислено» не должно писаться раньше, чем панель приняла."""
    src = inspect.getsource(combo_traffic.grant_combo_traffic)
    granted_log = src.index("COMBO_TRAFFIC_GRANTED")
    panel_call = src.index("add_bypass_traffic(")
    refusal = src.index("COMBO_TRAFFIC_FAILED")
    assert panel_call < granted_log, "успех пишется до вызова панели"
    assert refusal < granted_log, "отказ панели не разбирается до записи об успехе"


# ─────────────────────────────────────────────────────────────────────
# A. Признак комбо доезжает до базы при оплате с баланса
# ─────────────────────────────────────────────────────────────────────

def test_balance_purchase_accepts_combo_flag():
    """Без этого параметра факт комбо-покупки нигде не сохраняется."""
    params = inspect.signature(database.finalize_balance_purchase).parameters
    assert "is_combo" in params, (
        "finalize_balance_purchase не знает про комбо — признак остаётся "
        "только в FSM и теряется вместе с ним"
    )


async def test_balance_purchase_writes_combo_flag_to_db(monkeypatch):
    """Признак комбо пишется в subscriptions.is_combo после коммита.

    Это единственный след покупки, переживающий перезапуск бота: по нему
    автопродление берёт цену комбо, а activation_worker понимает, что
    подписке полагаются гигабайты.
    """
    from database import balance_purchases as bp

    flags = []

    class _Tx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    class _Conn:
        async def fetchrow(self, query, *args):
            if "FROM subscriptions" in query:
                return None
            if "SELECT balance" in query:
                return {"balance": 10_000_00}
            return None

        async def fetchval(self, query, *args):
            if "INSERT INTO payments" in query:
                return 42
            if "SELECT balance" in query:
                return 0
            return None

        async def execute(self, query, *args):
            return None

        def transaction(self):
            return _Tx()

    class _Acquire:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *exc):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    async def _get_pool():
        return _Pool()

    async def _grant_access(**kwargs):
        from datetime import datetime, timedelta, timezone
        return {
            "subscription_end": datetime.now(timezone.utc) + timedelta(days=30),
            "vless_url": "vless://key",
            "action": "new",
            "subscription_type": "plus",
        }

    async def _referral(**kwargs):
        return {"success": False}

    async def _set_combo_flag(telegram_id, value):
        flags.append((telegram_id, value))

    monkeypatch.setattr(bp, "get_pool", _get_pool)
    monkeypatch.setattr(config, "VPN_ENABLED", False)
    monkeypatch.setattr("database.subscriptions.grant_access", _grant_access)
    monkeypatch.setattr("database.users.process_referral_reward", _referral)
    monkeypatch.setattr("database.subscription_state.set_combo_flag", _set_combo_flag)

    result = await bp.finalize_balance_purchase(
        telegram_id=555,
        tariff_type="plus",
        period_days=30,
        amount_rubles=499.0,
        is_combo=True,
    )

    assert result["success"] is True
    assert result.get("is_combo") is True, "покупка не сообщает, что она комбо"
    assert flags == [(555, True)], "признак комбо не записан в базу"


def test_balance_handler_passes_the_combo_flag_down():
    """Обработчик обязан прокинуть признак в финализацию покупки."""
    from app.handlers.callbacks import pay_balance

    src = inspect.getsource(pay_balance.callback_pay_balance)
    call = src[src.index("finalize_balance_purchase("):]
    call = call[: call.index("\n        )")]
    assert "is_combo=" in call, "комбо снова остаётся только в FSM"


# ─────────────────────────────────────────────────────────────────────
# C. Ранние выходы не проносят начисление мимо
# ─────────────────────────────────────────────────────────────────────

def _handler_source():
    from app.handlers.callbacks import pay_balance

    return inspect.getsource(pay_balance.callback_pay_balance)


def _grant_position(src: str) -> int:
    """Где в обработчике начисляются ГБ — как ни назови вызов."""
    positions = [
        src.find(token)
        for token in ("grant_combo_traffic(", "add_bypass_traffic(")
        if src.find(token) != -1
    ]
    assert positions, "в обработчике нет начисления трафика вовсе"
    return min(positions)


def test_grant_happens_before_the_idempotent_return():
    """Проверка «уведомление уже отправлено» делает return. Пока начисление
    стояло после неё, повторный прогон не начислял ничего: первый успел
    отправить уведомление, но упал раньше выдачи гигабайтов."""
    src = _handler_source()
    assert _grant_position(src) < src.index("if notification_already_sent:"), (
        "начисление ГБ стоит за идемпотентным return — повтор ничего не начислит"
    )


def test_grant_happens_before_the_pending_activation_return():
    """Отложенная активация тоже выходит из обработчика ранним return.

    У покупки с баланса нет строки в pending_purchases, поэтому
    activation_worker её не подхватит — начислить, кроме как здесь, некому.
    """
    src = _handler_source()
    assert _grant_position(src) < src.index("if is_pending_activation:"), (
        "при отложенной активации ГБ теряются полностью"
    )


# ─────────────────────────────────────────────────────────────────────
# B. Отложенная активация доначисляет ГБ
# ─────────────────────────────────────────────────────────────────────

class _FakeConn:
    async def fetchrow(self, query, *args):
        return {
            "activation_status": "active",
            "uuid": "uuid-1",
            "subscription_type": "plus",
        }

    async def fetchval(self, query, *args):
        return 0


class _FakeAcquire:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return _FakeConn()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def worker(monkeypatch):
    """Воркер активации без БД, панели и Telegram."""
    import activation_worker as aw
    from app.services.activation import service as activation_service

    monkeypatch.setattr(database, "DB_READY", True)
    monkeypatch.setattr(config, "VPN_ENABLED", True)

    async def _get_pool():
        return object()

    async def _no_notifications(*args, **kwargs):
        return []

    async def _attempt(subscription_id, telegram_id, current_attempts, pool):
        return SimpleNamespace(uuid="uuid-1", attempts=current_attempts + 1)

    async def _lang(_tg):
        return "ru"

    async def _send(bot, chat_id, text, **kwargs):
        return True

    monkeypatch.setattr(database, "get_pool", _get_pool)
    monkeypatch.setattr(aw, "acquire_connection", _FakeAcquire)
    monkeypatch.setattr(activation_service, "is_subscription_expired", lambda _e: False)
    monkeypatch.setattr(activation_service, "get_pending_for_notification", _no_notifications)
    monkeypatch.setattr(activation_service, "attempt_activation", _attempt)
    monkeypatch.setattr(aw, "resolve_user_language", _lang)
    monkeypatch.setattr(aw, "safe_send_message", _send)
    return aw, activation_service


def _pending_sub(**over):
    base = dict(
        telegram_id=900,
        subscription_id=1,
        activation_attempts=0,
        expires_at=None,
        amount_rubles=499,
        subscription_type="plus",
        period_days=30,
        is_combo=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def test_deferred_activation_grants_combo_traffic(monkeypatch, worker):
    """Подписка активирована с опозданием — гигабайты обязаны догнать её.

    Раньше в activation_worker слова «комбо» не было вовсе: подписка
    активировалась, ГБ не начислялись никогда и без единой ошибки в логе.
    """
    aw, activation_service = worker
    granted = []

    async def _get_pending(**kwargs):
        return [_pending_sub()]

    async def _grant(telegram_id, tariff, period_days, **kwargs):
        granted.append((telegram_id, tariff, period_days, kwargs.get("is_combo")))
        return combo_traffic.ComboTrafficOutcome(True, 75, "granted")

    monkeypatch.setattr(activation_service, "get_pending_subscriptions", _get_pending)
    monkeypatch.setattr(combo_traffic, "grant_combo_traffic", _grant)

    processed, outcome, _ = await aw.process_pending_activations(bot=object())

    assert processed == 1
    assert outcome == "success"
    assert granted == [(900, "plus", 30, True)], (
        "activation_worker не начислил комбо-гигабайты при активации"
    )


async def test_plain_subscription_gets_no_bonus_traffic(monkeypatch, worker):
    """Обычная подписка не должна получать пакет обхода даром."""
    aw, activation_service = worker
    granted = []

    async def _get_pending(**kwargs):
        return [_pending_sub(is_combo=False)]

    async def _grant(*args, **kwargs):
        granted.append(args)
        return combo_traffic.ComboTrafficOutcome(True, 75, "granted")

    monkeypatch.setattr(activation_service, "get_pending_subscriptions", _get_pending)
    monkeypatch.setattr(combo_traffic, "grant_combo_traffic", _grant)

    await aw.process_pending_activations(bot=object())
    assert granted == []


def test_pending_query_carries_the_combo_flag_and_bounds_the_purchase():
    """Признак и срок берутся из оплаченной покупки, и покупка обязана быть
    свежей: без ограничения по времени сюда попадала бы любая прошлая
    покупка человека, и пакет посчитался бы за чужой срок."""
    from app.services.activation import service as activation_service

    src = inspect.getsource(activation_service._fetch_pending_subscriptions)
    assert "is_combo" in src, "воркер не видит признак комбо"
    assert "INTERVAL '1 day'" in src, "боковой join не ограничен по времени"


# ─────────────────────────────────────────────────────────────────────
# Разница в поведении при отказе: вебхук повторяет, экран — нет
# ─────────────────────────────────────────────────────────────────────

def test_webhook_still_asks_the_provider_to_retry():
    """На вебхуке отказ обязан подниматься исключением — иначе провайдер не
    повторит запрос и второго шанса начислить ГБ не будет."""
    from app.services.payments import confirmation

    src = inspect.getsource(confirmation._send_confirmation)
    grant = src.index("grant_combo_traffic(")
    tail = src[grant:]
    assert "outcome.granted" in tail
    assert "TransientPaymentError" in tail, "сигнал на повтор вебхука пропал"


@pytest.mark.parametrize(
    "module,func",
    [
        ("app.handlers.payments.combo_bypass", "grant_combo_and_bypass_traffic"),
        ("app.handlers.callbacks.pay_balance", "callback_pay_balance"),
    ],
)
def test_interactive_paths_do_not_raise_on_refusal(module, func):
    """В интерактивных путях деньги уже взяты и подписка уже выдана: бросок
    отсюда оставил бы человека без экрана успеха."""
    import importlib

    mod = importlib.import_module(module)
    src = inspect.getsource(getattr(mod, func))
    assert "grant_combo_traffic(" in src
    assert "raise" not in src.split("grant_combo_traffic(")[1].split("\n\n")[0], (
        "отказ начисления бросает исключение в интерактивном пути"
    )
