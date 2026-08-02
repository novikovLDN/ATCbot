"""Пути импорта платёжных сервисов.

Дефект: обработчик оплаты Telegram Stars через СБП импортировал
platega_service из app.services.payments, где его нет — модуль лежит
в корне проекта. Кнопка падала с ImportError при каждом нажатии, а тесты
этого не видели: обработчики колбэков не покрыты, а импорт локальный
и срабатывает только в момент вызова.
"""
import importlib

import pytest

# Платёжные сервисы провайдеров живут в корне проекта.
ROOT_LEVEL_SERVICES = ["platega_service", "lava_service", "cryptobot_service"]


@pytest.mark.parametrize("module", ROOT_LEVEL_SERVICES)
def test_service_importable_from_root(module):
    assert importlib.import_module(module) is not None


@pytest.mark.parametrize("module", ROOT_LEVEL_SERVICES)
def test_service_not_importable_from_app_services(module):
    """Фиксируем, где модулей НЕТ: иначе неверный путь снова уедет в код."""
    with pytest.raises(ImportError):
        importlib.import_module(f"app.services.payments.{module}")


def test_no_wrong_import_paths_in_codebase():
    """Прямой поиск неверного пути по всему коду."""
    import pathlib
    # Собираем строку по частям, иначе тест найдёт сам себя.
    wrong = "from app.services.payments import " + "platega_service"
    hits = []
    for path in pathlib.Path(".").rglob("*.py"):
        if any(part in {".venv", "graphify-out", "__pycache__"} for part in path.parts):
            continue
        if wrong in path.read_text(encoding="utf-8", errors="ignore"):
            hits.append(str(path))
    assert not hits, f"неверный путь импорта platega_service в: {hits}"


@pytest.mark.parametrize("module,func", [
    ("platega_service", "create_transaction"),
    ("lava_service", "create_invoice"),
    ("cryptobot_service", "process_webhook_data"),
])
def test_expected_entrypoints_exist(module, func):
    """Обработчики зовут именно эти функции — переименование сломает оплату."""
    assert hasattr(importlib.import_module(module), func)
