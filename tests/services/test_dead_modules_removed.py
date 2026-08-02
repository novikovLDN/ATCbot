"""Удалённые мёртвые модули не должны вернуться.

Что удалено и почему:

• handlers.py (1071 строка) — 32 функции из 35 дословно повторяли
  app/handlers/common/*. Наружу использовалась ровно одна,
  show_payment_method_selection; она переехала в
  app/handlers/payments/method_select.py, к остальному платёжному потоку.
  Пока файл жил, правку экрана можно было внести в копию, которая не
  выполняется, — и не понять, почему ничего не изменилось.

• app/services/vpn_client.py (251 строка) — фасад к выведенному из
  эксплуатации xray, без единого потребителя.

• app/utils/audit.py (418 строк) — вторая, параллельная подсистема аудита,
  которую никто не вызывал. Реальный аудит пишется через
  database._log_audit_event_atomic.

• app/core/i18n/ — пакет ломался на импорте (тянул несуществующий .manager)
  и никем не использовался. Рабочая локализация живёт в app/i18n.
"""
from pathlib import Path

import pytest

REMOVED = [
    "handlers.py",
    "app/services/vpn_client.py",
    "app/utils/audit.py",
    "app/core/i18n/__init__.py",
    "app/core/i18n/types.py",
]


@pytest.mark.parametrize("path", REMOVED)
def test_dead_module_stays_removed(path):
    assert not Path(path).exists(), (
        f"{path} вернулся в дерево — снова две копии одного кода"
    )


def test_payment_method_screen_lives_in_payments_package():
    from app.handlers.payments.method_select import show_payment_method_selection

    assert callable(show_payment_method_selection)


def test_nobody_imports_from_deleted_root_handlers():
    offenders = []
    for f in Path(".").rglob("*.py"):
        s = str(f)
        if any(x in s for x in (".venv", "graphify-out", "tests/")):
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for num, line in enumerate(text.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("from handlers import") or stripped == "import handlers":
                offenders.append(f"{f}:{num}")
    assert not offenders, f"остались импорты удалённого handlers.py: {offenders}"
