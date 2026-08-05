"""Мини-игра «Ферма»: сборка роутера и допуск к нему.

ЧТО ЗДЕСЬ
    Только две вещи: гард подписки и подключение подроутеров. Сама игра
    разложена по соседям:

        mechanics.py  правила: время роста, потолок ускорения, окно шторма
        screen.py     экран фермы — текст и клавиатура по состоянию грядок
        plots.py      грядки: посадка, уход, сбор, покупка, выкапывание
        storm.py      шторм: плёнка (баланс/карта/СБП) и ранний сбор

    Разложено так, потому что в одном файле на 1386 строк лежали четыре
    вещи, которые правят по разным поводам: формула ускорения, вёрстка
    экрана, игровые действия и платежи внешним провайдерам.

ПОЧЕМУ ГАРД ЖИВЁТ ЗДЕСЬ, А НЕ В ПОДРОУТЕРАХ
    aiogram собирает inner-middleware по всей цепочке родителей
    (TelegramEventObserver._resolve_middlewares идёт по router.chain_head),
    поэтому middleware, повешенный на этот роутер, применяется и к
    обработчикам подроутеров. Одна точка входа закрывает все callback'и
    фермы разом, включая те, что добавят позже.

    Именно inner, а не outer: outer-middleware отрабатывает ДО подбора
    обработчика, то есть на каждом callback'е, дошедшем до роутера фермы, —
    в том числе на чужих. Он бы отвечал на них алертом про подписку и
    возвращал None, а None для aiogram — «обработано»: чужие кнопки после
    фермы замолчали бы.

ЧТО ЛЕГКО СЛОМАТЬ
    Забытый include_router. Обработчик остаётся объявленным, ошибок в логах
    нет — кнопка просто перестаёт отвечать. Поэтому список подроутеров ниже
    сторожит tests/services/test_farm_split.py.

    Порядок подключения повторяет порядок объявления в исходном файле:
    сначала грядки, потом шторм. Фильтры не пересекаются, но менять порядок
    без нужды не стоит — aiogram отдаёт апдейт первому подошедшему.

    Реэкспорт внизу. Через `app.handlers.farm.X` к ферме обращаются тесты и
    соседний код; убранное отсюда имя падает не при импорте, а в момент
    обращения.
"""
import logging

from aiogram import Router
from aiogram.types import CallbackQuery

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query.middleware()
async def require_active_subscription(handler, event: CallbackQuery, data: dict):
    """Пускать на ферму только действующих подписчиков.

    Зачем middleware, а не проверка в каждом обработчике: ферма начисляет
    реальный баланс, и хендлеров у неё полтора десятка — посадка, полив,
    удобрение, сбор, ранний сбор, покупка грядки, щит. Забыть проверку в
    одном из них = неплательщик продолжает майнить деньги. Один вход в
    роутер закрывает все callback'и разом, включая те, что добавят позже.

    Почему это вообще возможно без проверки: старое сообщение с инлайн-
    клавиатурой остаётся в чате навсегда. Подписка истекла, была отменена
    или возвращена — а кнопки «Собрать» в старом экране по-прежнему живые.

    Меню игр и остальные игры проверяют подписку сами (app/handlers/game.py),
    здесь повторяем ту же семантику: get_subscription возвращает строку
    только при status='active' и expires_at в будущем.
    """
    telegram_id = event.from_user.id if event.from_user else 0
    try:
        subscription = await database.get_subscription(telegram_id)
    except Exception as e:
        # База недоступна — не наказываем плательщика: пропускаем дальше,
        # обработчик сам упрётся в ensure_db_ready_callback и покажет ошибку.
        logger.warning("FARM_GUARD_DB_ERROR user=%s: %s", telegram_id, e)
        return await handler(event, data)

    if subscription:
        return await handler(event, data)

    logger.info("FARM_GUARD_BLOCKED user=%s no_active_subscription", telegram_id)
    language = await resolve_user_language(telegram_id)
    await event.answer(
        i18n_get_text(language, "farm.paywall"),
        show_alert=True,
    )
    return None


# Подключение подроутеров. Импорт стоит ПОСЛЕ создания router: подмодули
# тянут screen/mechanics, а те — этот пакет; импорт наверху замкнул бы кольцо.
from app.handlers.farm import plots, storm  # noqa: E402

router.include_router(plots.router)
router.include_router(storm.router)


# Реэкспорт: снаружи к ферме обращаются как к одному модулю (`farm.PLANT_TYPES`,
# `farm._render_farm`, `farm.callback_farm_shield`). Имена перечислены явно, а
# не через `import *`, чтобы пропажу было видно глазами при ревью.
from app.handlers.farm.mechanics import (  # noqa: F401,E402
    FARM_BOOST_MAX_FRACTION,
    PLANT_TYPES,
    SHIELD_INVOICE_MIN_LEAD_MINUTES,
    STORM_STALE_AFTER_HOURS,
    _apply_growth_boost,
    _get_imminent_storm,
    _invoice_can_arrive_in_time,
    _plant_name,
    _storm_seconds_left,
)
from app.handlers.farm.screen import _render_farm  # noqa: F401,E402
from app.handlers.farm.plots import (  # noqa: F401,E402
    callback_farm_buy_plot,
    callback_farm_choose_plant,
    callback_farm_dig,
    callback_farm_dig_confirm,
    callback_farm_fert,
    callback_farm_harvest,
    callback_farm_noop,
    callback_farm_plant,
    callback_farm_remove,
    callback_farm_water,
    callback_game_farm,
)
from app.handlers.farm.storm import (  # noqa: F401,E402
    _find_growing_plot,
    _parse_plot_id,
    _shield_invoice_allowed,
    callback_farm_early_harvest,
    callback_farm_shield,
    callback_farm_shield_lava,
    callback_farm_shield_sbp,
)
