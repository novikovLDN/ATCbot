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


def get_tariff_keyboard(language: str):
    """Клавиатура выбора тарифа"""
    buttons = []
    for tariff_key, tariff_data in config.TARIFFS.items():
        months = tariff_data["months"]
        price = tariff_data["price"]
        
        # Форматирование текста в зависимости от языка
        if language == "ru":
            if months == 1:
                text = f"{months} месяц — {price} руб."
            elif months in [3, 6]:
                text = f"{months} месяца — {price} руб."
            else:
                text = f"{months} месяцев — {price} руб."
        elif language == "en":
            text = f"{months} month{'s' if months > 1 else ''} — {price} rub."
        elif language == "uz":
            text = f"{months} oy — {price} so'm"
        elif language == "tj":
            text = f"{months} моҳ — {price} сом."
        else:
            text = f"{months} — {price}"
        
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
        
        text = f"{localization.get_text(language, 'subscription_active')}\n"
        text += localization.get_text(language, "subscription_expires", date=expires_str) + "\n"
        text += localization.get_text(language, "vpn_key", key=subscription["vpn_key"])
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
    text = localization.get_text(language, "sbp_payment_text")
    text += f"\n\n"
    text += f"Банк: {config.SBP_DETAILS['bank']}\n"
    text += f"Счет: {config.SBP_DETAILS['account']}\n"
    text += f"Получатель: {config.SBP_DETAILS['name']}\n"
    text += f"\nСумма: {tariff_data['price']} руб."
    
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
    
    # Создаем платеж
    payment_id = await database.create_payment(telegram_id, tariff_key)
    
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
        print(f"Error sending admin notification: {e}")
    
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
    
    text = localization.get_text(
        language,
        "support_text",
        email=config.SUPPORT_EMAIL,
        telegram=config.SUPPORT_TELEGRAM
    )
    await callback.message.edit_text(text, reply_markup=get_support_keyboard(language))
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("approve_payment:"))
async def approve_payment(callback: CallbackQuery):
    """Админ подтвердил платеж"""
    await callback.answer()  # ОБЯЗАТЕЛЬНО
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    try:
        payment_id = int(callback.data.split(":")[1])
        
        logging.info(
            f"APPROVE pressed by {callback.from_user.id}, payment_id={payment_id}"
        )
        
        # Получить платеж из БД
        payment = await database.get_payment(payment_id)
        
        if not payment or payment["status"] != "pending":
            await callback.answer("Платеж не найден или уже обработан", show_alert=True)
            return
        
        telegram_id = payment["telegram_id"]
        tariff_key = payment["tariff"]
        tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
        
        # Получаем свободный VPN-ключ
        vpn_key = vpn_utils.get_free_vpn_key()
        
        # Создаем подписку
        await database.create_subscription(telegram_id, vpn_key, tariff_data["months"])
        
        # Обновляем статус платежа на approved
        await database.update_payment_status(payment_id, "approved")
        
        # Уведомляем пользователя
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        
        # Получаем expires_at из подписки
        subscription = await database.get_subscription(telegram_id)
        if subscription:
            expires_at = datetime.fromisoformat(subscription["expires_at"])
            expires_str = expires_at.strftime("%d.%m.%Y")
        else:
            expires_str = "не определено"
        
        text = localization.get_text(language, "payment_approved", key=vpn_key, date=expires_str)
        
        try:
            await callback.bot.send_message(telegram_id, text)
        except Exception as e:
            logging.error(f"Error sending approval message to user {telegram_id}: {e}")
        
        await callback.message.edit_text(f"✅ Платеж {payment_id} подтвержден")
        
    except Exception as e:
        logging.exception("Error in approve_payment callback")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(lambda c: c.data.startswith("reject_payment:"))
async def reject_payment(callback: CallbackQuery):
    """Админ отклонил платеж"""
    await callback.answer()  # ОБЯЗАТЕЛЬНО
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    try:
        payment_id = int(callback.data.split(":")[1])
        
        logging.info(
            f"REJECT pressed by {callback.from_user.id}, payment_id={payment_id}"
        )
        
        # Получить платеж из БД
        payment = await database.get_payment(payment_id)
        
        if not payment or payment["status"] != "pending":
            await callback.answer("Платеж не найден или уже обработан", show_alert=True)
            return
        
        telegram_id = payment["telegram_id"]
        
        # Обновляем статус платежа на rejected
        await database.update_payment_status(payment_id, "rejected")
        
        # Уведомляем пользователя
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        
        text = localization.get_text(language, "payment_rejected")
        
        try:
            await callback.bot.send_message(telegram_id, text)
        except Exception as e:
            logging.error(f"Error sending rejection message to user {telegram_id}: {e}")
        
        await callback.message.edit_text(f"❌ Платеж {payment_id} отклонен")
        
    except Exception as e:
        logging.exception("Error in reject_payment callback")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)

