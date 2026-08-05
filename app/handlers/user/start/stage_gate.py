"""Развилка для новых пользователей в STAGE.

ЧТО ЗДЕСЬ
    Экран «ты разработчик или пользователь», который видит новый человек
    на первом /start в stage-боте, и обработчик кнопки «Разработчик».

ПОЧЕМУ ВЫДЕЛЕНО
    Это единственный кусок /start, который в проде не выполняется
    никогда. Держать его в общем потоке — путать читателя.

ЧТО ЛЕГКО СЛОМАТЬ
    Кнопка «Пользователь» — обычная ссылка на прод-бота с нашим реф-
    payload: она вообще не касается stage-базы. Сделаете её callback'ом —
    в stage начнут появляться настоящие пользователи.

    Обработчик «Разработчик» ещё раз проверяет config.IS_STAGE: кнопка
    живёт в сообщении, а сообщение может пережить смену окружения.
"""
import logging

import database
import config
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.keyboards import get_main_menu_keyboard
from app.handlers.common.utils import safe_resolve_username

router = Router()
logger = logging.getLogger(__name__)


async def _show_stage_gate(message: Message) -> None:
    """Greeting screen shown on the FIRST /start to any new user in STAGE.

    The «Пользователь» button is a URL deep-link to the production bot with
    our referral payload — clicking it never touches the stage DB. The
    «Разработчик» button creates the user record locally and continues to
    the normal flow (see callback_stage_gate_dev).
    """
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="👤 Пользователь",
            url="https://t.me/atlassecure_bot?start=ref_RC26QG",
        )],
        [InlineKeyboardButton(
            text="💻 Разработчик",
            callback_data="stage_gate:dev",
        )],
    ])
    text = (
        "Привет 👋\n\n"
        "Ты разработчик Atlas Secure или пользователь?\n"
        "Выбери вариант ниже 👇"
    )
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "stage_gate:dev")
async def callback_stage_gate_dev(callback: CallbackQuery, state: FSMContext):
    """«Разработчик» — создаём user-запись и пускаем в обычный главный экран."""
    if not config.IS_STAGE:
        await callback.answer()
        return
    await callback.answer()

    telegram_id = callback.from_user.id

    if not database.DB_READY:
        # Degraded: just render the menu without persisting anything.
        language = await resolve_user_language(telegram_id)
        text = i18n_get_text(language, "main.welcome")
        keyboard = await get_main_menu_keyboard(language, telegram_id)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.bot.send_message(telegram_id, text, reply_markup=keyboard, parse_mode="HTML")
        return

    user = await database.get_user(telegram_id)
    if user is None:
        username = safe_resolve_username(callback.from_user, "ru", telegram_id)
        if username and len(username) > 64:
            username = username[:64]
        try:
            await database.create_user(telegram_id, username, "ru")
        except Exception as e:
            logger.warning(f"STAGE_GATE_DEV: create_user failed user={telegram_id}: {e}")

    language = await resolve_user_language(telegram_id)
    text = i18n_get_text(language, "main.welcome")
    keyboard = await get_main_menu_keyboard(language, telegram_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(telegram_id, text, reply_markup=keyboard, parse_mode="HTML")
