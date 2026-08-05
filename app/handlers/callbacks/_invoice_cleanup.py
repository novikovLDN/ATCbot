"""Общее для платёжных экранов: автоудаление инвойса.

Инвойс живёт 15 минут (config.INVOICE_TIMEOUT_SECONDS). Сообщение с ним
нужно убрать по истечении срока, иначе человек нажмёт на просроченную
кнопку и получит ошибку провайдера вместо понятного объяснения.

Функция вынесена отдельно, потому что её используют ВСЕ платёжные экраны —
оплата с баланса, внешние провайдеры, пополнение, подарки, магазин товаров
(Steam, Stars, Premium, Spotify, Apple ID), трафик и прокси. Без своего
модуля они тянули бы друг друга по кругу.

ЕДИНСТВЕННАЯ РЕАЛИЗАЦИЯ

    Копий этой функции было семь, под тремя разными именами
    (_schedule_invoice_deletion, _auto_delete_lava_msg, _del). Часть из них
    не писала INVOICE_EXPIRED в лог, часть держала свою константу 15 минут
    вместо config.INVOICE_TIMEOUT_SECONDS. Из-за этого правка срока жизни
    счёта доезжала до одних способов оплаты и не доезжала до других, а по
    жалобе «счёт исчез, я не успел оплатить» половину случаев нельзя было
    поднять в логах. Сторожит возврат копии
    tests/services/test_payments_split.py::test_app_has_one_auto_delete_implementation.

ПОЧЕМУ message_id, А НЕ ОБЪЕКТ Message

    Задача уходит в фон через asyncio.create_task и живёт ещё 15 минут
    после того, как обработчик вернул управление. Всё это время она держит
    ссылку на всё, что ей передали. Message тянет за собой bot, chat и
    from_user — состояние, устаревшее сразу после выхода из обработчика;
    обращение к нему через четверть часа — гонка. Для удаления нужны ровно
    два числа, их и передаём: bot берём отдельным аргументом, а chat_id и
    message_id вызывающий достаёт из Message прямо на месте вызова.
"""
import asyncio
import logging

import config
from aiogram import Bot

logger = logging.getLogger(__name__)

INVOICE_TIMEOUT = config.INVOICE_TIMEOUT_SECONDS  # 15 минут


async def _schedule_invoice_deletion(bot: Bot, chat_id: int, message_id: int, timeout: int = INVOICE_TIMEOUT):
    """Удаляет сообщение с инвойсом через timeout секунд.

    timeout — параметр, а не константа внутри: у провайдера может быть
    свой срок жизни счёта, и тогда сообщение должно исчезать вместе с ним,
    а не раньше и не позже. Сейчас его передаёт только тот, кто явно хочет
    другой срок; остальные берут config.INVOICE_TIMEOUT_SECONDS.

    Неудача удаления — ожидаемый исход, а не сбой: человек мог убрать
    сообщение сам, мог заблокировать бота, чат мог стать недоступен.
    Поэтому debug, а не error, но НЕ голый pass: без записи в логе разбор
    жалобы «счёт пропал» упирается в пустоту.
    """
    try:
        await asyncio.sleep(timeout)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"INVOICE_EXPIRED: deleted invoice message_id={message_id} chat_id={chat_id}")
    except Exception as e:
        logger.debug(f"Failed to delete expired invoice: chat_id={chat_id}, error={e}")
