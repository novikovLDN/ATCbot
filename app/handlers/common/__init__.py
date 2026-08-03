"""
Common handlers module: guards, decorators, utils, keyboards, FSM states.
Shared across all handler domains. Zero domain logic.
"""
from app.handlers.common.guards import ensure_db_ready_message, ensure_db_ready_callback
from app.handlers.common.utils import (
    safe_resolve_username,
    safe_resolve_username_from_db,
    safe_edit_text,
    safe_edit_reply_markup,
    get_promo_session,
    create_promo_session,
    clear_promo_session,
    format_text_with_incident,
    detect_platform,
    format_promo_stats_text,
    get_reissue_lock,
    get_reissue_notification_text,
)
from app.handlers.common.keyboards import (
    get_language_keyboard,
    get_main_menu_keyboard,
    get_back_keyboard,
    get_connect_keyboard,
    get_profile_keyboard,
    get_about_keyboard,
    get_service_status_keyboard,
    get_reissue_notification_keyboard,
    _get_promo_error_keyboard,
)
__all__ = [
    "ensure_db_ready_message",
    "ensure_db_ready_callback",
    "safe_resolve_username",
    "safe_resolve_username_from_db",
    "safe_edit_text",
    "safe_edit_reply_markup",
    "get_promo_session",
    "create_promo_session",
    "clear_promo_session",
    "format_text_with_incident",
    "detect_platform",
    "format_promo_stats_text",
    "get_reissue_lock",
    "get_reissue_notification_text",
    "get_language_keyboard",
    "get_main_menu_keyboard",
    "get_back_keyboard",
    "get_connect_keyboard",
    "get_profile_keyboard",
    "get_about_keyboard",
    "get_service_status_keyboard",
    "get_reissue_notification_keyboard",
    "_get_promo_error_keyboard",
]
