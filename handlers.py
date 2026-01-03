from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from datetime import datetime
import logging
import database
import localization
import config
import vpn_utils

router = Router()

logging.basicConfig(level=logging.INFO)


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


def get_profile_keyboard(language: str):
    """Клавиатура с кнопкой профиля"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "profile"),
            callback_data="menu_profile"
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
            text=localization.get_text(language, "back"),
            callback_data="menu_main"
        )],
    ])
    return keyboard


def get_support_keyboard(language: str):
    """Клавиатура раздела 'Поддержка'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "support_payment_not_confirmed"),
            callback_data="support_payment"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "support_vpn_not_working"),
            callback_data="support_vpn"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "support_other"),
            callback_data="support_other"
        )],
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
        expires_at = datetime.fromisoformat(subscription["expires_at"])
        expires_str = expires_at.strftime("%d.%m.%Y")
        text = localization.get_text(language, "profile_active", date=expires_str, vpn_key=subscription["vpn_key"])
        text += localization.get_text(language, "profile_renewal_hint")
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


@router.callback_query(F.data == "about_privacy")
async def callback_privacy(callback: CallbackQuery):
    """Политика конфиденциальности"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "privacy_policy_text")
    await callback.message.edit_text(text, reply_markup=get_about_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "menu_support")
async def callback_support(callback: CallbackQuery):
    """Поддержка"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "support_text")
    await callback.message.edit_text(text, reply_markup=get_support_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "support_payment")
async def callback_support_payment(callback: CallbackQuery):
    """Поддержка - платеж не подтвердили"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "support_theme_selection")
    await callback.message.edit_text(text, reply_markup=get_support_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "support_vpn")
async def callback_support_vpn(callback: CallbackQuery):
    """Поддержка - VPN не работает"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "support_theme_selection")
    await callback.message.edit_text(text, reply_markup=get_support_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "support_other")
async def callback_support_other(callback: CallbackQuery):
    """Поддержка - другой вопрос"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "support_theme_selection")
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
        
        # Проверка наличия свободных VPN-ключей
        if not vpn_utils.has_free_vpn_keys():
            logging.error(f"No free VPN keys available for payment {payment_id}")
            await callback.answer("Нет свободных VPN-ключей. Пополните файл vpn_keys.txt", show_alert=True)
            # Статус платежа НЕ меняем
            return
        
        telegram_id = payment["telegram_id"]
        tariff_key = payment["tariff"]
        tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
        
        # Получаем свободный VPN-ключ
        try:
            vpn_key = vpn_utils.get_free_vpn_key()
        except Exception as e:
            logging.exception(f"Error getting VPN key for payment {payment_id}: {e}")
            await callback.answer("Ошибка получения VPN-ключа. Проверь логи.", show_alert=True)
            return
        
        # Атомарно подтверждаем платеж и создаем/продлеваем подписку
        result = await database.approve_payment_atomic(payment_id, vpn_key, tariff_data["months"])
        expires_at, is_renewal = result
        
        if expires_at is None:
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
        
        # Обновляем статус платежа на rejected
        await database.update_payment_status(payment_id, "rejected")
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



