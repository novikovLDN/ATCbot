"""Общая арифметика админских сверок подписок.

ЗАЧЕМ ЭТОТ МОДУЛЬ
    В админке четыре экрана сверяют одно и то же — сколько дней доступа
    человек оплатил и до какой даты он должен действовать:

        audit_subs.py       сверка с датами в панели Remnawave
        audit_db_dates.py   сверка с датами в базе бота
        recovery_premium.py восстановление премиум-сущностей
        reconcile.py        разбор расхождений по одному пользователю

    Различаются они только источником сравнения и набором callback-имён, а
    ядро — «проиграть историю покупок и получить дату окончания» — во всех
    было скопировано дословно. Три копии _compute_real_end и четыре копии
    разбора даты из панели.

ЧЕМ ЭТО ОПАСНО
    Функция считает ДЕНЬГИ в днях доступа. Правка правила продления в одной
    копии оставляет три другие со старым поведением, и два админских экрана
    начинают показывать разные ответы про одного и того же пользователя.
    Понять, какой из них прав, по интерфейсу невозможно.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def compute_real_end(rows: list) -> Optional[datetime]:
    """Дата окончания доступа, вытекающая из истории оплаченных покупок.

    Правило продления: если очередная покупка приходит, пока предыдущий
    период ещё идёт, её дни добавляются к текущему окончанию; если
    предыдущий период уже закончился — новый отсчитывается от даты покупки.
    Это ровно то, что делает grant_access при продлении, поэтому сверка и
    выдача не расходятся.

    Args:
        rows: покупки по возрастанию даты, каждая с ключами `created_at`
            (datetime, допускается naive — считаем UTC) и `period_days`.
            Строки с нулевым или отрицательным периодом пропускаются:
            это пополнения баланса и товары, доступ они не продлевают.

    Returns:
        Дата окончания или None, если оплаченной истории нет вовсе —
        то есть платного доступа у человека никогда не было.
    """
    end: Optional[datetime] = None
    for row in rows:
        created = row["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        days = int(row["period_days"] or 0)
        if days <= 0:
            continue
        if end is None or created >= end:
            end = created + timedelta(days=days)
        else:
            end = end + timedelta(days=days)
    return end


def parse_panel_dt(value) -> Optional[datetime]:
    """Разобрать дату из панели Remnawave в UTC-aware datetime.

    Панель отдаёт ISO-8601, обычно с хвостом «Z», который fromisoformat до
    Python 3.11 не понимал. Naive-результат считаем UTC: панель работает в
    UTC, и локальная интерпретация сдвинула бы срок на часовой пояс сервера.

    Возвращает None на любом мусоре: сверка не должна падать из-за одной
    испорченной записи — она обязана показать остальные.
    """
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def iso_z(dt: Optional[datetime]) -> Optional[str]:
    """Обратное преобразование: datetime → строка для панели Remnawave.

    Панель принимает ISO-8601 с «Z». Naive-дату считаем UTC по той же
    причине, что и при разборе.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["compute_real_end", "parse_panel_dt", "iso_z"]
