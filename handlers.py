from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
import logging
import database
import localization
import config
import time
import csv
import tempfile
import os
import asyncio

# Время последней отправки алерта о ключах (для предотвращения спама)
_last_keys_alert_time: datetime = None
_last_keys_alert_count: int = -1  # Количество ключей при последнем алерте
_ALERT_COOLDOWN_MINUTES = 30  # Минимальный интервал между алертами (в минутах)

# Время запуска бота (для uptime)
_bot_start_time = time.time()


class AdminUserSearch(StatesGroup):
    waiting_for_user_id = State()


class BroadcastCreate(StatesGroup):
    waiting_for_title = State()
    waiting_for_message = State()
    waiting_for_type = State()
    waiting_for_confirm = State()

router = Router()

logging.basicConfig(level=logging.INFO)


async def send_vpn_keys_alert(bot: Bot, keys_count: int):
    """Отправить алерт администратору о количестве VPN-ключей
    
    Args:
        bot: Экземпляр бота для отправки сообщения
        keys_count: Текущее количество свободных ключей
    """
    global _last_keys_alert_time, _last_keys_alert_count
    
    now = datetime.now()
    
    # Проверяем, нужно ли отправлять алерт
    should_send = False
    
    if keys_count == 0:
        # Критический алерт - отправляем всегда
        should_send = True
        alert_text = "🚨 КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ\n\nСвободные VPN-ключи закончились!\nПодтверждение платежей заблокировано.\n\nНеобходимо срочно пополнить таблицу vpn_keys."
    elif keys_count <= 5:
        # Предупреждение - отправляем не чаще раза в N минут
        if _last_keys_alert_time is None:
            should_send = True
        else:
            time_since_last = now - _last_keys_alert_time
            if time_since_last >= timedelta(minutes=_ALERT_COOLDOWN_MINUTES):
                should_send = True
            # Также отправляем, если количество изменилось (уменьшилось)
            elif keys_count < _last_keys_alert_count:
                should_send = True
        
        if should_send:
            alert_text = f"⚠️ Предупреждение\n\nСвободных VPN-ключей осталось: {keys_count}\nРекомендуется пополнить таблицу vpn_keys."
    else:
        # Достаточно ключей - не отправляем
        should_send = False
    
    if should_send:
        try:
            await bot.send_message(config.ADMIN_TELEGRAM_ID, alert_text)
            _last_keys_alert_time = now
            _last_keys_alert_count = keys_count
            logging.info(f"VPN keys alert sent to admin: {keys_count} keys remaining")
        except Exception as e:
            logging.error(f"Error sending VPN keys alert to admin: {e}")


def get_language_keyboard():
    """Клавиатура для выбора языка"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="English", callback_data="lang_en"),
        ],
        [
            InlineKeyboardButton(text="O'zbek", callback_data="lang_uz"),
            InlineKeyboardButton(text="Тоҷикӣ", callback_data="lang_tj"),
        ],
    ])
    return keyboard


def get_main_menu_keyboard(language: str):
    """Клавиатура главного меню"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "profile"),
            callback_data="menu_profile"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "buy_vpn"),
            callback_data="menu_buy_vpn"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "instruction"),
            callback_data="menu_instruction"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "service_status"),
            callback_data="menu_service_status"
        )],
        [
            InlineKeyboardButton(
                text=localization.get_text(language, "about"),
                callback_data="menu_about"
            ),
            InlineKeyboardButton(
                text=localization.get_text(language, "support"),
                callback_data="menu_support"
            ),
        ],
    ])
    return keyboard


def get_back_keyboard(language: str):
    """Кнопка Назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "back"),
            callback_data="menu_main"
        )]
    ])


def get_profile_keyboard_with_copy(language: str):
    """Клавиатура профиля с кнопкой копирования ключа и историей"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "copy_key"),
            callback_data="copy_key"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "subscription_history"),
            callback_data="subscription_history"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "back"),
            callback_data="menu_main"
        )]
    ])
    return keyboard


def get_profile_keyboard(language: str):
    """Клавиатура с кнопками профиля и инструкции (после активации)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=localization.get_text(language, "profile"),
                callback_data="menu_profile"
            ),
            InlineKeyboardButton(
                text=localization.get_text(language, "instruction"),
                callback_data="menu_instruction"
            ),
        ],
        [InlineKeyboardButton(
            text=localization.get_text(language, "copy_key"),
            callback_data="copy_key"
        )]
    ])
    return keyboard


def get_tariff_keyboard(language: str):
    """Клавиатура выбора тарифа"""
    buttons = []
    for tariff_key, tariff_data in config.TARIFFS.items():
        price = tariff_data["price"]
        
        # Используем локализованные тексты кнопок
        tariff_button_key = f"tariff_button_{tariff_key}"
        text = localization.get_text(language, tariff_button_key, price=price)
        
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"tariff_{tariff_key}")])
    
    buttons.append([InlineKeyboardButton(
        text=localization.get_text(language, "back"),
        callback_data="menu_main"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_method_keyboard(language: str):
    """Клавиатура выбора способа оплаты"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "payment_test"),
            callback_data="payment_test"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "payment_sbp"),
            callback_data="payment_sbp"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "back"),
            callback_data="menu_buy_vpn"
        )],
    ])
    return keyboard


def get_sbp_payment_keyboard(language: str):
    """Клавиатура для оплаты СБП"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "paid_button"),
            callback_data="payment_paid"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "back"),
            callback_data="menu_main"
        )],
    ])
    return keyboard


def get_pending_payment_keyboard(language: str):
    """Клавиатура после нажатия 'Я оплатил'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "back"),
            callback_data="menu_main"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "support"),
            callback_data="menu_support"
        )],
    ])
    return keyboard


def get_about_keyboard(language: str):
    """Клавиатура раздела 'О сервисе'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "privacy_policy"),
            callback_data="about_privacy"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "service_status"),
            callback_data="menu_service_status"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "back"),
            callback_data="menu_main"
        )],
    ])
    return keyboard


def get_service_status_keyboard(language: str):
    """Клавиатура экрана 'Статус сервиса'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "back"),
            callback_data="menu_main"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "support"),
            callback_data="menu_support"
        )],
    ])
    return keyboard


def get_support_keyboard(language: str):
    """Клавиатура раздела 'Поддержка'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "change_language"),
            callback_data="change_language"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "back"),
            callback_data="menu_main"
        )],
    ])
    return keyboard


def get_instruction_keyboard(language: str):
    """Клавиатура экрана 'Инструкция'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=localization.get_text(language, "instruction_device_ios"),
                url="https://apps.apple.com/app/outline-app/id1356177741"
            ),
            InlineKeyboardButton(
                text=localization.get_text(language, "instruction_device_android"),
                url="https://play.google.com/store/apps/details?id=org.outline.android.client"
            ),
        ],
        [
            InlineKeyboardButton(
                text=localization.get_text(language, "instruction_device_desktop"),
                url="https://getoutline.org/ru/get-started/"
            ),
        ],
        [InlineKeyboardButton(
            text=localization.get_text(language, "back"),
            callback_data="menu_main"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "support"),
            callback_data="menu_support"
        )],
    ])
    return keyboard


