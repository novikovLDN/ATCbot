"""Сброс флагов напоминаний при выдаче и продлении подписки.

Дефект: колонки reminder_7d_sent и reminder_1d_sent (миграция 036) код
выставлял в TRUE после отправки, но в FALSE не возвращал нигде. Сброс в
grant_access перечислял только старые флаги. Итог: напоминание «за 7 дней»
и «за сутки» человек получал один раз за всю жизнь, а при следующих
продлениях оставался без предупреждения об окончании подписки.

Тест статический — по исходнику, потому что grant_access ходит в живую БД.
Проверяем инвариант: в каждом месте, где сбрасывается любой флаг reminder_*,
сбрасываются ВСЕ.
"""
import re
from pathlib import Path

SRC = Path("database/subscriptions.py")

# Все флаги напоминаний об окончании подписки. trial_notif_* — отдельная
# группа со своим сбросом, здесь не участвует.
ALL_REMINDER_FLAGS = {
    "reminder_sent",
    "reminder_3d_sent",
    "reminder_24h_sent",
    "reminder_3h_sent",
    "reminder_6h_sent",
    "reminder_7d_sent",
    "reminder_1d_sent",
}


def _sql_statements(text: str):
    """Грубо режем исходник на SQL-строки: нас интересуют блоки между
    тройными кавычками, внутри которых есть сброс флага напоминания."""
    for block in re.findall(r'"""(.*?)"""', text, flags=re.S):
        if "reminder_sent = FALSE" in block or "reminder_7d_sent = FALSE" in block:
            yield block


def test_every_reset_site_covers_all_reminder_flags():
    text = SRC.read_text(encoding="utf-8")
    blocks = list(_sql_statements(text))
    assert blocks, "не найдено ни одного места сброса флагов — тест устарел"

    for block in blocks:
        reset = {
            m.group(1)
            for m in re.finditer(r"(reminder_\w+) = FALSE", block)
        }
        missing = ALL_REMINDER_FLAGS - reset
        assert not missing, (
            f"в SQL сбрасываются не все флаги напоминаний, забыты: {sorted(missing)}\n"
            f"фрагмент: {block.strip()[:300]}"
        )


def test_migration_resets_stale_flags_for_active_subscriptions():
    """Кода мало: у текущей базы флаги уже стоят в TRUE, и без разового
    сброса напоминания не заработают до следующего продления."""
    migration = Path("migrations/071_reset_stale_reminder_flags.sql")
    assert migration.exists(), "нужна миграция, сбрасывающая залипшие флаги"
    sql = migration.read_text(encoding="utf-8")
    assert "reminder_7d_sent = FALSE" in sql
    assert "reminder_1d_sent = FALSE" in sql
    assert "expires_at > NOW()" in sql, "трогаем только действующие подписки"
