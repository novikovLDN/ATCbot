"""Восстановление после падения БД поднимает все воркеры, а не часть.

Два дефекта:

1. Стартовый код и код восстановления были написаны отдельно, и списки
   разошлись: при восстановлении поднимались пять воркеров из девяти.
   trial-уведомления, ферма, монитор трафика и синхронизация с сайтом
   молчали до перезапуска процесса — без единой ошибки в логах.

2. Advisory-лок single-instance брался только на старте и только при
   готовой БД. Инстанс, стартовавший при недоступной базе, терял гарантию
   «одна реплика» навсегда: задача восстановления лок не пыталась взять
   никогда, и проверка IS_PROD с выходом из процесса не срабатывала. Две
   реплики параллельно продлевают подписки и рассылают уведомления.

Тест статический: main.py поднимает бота, БД и вебхук — импортировать его
в юнит-тестах нельзя. Проверяем ровно то, что было сломано: единственность
списка воркеров и вызов захвата лока при восстановлении.
"""
import re
from pathlib import Path

import pytest

SRC = Path("main.py")


@pytest.fixture(scope="module")
def source():
    return SRC.read_text(encoding="utf-8")


def test_workers_are_declared_once(source):
    assert "DB_DEPENDENT_WORKERS = [" in source, "нет единой таблицы воркеров"
    assert source.count("DB_DEPENDENT_WORKERS = [") == 1


def test_all_known_workers_are_in_the_table(source):
    """Список из аудита: девять воркеров, зависящих от БД."""
    table = source[source.index("DB_DEPENDENT_WORKERS = ["):]
    table = table[: table.index("\n    ]")]
    for name in (
        "reminders", "trial_notifications", "farm_notifications",
        "traffic_monitor", "fast_expiry_cleanup", "auto_renewal",
        "activation_worker", "site_sync", "xray_sync",
    ):
        assert f'"name": "{name}"' in table, f"воркер {name} выпал из таблицы"


def test_startup_and_recovery_use_the_same_function(source):
    """Два вызова: старт и восстановление. Иначе списки снова разъедутся."""
    assert source.count("await start_db_workers(") == 2
    recovery = source[source.index("async def retry_db_init"):]
    assert "await start_db_workers(" in recovery


def test_recovery_does_not_hand_roll_worker_starts(source):
    """В ветке восстановления не должно быть собственных create_task —
    именно так списки и разошлись в прошлый раз."""
    recovery = source[source.index("async def retry_db_init"):]
    recovery = recovery[: recovery.index("\n    # ")] if "\n    # " in recovery else recovery
    offenders = re.findall(r"asyncio\.create_task\((\w+)", recovery)
    assert not offenders, f"ручной запуск воркеров в восстановлении: {offenders}"


def test_instance_lock_is_a_reusable_function(source):
    assert "async def acquire_instance_lock(" in source
    assert source.count("await acquire_instance_lock(") == 2, (
        "лок берётся не в обоих местах (старт + восстановление)"
    )


def test_lock_is_taken_before_workers_on_recovery(source):
    """Иначе восстановленная реплика начнёт продлевать подписки раньше,
    чем выяснит, что она вторая."""
    recovery = source[source.index("async def retry_db_init"):]
    lock_at = recovery.index("await acquire_instance_lock(")
    workers_at = recovery.index("await start_db_workers(")
    assert lock_at < workers_at


def test_prod_still_exits_when_lock_is_unavailable(source):
    block = source[source.index("async def acquire_instance_lock("):]
    block = block[: block.index("await acquire_instance_lock(")]
    assert "config.IS_PROD" in block and "sys.exit(1)" in block
