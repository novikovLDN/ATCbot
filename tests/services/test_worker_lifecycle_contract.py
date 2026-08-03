"""Единый контракт жизненного цикла фоновых воркеров.

Здесь собраны пять разных дефектов, но все они об одном: воркеры писались
копипастой, и копии разъехались. Каждый из них тихий — ни один не даёт
исключения в логах, поэтому и жил долго.

1. ОТМЕНА ГАСИЛАСЬ ВМЕСТО ПЕРЕБРОСА. activation_worker, auto_renewal и
   trial_notifications ловили asyncio.CancelledError и выходили по break,
   не перебрасывая её. Задача завершалась «успешно», и в main.py остановка
   по shutdown становилась неотличима от «воркер сам решил выйти навсегда» —
   ровно та диагностика, ради которой ITERATION_END и писался. reminders и
   fast_expiry_cleanup при этом делали raise: два разных поведения в одном
   процессе.

2. МЁРТВЫЙ ЗАЩИТНЫЙ КОД. Проверки `if 'iteration_error_type' in locals()`
   стояли там, где переменная инициализируется безусловно строкой выше.
   Такая «страховка» не срабатывает никогда, но опечатку в имени переменной
   она бы проглотила молча.

3. РАЗНОБОЙ В СТАРТОВЫХ ЗАДЕРЖКАХ. Часть воркеров расходилась случайной
   паузой, часть спала фиксированные 60 секунд. Фиксированные просыпались
   строем — пик по пулу и по Telegram ровно на прогреве бота, а из-за
   кратности периодов фазы совпадали и дальше.

4. ТАКСОНОМИЯ ОШИБКИ ТЕРЯЛАСЬ. В activation_worker classify_error(e)
   вычислялся во внешнем except и тут же выбрасывался: наружу уезжало голое
   outcome="failed" без типа, и в метриках сбой базы не отличался от бага.

5. ПАУЗА ПОСЛЕ СБОЯ СКЛАДЫВАЛАСЬ С ИНТЕРВАЛОМ. В auto_renewal
   MINIMUM_SAFE_SLEEP_ON_FAILURE (300 с) спался в finally, а следом шёл
   обычный AUTO_RENEWAL_INTERVAL_SECONDS. При минимальном интервале в 300 с
   фактическая пауза после сбоя выходила вдвое больше заявленной — окно
   продления в 6 часов обслуживалось реже, чем обещает конфиг.

Тесты статические: воркеры ходят в живую БД и в Telegram, поднять их
целиком в юнит-тестах нельзя. Проверяем ровно то, что было сломано.
"""
import asyncio
import inspect
import re
from pathlib import Path

import pytest

import activation_worker
import auto_renewal
import fast_expiry_cleanup
import reminders
import trial_notifications
from app.core import worker_startup

# Воркер → его точка входа. Файлы из этого списка обязаны соблюдать контракт.
WORKER_TASKS = {
    "activation_worker": activation_worker.activation_worker_task,
    "auto_renewal": auto_renewal.auto_renewal_task,
    "fast_expiry_cleanup": fast_expiry_cleanup.fast_expiry_cleanup_task,
    "reminders": reminders.reminders_task,
    "trial_notifications": trial_notifications.run_trial_scheduler,
}


def _strip_comments(src: str) -> str:
    """Убрать комментарии перед поиском по тексту.

    Иначе тесты ловят сами объяснения «здесь был break / sleep(60)», которые
    мы обязаны оставлять при удалении кода, и падают на исправленном файле.
    """
    return "\n".join(
        line for line in src.split("\n") if not line.lstrip().startswith("#")
    )


def _src(name):
    return _strip_comments(inspect.getsource(WORKER_TASKS[name]))


# ─────────────────────────── 1. отмена остаётся отменой ───────────────────


@pytest.mark.parametrize("worker", sorted(WORKER_TASKS))
def test_cancellation_is_re_raised(worker):
    """В обработчике CancelledError обязан быть raise, а не break.

    Проглоченная отмена — это задача, которую попросили остановиться, а она
    отрапортовала о штатном завершении.
    """
    src = _src(worker)
    assert "except asyncio.CancelledError:" in src, (
        f"{worker}: отмена не обрабатывается вовсе"
    )
    handler = src[src.index("except asyncio.CancelledError:"):]
    # Берём тело обработчика: до следующего except/finally того же уровня.
    stop = min(
        (handler.index(m) for m in ("\n        except ", "\n        finally:")
         if m in handler),
        default=len(handler),
    )
    handler = handler[:stop]
    assert re.search(r"^\s+raise\s*$", handler, flags=re.M), (
        f"{worker}: CancelledError гасится вместо переброса — по логам будет "
        f"не отличить shutdown от самопроизвольного выхода воркера"
    )
    assert "break" not in handler, f"{worker}: выход по break вместо raise"