def get_admin_dashboard_keyboard():
    """Клавиатура главного экрана админ-дашборда"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
        [InlineKeyboardButton(text="📜 Аудит", callback_data="admin:audit")],
        [InlineKeyboardButton(text="🔑 VPN-ключи", callback_data="admin:keys")],
        [InlineKeyboardButton(text="👤 Пользователь", callback_data="admin:user")],
        [InlineKeyboardButton(text="🚨 Система", callback_data="admin:system")],
        [InlineKeyboardButton(text="📤 Экспорт данных", callback_data="admin:export")],
        [InlineKeyboardButton(text="📣 Уведомления", callback_data="admin:broadcast")],
    ])
    return keyboard


def get_admin_back_keyboard():
    """Клавиатура с кнопкой 'Назад' для админ-разделов"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:main")],
    ])
    return keyboard


def get_broadcast_type_keyboard():
    """Клавиатура выбора типа уведомления"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ℹ️ Информация", callback_data="broadcast_type:info")],
        [InlineKeyboardButton(text="🔧 Технические работы", callback_data="broadcast_type:maintenance")],
        [InlineKeyboardButton(text="🔒 Безопасность", callback_data="broadcast_type:security")],
        [InlineKeyboardButton(text="🎯 Промо", callback_data="broadcast_type:promo")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:broadcast")],
    ])
    return keyboard


def get_broadcast_confirm_keyboard():
    """Клавиатура подтверждения отправки уведомления"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast:confirm_send")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:broadcast")],
    ])
    return keyboard


def get_admin_export_keyboard():
    """Клавиатура выбора типа экспорта"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:export:users")],
        [InlineKeyboardButton(text="🔑 Активные подписки", callback_data="admin:export:subscriptions")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:main")],
    ])
    return keyboard


def get_admin_user_keyboard(has_active_subscription: bool = False, user_id: int = None):
    """Клавиатура для раздела пользователя"""
    buttons = []
    if has_active_subscription:
        callback_data = f"admin:user_reissue:{user_id}" if user_id else "admin:user_reissue"
        buttons.append([InlineKeyboardButton(text="🔁 Перевыпустить ключ", callback_data=callback_data)])
    if user_id:
        buttons.append([InlineKeyboardButton(text="🧾 История подписок", callback_data=f"admin:user_history:{user_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin:main")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard


def get_admin_payment_keyboard(payment_id: int):
    """Клавиатура для администратора (подтверждение/отклонение платежа)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить",
                callback_data=f"approve_payment:{payment_id}"
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"reject_payment:{payment_id}"
            ),
        ],
    ])
    return keyboard


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    telegram_id = message.from_user.id
    username = message.from_user.username
    
    # Создаем пользователя если его нет
    user = await database.get_user(telegram_id)
    if not user:
        await database.create_user(telegram_id, username, "ru")
    else:
        # Обновляем username если изменился
        await database.update_username(telegram_id, username)
    
    text = localization.get_text("ru", "language_select")
    await message.answer(text, reply_markup=get_language_keyboard())


@router.message(Command("profile"))
async def cmd_profile(message: Message):
    """Обработчик команды /profile"""
    telegram_id = message.from_user.id
    user = await database.get_user(telegram_id)
    
    if not user:
        await message.answer("Пожалуйста, начните с команды /start")
        return
    
    language = user.get("language", "ru")
    await show_profile(message, language)


