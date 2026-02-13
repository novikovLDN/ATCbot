"""
Application runtime context.
Holds process-level metadata such as start time.
Must not import handlers or routers.
"""
from datetime import datetime, timezone
from typing import Optional

_bot_start_time: Optional[datetime] = None


def set_bot_start_time(dt: datetime) -> None:
    global _bot_start_time
    _bot_start_time = dt


def get_bot_start_time() -> Optional[datetime]:
    return _bot_start_time
