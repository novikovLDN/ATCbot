"""Бюджет времени итерации у воркеров считается на проход, а не на батч.

Дефект: в fast_expiry_cleanup и auto_renewal отсчёт `time.monotonic()` стоял
ВНУТРИ цикла по батчам и обнулялся на каждой сотне строк. Заявленные 15
секунд на итерацию превращались в 15 секунд НА БАТЧ: при тысяче истёкших
подписок воркер держал event loop минутами — ровно то, от чего лимит и
должен защищать. Комментарий рядом обещал «prevents 300s blocking».

Плюс два дефекта учёта в fast_expiry_cleanup:
  • ветка feature-flag логировала конец итерации вручную, а потом finally
    логировал его второй раз — каждая пропущенная итерация попадала в
    метрики дважды;
  • ветка «БД не готова» не выставляла исход, и переменная оставалась
    "success": недоступность базы выглядела в метриках успехом.

Тест статический: воркеры ходят в живую БД и в Telegram, поднять их в
юнит-тестах нельзя. Проверяем ровно то, что было сломано, — где стоит
отсчёт относительно цикла.
"""
import re
from pathlib import Path

import pytest

WORKERS = {
    "fast_expiry_cleanup.py": "loop_start",
    "auto_renewal.py": "iteration_start",
}


@pytest.mark.parametrize("filename, var", sorted(WORKERS.items()))
def test_budget_is_started_once_per_pass(filename, var):
    """Присваивание отсчёта должно быть ровно одно: второе означает,
    что бюджет где-то обнуляется заново."""
    src = Path(filename).read_text(encoding="utf-8")
    starts = re.findall(rf"^\s*{var} = time\.monotonic\(\)", src, flags=re.M)
    assert len(starts) == 1, (
        f"{filename}: отсчёт бюджета встречается {len(starts)} раз — "
        f"он снова обнуляется внутри цикла"
    )


@pytest.mark.parametrize("filename, var", sorted(WORKERS.items()))
def test_budget_starts_before_the_batch_loop(filename, var):
    """Отсчёт обязан стоять ДО цикла по батчам.

    Тонкость: у воркера два вложенных `while True`. Внешний — сам цикл
    воркера, и там бюджет как раз ОБЯЗАН сбрасываться: это новая итерация.
    Внутренний перебирает батчи внутри одной итерации, и вот перед ним
    отсчёт должен уже идти. Различаем их по отступу: батчевый цикл всегда
    вложен глубже, чем присваивание бюджета.
    """
    lines = Path(filename).read_text(encoding="utf-8").split("\n")
    start_line, start_indent = next(
        (i, len(line) - len(line.lstrip()))
        for i, line in enumerate(lines)
        if re.match(rf"^\s*{var} = time\.monotonic\(\)", line)
    )
    deeper_loops = [
        i for i, line in enumerate(lines)
        if re.match(r"^\s*while True:", line)
        and (len(line) - len(line.lstrip())) >= start_indent
        and i > start_line
    ]
    assert deeper_loops, f"{filename}: цикл по батчам после отсчёта не найден"
    assert start_line < deeper_loops[0], (
        f"{filename}: отсчёт бюджета внутри цикла по батчам"
    )


@pytest.mark.parametrize("filename, var", sorted(WORKERS.items()))
def test_budget_is_checked_between_batches(filename, var):
    """Проверять бюджет только внутри строки батча мало: если батч
    выбран целиком, следующий начнётся всё равно."""
    src = Path(filename).read_text(encoding="utf-8")
    checks = re.findall(rf"time\.monotonic\(\) - {var} > MAX_ITERATION_SECONDS", src)
    assert len(checks) >= 2, (
        f"{filename}: бюджет проверяется только в одном месте — "
        f"между батчами проверки нет"
    )


def test_skipped_iterations_are_marked_as_skipped():
    """Пропущенная итерация не должна выглядеть в метриках успешной."""
    src = Path("fast_expiry_cleanup.py").read_text(encoding="utf-8")
    db_branch = src[src.index("if not database.DB_READY:"):]
    db_branch = db_branch[: db_branch.index("continue")]
    assert 'outcome = "skipped"' in db_branch, (
        "ветка «БД не готова» оставляет outcome='success'"
    )
    assert "iteration_reason" in db_branch, "не записана причина пропуска"


def test_iteration_end_is_logged_exactly_once():
    """finally срабатывает и на continue: ручной вызов рядом с ним давал
    двойную запись в метриках."""
    src = Path("fast_expiry_cleanup.py").read_text(encoding="utf-8")
    calls = re.findall(r"log_worker_iteration_end\(", src)
    assert len(calls) == 1, (
        f"log_worker_iteration_end вызывается {len(calls)} раз — "
        f"пропущенные итерации считаются дважды"
    )


def test_lava_invoice_lifetime_matches_the_promise():
    """Счёт Lava жил 300 секунд, а сообщение с кнопкой — 900, и человеку
    обещали 15 минут: на седьмой минуте он упирался в истёкший счёт."""
    src = Path("lava_service.py").read_text(encoding="utf-8")
    assert "expire: int = config.INVOICE_TIMEOUT_SECONDS" in src, (
        "срок счёта снова задан отдельным числом и разойдётся с обещанием"
    )
