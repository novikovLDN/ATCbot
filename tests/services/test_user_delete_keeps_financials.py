"""Удаление пользователя не должно переписывать выручку задним числом.

Дефект: admin_delete_user_complete в одной транзакции делал DELETE FROM
payments, pending_purchases и balance_transactions. Админ удалял одного
платившего пользователя — и «Общий доход», ARPU, LTV и график по дням
пересчитывались на других числах. Отчёт за прошлый месяц переставал
сходиться сам с собой, а причину по логам было не найти: строк больше нет.

Внешних ключей на users у этих таблиц нет, персональных данных в них тоже
нет — только telegram_id, суммы и тарифы. Поэтому финансовые строки
остаются, а в audit_log пишется, сколько их и на какую сумму.
"""
import re
from pathlib import Path

# Удаление пользователя переехало из database/admin.py (тот стал фасадом)
# в модуль, где собраны все админские действия над доступом.
SRC = Path("database/admin_access.py")

FINANCIAL_TABLES = ("payments", "pending_purchases", "balance_transactions")


def _delete_body() -> str:
    text = SRC.read_text(encoding="utf-8")
    start = text.index("async def admin_delete_user_complete")
    # Функция может оказаться последней в файле — тогда следующего
    # `async def` просто нет, и берём всё до конца.
    end = text.find("\nasync def ", start + 10)
    return text[start:] if end == -1 else text[start:end]


def test_financial_tables_are_not_deleted():
    body = _delete_body()
    for table in FINANCIAL_TABLES:
        pattern = re.compile(rf"DELETE FROM {table}\b")
        assert not pattern.search(body), (
            f"{table} удаляется — выручка изменится задним числом"
        )


def test_personal_tables_are_still_deleted():
    """Удаление обязано оставаться удалением: профиль и доступ уходят."""
    body = _delete_body()
    for table in ("users", "subscriptions", "referrals", "vip_users"):
        assert f"DELETE FROM {table} WHERE" in body, f"{table} перестала удаляться"


def test_audit_log_records_what_was_kept():
    """Иначе потом нечем объяснить telegram_id в выручке, которого нет в users."""
    body = _delete_body()
    assert "Финансовая история сохранена" in body
    assert "payments_kopecks" in body and "purchases_kopecks" in body
    assert "admin_delete_user" in body


def test_counts_are_taken_before_deletion():
    """Считать после удаления бессмысленно — порядок важен."""
    body = _delete_body()
    assert body.index("AS payments_n") < body.index("DELETE FROM users WHERE")
