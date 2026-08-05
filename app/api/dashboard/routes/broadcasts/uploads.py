"""Рассылки: загрузка картинки и GIF ради Telegram file_id.

ЧТО ЗДЕСЬ
    Два эндпоинта: приняли файл от дашборда, отправили его в чат админа,
    вернули file_id.

ПОЧЕМУ ВЫДЕЛЕНО
    Единственное место в рассылках, которое работает с бинарными
    загрузками и лимитами Telegram. К созданию рассылки отношения не
    имеет: file_id клиент присылает обратно уже в составе payload.

ЧТО ЛЕГКО СЛОМАТЬ
    Обходной путь через чат админа выглядит странно, но иначе никак:
    Telegram не выдаёт file_id, пока файл не отправлен. Сообщение в чате
    админа — заодно и подтверждение, что загрузка прошла.

    Лимиты разные (10 MB фото, 20 MB animation) — это лимиты Bot API, а
    не наша выдумка; подняв их, получим отказ от Telegram вместо
    понятной 413.
"""
from __future__ import annotations

from aiogram.types import BufferedInputFile
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.dashboard.deps import require_admin
from app.api.dashboard.routes.broadcasts.common import _get_bot

router = APIRouter()


@router.post("/upload-photo")
async def upload_photo(
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
):
    """Echo the photo to the admin's Telegram chat to obtain a Telegram
    file_id, return it for the wizard to embed in the broadcast.

    Telegram requires that ANY file_id used to forward / send a photo
    come from a previous Telegram-side send/upload — there's no way
    to mint a file_id without first calling send_photo. We use the
    admin's own chat as the staging area; the message also serves as a
    visual confirmation that the upload worked."""
    bot = _get_bot()
    content = await file.read()
    if not content:
        raise HTTPException(400, "empty_file")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "file_too_large_max_10MB")

    photo = BufferedInputFile(content, filename=file.filename or "photo.jpg")
    try:
        msg = await bot.send_photo(
            chat_id=int(admin["sub"]),
            photo=photo,
            caption="🖼 Загружено для рассылки",
        )
    except Exception as e:
        raise HTTPException(500, f"upload_to_telegram_failed: {e}")

    if not msg.photo:
        raise HTTPException(500, "telegram_returned_no_photo")
    return {"file_id": msg.photo[-1].file_id}


@router.post("/upload-animation")
async def upload_animation(
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
):
    """Загрузить GIF/MP4-animation → получить Telegram file_id.

    Механика та же что у /upload-photo: bot шлёт файл в чат админа
    как animation, Telegram возвращает file_id, который потом
    используется в broadcast для send_animation.

    Ограничение размера: 20 MB (Telegram Bot API лимит на animation).
    Accept: image/gif, video/mp4.
    """
    bot = _get_bot()
    content = await file.read()
    if not content:
        raise HTTPException(400, "empty_file")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "file_too_large_max_20MB")

    filename = (file.filename or "animation.gif").lower()
    if not (filename.endswith(".gif") or filename.endswith(".mp4")):
        # По content-type тоже проверим на всякий случай
        ct = (file.content_type or "").lower()
        if ct not in ("image/gif", "video/mp4"):
            raise HTTPException(
                400, "only .gif or .mp4 accepted"
            )

    animation = BufferedInputFile(
        content, filename=file.filename or "animation.gif",
    )
    try:
        msg = await bot.send_animation(
            chat_id=int(admin["sub"]),
            animation=animation,
            caption="🎬 GIF загружен для рассылки",
        )
    except Exception as e:
        raise HTTPException(500, f"upload_to_telegram_failed: {e}")

    if not msg.animation:
        raise HTTPException(500, "telegram_returned_no_animation")
    return {"file_id": msg.animation.file_id}
