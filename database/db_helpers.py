"""Приведение времени и безопасные преобразования — то, что зовут из каждого запроса.

ЧТО ЗДЕСЬ
    Две группы мелких функций без единого обращения к базе:
    граница по времени (_to_db_utc / _from_db_utc / _ensure_utc /
    _normalize_subscription_row), генерация UUID подписки и NULL-safe
    приведения (safe_int / safe_float / safe_get).

ПОЧЕМУ ОТДЕЛЬНО ОТ core.py
    Это единственная часть фундамента, которая никому ничего не должна:
    ни пула, ни DB_READY, ни конфига. Отсюда следует главное свойство —
    модуль НЕ импортирует database.core. Кольца не будет, даже если его
    начнут тянуть напрямую из любого места проекта.

    В core.py эти 130 строк лежали между глобальным флагом готовности и
    созданием пула и мешали читать и то, и другое.

ЧТО ЛЕГКО СЛОМАТЬ
    1. Правило границы. Колонки TIMESTAMP WITHOUT TIME ZONE, asyncpg ждёт
       naive datetime, а приложение живёт в aware UTC. Всё, что идёт В
       asyncpg, проходит через _to_db_utc; всё, что читается ИЗ базы, —
       через _from_db_utc. Пропущенное преобразование не падает: получится
       сравнение naive с aware (TypeError) либо, что хуже, сдвиг на часовой
       пояс, который заметят по кривым срокам подписок.

    2. _to_db_utc намеренно бросает ValueError на любой tzinfo, кроме UTC.
       Не «чините» это на astimezone: исключение ловит того, кто собрался
       положить в базу местное время, — молчаливая конверсия спрячет
       ошибку в данных.

    3. safe_* глотают любой мусор и возвращают 0 / 0.0. Это осознанно для
       отчётов, но не годится для денег: там ноль вместо суммы — тихая
       потеря, а не защита.
"""
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _to_db_utc(dt: datetime) -> datetime:
    """
    Convert aware UTC datetime to naive UTC for DB storage.
    Must raise if dt is not timezone-aware UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo != timezone.utc:
        raise ValueError(f"Expected UTC, got tzinfo={dt.tzinfo}")
    return dt.replace(tzinfo=None)


def _from_db_utc(dt: datetime) -> datetime:
    """
    Convert naive DB datetime to aware UTC.
    DB TIMESTAMP columns return naive datetime (stored as UTC).
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _generate_subscription_uuid() -> str:
    """Canonical subscription UUID generation. DB is source of truth. Single place for new UUIDs."""
    u = str(uuid_lib.uuid4())
    if not u:
        raise RuntimeError("UUID generation failed: empty")
    if len(u) < 32:
        raise RuntimeError(f"UUID generation failed: invalid length {len(u)}")
    return u


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware UTC. Naive assumed UTC. Other TZ converted. Use _from_db_utc for DB reads."""
    if dt is None:
        return None
    if dt.tzinfo is not None and dt.tzinfo == timezone.utc:
        return dt
    if dt.tzinfo is None:
        return datetime(
            dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond,
            tzinfo=timezone.utc
        )
    return dt.astimezone(timezone.utc)


def _normalize_subscription_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Convert naive DB datetime columns to aware UTC. Use when returning subscription dicts."""
    if row is None:
        return None
    d = dict(row)
    for k in ("expires_at", "trial_expires_at", "created_at", "activated_at", "last_reminder_at",
              "last_auto_renewal_at", "last_notification_sent_at", "first_traffic_at"):
        if k in d and d[k] is not None and isinstance(d[k], datetime):
            d[k] = _from_db_utc(d[k])
    return d


def safe_int(value: Any) -> int:
    """
    Безопасное преобразование значения в int с обработкой None
    
    Args:
        value: Значение для преобразования (может быть None, int, str, Decimal)
    
    Returns:
        int: Преобразованное значение или 0 если None
    """
    if value is None:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def safe_float(value: Any) -> float:
    """
    Безопасное преобразование значения в float с обработкой None
    
    Args:
        value: Значение для преобразования (может быть None, int, float, str, Decimal)
    
    Returns:
        float: Преобразованное значение или 0.0 если None
    """
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def safe_get(dictionary: Dict[str, Any], key: str, default: Any = None) -> Any:
    """
    Безопасное получение значения из словаря с обработкой отсутствующих ключей
    
    Args:
        dictionary: Словарь
        key: Ключ
        default: Значение по умолчанию
    
    Returns:
        Значение из словаря или default
    """
    if dictionary is None:
        return default
    return dictionary.get(key, default)
