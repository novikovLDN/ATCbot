"""
Admin utility: echo file_id обратно админу при отправке любого медиа.
Работает только в личке, только для админа, и только если нет активного FSM
(чтобы не перехватывать загрузку фото в BroadcastCreate / AdminChat / promo_trial).
"""
import logging

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message

from app.utils.security import is_admin

admin_fileid_echo_router = Router()
logger = logging.getLogger(__name__)


def _extract(message: Message) -> tuple[str, str] | None:
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


@admin_fileid_echo_router.message(
    StateFilter(None),
    F.chat.type == "private",
    F.photo | F.animation | F.video | F.video_note
    | F.sticker | F.audio | F.voice | F.document,
)
async def echo_file_id(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    extracted = _extract(message)
    if not extracted:
        return

    kind, file_id = extracted
    await message.reply(
        f"🆔 <b>{kind}</b>\n<code>{file_id}</code>",
        parse_mode="HTML",
    )
