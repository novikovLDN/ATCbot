"""
Админ: отправил медиа боту → бот вернул file_id.

Два пути:
1. Автоматический: любое медиа от админа в личке → эхо с file_id
2. Ручной /id: команда, работает всегда (даже если авто-эхо чем-то перехвачено).
   Ответить командой /id на любое сообщение с медиа → эхо file_id.

Регистрация: этот роутер стоит последним в admin/__init__.py, поэтому FSM-загрузки
(broadcast, promo_trial, admin_chat) перехватывают медиа первыми — эхо не мешает.
"""
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from app.utils.security import is_admin

admin_fileid_echo_router = Router()
logger = logging.getLogger(__name__)


def _extract(message: Message) -> tuple[str, str] | None:
    if not message:
        return None
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.animation:
        return "animation", message.animation.file_id
    if message.video:
        return "video", message.video.file_id
    if message.video_note:
        return "video_note", message.video_note.file_id
    if message.sticker:
        return "sticker", message.sticker.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.voice:
        return "voice", message.voice.file_id
    if message.document:
        return "document", message.document.file_id
    return None


async def _send_echo(message: Message, source_msg: Message) -> None:
    """Отправить file_id из source_msg в ответ на message."""
    extracted = _extract(source_msg)
    if not extracted:
        await message.reply("⚠️ В сообщении нет медиа с file_id")
        return
    kind, file_id = extracted
    logger.info(
        "FILEID_ECHO admin=%s kind=%s file_id=%s",
        message.from_user.id if message.from_user else "?",
        kind, file_id,
    )
    await message.reply(
        f"🆔 <b>{kind}</b>\n<code>{file_id}</code>",
        parse_mode="HTML",
    )


@admin_fileid_echo_router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """/id — ответить командой на медиа-сообщение и получить file_id.

    Работает ВСЕГДА (никаких state-фильтров), т.к. это явная команда админа.
    """
    if not message.from_user or not is_admin(message.from_user.id):
        return
    if message.chat.type != "private":
        return
    # Приоритет: reply_to_message, иначе — сама команда (если фото с caption /id)
    source = message.reply_to_message or message
    await _send_echo(message, source)


@admin_fileid_echo_router.message(
    F.photo | F.animation | F.video | F.video_note
    | F.sticker | F.audio | F.voice | F.document,
)
async def auto_echo(message: Message) -> None:
    """Авто-эхо: любое медиа от админа в личке."""
    if not message.from_user or not is_admin(message.from_user.id):
        return
    if message.chat.type != "private":
        return
    await _send_echo(message, message)
