"""Запись file_id входящего фото в лог.

ЧТО ЗДЕСЬ
    Один обработчик, который НЕ отвечает пользователю: ловит фото и пишет
    его file_id в лог, чтобы админ мог взять готовый file_id для баннеров
    рассылок вместо повторной загрузки картинки.

ПОЧЕМУ ОТДЕЛЬНЫМ МОДУЛЕМ
    К платежам он отношения не имеет — оказался в payments_messages.py
    исторически. Отдельный файл хотя бы делает это видимым.

ЧТО ЛЕГКО СЛОМАТЬ
    Исключения по состояниям FSM. Обработчик ловит ЛЮБОЕ фото, а обработка
    сообщения в aiogram останавливается на первом подошедшем обработчике.
    Убрать отсюда ~StateFilter(...) — значит проглотить фото, которого ждёт
    другой сценарий (баннер рассылки, промо-триал), и админ решит, что бот
    на картинку не отреагировал. Добавляя новый сценарий с фото, добавляйте
    исключение и сюда.
"""
import logging

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message

from app.handlers.common.states import BroadcastCreate
from app.handlers.admin.promo_trial import PromoTrialFSM

photo_log_router = Router()
logger = logging.getLogger(__name__)


@photo_log_router.message(
    F.photo,
    ~StateFilter(BroadcastCreate.waiting_for_message),
    ~StateFilter(PromoTrialFSM.waiting_for_photo),
)
async def log_incoming_photo_file_id(message: Message):
    """Записать file_id входящего фото в лог. Ответа не шлёт.

    Исключаем состояния, в которых приложенное админом фото штатно
    ждёт другой обработчик, — иначе мы проглотим сообщение ради строчки
    в логе, и админ увидит, что бот на фото не отреагировал.
    """
    try:
        telegram_id = message.from_user.id if message.from_user else 0
        file_id = message.photo[-1].file_id
        logger.info(
            "PHOTO_FILE_ID_RECEIVED [telegram_id=%s, file_id=%s]",
            telegram_id,
            file_id,
        )
    except Exception as e:
        logger.warning("PHOTO_FILE_ID_RECEIVED log failed: %s", e)