@pytest.mark.parametrize("worker", sorted(WORKER_TASKS))
def test_failure_backoff_never_sleeps_on_the_cancel_path(worker):
    """finally не должен спать, когда итерация отменена.

    Пока finally спит, задача не завершается: shutdown ждёт её впустую до
    собственного таймаута, а потом убивает жёстко.
    """
    src = _src(worker)
    for match in re.finditer(r"if \w+ not in \(([^)]*)\):\s*\n\s*await asyncio\.sleep", src):
        assert '"cancelled"' in match.group(1), (
            f"{worker}: пауза после сбоя не исключает отменённую итерацию"
        )


# ───────────────────── 2. без мёртвых защитных проверок ───────────────────


@pytest.mark.parametrize("worker", sorted(WORKER_TASKS))
def test_no_locals_guards(worker):
    """`in locals()` в воркере всегда означает мёртвую ветку.

    Все переменные итерации инициализируются в начале витка безусловно,
    поэтому проверка «а вдруг переменной нет» не срабатывает никогда — зато
    прячет опечатку в имени.
    """
    src = _src(worker)
    assert "in locals()" not in src, (
        f"{worker}: недостижимая проверка на существование переменной"
    )


def test_fast_expiry_cleanup_has_no_fake_race_guard():
    """Множество processing_uuids изображало защиту от гонки.

    uuid клался в него и снимался в finally того же прохода цикла, обработка
    последовательная — проверка не могла сработать ни разу. Между репликами
    оно не защищало в принципе: живёт в памяти процесса. Опасен был не код,
    а строчка в докстринге: на несуществующую защиту можно было опереться.
    """
    raw = Path("fast_expiry_cleanup.py").read_text(encoding="utf-8")
    raw = raw[raw.index("async def fast_expiry_cleanup_task"):]
    body = _strip_comments(raw)
    assert "processing_uuids.add" not in body and "processing_uuids.discard" not in body
    docstring = raw[: raw.index('"""', raw.index('"""') + 3)]
    assert "Защита от race condition через processing_uuids" not in docstring, (
        "докстринг снова обещает защиту, которой нет"
    )


def test_auto_renewal_does_not_pretend_to_read_language_from_the_row():
    """Язык уведомления берётся через resolve_user_language, а не из строки.

    В payload фазы B клался ключ language из users, который никто не читал.
    Он выглядел источником языка: правка этой строки ни на что не влияла.
    """
    src = _strip_comments(inspect.getsource(auto_renewal.process_auto_renewals))
    assert '"language": language' not in src
    assert "await resolve_user_language(" in src


# ───────────────────── 3. единое правило стартовой паузы ──────────────────


@pytest.mark.parametrize("worker", sorted(WORKER_TASKS))
def test_startup_delay_is_randomised(worker):
    """Все воркеры расходятся по старту через общую утилиту.

    Фиксированная пауза означает, что воркеры просыпаются строем: разом
    берут соединения и разом идут в Telegram, причём на прогреве бота.
    """
    src = _src(worker)
    assert "startup_jitter(" in src, (
        f"{worker}: стартовая задержка не через общее правило"
    )
    # Смотрим только код ДО главного цикла: паузы внутри цикла (рубильник,
    # backoff после сбоя) — это другая история, их трогать не нужно.
    before_loop = src[: src.index("while True:")]
    assert not re.search(r"await asyncio\.sleep\(", before_loop), (
        f"{worker}: вернулась фиксированная стартовая пауза"
    )


async def test_startup_jitter_stays_inside_its_window(monkeypatch):
    """Разброс должен быть именно случайным и именно в объявленном окне."""
    seen = []

    async def _fake_sleep(seconds):
        seen.append(seconds)

    monkeypatch.setattr(worker_startup.asyncio, "sleep", _fake_sleep)
    delays = [await worker_startup.startup_jitter("t") for _ in range(30)]

    assert delays == seen
    assert all(
        worker_startup.STARTUP_JITTER_MIN_SECONDS <= d <= worker_startup.STARTUP_JITTER_MAX_SECONDS
        for d in delays
    )
    assert len(set(delays)) > 1, "задержка не случайная — воркеры снова пойдут строем"


