"""Доставка сообщений рассылки: одна отправка, ретраи, темп.

ЧТО ЗДЕСЬ
    Самый нижний уровень: как отправить ОДНО сообщение и что делать,
    когда Telegram отказал. Ничего про то, кому и что рассылаем, —
    это выше по стеку (broadcast_sender и промо-рассылка триальщикам).

ЧТО ЛЕГКО СЛОМАТЬ
    Telegram ограничивает частоту и отвечает TelegramRetryAfter с точным
    временем ожидания. Игнорировать его нельзя: без паузы бот получает
    временную блокировку, и встаёт РАССЫЛКА ЦЕЛИКОМ, а не одна отправка.

ГДЕ ЭТО ЛЕЖИТ И ПОЧЕМУ
    Раньше файл жил в app/handlers/admin рядом с мастером создания
    рассылки. Мастер переехал в веб-дашборд, а доставка нужна дальше —
    это сервис, а не экран, поэтому он здесь.

    Вместе с мастером отсюда ушла и сборка клавиатуры рассылки: её
    строит дашборд (app/api/dashboard/routes/broadcasts.py,
    _build_reply_markup) из своего же списка кнопок. Держать вторую
    копию тут значило бы однажды получить кнопку, которая есть в одном
    списке и отсутствует в другом.
"""
import logging
import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Параметры темпа рассылки: сколько отправок параллельно, каким батчем
# и с какой паузой между батчами. Подобраны под лимит Telegram ~30 msg/s.
BROADCAST_CONCURRENCY = 15          # одновременных отправок
BROADCAST_BATCH_SIZE = 200          # размер батча
BROADCAST_BATCH_PAUSE = 2           # секунд между батчами
BROADCAST_RETRY_LIMIT = 3           # попыток на одного получателя


async def _safe_send_with_buttons(
    bot: Bot,
    user_id: int,
    text: str,
    semaphore: asyncio.Semaphore,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo_file_id: str | None = None,
    animation_file_id: str | None = None,
    caption: str | None = None,
) -> int | None:
    """Отправить одно сообщение рассылки. Вернуть message_id или None.

    message_id нужен выше по стеку: по нему потом удаляют разосланное,
    если рассылку отозвали.

    Приоритет вложения:
      1) animation_file_id (GIF/MP4) → send_animation
      2) photo_file_id                → send_photo
      3) без вложения                 → send_message

    Семафор ограничивает число одновременных отправок — он общий на всю
    рассылку и создаётся вызывающей стороной.
    """
    from app.utils.telegram_safe import convert_tg_emoji

    # Премиум-эмодзи в тексте: у кого нет Premium, тот увидит подстановку.
    text = convert_tg_emoji(text)
    if caption:
        caption = convert_tg_emoji(caption)

    async with semaphore:
        for _attempt in range(BROADCAST_RETRY_LIMIT):
            try:
                if animation_file_id:
                    result = await bot.send_animation(
                        user_id,
                        animation=animation_file_id,
                        caption=caption or text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                elif photo_file_id:
                    result = await bot.send_photo(
                        user_id,
                        photo=photo_file_id,
                        caption=caption or text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                else:
                    result = await bot.send_message(
                        user_id, text, reply_markup=reply_markup, parse_mode="HTML",
                    )
                return result.message_id

            except TelegramRetryAfter as e:
                # Ждём ровно столько, сколько попросил Telegram, плюс секунда
                # запаса. Без этого следующая попытка приходит в тот же лимит.
                #
                # Записи здесь не было ни одной. Если все попытки упёрлись в
                # лимит, функция возвращает None, наверху это станет просто
                # failed_count += 1 — и причина «нас затормозил Telegram»
                # неотличима от «человек заблокировал бота». Соседняя ветка
                # except Exception логирует, эта молчала.
                logger.warning(
                    "BROADCAST_RATE_LIMITED user=%s attempt=%s retry_after=%ss",
                    user_id, _attempt + 1, e.retry_after,
                )
                await asyncio.sleep(e.retry_after + 1)

            except Exception as e:
                # Одна неудачная отправка не должна валить рассылку: причина
                # у каждого получателя своя (заблокировал бота, удалил аккаунт,
                # битая разметка). Логируем, чтобы не гадать потом по счётчику.
                logger.warning(
                    "BROADCAST_SEND_FAILED user=%s attempt=%s err=%s",
                    user_id, _attempt + 1, e,
                )
                await asyncio.sleep(1)

        return None
