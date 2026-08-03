"""Миграционная рассылка удалена — она не должна вернуться.

Дефект (app/services/migration_broadcast.py:83): render_migration_text
возвращал полностью русский HTML («Atlas Secure стал лучше — обнови
ссылку!», «Как обновить»), не принимал language и не звал get_text.
Уходило это всем подряд: немец и араб получали критическое
уведомление о смене инфраструктуры на непонятном языке и рисковали
потерять доступ при оплаченной подписке.

Почему удалили, а не перевели на семь языков:

  • у модуля не было ни одного живого вызова. По всему дереву
    (кроме самого модуля, слоя БД и его же теста) ни одна строка не
    импортировала migration_broadcast — ни хендлер, ни роут дашборда,
    ни воркер. Кнопка «Broadcast migration notice» из админки исчезла
    раньше: от неё остался только осиротевший стейт
    AdminMigrationApply.confirm_broadcast;
  • дата отключения старых ссылок — MIGRATION_CUTOFF_DATE_STR =
    18.05.2026 — прошла. Рассылать «успей до 18.05» в августе нечего;
  • перевод мёртвого текста на семь языков — это работа, которая
    никогда не будет показана человеку.

Следом убраны три осиротевшие функции слоя БД —
count_migration_broadcast_candidates, list_migration_broadcast_candidates,
mark_migration_notice_sent (database/traffic.py) и их реэкспорт из
database/__init__.py. Без рассылки их никто не звал, а выглядели они
рабочей частью API базы.

Что осталось и требует отдельного решения владельца: колонка
subscriptions.migration_notice_sent_at (миграция 049). Её удаление —
миграция схемы, а не правка кода.
"""
from pathlib import Path

import database

REMOVED = [
    "app/services/migration_broadcast.py",
    "tests/services/test_migration_broadcast.py",
]

REMOVED_DB_FUNCS = [
    "count_migration_broadcast_candidates",
    "list_migration_broadcast_candidates",
    "mark_migration_notice_sent",
]


def test_migration_broadcast_module_stays_removed():
    for path in REMOVED:
        assert not Path(path).exists(), (
            f"{path} вернулся: это одноразовая русскоязычная рассылка "
            f"с истёкшей датой отключения ссылок"
        )


def test_db_helpers_stay_removed():
    """Функции слоя БД ушли вместе с рассылкой — и из traffic.py, и из фасада."""
    import database.traffic as traffic

    for name in REMOVED_DB_FUNCS:
        assert not hasattr(traffic, name), f"database.traffic.{name} вернулась без потребителя"
        assert not hasattr(database, name), f"database.{name} снова реэкспортируется"


def test_nothing_imports_migration_broadcast():
    """Импорт удалённого модуля уронит бот на старте, а не в рантайме
    рассылки — поэтому ловим его тестом, а не логами продакшена."""
    offenders = []
    for f in Path(".").rglob("*.py"):
        s = str(f)
        if any(x in s for x in (".venv", "graphify-out", "__pycache__")):
            continue
        if f.name == Path(__file__).name:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for num, line in enumerate(text.split("\n"), 1):
            if "migration_broadcast" in line and ("import" in line or "getattr" in line):
                offenders.append(f"{f}:{num}: {line.strip()}")
    assert not offenders, f"остались ссылки на удалённый модуль: {offenders}"
