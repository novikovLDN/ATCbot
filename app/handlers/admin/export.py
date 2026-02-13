"""
Admin export handlers: CSV export logic.
"""
import logging
import csv
import tempfile
import os

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.admin.keyboards import get_admin_export_keyboard, get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_export_router = Router()
logger = logging.getLogger(__name__)
