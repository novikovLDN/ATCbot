"""Date formatting utilities."""
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def format_date_msk(dt: datetime) -> str:
    """«ДД.ММ.ГГГГ» in Moscow time (#21: every date a user sees is MSK; a UTC
    date near midnight showed the day before). Naive = UTC (DB contract)."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).strftime("%d.%m.%Y")

MONTHS_RU = ("", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def format_date_ru(dt: datetime) -> str:
    """Format date in Russian short form: '25 фев 2027'."""
    if dt is None:
        return "N/A"
    month_idx = dt.month if 1 <= dt.month <= 12 else 1
    return f"{dt.day} {MONTHS_RU[month_idx]} {dt.year}"
