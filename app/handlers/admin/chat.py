"""Переписка админа с пользователем прямо из бота.

ЧТО ЗДЕСЬ
    Три шага: кнопка «Написать пользователю», ввод id или @username, режим
    пересылки сообщений до команды /end.

ПОЧЕМУ ОТДЕЛЬНО
    Единственный живой FSM-диалог в админском разделе, и единственное место,
    где бот пересылает контент админа живому пользователю. Ошибка здесь
    видна не в логах, а в чужом чате.

ЧТО ЛЕГКО СЛОМАТЬ
    Первый экран отвечает НОВЫМ сообщением, а не правит текущее. Кнопка
    «Написать пользователю» висит в том числе под уведомлениями о заказах
    Spotify, Apple ID и Steam — там же, где лежат выданные логин, пароль и
    кнопка «Выполнено». Замена answer на edit_text затирает данные заказа, и
    выдать его становится нечем.

    Режим пересылки ловит ЛЮБОЕ сообщение админа, пока стоит состояние
    AdminChat.chatting. Пока оно висит, обычные команды админа уходят
    пользователю. Поэтому выходов из состояния несколько (/end, /cancel,
    /stop, «отмена»), и убирать их не стоит.
"""
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import config
import database
from app.handlers.common.states import AdminChat

admin_chat_router = Router()
logger = logging.getLogger(__name__)


@admin_chat_router.callback_query(F.data == "admin:chat")
async def callback_admin_chat_start(callback: CallbackQuery, state: FSMContext):
    """Start admin chat — ask for user ID."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AdminChat.waiting_for_user_id)
    # Отправляем НОВЫМ сообщением, а не правкой текущего.
    #
    # Кнопка «💬 Написать пользователю» висит в том числе под уведомлениями
    # о заказах Spotify, Apple ID и Steam — там же, где лежат email, пароль
    # и кнопка «Выполнено». Редактирование затирало всё это: админ нажимал
    # «написать», терял данные заказа и не мог его выдать.
    await callback.message.answer(
        "💬 <b>Написать пользователю</b>\n\n"
        "Введите Telegram ID или @username пользователя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:main")],
        ]),
        parse_mode="HTML",
    )


@admin_chat_router.message(AdminChat.waiting_for_user_id)
async def process_admin_chat_user_id(message: Message, state: FSMContext):
    """Process user ID input, enter chatting mode."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    if message.text and message.text.strip().lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("Отменено.", parse_mode="HTML")
        return

    user_input = message.text.strip() if message.text else ""

    # Find user by ID or username
    target_user_id = None
    target_username = None
    try:
        target_user_id = int(user_input)
    except ValueError:
        # Try username
        username = user_input.lstrip("@").lower()
        if username:
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT telegram_id, username FROM users WHERE LOWER(username) = $1",
                    username,
                )
            if row:
                target_user_id = row["telegram_id"]
                target_username = row["username"]

    if not target_user_id:
        await message.answer("❌ Пользователь не найден. Введите корректный ID или @username:", parse_mode="HTML")
        return

    if not target_username:
        user = await database.get_user(target_user_id)
        target_username = user.get("username") if user else None

    uname_display = f"@{target_username}" if target_username else str(target_user_id)

    await state.update_data(chat_target_id=target_user_id, chat_target_name=uname_display)
    await state.set_state(AdminChat.chatting)
    await message.answer(
        f"💬 <b>Чат с {uname_display}</b> (<code>{target_user_id}</code>)\n\n"
        f"Отправляйте сообщения — бот перешлёт их пользователю.\n"
        f"Поддерживается: текст, фото, документы, стикеры.\n\n"
        f"Для завершения отправьте <code>/end</code>",
        parse_mode="HTML",
    )


@admin_chat_router.message(AdminChat.chatting)
async def process_admin_chat_message(message: Message, state: FSMContext, bot: Bot):
    """Forward admin message to target user."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return

    # Exit command
    if message.text and message.text.strip().lower() in ("/end", "/cancel", "/stop", "отмена"):
        data = await state.get_data()
        name = data.get("chat_target_name", "")
        await state.clear()
        await message.answer(
            f"✅ Чат с {name} завершён.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="← Админ-панель", callback_data="admin:main")],
            ]),
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    target_id = data.get("chat_target_id")
    target_name = data.get("chat_target_name", "")

    if not target_id:
        await state.clear()
        await message.answer("❌ Ошибка: ID пользователя потерян. Начните заново.", parse_mode="HTML")
        return

    try:
        # Forward different content types
        if message.photo:
            await bot.send_photo(
                chat_id=target_id,
                photo=message.photo[-1].file_id,
                caption=message.caption or None,
                parse_mode="HTML" if message.caption else None,
            )
        elif message.document:
            await bot.send_document(
                chat_id=target_id,
                document=message.document.file_id,
                caption=message.caption or None,
            )
        elif message.sticker:
            await bot.send_sticker(chat_id=target_id, sticker=message.sticker.file_id)
        elif message.text:
            await bot.send_message(chat_id=target_id, text=message.text, parse_mode="HTML")
        else:
            await message.answer("⚠️ Этот тип сообщения не поддерживается.", parse_mode="HTML")
            return

        await message.answer(f"✅ Доставлено → {target_name}", parse_mode="HTML")

    except Exception as e:
        logger.warning("ADMIN_CHAT_SEND_ERROR: target=%s error=%s", target_id, e)
        await message.answer(f"❌ Не удалось отправить: {e}", parse_mode="HTML")
