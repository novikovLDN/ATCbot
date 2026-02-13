"""
Admin stats handlers: promo_stats, metrics, analytics, referral_stats.
"""
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.utils.security import require_admin, log_audit_event
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_stats_router = Router()
logger = logging.getLogger(__name__)
