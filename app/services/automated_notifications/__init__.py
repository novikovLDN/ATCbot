"""
Automated notifications — управление зашитыми в код уведомлениями
через админ-дашборд.

См. registry.py для регистрации notification-keys и helper.py для
runtime-вызовов из bot-кода.
"""
from .registry import (
    NotificationSpec,
    REGISTRY,
    register_notification,
    all_specs,
)
from .helper import (
    get_notification_text,
    is_notification_enabled,
    log_notification_send,
    get_trigger_config,
    sync_registry_to_db,
)

__all__ = [
    "NotificationSpec",
    "REGISTRY",
    "register_notification",
    "all_specs",
    "get_notification_text",
    "is_notification_enabled",
    "log_notification_send",
    "get_trigger_config",
    "sync_registry_to_db",
]
