"""Админская часть бота — только то, что удобнее делать прямо в чате.

ЧТО ЗДЕСЬ ОСТАЛОСЬ И ПОЧЕМУ

    base.py               вход /admin, ссылка на дашборд, сброс пароля;
    chat.py               переписка админа с пользователем;
    system.py             здоровье системы, обслуживание, тестовые уведомления;
    reissue.py            перевыпуск VPN-ключей (сейчас без кнопок в UI);
    promocodes.py         мастер промокода (недоведён, см. докстринг файла);
    stats.py              статистика;
    apple_id_delivery.py  выдача оплаченного заказа Apple ID;
    spotify_delivery.py   выдача оплаченного заказа Spotify;
    bonus.py              массовая выдача бонусов сегменту;
    traffic_admin.py      выдача и списание ГБ обхода вручную;
    farm_storm.py         консоль штормов фермы;
    promo_trial.py        промо-рассылка тем, кто на пробном периоде.

    Экраны выдачи заказов — не «панель»: админу приходит уведомление с
    данными покупателя и кнопкой «Выполнено», и работать с ним удобнее
    там же, где оно пришло. Последние четыре модуля остались потому, что
    аналога в веб-дашборде у них пока нет.

ЧЕГО ЗДЕСЬ БОЛЬШЕ НЕТ

    Выдача и отзыв доступа, смена тарифа, скидки, баланс, VIP, кешбэк,
    рассылки, промокоды, рефералы, экспорт, аудит, сверка подписок,
    перевыпуск ключей — всё это живёт в веб-дашборде (app/api/dashboard
    и dashboard/). Держать две реализации одного действия значит однажды
    получить разное поведение: правку вносят в одну, а работает вторая.

    Удалено 23 модуля, 11 111 строк. Экран можно достать из истории git,
    но сначала стоит спросить, почему дашборда оказалось недостаточно.

ПРОВЕРКА ДОСТУПА

    Ниже висит middleware на весь раздел: она пускает дальше только
    администратора. Ручные проверки внутри обработчиков остаются вторым
    рубежом, но забыть их в новом экране больше не опасно.
"""
import logging

from aiogram import Router

# base.py был на 1167 строк и держал пять несвязанных разделов сразу.
# Разрезан по обязанностям; каждый кусок обязан быть подключён ниже —
# забытый include_router не даёт ошибки, кнопка просто молчит.
from .base import admin_base_router
from .chat import admin_chat_router
from .system import admin_system_router
from .reissue import admin_reissue_router
from .promocodes import admin_promocodes_router
from .stats import admin_stats_router
from .bonus import admin_bonus_router
from .traffic_admin import admin_traffic_router
from .farm_storm import admin_farm_storm_router
from .promo_trial import admin_promo_trial_router
from .apple_id_delivery import apple_id_delivery_router
from .spotify_delivery import spotify_delivery_router

_admin_logger = logging.getLogger(__name__)

router = Router()

router.include_router(admin_base_router)
router.include_router(admin_chat_router)
router.include_router(admin_system_router)
router.include_router(admin_reissue_router)
router.include_router(admin_promocodes_router)
router.include_router(admin_stats_router)
router.include_router(admin_bonus_router)
router.include_router(admin_traffic_router)
router.include_router(admin_farm_storm_router)
router.include_router(admin_promo_trial_router)
router.include_router(apple_id_delivery_router)
router.include_router(spotify_delivery_router)


# ──────────────────────────────────────────────────────────────────────
#  Единая проверка «я админ» на входе в весь админский раздел
# ──────────────────────────────────────────────────────────────────────
#
# Зачем middleware, когда проверка и так стоит в каждом обработчике.
# Она стоит не в каждом: проверка написана руками десятки раз в виде
# `if callback.from_user.id != config.ADMIN_TELEGRAM_ID: return`, и это
# ровно тот случай, когда достаточно один раз забыть строчку в новом
# обработчике, чтобы админская операция стала доступна кому угодно.
#
# Middleware на родительском роутере закрывает раздел целиком, включая
# обработчики, которые напишут завтра.
#
# Источник истины по «кто админ» — app.services.admin_auth.is_admin.
async def _require_admin(handler, event, data: dict):
    """Пропустить дальше только администратора.

    Молчим в ответ чужому: админский раздел не должен подтверждать своё
    существование посторонним. Telegram сам погасит «часики» на кнопке.
    """
    from app.services.admin_auth import is_admin

    user = getattr(event, "from_user", None)
    if user is not None and is_admin(user.id):
        return await handler(event, data)

    _admin_logger.warning(
        "ADMIN_ACCESS_DENIED user=%s event=%s",
        getattr(user, "id", None), type(event).__name__,
    )
    return None


router.message.middleware(_require_admin)
router.callback_query.middleware(_require_admin)
