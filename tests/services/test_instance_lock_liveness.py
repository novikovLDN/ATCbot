"""Advisory-лок single-instance надо перепроверять, а не брать один раз.

Дефект: лок брался на соединении из пула на всё время жизни процесса, и никто
никогда не проверял, что он всё ещё за нами. Postgres снимает session-level
advisory lock автоматически при обрыве соединения — сетевой блип, рестарт
базы, idle_session_timeout.

Сценарий: соединение рвётся, лок молча освобождается, процесс продолжает
считать себя единственной репликой. Следующая реплика при старте спокойно
берёт лок и работает параллельно: две реплики продлевают подписки и шлют
дубли напоминаний. Ни лога, ни алерта при этом раньше не возникало.

Тест статический: main.py поднимает бота, БД и вебхук — импортировать его в
юнит-тестах нельзя. Поведение самой проверки (алерт при потере) покрыто в
test_healthcheck_coverage.py.
"""
from pathlib import Path

import pytest

SRC = Path("main.py")


@pytest.fixture(scope="module")
def source():
    return SRC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def verify_block(source):
    block = source[source.index("async def verify_instance_lock("):]
    return block[: block.index('await acquire_instance_lock("старт")')]


def test_verification_exists(source):
    assert "async def verify_instance_lock(" in source, (
        "проверки удержания лока нет — потеря лока останется незамеченной"
    )


def test_verification_asks_postgres_not_a_local_flag(verify_block):
    """Локальная переменная «мы брали лок» ничего не доказывает.

    Спрашивать надо саму базу и именно то соединение, на котором лок брали:
    pg_locks с pid = pg_backend_pid().
    """
    assert "pg_locks" in verify_block
    assert "pg_backend_pid()" in verify_block


def test_dead_connection_counts_as_lost_lock(verify_block):
    """Упавший запрос — это и есть потеря лока, а не повод промолчать."""
    assert "except Exception" in verify_block
    assert "held = False" in verify_block


def test_lost_lock_is_retaken(verify_block):
    """После потери лок берут заново — той же функцией, что и на старте.

    В PROD неудача перезахвата означает вторую реплику, и acquire_instance_lock
    завершает процесс.
    """
    assert "await acquire_instance_lock(" in verify_block
    assert "instance_lock_conn = None" in verify_block or "instance_lock_conn," in verify_block


def test_lost_lock_alerts_admin(verify_block):
    assert "send_alert(" in verify_block
    assert "force=True" in verify_block


def test_verification_is_wired_into_healthcheck(source):
    """Проверка бесполезна, если её никто не вызывает."""
    assert "healthcheck.register_instance_lock_check(verify_instance_lock)" in source


def test_workers_are_watched_by_healthcheck(source):
    """Живость фоновых задач тоже под наблюдением, включая uvicorn."""
    assert "healthcheck.watch_tasks(started_workers)" in source
    assert "uvicorn_webhook" in source