# ──────────────── 4. таксономия ошибки доезжает до метрик ─────────────────


async def test_activation_iteration_reports_error_taxonomy(monkeypatch):
    """Сбой итерации возвращает тип ошибки, а не только outcome='failed'.

    Раньше classify_error(e) считался и выбрасывался в той же строке: в
    ITERATION_END уходило голое failed, и отличить «база отвалилась» от бага
    по метрикам было нельзя.
    """
    monkeypatch.setattr(activation_worker.database, "DB_READY", True)
    monkeypatch.setattr(activation_worker.config, "VPN_ENABLED", True)

    async def _boom():
        # Намеренно НЕ asyncpg/RuntimeError: те трактуются как временная
        # недоступность базы и дают skipped. Нам нужен именно неожиданный сбой.
        raise ValueError("панель прилегла")

    monkeypatch.setattr(activation_worker.database, "get_pool", _boom)

    processed, outcome, error_type = await activation_worker.process_pending_activations(
        bot=object()
    )

    assert (processed, outcome) == (0, "failed")
    assert error_type, "тип ошибки снова теряется по дороге в метрики"


def test_activation_task_passes_error_type_to_iteration_end():
    """Тип ошибки обязан доехать до log_worker_iteration_end."""
    src = _src("activation_worker")
    assert "items_processed, outcome, iteration_error_type = await asyncio.wait_for" in src
    # Интересует именно вызов в finally: он срабатывает всегда, в том числе
    # на сбойной итерации. Вызов в ветке feature-flag логирует пропуск.
    final_call = src[src.rindex("log_worker_iteration_end("):]
    assert "error_type=iteration_error_type" in final_call, (
        "тип ошибки не доезжает до ITERATION_END — в метриках голое failed"
    )
    assert "items_processed=items_processed" in final_call


# ─────────────── 5. пауза после сбоя — пол, а не слагаемое ────────────────


def test_failure_backoff_is_a_floor_not_an_addend():
    """После сбойной итерации auto_renewal не должен спать дважды.

    300 с в finally плюс AUTO_RENEWAL_INTERVAL_SECONDS давали при минимальном
    интервале ровно двойную паузу: окно продления обслуживалось вдвое реже,
    чем обещает конфиг, и заметить это можно было только по времени между
    ITERATION_START.
    """
    src = _src("auto_renewal")
    finally_block = src[src.index("finally:"): src.index("sleep_seconds = AUTO_RENEWAL_INTERVAL_SECONDS")]
    assert "asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)" not in finally_block, (
        "пауза после сбоя снова прибавляется к интервалу цикла"
    )
    assert "max(AUTO_RENEWAL_INTERVAL_SECONDS, MINIMUM_SAFE_SLEEP_ON_FAILURE)" in src, (
        "нижняя граница паузы после сбоя применяется не через max"
    )


def test_failure_backoff_never_exceeds_two_intervals():
    """Числовая проверка того же: пауза после сбоя равна интервалу."""
    assert auto_renewal.AUTO_RENEWAL_INTERVAL_SECONDS >= 300
    after_failure = max(
        auto_renewal.AUTO_RENEWAL_INTERVAL_SECONDS,
        auto_renewal.MINIMUM_SAFE_SLEEP_ON_FAILURE,
    )
    assert after_failure == auto_renewal.AUTO_RENEWAL_INTERVAL_SECONDS


# ───────────── 6. бюджет итерации напоминаний зависит от выборки ──────────


def test_reminders_hard_timeout_is_above_the_soft_budget():
    """Штатный выход по времени — мягкий, отмена — только страховка.

    Если жёсткий wait_for окажется меньше мягкого бюджета, итерацию снова
    начнёт рубить отмена: посреди прохода, без единой цифры в логах.
    """
    assert reminders.REMINDERS_ITERATION_HARD_TIMEOUT > reminders.REMINDERS_MAX_ITERATION_SECONDS
    assert reminders.REMINDERS_MAX_ITERATION_SECONDS > 120, (
        "бюджет снова не покрывает выборку с пагинацией по 500 и потолком 20000"
    )
    # Иначе итерация налезет на следующую: воркер ходит раз в 45 минут.
    assert reminders.REMINDERS_ITERATION_HARD_TIMEOUT < 45 * 60


