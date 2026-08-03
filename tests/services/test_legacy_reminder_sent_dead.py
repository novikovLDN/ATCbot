"""Legacy-пара напоминаний остаётся невызванной, живую подменять нельзя.

ЧТО ЗДЕСЬ ЗА ЛОВУШКА

    В дереве два `mark_reminder_sent` с разными сигнатурами:

      • database.mark_reminder_sent(telegram_id) — legacy. Ставит старый флаг
        subscriptions.reminder_sent, который планировщик напоминаний не
        читает вообще. Вызовов нет.
      • app/services/notifications/service.py:mark_reminder_sent(
            telegram_id, reminder_type, conn) — живая. Её зовёт reminders.py
        через notification_service, она ставит конкретный флаг
        reminder_7d/3d/1d/24h/6h/3h_sent.

    Перепутать легко: имена совпадают, ошибка молчаливая. Вызов
    database.mark_reminder_sent(telegram_id) выставит флаг, который никто не
    проверяет, — пользователь получит напоминание повторно. Вызов с двумя
    аргументами упадёт TypeError уже в проде, в воркере рассылки.

    То же со выборкой: живая — get_subscriptions_for_reminders (окна и
    актуальные флаги), legacy — get_subscriptions_needing_reminder (тот же
    мёртвый reminder_sent).

ПОЧЕМУ LEGACY НЕ УДАЛЁН

    Обе функции лежат в database/reminders_queries.py и удерживаются
    тестом tests/services/test_module_split.py, который требует их наличия
    в фасаде database. Колонка reminder_sent остаётся в схеме (её сбрасывают
    при каждой выдаче доступа) — её снос это миграция схемы, решение
    владельца. Здесь фиксируем главное: в проде их никто не зовёт.
"""
import inspect
from pathlib import Path

import pytest

SKIP_DIRS = (".venv", "graphify-out", "__pycache__", "node_modules", "tests/")
LEGACY = ("mark_reminder_sent", "get_subscriptions_needing_reminder")


def _sources():
    for path in Path(".").rglob("*.py"):
        s = str(path)
        if any(x in s for x in SKIP_DIRS):
            continue
        if s.startswith("database/"):
            # сам слой БД: определение и реэкспорт — это не вызовы
            continue
        yield path


@pytest.mark.parametrize("name", LEGACY)
def test_legacy_reminder_helper_has_no_callers(name):
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for num, line in enumerate(text.split("\n"), 1):
            if f"database.{name}(" in line or f"db.{name}(" in line:
                offenders.append(f"{path}:{num}")
    assert not offenders, (
        f"кто-то зовёт legacy database.{name}: {offenders}. "
        f"Живые: notification_service.mark_reminder_sent / "
        f"database.get_subscriptions_for_reminders"
    )


def test_two_mark_reminder_sent_have_different_signatures():
    """Если сигнатуры вдруг совпадут — перепутать станет ещё проще."""
    import database
    from app.services.notifications.service import mark_reminder_sent as live

    legacy_params = list(inspect.signature(database.mark_reminder_sent).parameters)
    live_params = list(inspect.signature(live).parameters)

    assert legacy_params == ["telegram_id"]
    assert live_params[:2] == ["telegram_id", "reminder_type"]


def test_subscriptions_module_does_not_define_its_own_copy():
    """В database/subscriptions.py остался только реэкспорт с предупреждением."""
    src = Path("database/subscriptions.py").read_text(encoding="utf-8")
    assert "async def mark_reminder_sent" not in src, (
        "третья копия mark_reminder_sent в subscriptions.py"
    )
    assert "ОСТОРОЖНО с первыми двумя именами" in src, (
        "предупреждение над реэкспортом убрали — ловушка снова невидима"
    )
