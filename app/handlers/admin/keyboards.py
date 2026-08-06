"""Клавиатуры админского раздела: главное меню /admin и кнопка «назад».

ЗДЕСЬ ТОЛЬКО ТО, У ЧЕГО ЕСТЬ ЖИВОЙ ОБРАБОТЧИК

    callback_data — это адрес обработчика. Кнопка с адресом, которого не
    существует, в Telegram не даёт НИЧЕГО: ни ошибки, ни ответа, ни строчки
    в логе. Человек жмёт и решает, что бот сломался.

    В этом файле лежали 26 сборщиков клавиатур, а вызывались два. Остальные
    24 обслуживали экраны, удалённые вместе с 23 админскими модулями,
    работу которых делает веб-дашборд (app/api/dashboard, dashboard/):
    карточка пользователя, выдача и отзыв доступа, скидки, VIP, баланс,
    история, экспорт, гифт-ссылки на ГБ, мастер рассылок. Их код никто не
    звал, но они держали в дереве около шестидесяти callback_data без
    обработчиков — при поиске мёртвых кнопок это ложный след, а при
    попытке «просто подключить обратно» — 24 молчащих экрана.

    Сборщики удалены. Экраны нужно возвращать из дашборда целиком, вместе
    с обработчиками, а не по клавиатуре.

ГЛАВНОЕ МЕНЮ

    Из главного меню /admin убраны тринадцать кнопок, у которых не осталось
    обработчика: рефералы, карточка пользователя, баланс, ключи, центр
    уведомлений, готовая рассылка о тех. работах, аудит, экспорт,
    гифт-ссылки на ГБ, откат premium, аудит подписок, аудит дат БД и
    STAGE-список пользователей. Всё это делает дашборд.

    Добавлена кнопка «ГБ обхода»: раздел ручной выдачи трафика остался в
    боте (в дашборде его нет), но потерял вход вместе с карточкой
    пользователя — см. app/handlers/admin/traffic_admin.py.

    Прежде чем добавлять сюда кнопку, убедитесь, что обработчик под её
    callback_data зарегистрирован. Сторож — tests/services/
    test_every_button_has_handler.py.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.i18n import get_text as i18n_get_text


def get_admin_dashboard_keyboard(language: str = "ru"):
    """Главное меню /admin. Только разделы, оставшиеся в боте."""
    return InlineKeyboardMarkup(inline_keyboard=[
        # — Обзор —
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.dashboard"), callback_data="admin:dashboard")],
        # — Аналитика и статистика —
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.stats"), callback_data="admin:stats"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.analytics"), callback_data="admin:analytics"),
        ],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.metrics"), callback_data="admin:metrics")],
        [InlineKeyboardButton(text="📦 Покупки по тарифам", callback_data="admin:purchase_stats")],
        # — Пользователи —
        [InlineKeyboardButton(text="💬 Написать пользователю", callback_data="admin:chat")],
        [InlineKeyboardButton(text="🌐 ГБ обхода — выдать / списать", callback_data="admin:traffic_user")],
        # — Маркетинг —
        [InlineKeyboardButton(text="🎁 Выдать бонус", callback_data="admin:bonus")],
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.create_promocode"), callback_data="admin:create_promocode"),
            InlineKeyboardButton(text=i18n_get_text(language, "admin.promo_stats"), callback_data="admin_promo_stats"),
        ],
        [InlineKeyboardButton(text="🎁 Trial → промо −30%", callback_data="admin:promo_trial")],
        # — Система и игры —
        [
            InlineKeyboardButton(text=i18n_get_text(language, "admin.system"), callback_data="admin:system"),
            InlineKeyboardButton(text="🌐 QoDev", callback_data="admin:qodev"),
        ],
        [InlineKeyboardButton(text="🌪 Шторм (Ферма)", callback_data="admin:storm")],
    ])


def get_admin_back_keyboard(language: str = "ru"):
    """Клавиатура с кнопкой 'Назад' для админ-разделов"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    return keyboard
