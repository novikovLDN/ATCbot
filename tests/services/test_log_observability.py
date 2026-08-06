"""Наблюдаемость: лог не врёт, ошибка не глохнет, секреты не утекают.

ЗАЧЕМ ЭТИ ТЕСТЫ
    Когда у человека пропал доступ или не пришёл товар, разбор идёт по логам.
    Три вещи ломают разбор молча, и все три уже случались в этом проекте:

    • запись ставится по НАМЕРЕНИЮ, а не по результату («выдано» на провале);
    • ошибка перехватывается и не записывается вовсе;
    • в лог попадает предъявительский секрет — ключ, код подарка, пароль.

    Ни одну из трёх обычные тесты поведения не ловят: код при этом работает
    ровно так же, ломается только возможность потом разобраться.
"""
from __future__ import annotations

import ast
import inspect
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────────
# 1. Секреты не утекают: фильтр логов
# ─────────────────────────────────────────────────────────────────────

def _make_record(msg: str, exc_info=None, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord("test", level, __file__, 1, msg, None, exc_info)


@pytest.mark.parametrize(
    "raw",
    [
        "SUB_PROXY_REDIRECT -> https://panel.example.com/sub/aBc123ShortUuid",
        "bot link https://atlassecure.ru/api/sub/deadbeefdeadbeefdeadbeefdeadbeef?id=1",
        "key vless://uuid@host:443?sni=x#name",
        "happ://crypt4/PAYLOADPAYLOAD",
        "ss://Y2hhY2hhMjA6cGFzcw==@host:8388",
        "trojan://secretpass@host:443",
    ],
)
def test_subscription_secrets_are_redacted_from_messages(raw):
    """Подписочная ссылка — предъявительский секрет: кто её прочитал, тот
    получил доступ к VPN. Ни одна схема выдачи ключа не должна доезжать
    до вывода в исходном виде."""
    from app.core.logging_config import PIISanitizingFilter

    record = _make_record(raw)
    PIISanitizingFilter().filter(record)
    text = record.getMessage()

    assert "REDACTED" in text, f"секрет не отредактирован: {text}"
    for leak in ("ShortUuid", "deadbeefdeadbeef", "uuid@host", "PAYLOADPAYLOAD",
                 "Y2hhY2hhMjA6cGFzcw", "secretpass"):
        assert leak not in text, f"в выводе осталось {leak!r}: {text}"


def test_traceback_is_sanitized():
    """Регресс: exc_text заполняет форматтер внутри emit(), а фильтр
    отрабатывает ДО него. Проверка `if record.exc_text:` поэтому не
    срабатывала никогда, и трейсбэки уходили в лог несанированными —
    при том что докстринг фильтра обещал ровно эту защиту."""
    from app.core.logging_config import PIISanitizingFilter

    try:
        raise ValueError("panel said https://p.example.com/sub/LEAKEDSECRET")
    except ValueError:
        record = _make_record("boom", exc_info=sys.exc_info(), level=logging.ERROR)

    PIISanitizingFilter().filter(record)

    assert record.exc_text, "трейсбэк должен быть сформирован фильтром"
    assert "LEAKEDSECRET" not in record.exc_text
    assert "REDACTED" in record.exc_text


def test_json_formatter_sanitizes_exception_and_message():
    """JSONFormatter собирает исключение напрямую из exc_info, минуя
    exc_text, — то есть мимо фильтра. LOG_FORMAT=json штатный для прода,
    поэтому чистка обязана быть и здесь."""
    from app.core.logging_config import JSONFormatter

    try:
        raise RuntimeError("token vless://abc@h:1#k")
    except RuntimeError:
        record = _make_record(
            "fail https://p.example.com/sub/ANOTHERSECRET",
            exc_info=sys.exc_info(),
            level=logging.ERROR,
        )

    out = JSONFormatter().format(record)

    assert "ANOTHERSECRET" not in out
    assert "abc@h:1" not in out


# ─────────────────────────────────────────────────────────────────────
# 2. Секреты не утекают: предъявительские коды не пишутся целиком
# ─────────────────────────────────────────────────────────────────────

def _log_calls(path: Path):
    """Вернуть (номер строки, исходник) всех вызовов logger.*(...) в файле."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    src = path.read_text(encoding="utf-8").splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in {"debug", "info", "warning", "error", "exception", "critical"}:
            continue
        owner = func.value
        if not (isinstance(owner, ast.Name) and owner.id == "logger"):
            continue
        yield node.lineno, "\n".join(src[node.lineno - 1:node.end_lineno])


# Код подарка активирует чужую оплаченную подписку, код bgift-ссылки выдаёт
# гигабайты и остаётся многоразовым. Оба — предъявительские: достаточно
# прочитать лог. Подстановка голого имени в запись запрещена, нужна маска.
_BEARER_CODE_FILES = {
    "database/gift_subscriptions.py": ("gift_code",),
    "database/subscriptions.py": ("gift_code",),
    # Подарки разрезаны на пакет: код подарка логируется на оплате
    # и попадает в экраны списка.
    "app/handlers/callbacks/gift/payment.py": ("gift_code",),
    "app/handlers/callbacks/gift/my_gifts.py": ("gift_code",),
    # /start разрезан на пакет; обе ветки активации кодов живут в command.py.
    "app/handlers/user/start/command.py": ("gift_code", "bgift_code"),
    "app/handlers/payments/goods_delivery.py": ("gift_code",),
    # Промокод — код на скидку с лимитом max_uses (обычно больше единицы):
    # кто прочитал его в логе, тот получил рабочую скидку. Проверяем ТОЛЬКО
    # файлы, где код на момент записи заведомо живой.
    #
    # `database/promo.py` в список НЕ входит намеренно: там есть записи о
    # заведомо мёртвых кодах (PROMO_EXHAUSTED — исчерпан, PROMO_DEACTIVATED —
    # выключен), и код в них оставлен целиком. Маскировать то, что уже не
    # работает, значит выкинуть единственный полезный факт записи и ничего
    # при этом не спрятать. Живые ветки того файла закрыты отдельным
    # тестом ниже — test_live_promocodes_are_masked_in_the_store.
    "app/handlers/common/utils.py": ("promo_code",),
    "app/handlers/payments/callbacks/purchase_flow.py": ("promo_code",),
}


@pytest.mark.parametrize("rel_path,names", sorted(_BEARER_CODE_FILES.items()))
def test_bearer_codes_never_logged_raw(rel_path, names):
    path = REPO_ROOT / rel_path
    if not path.exists():
        pytest.skip(f"{rel_path} отсутствует")

    offenders = []
    for lineno, snippet in _log_calls(path):
        if "mask_secret" in snippet:
            continue
        for name in names:
            # Голая подстановка кода: {gift_code} в f-строке или
            # позиционный аргумент gift_code у %s-записи.
            if f"{{{name}}}" in snippet or f", {name}" in snippet or f" {name}," in snippet:
                offenders.append(f"{rel_path}:{lineno} -> {snippet.strip()[:120]}")
                break

    assert not offenders, (
        "предъявительский код пишется в лог целиком:\n" + "\n".join(offenders)
    )


def test_live_promocodes_are_masked_in_the_store():
    """`database/promo.py` пишет и живые коды, и мёртвые. Маска нужна ровно
    на живых.

    Живой код — тот, которым в момент записи ещё можно воспользоваться:
    только что созданный (PROMO_CREATED — впереди весь max_uses), заново
    включённый (PROMO_REACTIVATED), успешно провалидированный
    (PROMOCODE_VALIDATED — валидация проходит ТОЛЬКО для активного
    неисчерпанного кода), потреблённый (PROMOCODE_CONSUMED — остаётся
    рабочим, пока used_count < max_uses).

    Мёртвый — исчерпанный (PROMO_EXHAUSTED) и выключенный
    (PROMO_DEACTIVATED). Там код оставлен целиком сознательно: прятать
    нечего, а «какая кампания кончилась» — единственный смысл записи.
    """
    path = REPO_ROOT / "database/promo.py"
    live_markers = (
        "PROMO_CREATED", "PROMO_RECREATED", "PROMO_REACTIVATED",
        "PROMO_CONFLICT", "PROMOCODE_VALIDATED", "PROMOCODE_CONSUMED",
        "PROMO_USAGE_INCREMENTED",
    )
    offenders = []
    for lineno, snippet in _log_calls(path):
        if not any(m in snippet for m in live_markers):
            continue
        if "mask_secret" in snippet:
            continue
        offenders.append(f"database/promo.py:{lineno} -> {snippet.strip()[:120]}")

    assert not offenders, (
        "рабочий промокод пишется в лог целиком — кто прочитал, тот получил "
        "скидку:\n" + "\n".join(offenders)
    )


def test_dead_promocodes_are_left_readable():
    """Обратная сторона: маскировка того, что не секрет, мешает разбору.

    Исчерпанный и выключенный код уже не работает. Спрятать его — значит
    выкинуть из записи единственный факт, ради которого её читают, и не
    закрыть при этом ничего.
    """
    src = (REPO_ROOT / "database/promo.py").read_text(encoding="utf-8")
    for marker in ("PROMO_EXHAUSTED", "PROMO_DEACTIVATED"):
        line = next(ln for ln in src.splitlines() if marker in ln and "logger" in ln)
        assert "mask_secret" not in line, (
            f"{marker}: замаскирован код, который уже не работает — "
            f"запись стала бесполезной, не закрыв ничего"
        )


def test_promo_rejection_keeps_the_code_readable():
    """`promo_validation_failed` — единственная запись, отвечающая на самое
    частое обращение «промокод не принимается».

    Ветка достижима только когда get_active_promo_by_code ничего не нашла:
    код неактивен, просрочен, исчерпан или это опечатка. Рабочим он здесь
    быть не может, поэтому маска не спрятала бы ничего, а разбор («что
    именно человек ввёл») сделала бы невозможным.
    """
    src = (REPO_ROOT / "app/handlers/payments/promo_fsm.py").read_text(encoding="utf-8")
    block = src[src.index("promo_validation_failed"):][:200]
    assert "mask_secret" not in block, (
        "замаскирован отвергнутый код — обращение «промокод не работает» "
        "снова нечем разбирать"
    )
    applied = src[src.index("promo_applied:"):][:200]
    assert "mask_secret" in applied, (
        "успешно применённый (то есть рабочий) промокод пишется целиком"
    )


def test_steam_account_login_is_not_logged_raw():
    """Логин Steam лежит в переиспользованной колонке `country`, и
    `database/pending_purchases.py:_country_for_log` редактирует её до
    <redacted:account> именно потому, что это учётка покупателя.

    Две записи в обработчике печатали то же значение целиком, рядом с
    telegram_id, на каждой покупке — в обход уже принятого решения по
    этому классу данных (email Spotify лежит в той же колонке).
    """
    path = REPO_ROOT / "app/handlers/payments/steam_purchase.py"
    offenders = []
    for lineno, snippet in _log_calls(path):
        if "login" not in snippet:
            continue
        if "mask_secret" in snippet:
            continue
        offenders.append(f"steam_purchase.py:{lineno} -> {snippet.strip()[:120]}")

    assert not offenders, (
        "учётная запись покупателя в Steam пишется в лог целиком:\n"
        + "\n".join(offenders)
    )


def test_site_link_token_is_never_printed_by_a_fixed_slice():
    """Токен привязки сайта печатался как `payload[:16]` при проверке длины
    `len(payload) >= 10`.

    То есть любой токен короче 17 символов уходил в лог ЦЕЛИКОМ. Токен
    предъявительский: кто его прочитал, тот привязал свой Telegram к чужому
    аккаунту на сайте, а следом идут sync_balance и sync_referrals — то есть
    чужие деньги. Маска обязана считать длину сама; срез по фиксированному
    числу символов снова откроет короткие токены целиком.
    """
    path = REPO_ROOT / "app/handlers/user/start/command.py"
    seen = set()
    for lineno, snippet in _log_calls(path):
        if "SITE_LINK_SUCCESS" not in snippet and "SITE_LINK_FAILED" not in snippet:
            continue
        seen.add("SITE_LINK_SUCCESS" if "SITE_LINK_SUCCESS" in snippet else "SITE_LINK_FAILED")
        assert "payload[:" not in snippet, (
            f"command.py:{lineno}: токен привязки снова режется срезом — "
            f"короткие токены пишутся целиком"
        )
        assert "mask_secret(payload)" in snippet, (
            f"command.py:{lineno}: токен привязки не замаскирован"
        )
    assert seen == {"SITE_LINK_SUCCESS", "SITE_LINK_FAILED"}, (
        f"проверены не все записи о привязке сайта: {seen}"
    )


def test_the_mask_hides_a_short_token_completely():
    """Проверка самой маски на границе: токен минимальной допустимой длины
    (10 символов) не должен узнаваться по своему следу в логе."""
    from app.utils.security import mask_secret

    token = "abcdefghij"
    masked = mask_secret(token)
    assert token not in masked
    assert masked.startswith("*")
    assert len(masked) == len(token)


def test_spotify_account_email_not_logged_on_purchase_creation():
    """Spotify и Steam переиспользуют колонку country под email/логин
    покупателя, а promo_code — под пароль от аккаунта Spotify. Запись о
    создании покупки печатала country как есть, то есть на каждой покупке
    в лог уровня INFO уходил email рядом с telegram_id."""
    import database.pending_purchases as pp

    src = inspect.getsource(pp.create_pending_purchase)

    assert "_country_for_log" in src, (
        "country должен редактироваться для покупок, где в нём лежит "
        "учётная запись покупателя"
    )
    assert "country={country}" not in src, "country пишется в лог без редакции"
    # Пароль лежит в promo_code — его не должно быть в записи ни в каком виде.
    assert "promo_code={promo_code}" not in src


# ─────────────────────────────────────────────────────────────────────
# 3. Лог не врёт: исход берётся из результата, а не из намерения
# ─────────────────────────────────────────────────────────────────────

_DELIVERY_FUNCS = [
    "deliver_gift",
    "deliver_premium",
    "deliver_stars",
    "deliver_steam",
    "deliver_spotify",
    "deliver_apple_id",
    "deliver_traffic_pack",
]


@pytest.mark.parametrize("func_name", _DELIVERY_FUNCS)
def test_goods_delivery_exit_outcome_is_not_hardcoded_success(func_name):
    """Каждая ветка выдачи товара завершалась log_handler_exit со строковым
    литералом outcome="success" — в том числе после except, погасившего сбой
    выдачи. По контракту log_handler_exit уровень записи следует из outcome,
    поэтому провал выдачи не только помечался успехом, но и падал с ERROR до
    INFO: заказ «оплачен, не выдан» в аудите неотличим от выданного."""
    from app.handlers.payments import goods_delivery

    func = getattr(goods_delivery, func_name)
    tree = ast.parse(inspect.getsource(func))

    outcomes = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "log_handler_exit":
            continue
        for kw in node.keywords:
            if kw.arg == "outcome":
                outcomes.append(kw.value)

    assert outcomes, f"{func_name}: нет ни одного log_handler_exit"

    # Ветка считается честной, если исход либо вычисляется (переменная), либо
    # среди литералов есть не только "success" — то есть провал выдачи
    # отражается в итоговой записи, а не растворяется в ней.
    reports_failure = any(
        not isinstance(node, ast.Constant) or node.value != "success"
        for node in outcomes
    )
    assert reports_failure, (
        f"{func_name}: единственный исход — литерал 'success'. Провал выдачи "
        f"будет помечен успехом, а уровень записи по контракту "
        f"log_handler_exit упадёт с ERROR до INFO"
    )


def test_every_goods_delivery_branch_closes_its_span():
    """log_handler_entry открывает span в process_successful_payment. Ветка
    Apple ID его не закрывала вовсе: по логам покупка обрывалась на входе в
    обработчик."""
    from app.handlers.payments import goods_delivery

    for func_name in _DELIVERY_FUNCS:
        src = inspect.getsource(getattr(goods_delivery, func_name))
        assert "log_handler_exit" in src, f"{func_name} не закрывает span"


def test_traffic_notification_reports_delivery_result():
    """Флаг однократности ставится независимо от исхода отправки, поэтому
    неудача окончательна — повтора не будет. Функция обязана сообщать
    вызывающему, дошло ли сообщение; раньше она всегда возвращала None, и
    отличить доставленное уведомление от съеденного исключением было
    нечем."""
    from app.workers import traffic_monitor

    src = inspect.getsource(traffic_monitor._send_traffic_notification)
    assert "return True" in src and "return False" in src, (
        "_send_traffic_notification должна возвращать результат отправки"
    )

    caller = inspect.getsource(traffic_monitor._check_user_traffic)
    assert "delivered" in caller, "результат отправки должен использоваться"
    assert "TRAFFIC_NOTIFICATION_LOST" in caller, (
        "потерянное навсегда уведомление должно оставлять запись"
    )


def test_activation_notification_logged_only_when_delivered():
    """safe_send_message возвращает None, если человек заблокировал бота.
    Запись ACTIVATION_NOTIFICATION_SENT ставилась всё равно: подписка
    активна, ключ выдан, человек не знает — а лог утверждал обратное."""
    import activation_worker

    src = inspect.getsource(activation_worker)
    # Именно сам вызов записи, а не упоминание тега в комментарии рядом.
    idx = src.find('f"ACTIVATION_NOTIFICATION_SENT')
    assert idx != -1, "запись ACTIVATION_NOTIFICATION_SENT не найдена"

    window = src[max(0, idx - 400):idx]
    assert "if telegram_message_sent:" in window, (
        "ACTIVATION_NOTIFICATION_SENT должна стоять под проверкой результата"
    )
    assert "ACTIVATION_NOTIFICATION_UNDELIVERED" in src, (
        "недоставленное уведомление должно оставлять собственную запись"
    )


def test_trial_24h_reminder_not_reported_as_sent_on_failure():
    """Ветка выполняется и при status='failed_permanently'. Текстовая запись
    в обоих случаях говорила '_sent', и подсчёт отправленных напоминаний по
    логу давал завышенное число."""
    import trial_notifications

    src = inspect.getsource(trial_notifications)
    idx = src.find("trial_reminder_24h_sent")
    assert idx != -1

    assert "if success:" in src[max(0, idx - 400):idx], (
        "запись об отправке должна стоять под проверкой success"
    )
    assert "trial_reminder_24h_undelivered" in src


def test_site_sync_counts_only_real_successes():
    """sync_balance/sync_referrals не бросают при отказе сайта — _post
    отдаёт None. Счётчик synced увеличивался по факту возврата из вызова,
    поэтому при полностью лежащем сайте итог гласил «synced=500 errors=0»."""
    from app.workers import site_sync_worker

    src = inspect.getsource(site_sync_worker.site_sync_worker_task)

    assert "is not None" in src, "результат синхронизации должен проверяться"
    assert "SITE_SYNC_USER_FAILED" in src, (
        "отказ сайта по конкретному пользователю должен быть виден в логе"
    )


def test_site_sync_cashback_logs_applied_not_received():
    """Это деньги на баланс: в записи должно стоять начисленное, а не
    присланное сайтом. Позиции с amountRubles <= 0 не начисляются, но в
    старую запись попадали — лог и баланс расходились."""
    from app.services import site_sync

    src = inspect.getsource(site_sync.sync_balance)

    assert "applied_count" in src and "applied_rubles" in src
    assert "items=%d" not in src, "логировался размер выборки вместо начисленного"


# ─────────────────────────────────────────────────────────────────────
# 4. Ошибка не глохнет
# ─────────────────────────────────────────────────────────────────────

def _silent_handlers(path: Path):
    """except-блоки без единой записи в лог и без проброса исключения."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        speaks = False
        for inner in ast.walk(node):
            if isinstance(inner, ast.Raise):
                speaks = True
                break
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                if inner.func.attr in {
                    "debug", "info", "warning", "error", "exception", "critical",
                }:
                    speaks = True
                    break
        if not speaks:
            yield node.lineno


# Пути, по которым идут деньги или выдача доступа. Молчаливый перехват здесь
# означает, что обращение «заплатил, товара нет» разобрать нечем.
_MONEY_PATHS = [
    "app/services/payments/confirmation.py",
    "app/handlers/payments/goods_delivery.py",
    "app/services/site_sync.py",
    "app/workers/site_sync_worker.py",
    "app/workers/traffic_monitor.py",
]


@pytest.mark.parametrize("rel_path", _MONEY_PATHS)
def test_no_silent_exception_handlers_on_money_paths(rel_path):
    path = REPO_ROOT / rel_path
    if not path.exists():
        pytest.skip(f"{rel_path} отсутствует")

    silent = list(_silent_handlers(path))
    assert not silent, (
        f"{rel_path}: перехват без записи в лог на строках {silent} — "
        f"ошибка на денежном пути исчезает бесследно"
    )


def test_gift_balance_debit_failure_is_logged():
    """Отказ списания не оставлял в логах ничего: человек видел «ошибка
    обработки платежа», а по логам покупки не существовало вовсе."""
    src = (REPO_ROOT / "app/handlers/callbacks/gift/payment.py").read_text(encoding="utf-8")
    assert "GIFT_BALANCE_DEBIT_FAILED" in src


def test_purchase_tariff_lookup_failure_is_logged():
    """Хелпер зовётся только с путей, где оплата уже сорвалась. Молчание
    здесь съедало вторую ошибку поверх первой: в алерте админу тариф и срок
    оказывались пустыми без единого следа почему."""
    from app.services.payments import confirmation

    src = inspect.getsource(confirmation._lookup_purchase_tariff)
    assert "PURCHASE_TARIFF_LOOKUP_FAILED" in src
    assert "except Exception:\n        pass" not in src


def test_retry_signal_is_reported_and_not_swallowed():
    """_send_confirmation бросает TransientPaymentError, чтобы вебхук ответил
    5xx и провайдер повторил платёж. Общий `except Exception` гасил сигнал
    записью «payment was successful»: человек оплачивал комбо, гигабайты не
    приходили, повтора не было.

    Теперь сигнал не гасится, а пробрасывается, — маркер переименован
    вместе с поведением: «SILENTLY» больше не про что писать. Запись
    осталась: отказ выдачи обязан быть виден, даже когда повтор его чинит.
    """
    from app.services.payments import confirmation

    src = inspect.getsource(confirmation.process_confirmed_payment)
    assert "except TransientPaymentError" in src, (
        "сигнал на повтор должен разбираться отдельной веткой"
    )
    assert "PAYMENT_DELIVERY_FAILED" in src
    assert "PAYMENT_DELIVERY_FAILED_SILENTLY" not in src, (
        "сигнал на повтор снова проглатывается"
    )


# ─────────────────────────────────────────────────────────────────────
# 6. Контекст записи доезжает до вывода
# ─────────────────────────────────────────────────────────────────────

def _record_with_extra(msg: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)
    record.__dict__.update(extra)
    return record


class TestExtraReachesTheOutput:
    """extra={...} обязан попадать в вывод — раньше он исчезал целиком.

    ЧТО БЫЛО

        45 мест по дереву кладут в extra ВЕСЬ смысл записи, а в сообщении
        оставляют голую константу:

            logger.critical("ACTIVATION_FAILED_NO_VPN_KEY",
                            extra={"telegram_id": ..., "purchase_id": ...})

        Ни текстовый шаблон ("%(message)s"), ни JSONFormatter (он собирал
        фиксированный словарь из четырёх полей) содержимое extra не
        читали. Оно доезжало до форматтера и исчезало.

        То есть единственная запись о том, что человеку не выдали
        оплаченный ключ, выглядела в логе как строка без единого
        идентификатора. По ней нельзя было понять даже, о ком речь.

    ПОЧЕМУ ОБА ФОРМАТА

        JSON — штатный для прода, текстовый — по умолчанию, то есть на нём
        сидят разработка и stage. Починить один и забыть другой значит
        оставить половину случаев неразбираемыми.
    """

    def test_json_output_carries_the_context(self):
        from app.core.logging_config import JSONFormatter

        import json as json_module

        out = json_module.loads(JSONFormatter().format(
            _record_with_extra("ACTIVATION_FAILED_NO_VPN_KEY",
                               telegram_id=12345, purchase_id="p1"),
        ))
        assert out["telegram_id"] == 12345, (
            "контекст записи снова теряется: по такой строке нельзя понять, "
            "кому не выдали оплаченный ключ"
        )
        assert out["purchase_id"] == "p1"

    def test_text_output_carries_the_context(self):
        from app.core.logging_config import TextFormatter

        out = TextFormatter("%(message)s").format(
            _record_with_extra("ACTIVATION_FAILED_NO_VPN_KEY", telegram_id=12345),
        )
        assert "telegram_id=12345" in out, out

    def test_the_tail_is_greppable_by_identifier(self):
        """Разбор идёт через `grep telegram_id=<id>` — формат хвоста важен."""
        from app.core.logging_config import TextFormatter

        out = TextFormatter("%(message)s").format(
            _record_with_extra("X", telegram_id=777),
        )
        assert "telegram_id=777" in out

    @pytest.mark.parametrize("secret,leak", [
        ("vless://uuid@host:443?sni=x#name", "uuid@host"),
        ("https://panel.example.com/sub/aBc123ShortUuid", "ShortUuid"),
    ])
    def test_secrets_in_extra_are_redacted_too(self, secret, leak):
        """Фильтр правит record.msg и про добавленные поля не знает.

        Значит, чистить их обязан сам форматтер — иначе новый путь вывода
        стал бы новым путём утечки подписочной ссылки.
        """
        from app.core.logging_config import JSONFormatter, TextFormatter

        import json as json_module

        as_json = json_module.dumps(json_module.loads(JSONFormatter().format(
            _record_with_extra("KEY_ISSUED", link=secret),
        )), ensure_ascii=False)
        as_text = TextFormatter("%(message)s").format(
            _record_with_extra("KEY_ISSUED", link=secret),
        )
        for out in (as_json, as_text):
            assert leak not in out, f"секрет из extra утёк в вывод: {out}"

    @pytest.mark.parametrize("value", [
        {"link": "https://panel.example.com/api/sub/LEAKED_TOKEN"},
        ["https://panel.example.com/api/sub/LEAKED_TOKEN"],
        ("https://panel.example.com/api/sub/LEAKED_TOKEN",),
        {"nested": {"deeper": ["https://panel.example.com/api/sub/LEAKED_TOKEN"]}},
        {"https://panel.example.com/api/sub/LEAKED_TOKEN": "ok"},
    ])
    def test_secrets_nested_inside_extra_are_redacted_too(self, value):
        """Дыра, найденная замером живого конвейера, а не чтением кода.

        JSONFormatter чистил значение из extra ТОЛЬКО если это `str`:

            log_entry[key] = _clean(value) if isinstance(value, str) else value

        Достаточно было положить ту же ссылку в словарь или список, и она
        уезжала в лог как есть. При этом в текстовом режиме та же запись
        редактировалась — хвост собирается f-строкой и чистится целиком.
        То есть два формата вывода расходились в том, что именно прячут,
        а дыра была ровно в том, который включён в проде (LOG_FORMAT=json).
        """
        import json as json_module

        from app.core.logging_config import JSONFormatter, TextFormatter

        as_json = JSONFormatter().format(_record_with_extra("KEY_ISSUED", payload=value))
        as_text = TextFormatter("%(message)s").format(
            _record_with_extra("KEY_ISSUED", payload=value),
        )
        for out in (as_json, as_text):
            assert "LEAKED_TOKEN" not in out, (
                f"секрет во вложенном extra утёк в вывод: {out}"
            )
        # Запись обязана остаться разбираемой: вычистили секрет, а не поле.
        assert "payload" in json_module.loads(as_json)

    def test_non_string_extra_values_keep_their_type(self):
        """Обратная ошибка: превратить всё в строку. Тогда в агрегаторе
        пропадёт возможность фильтровать по числовым полям."""
        import json as json_module

        from app.core.logging_config import JSONFormatter

        out = json_module.loads(JSONFormatter().format(
            _record_with_extra("X", telegram_id=12345, ok=True, ratio=1.5, nothing=None),
        ))
        assert out["telegram_id"] == 12345 and isinstance(out["telegram_id"], int)
        assert out["ok"] is True
        assert out["ratio"] == 1.5
        assert out["nothing"] is None

    def test_no_service_fields_leak_into_the_output(self):
        """Обратная ошибка: в вывод поедет весь служебный состав записи."""
        from app.core.logging_config import TextFormatter

        out = TextFormatter("%(message)s").format(
            _record_with_extra("X", telegram_id=1),
        )
        for service in ("pathname=", "levelno=", "msecs=", "relativeCreated="):
            assert service not in out, f"в вывод попало служебное поле: {out}"

    def test_a_record_without_extra_is_unchanged(self):
        from app.core.logging_config import TextFormatter

        assert TextFormatter("%(message)s").format(
            _record_with_extra("BARE"),
        ) == "BARE"

    def test_the_setup_actually_installs_this_formatter(self):
        """Иначе форматтер починен, а бот пишет прежним — и никто не заметит."""
        import inspect

        from app.core import logging_config

        src = inspect.getsource(logging_config.setup_logging)
        assert "TextFormatter(" in src, (
            "setup_logging снова ставит голый logging.Formatter — "
            "контекст записей опять теряется в текстовом режиме"
        )