async def show_profile(message_or_query, language: str):
    """Показать профиль пользователя"""
    if isinstance(message_or_query, Message):
        telegram_id = message_or_query.from_user.id
        send_func = message_or_query.answer
    else:
        telegram_id = message_or_query.from_user.id
        send_func = message_or_query.message.edit_text
    
    subscription = await database.get_subscription(telegram_id)
    
    if subscription:
        # asyncpg возвращает datetime объекты напрямую, не строки
        expires_at = subscription["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        expires_str = expires_at.strftime("%d.%m.%Y")
        text = localization.get_text(language, "profile_active", date=expires_str, vpn_key=subscription["vpn_key"])
        text += localization.get_text(language, "profile_renewal_hint")
        await send_func(text, reply_markup=get_profile_keyboard_with_copy(language))
    else:
        # Проверяем, есть ли pending платеж
        pending_payment = await database.get_pending_payment_by_user(telegram_id)
        if pending_payment:
            text = localization.get_text(language, "profile_payment_check")
        else:
            text = localization.get_text(language, "no_subscription")
        await send_func(text, reply_markup=get_back_keyboard(language))


@router.callback_query(F.data == "change_language")
async def callback_change_language(callback: CallbackQuery):
    """Изменить язык"""
    text = localization.get_text("ru", "language_select")
    await callback.message.edit_text(text, reply_markup=get_language_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("lang_"))
async def callback_language(callback: CallbackQuery):
    """Обработчик выбора языка"""
    language = callback.data.split("_")[1]
    telegram_id = callback.from_user.id
    
    await database.update_user_language(telegram_id, language)
    
    text = localization.get_text(language, "welcome")
    await callback.message.edit_text(text, reply_markup=get_main_menu_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "menu_main")
async def callback_main_menu(callback: CallbackQuery):
    """Главное меню"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "welcome")
    await callback.message.edit_text(text, reply_markup=get_main_menu_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "menu_profile")
async def callback_profile(callback: CallbackQuery):
    """Мой профиль"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    await show_profile(callback, language)
    await callback.answer()


@router.callback_query(F.data == "copy_key")
async def callback_copy_key(callback: CallbackQuery):
    """Копировать VPN-ключ"""
    await callback.answer()
    
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    # Проверяем, что у пользователя есть активная подписка
    subscription = await database.get_subscription(telegram_id)
    
    if not subscription:
        text = localization.get_text(language, "no_active_subscription")
        await callback.message.answer(text)
        return
    
    # Отправляем VPN-ключ отдельным сообщением
    vpn_key = subscription["vpn_key"]
    await callback.message.answer(f"`{vpn_key}`", parse_mode="Markdown")


@router.callback_query(F.data == "subscription_history")
async def callback_subscription_history(callback: CallbackQuery):
    """История подписок"""
    await callback.answer()
    
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    # Получаем историю подписок
    history = await database.get_subscription_history(telegram_id, limit=5)
    
    if not history:
        text = localization.get_text(language, "subscription_history_empty")
        await callback.message.answer(text)
        return
    
    # Формируем текст истории
    text = localization.get_text(language, "subscription_history") + "\n\n"
    
    action_type_map = {
        "purchase": localization.get_text(language, "subscription_history_action_purchase"),
        "renewal": localization.get_text(language, "subscription_history_action_renewal"),
        "reissue": localization.get_text(language, "subscription_history_action_reissue"),
        "manual_reissue": localization.get_text(language, "subscription_history_action_manual_reissue"),
    }
    
    for record in history:
        start_date = record["start_date"]
        if isinstance(start_date, str):
            start_date = datetime.fromisoformat(start_date)
        start_str = start_date.strftime("%d.%m.%Y")
        
        end_date = record["end_date"]
        if isinstance(end_date, str):
            end_date = datetime.fromisoformat(end_date)
        end_str = end_date.strftime("%d.%m.%Y")
        
        action_type = record["action_type"]
        action_text = action_type_map.get(action_type, action_type)
        
        text += f"• {start_str} — {action_text}\n"
        
        # Для purchase и reissue показываем ключ
        if action_type in ["purchase", "reissue", "manual_reissue"]:
            text += f"  Ключ: {record['vpn_key']}\n"
        
        text += f"  До: {end_str}\n\n"
    
    await callback.message.answer(text, reply_markup=get_back_keyboard(language))


@router.callback_query(F.data == "menu_buy_vpn")
async def callback_buy_vpn(callback: CallbackQuery):
    """Купить VPN - выбор тарифа"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "select_tariff")
    await callback.message.edit_text(text, reply_markup=get_tariff_keyboard(language))
    await callback.answer()


@router.callback_query(F.data.startswith("tariff_"))
async def callback_tariff(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора тарифа"""
    tariff_key = callback.data.split("_")[1]
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    # Сохраняем выбранный тариф в состоянии
    await state.update_data(tariff=tariff_key)
    
    text = localization.get_text(language, "select_payment")
    await callback.message.edit_text(text, reply_markup=get_payment_method_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "payment_test")
async def callback_payment_test(callback: CallbackQuery):
    """Тестовая оплата (не работает)"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    # Тестовая оплата не работает - возвращаем назад
    await callback.answer("Эта функция не работает", show_alert=True)
    text = localization.get_text(language, "select_payment")
    await callback.message.edit_text(text, reply_markup=get_payment_method_keyboard(language))


@router.callback_query(F.data == "payment_sbp")
async def callback_payment_sbp(callback: CallbackQuery, state: FSMContext):
    """Оплата через СБП"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    data = await state.get_data()
    tariff_key = data.get("tariff", "1")
    tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
    
    # Формируем текст с реквизитами
    # Форматируем счет с пробелами (каждые 4 цифры)
    account_formatted = ' '.join(config.SBP_DETAILS['account'][i:i+4] for i in range(0, len(config.SBP_DETAILS['account']), 4))
    text = localization.get_text(
        language, 
        "sbp_payment_text",
        bank=config.SBP_DETAILS['bank'],
        account=account_formatted,
        name=config.SBP_DETAILS['name'],
        price=tariff_data['price']
    )
    
    await callback.message.edit_text(text, reply_markup=get_sbp_payment_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "payment_paid")
async def callback_payment_paid(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал 'Я оплатил'"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    data = await state.get_data()
    tariff_key = data.get("tariff", "1")
    
    # Проверяем наличие pending платежа перед созданием
    existing_payment = await database.get_pending_payment_by_user(telegram_id)
    if existing_payment:
        text = localization.get_text(language, "payment_pending")
        await callback.message.edit_text(text, reply_markup=get_pending_payment_keyboard(language))
        await callback.answer("У вас уже есть ожидающий платеж", show_alert=True)
        await state.clear()
        return
    
    # Создаем платеж
    payment_id = await database.create_payment(telegram_id, tariff_key)
    
    if payment_id is None:
        # Это не должно произойти, так как мы проверили выше, но на всякий случай
        text = localization.get_text(language, "payment_pending")
        await callback.message.edit_text(text, reply_markup=get_pending_payment_keyboard(language))
        await callback.answer("Не удалось создать платеж. Попробуйте позже.", show_alert=True)
        await state.clear()
        return
    
    # Отправляем сообщение пользователю
    text = localization.get_text(language, "payment_pending")
    await callback.message.edit_text(text, reply_markup=get_pending_payment_keyboard(language))
    await callback.answer()
    
    # Уведомляем администратора
    tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
    username = callback.from_user.username or "не указан"
    
    admin_text = f"💰 Новая оплата\n"
    admin_text += f"Пользователь: @{username}\n"
    admin_text += f"Telegram ID: {telegram_id}\n"
    admin_text += f"Тариф: {tariff_data['months']} месяцев\n"
    admin_text += f"Стоимость: {tariff_data['price']} руб."
    
    try:
        await callback.bot.send_message(
            config.ADMIN_TELEGRAM_ID,
            admin_text,
            reply_markup=get_admin_payment_keyboard(payment_id)
        )
    except Exception as e:
        logging.error(f"Error sending admin notification: {e}")
    
    await state.clear()


@router.callback_query(F.data == "menu_about")
async def callback_about(callback: CallbackQuery):
    """О сервисе"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "about_text")
    await callback.message.edit_text(text, reply_markup=get_about_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "menu_service_status")
async def callback_service_status(callback: CallbackQuery):
    """Статус сервиса"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "service_status_text")
    await callback.message.edit_text(text, reply_markup=get_service_status_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "about_privacy")
async def callback_privacy(callback: CallbackQuery):
    """Политика конфиденциальности"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "privacy_policy_text")
    await callback.message.edit_text(text, reply_markup=get_about_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "menu_instruction")
async def callback_instruction(callback: CallbackQuery):
    """Инструкция"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "instruction_text")
    await callback.message.edit_text(text, reply_markup=get_instruction_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "menu_support")
async def callback_support(callback: CallbackQuery):
    """Поддержка"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(
        language,
        "support_text",
        email=config.SUPPORT_EMAIL,
        telegram=config.SUPPORT_TELEGRAM
    )
    await callback.message.edit_text(text, reply_markup=get_support_keyboard(language))
    await callback.answer()


@router.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment(callback: CallbackQuery):
    """Админ подтвердил платеж"""
    await callback.answer()  # ОБЯЗАТЕЛЬНО
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized approve attempt by user {callback.from_user.id}")
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    try:
        payment_id = int(callback.data.split(":")[1])
        
        logging.info(f"APPROVE pressed by admin {callback.from_user.id}, payment_id={payment_id}")
        
        # Получить платеж из БД
        payment = await database.get_payment(payment_id)
        
        if not payment:
            logging.warning(f"Payment {payment_id} not found for approve")
            await callback.answer("Платеж не найден", show_alert=True)
            return
        
        if payment["status"] != "pending":
            logging.warning(
                f"Attempt to approve already processed payment {payment_id}, status={payment['status']}"
            )
            await callback.answer("Платеж уже обработан", show_alert=True)
            # Удаляем кнопки даже если платеж уже обработан
            await callback.message.edit_reply_markup(reply_markup=None)
            return
        
        telegram_id = payment["telegram_id"]
        tariff_key = payment["tariff"]
        tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
        
        # Проверяем количество свободных ключей перед approve
        keys_count = await database.get_free_vpn_keys_count()
        
        # Отправляем алерт если нужно
        await send_vpn_keys_alert(callback.bot, keys_count)
        
        # Если ключей нет - блокируем approve
        if keys_count == 0:
            logging.error(f"Cannot approve payment {payment_id}: no free VPN keys available")
            await callback.answer("Нет свободных VPN-ключей. Подтверждение платежей заблокировано. Пополните таблицу vpn_keys.", show_alert=True)
            return
        
        # Атомарно подтверждаем платеж и создаем/продлеваем подписку
        # Логика получения ключа находится внутри approve_payment_atomic
        admin_telegram_id = callback.from_user.id
        result = await database.approve_payment_atomic(payment_id, tariff_data["months"], admin_telegram_id)
        expires_at, is_renewal, vpn_key = result
        
        if expires_at is None or vpn_key is None:
            if vpn_key is None:
                logging.error(f"No free VPN keys available for payment {payment_id}")
                await callback.answer("Нет свободных VPN-ключей. Пополните таблицу vpn_keys в базе данных.", show_alert=True)
            else:
                logging.error(f"Failed to approve payment {payment_id} atomically")
                await callback.answer("Ошибка подтверждения платежа. Проверь логи.", show_alert=True)
            return
        
        # Логируем продление, если было
        if is_renewal:
            logging.info(f"Subscription renewed for user {telegram_id}, payment_id={payment_id}, expires_at={expires_at}")
        else:
            logging.info(f"New subscription created for user {telegram_id}, payment_id={payment_id}, expires_at={expires_at}")
        
        # Уведомляем пользователя
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        
        expires_str = expires_at.strftime("%d.%m.%Y")
        text = localization.get_text(language, "payment_approved", vpn_key=vpn_key, date=expires_str)
        
        try:
            await callback.bot.send_message(
                telegram_id, 
                text, 
                reply_markup=get_profile_keyboard(language)
            )
            logging.info(f"Approval message sent to user {telegram_id} for payment {payment_id}")
        except Exception as e:
            logging.error(f"Error sending approval message to user {telegram_id}: {e}")
        
        await callback.message.edit_text(f"✅ Платеж {payment_id} подтвержден")
        # Удаляем inline-кнопки после обработки
        await callback.message.edit_reply_markup(reply_markup=None)
        
    except Exception as e:
        logging.exception(f"Error in approve_payment callback for payment_id={payment_id if 'payment_id' in locals() else 'unknown'}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Административный дашборд"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized admin dashboard attempt by user {message.from_user.id}")
        await message.answer("Недостаточно прав доступа")
        return
    
    text = "🛠 Atlas Secure · Admin Dashboard\n\nВыберите действие:"
    await message.answer(text, reply_markup=get_admin_dashboard_keyboard())


@router.callback_query(F.data == "admin:main")
async def callback_admin_main(callback: CallbackQuery):
    """Главный экран админ-дашборда"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    text = "🛠 Atlas Secure · Admin Dashboard\n\nВыберите действие:"
    await callback.message.edit_text(text, reply_markup=get_admin_dashboard_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin:stats")
async def callback_admin_stats(callback: CallbackQuery):
    """Раздел Статистика"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        stats = await database.get_admin_stats()
        
        text = "📊 Статистика\n\n"
        text += f"👥 Всего пользователей: {stats['total_users']}\n"
        text += f"🔑 Активных подписок: {stats['active_subscriptions']}\n"
        text += f"⛔ Истёкших подписок: {stats['expired_subscriptions']}\n"
        text += f"💳 Всего платежей: {stats['total_payments']}\n"
        text += f"✅ Подтверждённых платежей: {stats['approved_payments']}\n"
        text += f"❌ Отклонённых платежей: {stats['rejected_payments']}\n"
        text += f"🔓 Свободных VPN-ключей: {stats['free_vpn_keys']}"
        
        await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
        await callback.answer()
        
        # Логируем просмотр статистики
        await database._log_audit_event_atomic_standalone("admin_view_stats", callback.from_user.id, None, "Admin viewed statistics")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_stats: {e}")
        await callback.answer("Ошибка при получении статистики", show_alert=True)


@router.callback_query(F.data == "admin:audit")
async def callback_admin_audit(callback: CallbackQuery):
    """Раздел Аудит (переиспользование логики /admin_audit)"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        # Получаем последние 10 записей из audit_log
        audit_logs = await database.get_last_audit_logs(limit=10)
        
        if not audit_logs:
            text = "📜 Аудит\n\nАудит пуст. Действий не зафиксировано."
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer()
            return
        
        # Формируем сообщение
        lines = ["📜 Аудит", ""]
        
        for log in audit_logs:
            # Форматируем дату и время
            created_at = log["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif isinstance(created_at, datetime):
                pass
            else:
                created_at = datetime.now()
            
            created_str = created_at.strftime("%Y-%m-%d %H:%M")
            
            lines.append(f"🕒 {created_str}")
            lines.append(f"Действие: {log['action']}")
            lines.append(f"Админ: {log['telegram_id']}")
            
            if log['target_user']:
                lines.append(f"Пользователь: {log['target_user']}")
            else:
                lines.append("Пользователь: —")
            
            if log['details']:
                details = log['details']
                if len(details) > 150:
                    details = details[:150] + "..."
                lines.append(f"Детали: {details}")
            else:
                lines.append("Детали: —")
            
            lines.append("")
            lines.append("⸻")
            lines.append("")
        
        # Убираем последний разделитель
        if lines[-1] == "" and lines[-2] == "⸻":
            lines = lines[:-2]
        
        text = "\n".join(lines)
        
        # Проверяем лимит Telegram (4096 символов на сообщение)
        if len(text) > 4000:
            # Уменьшаем до 5 записей
            audit_logs = await database.get_last_audit_logs(limit=5)
            lines = ["📜 Аудит", ""]
            
            for log in audit_logs:
                created_at = log["created_at"]
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif isinstance(created_at, datetime):
                    pass
                else:
                    created_at = datetime.now()
                
                created_str = created_at.strftime("%Y-%m-%d %H:%M")
                
                lines.append(f"🕒 {created_str}")
                lines.append(f"Действие: {log['action']}")
                lines.append(f"Админ: {log['telegram_id']}")
                
                if log['target_user']:
                    lines.append(f"Пользователь: {log['target_user']}")
                else:
                    lines.append("Пользователь: —")
                
                if log['details']:
                    details = log['details']
                    if len(details) > 100:
                        details = details[:100] + "..."
                    lines.append(f"Детали: {details}")
                else:
                    lines.append("Детали: —")
                
                lines.append("")
                lines.append("⸻")
                lines.append("")
            
            if lines[-1] == "" and lines[-2] == "⸻":
                lines = lines[:-2]
            
            text = "\n".join(lines)
        
        await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
        await callback.answer()
        
        # Логируем просмотр аудита
        await database._log_audit_event_atomic_standalone("admin_view_audit", callback.from_user.id, None, "Admin viewed audit log")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_audit: {e}")
        await callback.answer("Ошибка при получении audit log", show_alert=True)


@router.callback_query(F.data == "admin:keys")
async def callback_admin_keys(callback: CallbackQuery):
    """Раздел VPN-ключи"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        stats = await database.get_vpn_keys_stats()
        
        text = "🔑 VPN-ключи\n\n"
        text += f"Всего ключей: {stats['total']}\n"
        text += f"Использованных: {stats['used']}\n"
        
        if stats['free'] <= 5:
            text += f"⚠️ Свободных: {stats['free']}\n"
            text += "\n⚠️ ВНИМАНИЕ: Количество свободных ключей критически низкое!"
        else:
            text += f"Свободных: {stats['free']}"
        
        await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
        await callback.answer()
        
        # Логируем просмотр статистики ключей
        await database._log_audit_event_atomic_standalone("admin_view_keys", callback.from_user.id, None, f"Admin viewed VPN keys stats: {stats['free']} free")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_keys: {e}")
        await callback.answer("Ошибка при получении статистики ключей", show_alert=True)


@router.callback_query(F.data == "admin:user")
async def callback_admin_user(callback: CallbackQuery, state: FSMContext):
    """Раздел Пользователь - запрос Telegram ID или username"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    text = "👤 Пользователь\n\nВведите Telegram ID или username пользователя:"
    await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
    await state.set_state(AdminUserSearch.waiting_for_user_id)
    await callback.answer()


@router.message(AdminUserSearch.waiting_for_user_id)
async def process_admin_user_id(message: Message, state: FSMContext):
    """Обработка введённого Telegram ID или username пользователя"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await message.answer("Недостаточно прав доступа")
        await state.clear()
        return
    
    try:
        user_input = message.text.strip()
        
        # Определяем, является ли ввод числом (ID) или строкой (username)
        try:
            target_user_id = int(user_input)
            # Это число - ищем по ID
            user = await database.find_user_by_id_or_username(telegram_id=target_user_id)
            search_by = "ID"
            search_value = str(target_user_id)
        except ValueError:
            # Это строка - ищем по username
            username = user_input.lstrip('@')  # Убираем @, если есть
            if not username:  # Пустая строка после удаления @
                await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.")
                await state.clear()
                return
            username = username.lower()  # Приводим к нижнему регистру
            user = await database.find_user_by_id_or_username(username=username)
            search_by = "username"
            search_value = username
        
        # Если пользователь не найден
        if not user:
            await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.")
            await state.clear()
            return
        
        # Получаем информацию о подписке
        subscription = await database.get_subscription(user["telegram_id"])
        
        # Формируем карточку пользователя
        text = "👤 Пользователь\n\n"
        text += f"Telegram ID: {user['telegram_id']}\n"
        username_display = user.get('username') or 'не указан'
        text += f"Username: @{username_display}\n"
        text += "\n"
        
        if subscription:
            expires_at = subscription["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            
            now = datetime.now()
            if expires_at > now:
                text += "Статус подписки: ✅ Активна\n"
                text += f"Срок действия: до {expires_str}\n"
                text += f"VPN-ключ: `{subscription['vpn_key']}`\n"
                
                await message.answer(text, reply_markup=get_admin_user_keyboard(has_active_subscription=True, user_id=user["telegram_id"]), parse_mode="Markdown")
            else:
                text += "Статус подписки: ⛔ Истекла\n"
                text += f"Срок действия: до {expires_str}\n"
                text += f"VPN-ключ: `{subscription['vpn_key']}`\n"
                
                await message.answer(text, reply_markup=get_admin_user_keyboard(has_active_subscription=False, user_id=user["telegram_id"]), parse_mode="Markdown")
        else:
            text += "Статус подписки: ❌ Нет подписки\n"
            text += "VPN-ключ: —\n"
            text += "Срок действия: —\n"
            
            await message.answer(text, reply_markup=get_admin_user_keyboard(has_active_subscription=False, user_id=user["telegram_id"]))
        
        # Логируем просмотр информации о пользователе
        details = f"Admin searched by {search_by}: {search_value}, found user {user['telegram_id']}"
        await database._log_audit_event_atomic_standalone("admin_view_user", message.from_user.id, user["telegram_id"], details)
        
        await state.clear()
        
    except Exception as e:
        logging.exception(f"Error in process_admin_user_id: {e}")
        await message.answer("Ошибка при получении информации о пользователе. Проверь логи.")
        await state.clear()


@router.callback_query(F.data.startswith("admin:user_history:"))
async def callback_admin_user_history(callback: CallbackQuery):
    """История подписок пользователя (админ)"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        # Получаем user_id из callback_data
        target_user_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
        return
    
    try:
        # Получаем историю подписок
        history = await database.get_subscription_history(target_user_id, limit=10)
        
        if not history:
            text = "🧾 История подписок\n\nИстория подписок пуста."
            await callback.message.answer(text, reply_markup=get_admin_back_keyboard())
            await callback.answer()
            return
        
        # Формируем текст истории
        text = "🧾 История подписок\n\n"
        
        action_type_map = {
            "purchase": "Покупка",
            "renewal": "Продление",
            "reissue": "Выдача нового ключа",
            "manual_reissue": "Перевыпуск ключа",
        }
        
        for record in history:
            start_date = record["start_date"]
            if isinstance(start_date, str):
                start_date = datetime.fromisoformat(start_date)
            start_str = start_date.strftime("%d.%m.%Y")
            
            end_date = record["end_date"]
            if isinstance(end_date, str):
                end_date = datetime.fromisoformat(end_date)
            end_str = end_date.strftime("%d.%m.%Y")
            
            action_type = record["action_type"]
            action_text = action_type_map.get(action_type, action_type)
            
            text += f"• {start_str} — {action_text}\n"
            
            # Для purchase и reissue показываем ключ
            if action_type in ["purchase", "reissue", "manual_reissue"]:
                text += f"  Ключ: {record['vpn_key']}\n"
            
            text += f"  До: {end_str}\n\n"
        
        await callback.message.answer(text, reply_markup=get_admin_back_keyboard())
        await callback.answer()
        
        # Логируем просмотр истории
        await database._log_audit_event_atomic_standalone("admin_view_user_history", callback.from_user.id, target_user_id, f"Admin viewed subscription history for user {target_user_id}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_user_history: {e}")
        await callback.answer("Ошибка при получении истории подписок", show_alert=True)


@router.callback_query(F.data.startswith("admin:user_reissue:"))
async def callback_admin_user_reissue(callback: CallbackQuery):
    """Перевыпуск ключа из админ-дашборда"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        # Получаем user_id из callback_data
        target_user_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
        return
    
    try:
        admin_telegram_id = callback.from_user.id
        
        # Атомарно перевыпускаем ключ
        result = await database.reissue_vpn_key_atomic(target_user_id, admin_telegram_id)
        new_vpn_key, old_vpn_key = result
        
        if new_vpn_key is None:
            await callback.answer("Не удалось перевыпустить ключ. Нет активной подписки или свободных ключей.", show_alert=True)
            return
        
        # Обновляем информацию о пользователе
        user = await database.get_user(target_user_id)
        subscription = await database.get_subscription(target_user_id)
        
        text = "👤 Информация о пользователе\n\n"
        text += f"Telegram ID: {target_user_id}\n"
        text += f"Username: @{user.get('username', 'не указан') if user else 'не указан'}\n"
        text += "\n"
        
        if subscription:
            expires_at = subscription["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            
            text += "Статус подписки: ✅ Активна\n"
            text += f"Срок действия: до {expires_str}\n"
            text += f"VPN-ключ: `{new_vpn_key}`\n"
            text += f"\n✅ Ключ перевыпущен!\nСтарый ключ: `{old_vpn_key[:20]}...`"
            
            await callback.message.edit_text(text, reply_markup=get_admin_user_keyboard(has_active_subscription=True, user_id=target_user_id), parse_mode="Markdown")
        
        await callback.answer("Ключ успешно перевыпущен")
        
        # Уведомляем пользователя
        try:
            user_text = f"🔐 Ваш VPN-ключ был перевыпущен администратором.\n\nНовый ключ: `{new_vpn_key}`\nРекомендуем сохранить новый ключ в надёжном месте."
            await callback.bot.send_message(target_user_id, user_text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Error sending reissue notification to user {target_user_id}: {e}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_user_reissue: {e}")
        await callback.answer("Ошибка при перевыпуске ключа", show_alert=True)


@router.callback_query(F.data == "admin:system")
async def callback_admin_system(callback: CallbackQuery):
    """Раздел Система"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        # Проверяем статус БД
        db_status = "ERROR"
        db_connections = "—"
        
        try:
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
                db_status = "ONLINE"
                # asyncpg не предоставляет прямых методов для получения количества соединений
                # Поэтому просто указываем, что пул работает
                db_connections = "Активен"
        except Exception as e:
            logging.error(f"Database health check failed: {e}")
            db_status = "ERROR"
            db_connections = "—"
        
        # Вычисляем uptime
        uptime_seconds = int(time.time() - _bot_start_time)
        uptime_days = uptime_seconds // 86400
        uptime_hours = (uptime_seconds % 86400) // 3600
        uptime_minutes = (uptime_seconds % 3600) // 60
        
        uptime_str = f"{uptime_days}д {uptime_hours}ч {uptime_minutes}м"
        
        text = "🚨 Система\n\n"
        text += f"Статус БД: {db_status}\n"
        text += f"Активных соединений: {db_connections}\n"
        text += f"Время работы бота: {uptime_str}"
        
        await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
        await callback.answer()
        
        # Логируем просмотр системной информации
        await database._log_audit_event_atomic_standalone("admin_view_system", callback.from_user.id, None, f"Admin viewed system info: DB={db_status}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_system: {e}")
        await callback.answer("Ошибка при получении системной информации", show_alert=True)


@router.callback_query(F.data == "admin:export")
async def callback_admin_export(callback: CallbackQuery):
    """Раздел Экспорт данных"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    text = "📤 Экспорт данных\n\nВыберите тип данных для экспорта:"
    await callback.message.edit_text(text, reply_markup=get_admin_export_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("admin:export:"))
async def callback_admin_export_data(callback: CallbackQuery):
    """Обработка экспорта данных"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        export_type = callback.data.split(":")[2]  # users или subscriptions
        
        # Получаем данные из БД
        if export_type == "users":
            data = await database.get_all_users_for_export()
            filename = "users_export.csv"
            headers = ["ID", "Telegram ID", "Username", "Language", "Created At"]
        elif export_type == "subscriptions":
            data = await database.get_active_subscriptions_for_export()
            filename = "active_subscriptions_export.csv"
            headers = ["ID", "Telegram ID", "VPN Key", "Expires At", "Reminder Sent"]
        else:
            await callback.message.answer("Неверный тип экспорта")
            return
        
        if not data:
            await callback.message.answer("Нет данных для экспорта")
            return
        
        # Создаём временный файл
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8', newline='') as tmp_file:
            csv_file_path = tmp_file.name
            
            # Записываем CSV
            writer = csv.writer(tmp_file)
            writer.writerow(headers)
            
            # Маппинг заголовков на ключи в данных
            if export_type == "users":
                key_mapping = {
                    "ID": "id",
                    "Telegram ID": "telegram_id",
                    "Username": "username",
                    "Language": "language",
                    "Created At": "created_at"
                }
            else:  # subscriptions
                key_mapping = {
                    "ID": "id",
                    "Telegram ID": "telegram_id",
                    "VPN Key": "vpn_key",
                    "Expires At": "expires_at",
                    "Reminder Sent": "reminder_sent"
                }
            
            for row in data:
                csv_row = []
                for header in headers:
                    key = key_mapping[header]
                    value = row.get(key)
                    
                    if key == "created_at" or key == "expires_at":
                        # Форматируем дату
                        if value:
                            if isinstance(value, datetime):
                                csv_row.append(value.strftime("%Y-%m-%d %H:%M:%S"))
                            elif isinstance(value, str):
                                csv_row.append(value)
                            else:
                                csv_row.append(str(value))
                        else:
                            csv_row.append("")
                    elif key == "reminder_sent":
                        # Преобразуем boolean в строку
                        csv_row.append("Да" if value else "Нет")
                    else:
                        csv_row.append(str(value) if value is not None else "")
                writer.writerow(csv_row)
        
        # Отправляем файл
        try:
            file_to_send = FSInputFile(csv_file_path, filename=filename)
            await callback.bot.send_document(
                config.ADMIN_TELEGRAM_ID,
                file_to_send,
                caption=f"📤 Экспорт: {export_type}"
            )
            await callback.message.answer("✅ Файл отправлен")
            
            # Логируем экспорт
            await database._log_audit_event_atomic_standalone(
                "admin_export_data",
                callback.from_user.id,
                None,
                f"Exported {export_type}: {len(data)} records"
            )
        finally:
            # Удаляем временный файл
            try:
                os.unlink(csv_file_path)
            except Exception as e:
                logging.error(f"Error deleting temp file {csv_file_path}: {e}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_export_data: {e}")
        await callback.message.answer("Ошибка при экспорте данных. Проверь логи.")


@router.callback_query(F.data == "admin:broadcast")
async def callback_admin_broadcast(callback: CallbackQuery):
    """Раздел уведомлений"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    text = "📣 Уведомления\n\nВыберите действие:"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать уведомление", callback_data="broadcast:create")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:main")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone("admin_broadcast_view", callback.from_user.id, None, "Admin viewed broadcast section")


@router.callback_query(F.data == "broadcast:create")
async def callback_broadcast_create(callback: CallbackQuery, state: FSMContext):
    """Начать создание уведомления"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    await state.set_state(BroadcastCreate.waiting_for_title)
    await callback.message.answer("Введите заголовок уведомления:")


@router.message(BroadcastCreate.waiting_for_title)
async def process_broadcast_title(message: Message, state: FSMContext):
    """Обработка заголовка уведомления"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    
    await state.update_data(title=message.text)
    await state.set_state(BroadcastCreate.waiting_for_message)
    await message.answer("Введите текст уведомления:")


@router.message(BroadcastCreate.waiting_for_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    """Обработка текста уведомления"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    
    await state.update_data(message=message.text)
    await state.set_state(BroadcastCreate.waiting_for_type)
    await message.answer("Выберите тип уведомления:", reply_markup=get_broadcast_type_keyboard())


@router.callback_query(F.data.startswith("broadcast_type:"))
async def callback_broadcast_type(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа уведомления"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    broadcast_type = callback.data.split(":")[1]
    
    data = await state.get_data()
    title = data.get("title")
    message_text = data.get("message")
    
    # Формируем предпросмотр
    type_emoji = {
        "info": "ℹ️",
        "maintenance": "🔧",
        "security": "🔒",
        "promo": "🎯"
    }
    type_name = {
        "info": "Информация",
        "maintenance": "Технические работы",
        "security": "Безопасность",
        "promo": "Промо"
    }
    
    preview_text = f"{type_emoji.get(broadcast_type, '📢')} {title}\n\n{message_text}\n\nТип: {type_name.get(broadcast_type, broadcast_type)}"
    
    await state.update_data(type=broadcast_type)
    await state.set_state(BroadcastCreate.waiting_for_confirm)
    
    await callback.message.edit_text(
        f"📋 Предпросмотр уведомления:\n\n{preview_text}\n\nПодтвердите отправку:",
        reply_markup=get_broadcast_confirm_keyboard()
    )


@router.callback_query(F.data == "broadcast:confirm_send")
async def callback_broadcast_confirm_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение и отправка уведомления"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    data = await state.get_data()
    title = data.get("title")
    message_text = data.get("message")
    broadcast_type = data.get("type")
    
    if not all([title, message_text, broadcast_type]):
        await callback.message.answer("Ошибка: не все данные заполнены. Начните заново.")
        await state.clear()
        return
    
    try:
        # Создаем уведомление в БД
        broadcast_id = await database.create_broadcast(title, message_text, broadcast_type, callback.from_user.id)
        
        # Формируем сообщение для отправки
        type_emoji = {
            "info": "ℹ️",
            "maintenance": "🔧",
            "security": "🔒",
            "promo": "🎯"
        }
        emoji = type_emoji.get(broadcast_type, "📢")
        final_message = f"{emoji} {title}\n\n{message_text}"
        
        # Получаем список всех пользователей
        user_ids = await database.get_all_users_telegram_ids()
        total_users = len(user_ids)
        
        await callback.message.edit_text(
            f"📤 Отправка уведомления...\n\nПользователей: {total_users}\nОжидайте завершения.",
            reply_markup=None
        )
        
        # Отправляем уведомления с задержкой
        sent_count = 0
        failed_count = 0
        
        for user_id in user_ids:
            try:
                await bot.send_message(user_id, final_message)
                await database.log_broadcast_send(broadcast_id, user_id, "sent")
                sent_count += 1
                
                # Задержка между отправками (0.3-0.5 сек)
                await asyncio.sleep(0.4)
                
            except Exception as e:
                logging.error(f"Error sending broadcast to user {user_id}: {e}")
                await database.log_broadcast_send(broadcast_id, user_id, "failed")
                failed_count += 1
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone(
            "broadcast_sent",
            callback.from_user.id,
            None,
            f"Broadcast ID: {broadcast_id}, Sent: {sent_count}, Failed: {failed_count}"
        )
        
        # Показываем результат
        result_text = (
            f"✅ Уведомление отправлено\n\n"
            f"📊 Статистика:\n"
            f"✅ Отправлено: {sent_count}\n"
            f"❌ Ошибок: {failed_count}\n"
            f"📝 ID уведомления: {broadcast_id}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:broadcast")],
        ])
        
        await callback.message.edit_text(result_text, reply_markup=keyboard)
        
    except Exception as e:
        logging.exception(f"Error in broadcast send: {e}")
        await callback.message.answer(f"Ошибка при отправке уведомления: {e}")
    
    finally:
        await state.clear()


@router.message(Command("admin_audit"))
async def cmd_admin_audit(message: Message):
    """Показать последние записи audit_log (только для админа)"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized admin_audit attempt by user {message.from_user.id}")
        await message.answer("Недостаточно прав")
        return
    
    try:
        # Получаем последние 10 записей из audit_log
        audit_logs = await database.get_last_audit_logs(limit=10)
        
        if not audit_logs:
            await message.answer("Аудит пуст. Действий не зафиксировано.")
            return
        
        # Формируем сообщение
        lines = ["📜 Audit Log", ""]
        
        for log in audit_logs:
            # Форматируем дату и время
            created_at = log["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif isinstance(created_at, datetime):
                pass
            else:
                created_at = datetime.now()
            
            created_str = created_at.strftime("%Y-%m-%d %H:%M")
            
            lines.append(f"🕒 {created_str}")
            lines.append(f"Действие: {log['action']}")
            lines.append(f"Админ: {log['telegram_id']}")
            
            if log['target_user']:
                lines.append(f"Пользователь: {log['target_user']}")
            else:
                lines.append("Пользователь: —")
            
            if log['details']:
                lines.append(f"Детали: {log['details']}")
            else:
                lines.append("Детали: —")
            
            lines.append("")
            lines.append("⸻")
            lines.append("")
        
        # Убираем последний разделитель
        if lines[-1] == "" and lines[-2] == "⸻":
            lines = lines[:-2]
        
        text = "\n".join(lines)
        
        # Проверяем лимит Telegram (4096 символов на сообщение)
        if len(text) > 4000:
            # Если текст слишком длинный, обрезаем до первых записей
            # Попробуем уменьшить количество записей
            audit_logs = await database.get_last_audit_logs(limit=5)
            lines = ["📜 Audit Log", ""]
            
            for log in audit_logs:
                created_at = log["created_at"]
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif isinstance(created_at, datetime):
                    pass
                else:
                    created_at = datetime.now()
                
                created_str = created_at.strftime("%Y-%m-%d %H:%M")
                
                lines.append(f"🕒 {created_str}")
                lines.append(f"Действие: {log['action']}")
                lines.append(f"Админ: {log['telegram_id']}")
                
                if log['target_user']:
                    lines.append(f"Пользователь: {log['target_user']}")
                else:
                    lines.append("Пользователь: —")
                
                if log['details']:
                    # Обрезаем детали если они слишком длинные
                    details = log['details']
                    if len(details) > 200:
                        details = details[:200] + "..."
                    lines.append(f"Детали: {details}")
                else:
                    lines.append("Детали: —")
                
                lines.append("")
                lines.append("⸻")
                lines.append("")
            
            if lines[-1] == "" and lines[-2] == "⸻":
                lines = lines[:-2]
            
            text = "\n".join(lines)
        
        await message.answer(text)
        logging.info(f"Admin audit log viewed by admin {message.from_user.id}")
        
    except Exception as e:
        logging.exception(f"Error in cmd_admin_audit: {e}")
        await message.answer("Ошибка при получении audit log. Проверь логи.")


@router.message(Command("reissue_key"))
async def cmd_reissue_key(message: Message):
    """Перевыпустить VPN-ключ для пользователя (только для админа)"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized reissue_key attempt by user {message.from_user.id}")
        await message.answer("Нет доступа")
        return
    
    try:
        # Парсим команду: /reissue_key <telegram_id>
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer("Использование: /reissue_key <telegram_id>")
            return
        
        try:
            target_telegram_id = int(parts[1])
        except ValueError:
            await message.answer("Неверный формат telegram_id. Используйте число.")
            return
        
        admin_telegram_id = message.from_user.id
        
        # Атомарно перевыпускаем ключ
        result = await database.reissue_vpn_key_atomic(target_telegram_id, admin_telegram_id)
        new_vpn_key, old_vpn_key = result
        
        if new_vpn_key is None:
            await message.answer(f"❌ Не удалось перевыпустить ключ для пользователя {target_telegram_id}.\nВозможные причины:\n- Нет активной подписки\n- Нет свободных VPN-ключей")
            return
        
        # Уведомляем пользователя
        user = await database.get_user(target_telegram_id)
        language = user.get("language", "ru") if user else "ru"
        
        # Получаем информацию о подписке для уведомления
        subscription = await database.get_subscription(target_telegram_id)
        expires_str = subscription["expires_at"].strftime("%d.%m.%Y") if subscription else "неизвестно"
        
        user_text = f"🔐 Ваш VPN-ключ был перевыпущен администратором.\n\nНовый ключ: `{new_vpn_key}`\nСрок действия подписки: до {expires_str}\n\nРекомендуем сохранить новый ключ в надёжном месте."
        
        try:
            await message.bot.send_message(target_telegram_id, user_text, parse_mode="Markdown")
            logging.info(f"Reissue notification sent to user {target_telegram_id}")
        except Exception as e:
            logging.error(f"Error sending reissue notification to user {target_telegram_id}: {e}")
            await message.answer(f"✅ Ключ перевыпущен, но не удалось отправить уведомление пользователю: {e}")
            return
        
        await message.answer(f"✅ VPN-ключ успешно перевыпущен для пользователя {target_telegram_id}\n\nСтарый ключ: `{old_vpn_key[:20]}...`\nНовый ключ: `{new_vpn_key}`", parse_mode="Markdown")
        logging.info(f"VPN key reissued for user {target_telegram_id} by admin {admin_telegram_id}")
        
    except Exception as e:
        logging.exception(f"Error in cmd_reissue_key: {e}")
        await message.answer("Ошибка при перевыпуске ключа. Проверь логи.")


@router.callback_query(F.data.startswith("reject_payment:"))
async def reject_payment(callback: CallbackQuery):
    """Админ отклонил платеж"""
    await callback.answer()  # ОБЯЗАТЕЛЬНО
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized reject attempt by user {callback.from_user.id}")
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    try:
        payment_id = int(callback.data.split(":")[1])
        
        logging.info(f"REJECT pressed by admin {callback.from_user.id}, payment_id={payment_id}")
        
        # Получить платеж из БД
        payment = await database.get_payment(payment_id)
        
        if not payment:
            logging.warning(f"Payment {payment_id} not found for reject")
            await callback.answer("Платеж не найден", show_alert=True)
            return
        
        if payment["status"] != "pending":
            logging.warning(
                f"Attempt to reject already processed payment {payment_id}, status={payment['status']}"
            )
            await callback.answer("Платеж уже обработан", show_alert=True)
            # Удаляем кнопки даже если платеж уже обработан
            await callback.message.edit_reply_markup(reply_markup=None)
            return
        
        telegram_id = payment["telegram_id"]
        admin_telegram_id = callback.from_user.id
        
        # Обновляем статус платежа на rejected (аудит записывается внутри функции)
        await database.update_payment_status(payment_id, "rejected", admin_telegram_id)
        logging.info(f"Payment {payment_id} rejected for user {telegram_id}")
        
        # Уведомляем пользователя
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        
        text = localization.get_text(language, "payment_rejected")
        
        try:
            await callback.bot.send_message(telegram_id, text)
            logging.info(f"Rejection message sent to user {telegram_id} for payment {payment_id}")
        except Exception as e:
            logging.error(f"Error sending rejection message to user {telegram_id}: {e}")
        
        await callback.message.edit_text(f"❌ Платеж {payment_id} отклонен")
        # Удаляем inline-кнопки после обработки
        await callback.message.edit_reply_markup(reply_markup=None)
        
    except Exception as e:
        logging.exception(f"Error in reject_payment callback for payment_id={payment_id if 'payment_id' in locals() else 'unknown'}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)



