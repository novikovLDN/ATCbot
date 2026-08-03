"""Устойчивость воркера активации к плохой записи в очереди.

Дефект: ActivationNotAllowedError не перехватывался внутри цикла и улетал
во внешний except всей функции. Обработка обрывалась, и все подписки после
испорченной оставались неактивированными до следующего тика — одна запись
блокировала выдачу всем остальным.
"""
import inspect

import pytest

import activation_worker as aw


def _loop_src():
    return inspect.getsource(aw.process_pending_activations)


@pytest.mark.parametrize("exc", [
    "ActivationNotAllowedError",
    "ActivationServiceError",
    "ActivationFailedError",
    "VPNActivationError",
])
def test_domain_exceptions_caught_inside_loop(exc):
    """Каждое доменное исключение должно обрабатываться внутри цикла."""
    assert f"except {exc}" in _loop_src(), (
        f"{exc} не перехвачен — одна запись оборвёт обработку очереди"
    )


def test_not_allowed_does_not_stop_queue():
    src = _loop_src()
    block = src[src.index("except ActivationNotAllowedError"):]
    block = block[:block.index("\n                except ") if "\n                except " in block else len(block)]
    assert "raise" not in block, "проброс исключения оборвёт очередь"
    assert "очередь продолжается" in block


def test_service_error_does_not_stop_queue():
    src = _loop_src()
    block = src[src.index("except ActivationServiceError"):]
    assert "raise" not in block.split("await asyncio.sleep")[0]


def test_exceptions_imported():
    """Перехват несуществующего имени сам стал бы ошибкой."""
    for name in ("ActivationServiceError", "ActivationNotAllowedError"):
        assert hasattr(aw, name), f"{name} не импортирован в модуль"


def test_loop_yields_and_takes_the_next_record():
    """После обработки записи цикл отдаёт управление и берёт следующую.

    Раньше здесь стоял безусловный sleep(0.5) на каждую подписку — он и был
    потолком в ~30 активаций за виток. Теперь пауза платится только за
    отправленное сообщение, а в остальных случаях идёт cooperative_yield.
    """
    src = _loop_src()
    assert "await cooperative_yield()" in src
    assert "NOTIFICATION_PAUSE_SECONDS" in src
