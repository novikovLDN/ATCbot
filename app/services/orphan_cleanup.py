"""Компенсация отката выдачи: удаление сущности, созданной в панели.

Выдача идёт в две фазы: сначала сущность создаётся в Remnawave (внешний
вызов, обязательно вне транзакции), затем подписка записывается в базу.
Если вторая фаза упала, сущность остаётся в панели сиротой — человек не
заплатил, а доступ у него рабочий до конца выставленного expireAt.

Раньше компенсацию делал vpn_utils.safe_remove_vless_user_with_retry —
заглушка снятого с эксплуатации xray. Она всегда возвращала успех,
вызывающие писали «сирота предотвращена», и разбор инцидента по такому
логу считал вопрос закрытым, пока сущность продолжала жить.

ПОЧЕМУ ПОИСК ПО ИМЕНИ, А НЕ ПО UUID ИЗ БАЗЫ
    В панели два разных идентификатора: `uuid` — внутренний, только он
    годится для DELETE /api/users/{uuid}, и `vlessUuid` — тот, что уходит
    в ссылку и в subscriptions.uuid. У вызывающих на руках второй
    (purchase_flow.provision_subscription возвращает именно его), и
    DELETE по нему вернёт 404: компенсация снова окажется мнимой, только
    теперь с сетевым вызовом.

    Панельный uuid лежит в subscriptions.remnawave_premium_uuid, но
    строку только что откатили — читать её бессмысленно, а у нового
    пользователя её и не было. Имя premium-сущности строится по
    telegram_id детерминированно, поэтому идентификатор спрашиваем у
    панели по имени.

ЧЕГО ЗДЕСЬ НЕТ
    Обращений к базе — ни одного. Функцию зовут с уже занятым
    соединением пула (местами ещё и под advisory-локом), и второй acquire
    отсюда мог бы выбрать пул досуха на пути обработки ошибки.

    Bypass-сущность (пакет ГБ обхода) не трогается: в панели это отдельный
    пользователь и отдельная покупка. Оставшиеся после отката гигабайты
    обхода чистятся отдельно — см. отчёт по задаче.
"""
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


async def delete_orphan_premium_entity(
    telegram_id: int,
    connection_uuid: Optional[str] = None,
) -> Tuple[bool, str]:
    """Удалить premium-сущность, оплата за которую откатилась.

    Args:
        telegram_id: владелец сущности — по нему строится имя в панели.
        connection_uuid: uuid подключения (subscriptions.uuid). Нужен
            только как идентификатор для ручной чистки, если панельный
            узнать не удалось.

    Returns:
        (deleted, entity):
          deleted=True  — панель подтвердила удаление, неоплаченного
                          доступа не осталось;
          deleted=False — сущность осталась либо удаление не подтверждено;
                          entity — по чему её искать руками.

    НИКОГДА НЕ БРОСАЕТ. Компенсация выполняется на пути обработки ошибки:
    исключение отсюда подменило бы исходную причину отката, и вызывающий
    вернул бы платёжному провайдеру не тот ответ.
    """
    fallback = (connection_uuid or "").strip()
    try:
        from app.services import remnawave_api, remnawave_premium

        username = remnawave_premium.build_premium_username(telegram_id)
        entity = await remnawave_api.find_user_by_username(username)
        if not entity:
            # find_user_by_username отдаёт None и на «имя свободно», и на
            # недоступную панель — по ответу эти случаи не различить.
            # Считаем, что сущность осталась: ложное «удалено» закрывает
            # разбор навсегда, лишняя ручная проверка — нет.
            logger.warning(
                "ORPHAN_CLEANUP_ENTITY_NOT_FOUND tg=%s username=%s — панель не "
                "отдала сущность (её нет либо панель недоступна)",
                telegram_id, username,
            )
            return False, fallback

        # Приватная проверка из соседнего модуля взята намеренно: имя
        # сущности мог занять админ вручную, и удаление чужой записи
        # отобрало бы доступ у другого человека.
        if not remnawave_premium._is_our_entity(entity, telegram_id):
            logger.error(
                "ORPHAN_CLEANUP_FOREIGN_ENTITY tg=%s username=%s — сущность с "
                "этим именем принадлежит не нам, не трогаем",
                telegram_id, username,
            )
            return False, fallback

        panel_uuid = (entity.get("uuid") or "").strip()
        if not panel_uuid:
            logger.error(
                "ORPHAN_CLEANUP_NO_PANEL_UUID tg=%s username=%s — панель нашла "
                "сущность, но не отдала её uuid",
                telegram_id, username,
            )
            return False, fallback

        result = await remnawave_api.delete_user(panel_uuid)
        if result is None:
            # delete_user отдаёт None и на 404, и на любой отказ, и на
            # таймаут — подтверждения удаления нет ни в одном из случаев.
            logger.error(
                "ORPHAN_CLEANUP_DELETE_UNCONFIRMED tg=%s uuid=%s — панель не "
                "подтвердила удаление",
                telegram_id, panel_uuid[:8],
            )
            return False, panel_uuid

        logger.info(
            "ORPHAN_CLEANUP_DELETED tg=%s uuid=%s", telegram_id, panel_uuid[:8],
        )
        return True, panel_uuid
    except Exception as e:
        logger.error(
            "ORPHAN_CLEANUP_ERROR tg=%s %s: %s",
            telegram_id, type(e).__name__, e,
        )
        return False, fallback


__all__ = ["delete_orphan_premium_entity"]
