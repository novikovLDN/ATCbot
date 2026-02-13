"""
Admin broadcast handlers: BroadcastCreate FSM, AdminBroadcastNoSubscription FSM.
"""
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.states import BroadcastCreate, AdminBroadcastNoSubscription
from app.handlers.admin.keyboards import (
    get_broadcast_test_type_keyboard,
    get_broadcast_type_keyboard,
    get_broadcast_segment_keyboard,
    get_broadcast_confirm_keyboard,
    get_ab_test_list_keyboard,
    get_admin_back_keyboard,
)
from app.handlers.common.utils import safe_edit_text

admin_broadcast_router = Router()
logger = logging.getLogger(__name__)
