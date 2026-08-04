"""Рассылки — точка входа. Реализация разложена по соседям.

ЧТО ЗДЕСЬ
    Ничего, кроме реэкспорта. Файл оставлен потому, что через
    `database.broadcasts` рассылки импортируют database/__init__.py,
    database/admin.py и тесты; переписывать эти импорты — отдельная работа.

ГДЕ ЧТО ЛЕЖИТ
    database/broadcast_records.py   — паспорт рассылки: текст, медиа, кнопки, скидки
    database/broadcast_segments.py  — подбор получателей по имени сегмента
    database/broadcast_analytics.py — журнал отправок, конверсия, A/B-разрез
    database/incident_mode.py       — режим инцидента (жил здесь по недоразумению)

    Разложено так, потому что раньше в одном файле на 1096 строк лежали
    четыре вещи, которые правят по разным поводам. Один только подбор
    сегментов занимал 508 строк — больше, чем всё остальное вместе.

ЧТО ЛЕГКО СЛОМАТЬ
    Список ниже дублируется в database/__init__.py и database/admin.py.
    Убрать отсюда имя, которое там перечислено, — импорт пакета упадёт при
    старте бота.

    Режим инцидента переехал в отдельный модуль, но реэкспортируется
    отсюда: он годами лежал среди рассылок, и обращения к нему через
    database.broadcasts могут быть где угодно.
"""
from database.broadcast_records import (  # noqa: F401
    create_broadcast,
    get_broadcast,
    save_broadcast_discount,
    save_broadcast_gift_reveal_percent,
    get_broadcast_discount,
    insert_admin_broadcast_record,
    update_admin_broadcast_record,
)
from database.broadcast_segments import get_users_by_segment  # noqa: F401
from database.broadcast_analytics import (  # noqa: F401
    log_broadcast_send,
    get_broadcast_stats,
    get_broadcast_analytics,
    get_recent_broadcasts,
    get_broadcast_message_ids,
    mark_broadcast_messages_deleted,
    get_ab_test_broadcasts,
    get_ab_test_stats,
)
from database.incident_mode import (  # noqa: F401
    get_incident_settings,
    set_incident_mode,
)

__all__ = [
    "create_broadcast",
    "get_broadcast",
    "save_broadcast_discount",
    "save_broadcast_gift_reveal_percent",
    "get_broadcast_discount",
    "insert_admin_broadcast_record",
    "update_admin_broadcast_record",
    "get_users_by_segment",
    "log_broadcast_send",
    "get_broadcast_stats",
    "get_broadcast_analytics",
    "get_recent_broadcasts",
    "get_broadcast_message_ids",
    "mark_broadcast_messages_deleted",
    "get_ab_test_broadcasts",
    "get_ab_test_stats",
    "get_incident_settings",
    "set_incident_mode",
]