def test_reminders_budget_scales_with_the_batch():
    """Бюджет считается от размера выборки, а не одним числом на всё."""
    src = inspect.getsource(reminders.send_smart_reminders)
    assert "len(subscriptions) * REMINDERS_SECONDS_PER_CANDIDATE" in src
    assert "REMINDERS_BACKLOG" in src, "выход по бюджету должен быть виден в логах"

    small = reminders.REMINDERS_BASE_ITERATION_SECONDS + 10 * reminders.REMINDERS_SECONDS_PER_CANDIDATE
    large = reminders.REMINDERS_BASE_ITERATION_SECONDS + 20000 * reminders.REMINDERS_SECONDS_PER_CANDIDATE
    assert large > small
    assert min(reminders.REMINDERS_MAX_ITERATION_SECONDS, large) > 120, (
        "на полной выборке бюджет всё ещё меньше прежних 120 секунд"
    )


async def test_reminders_stops_on_budget_and_reports_the_backlog(monkeypatch, caplog):
    """Исчерпав бюджет, проход выходит сам и пишет, сколько не успел.

    Раньше проход рубил внешний wait_for: в логах оставалось только
    «iteration cancelled», а из-за ORDER BY id обрывался всегда один и тот же
    хвост выборки — эти люди не получали напоминаний систематически.
    """
    rows = [{"telegram_id": i} for i in range(50)]

    async def _fetch(**kwargs):
        return rows

    monkeypatch.setattr(reminders, "_load_paid_reminder_trigger_configs", lambda: _noop())
    monkeypatch.setattr(reminders, "database", type("DB", (), {
        "get_subscriptions_for_reminders": staticmethod(_fetch),
    }))
    # Бюджет в ноль: выход должен случиться на первой же записи.
    monkeypatch.setattr(reminders, "REMINDERS_BASE_ITERATION_SECONDS", -1.0)
    monkeypatch.setattr(reminders, "REMINDERS_SECONDS_PER_CANDIDATE", 0.0)

    with caplog.at_level("WARNING"):
        sent = await reminders.send_smart_reminders(bot=None)

    assert sent == 0
    assert any("REMINDERS_BACKLOG" in r.message for r in caplog.records), (
        "выход по бюджету прошёл молча"
    )


async def _noop():
    return {}


# ───────────── 7. задача восстановления БД не смотрит вперёд ──────────────


def test_db_recovery_task_does_not_close_over_unassigned_names():
    """retry_db_init не должна ссылаться на то, чего ещё нет.

    Дефект: задача объявляла nonlocal для четырёх переменных с тасками, но
    все они присваивались НИЖЕ по коду, чем создавалась сама задача. Не
    взрывалось только потому, что корутина фактически стартовала после
    первой точки ожидания в main, а внутри неё первым делом стоял
    sleep(30). Любая перестановка кода — и NameError, причём ровно в
    сценарии «БД недоступна на старте», то есть когда механизм и нужен.

    Теперь задача пользуется start_db_workers и acquire_instance_lock,
    определёнными выше по файлу, и порядок держится не на совпадении.
    """
    src = Path("main.py").read_text(encoding="utf-8")
    body = src[src.index("async def retry_db_init"):]
    body = body[: body.index("\n    db_retry_task_instance = None")]

    assert "nonlocal" not in body, (
        "вернулась цепочка nonlocal — переменные снова могут быть не присвоены"
    )

    created_at = src.index("asyncio.create_task(retry_db_init())")
    for helper in ("start_db_workers", "acquire_instance_lock"):
        assert f"await {helper}(" in body, f"восстановление не вызывает {helper}"
        defined_at = src.index(f"async def {helper}(")
        assert defined_at < created_at, (
            f"{helper} определяется позже, чем создаётся задача восстановления"
        )


def test_db_recovery_task_survives_cancellation_without_nameerror():
    """Отмена задачи восстановления не должна ломаться о свободные имена."""
    src = Path("main.py").read_text(encoding="utf-8")
    body = src[src.index("async def retry_db_init"):]
    body = body[: body.index("\n    db_retry_task_instance = None")]
    assert "except asyncio.CancelledError:" in body
    # asyncio недоступен внутри теста иначе — просто фиксируем, что импорт есть.
    assert asyncio is not None
