"""Date formatting utilities."""
from datetime import datetime

MONTHS_RU = ("", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def format_date_ru(dt: datetime) -> str:
    """Format date in Russian short form: '25 фев 2027'."""
    if dt is None:
        return "N/A"
    month_idx = dt.month if 1 <= dt.month <= 12 else 1
    return f"{dt.day} {MONTHS_RU[month_idx]} {dt.year}"
