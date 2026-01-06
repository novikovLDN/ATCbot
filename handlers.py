from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, LabeledPrice, PreCheckoutQuery
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
import random

# Время запуска бота (для uptime)
_bot_start_time = time.time()


class AdminUserSearch(StatesGroup):
    waiting_for_user_id = State()


class BroadcastCreate(StatesGroup):
    waiting_for_title = State()
    waiting_for_test_type = State()
    waiting_for_message = State()
    waiting_for_message_a = State()
    waiting_for_message_b = State()
    waiting_for_type = State()
    waiting_for_segment = State()
    waiting_for_confirm = State()


class IncidentEdit(StatesGroup):
    waiting_for_text = State()


class AdminGrantAccess(StatesGroup):
    waiting_for_days = State()


class AdminDiscountCreate(StatesGroup):
    waiting_for_percent = State()
    waiting_for_expires = State()


class PromoCodeInput(StatesGroup):
    waiting_for_promo = State()

router = Router()

logger = logging.getLogger(__name__)


# Функция send_vpn_keys_alert удалена - больше не используется
# VPN-ключи теперь создаются динамически через Outline API, лимита нет

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


async def format_text_with_incident(text: str, language: str) -> str:
    """Добавить баннер инцидента к тексту, если режим активен"""
    incident = await database.get_incident_settings()
    if incident["is_active"]:
        banner = localization.get_text(language, "incident_banner")
        incident_text = incident.get("incident_text")
        if incident_text:
            banner += f"\n{incident_text}"
        return f"{banner}\n\n⸻\n\n{text}"
    return text


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


def get_profile_keyboard_with_copy(language: str, last_tariff: str = None, is_vip: bool = False, has_subscription: bool = True):
    """Клавиатура профиля с кнопкой копирования ключа и историей"""
    buttons = []
    
    if has_subscription:
        # Кнопка продления (всегда показываем, если есть активная подписка)
        buttons.append([InlineKeyboardButton(
            text=localization.get_text(language, "renew_subscription"),
            callback_data="renew_same_period"
        )])
        
        buttons.append([InlineKeyboardButton(
            text=localization.get_text(language, "copy_key"),
            callback_data="copy_key"
        )])
        buttons.append([InlineKeyboardButton(
            text=localization.get_text(language, "subscription_history"),
            callback_data="subscription_history"
        )])
        
        # Кнопка VIP-доступ (доступна всем)
        buttons.append([InlineKeyboardButton(
            text=localization.get_text(language, "vip_access_button"),
            callback_data="menu_vip_access"
        )])
    else:
        # Кнопка для оформления доступа (если нет подписки)
        buttons.append([InlineKeyboardButton(
            text=localization.get_text(language, "get_access_button", default="🔐 Оформить доступ"),
            callback_data="menu_buy_vpn"
        )])
    
    buttons.append([InlineKeyboardButton(
        text=localization.get_text(language, "back"),
        callback_data="menu_main"
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
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


def get_vpn_key_keyboard(language: str):
    """Клавиатура для экрана выдачи VPN-ключа после оплаты"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "go_to_connection", default="🔌 Перейти к подключению"),
            callback_data="menu_instruction"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "copy_key"),
            callback_data="copy_vpn_key"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "profile"),
            callback_data="go_profile"
        )],
    ])
    return keyboard


async def get_tariff_keyboard(language: str, telegram_id: int, promo_code: str = None):
    """Клавиатура выбора тарифа с учетом скидок (промокод имеет высший приоритет)"""
    buttons = []
    
    # ПРИОРИТЕТ 0: Промокод (высший приоритет, перекрывает все остальные скидки)
    promo_data = None
    if promo_code:
        promo_data = await database.check_promo_code_valid(promo_code.upper())
    
    has_promo = promo_data is not None
    
    # ПРИОРИТЕТ 1: Проверяем VIP-статус (только если нет промокода)
    is_vip = await database.is_vip_user(telegram_id) if not has_promo else False
    
    # ПРИОРИТЕТ 2: Проверяем персональную скидку (только если нет промокода и VIP)
    personal_discount = await database.get_user_discount(telegram_id) if not has_promo and not is_vip else None
    
    for tariff_key, tariff_data in config.TARIFFS.items():
        base_price = tariff_data["price"]
        discount_label = ""
        has_discount_for_tariff = False
        
        # Применяем скидку в порядке приоритета
        if has_promo:
            # Промокод применяется ко всем тарифам
            discount_percent = promo_data["discount_percent"]
            discounted_price = int(base_price * (100 - discount_percent) / 100)
            price = discounted_price
            discount_label = f"🎟 −{discount_percent}%"
            has_discount_for_tariff = True
        elif is_vip:
            # VIP-скидка 30% применяется ко всем тарифам
            discounted_price = int(base_price * 0.70)  # 30% скидка
            price = discounted_price
            discount_label = localization.get_text(
                language, 
                "vip_discount_label", 
                default="👑 VIP-доступ"
            )
            has_discount_for_tariff = True
        elif personal_discount:
            # Персональная скидка применяется ко всем тарифам
            discount_percent = personal_discount["discount_percent"]
            discounted_price = int(base_price * (1 - discount_percent / 100))
            price = discounted_price
            discount_label = localization.get_text(
                language, 
                "personal_discount_label", 
                default="🎯 Персональная скидка"
            ).format(percent=discount_percent)
            has_discount_for_tariff = True
        else:
            price = base_price
            has_discount_for_tariff = False
        
        # Формируем текст кнопки
        base_text = localization.get_text(language, f"tariff_button_{tariff_key}")
        
        if has_discount_for_tariff and discount_label:
            # Если есть скидка (промокод) - используем новый формат с описаниями
            if has_promo:
                # Для промокода используем специальные описания
                promo_descriptions = {
                    "1": "Для знакомства",
                    "3": "Оптимальный выбор",
                    "6": "Реже продлевать",
                    "12": "Не думать о доступе"
                }
                
                # Извлекаем срок из base_text (первые 2-3 слова)
                if "·" in base_text:
                    parts = base_text.split("·")
                    full_part = parts[0].strip()
                    words = full_part.split()
                    
                    # Извлекаем срок (первые 2 слова обычно: "1 месяц", "3 месяца", и т.д.)
                    period_words = []
                    skip_keywords = {
                        "ru": ["Для", "знакомства", "Чаще", "всего", "выбирают", "Реже", "продлевать", "Не", "думать", "о", "доступе"],
                        "en": ["For", "Temporary", "Standard", "Extended", "Priority", "Access"],
                        "uz": ["Vaqtinchalik", "Standart", "Kengaytirilgan", "Ustuvor", "kirish"],
                        "tj": ["муваққатӣ", "стандартӣ", "васеъ", "афзалиятнок", "Дастрасии"]
                    }
                    
                    skip_list = skip_keywords.get(language, skip_keywords["ru"])
                    
                    for word in words:
                        if any(skip_word.lower() in word.lower() for skip_word in skip_list):
                            break
                        period_words.append(word)
                    
                    if not period_words:
                        period_words = words[:2] if len(words) >= 2 else words
                    
                    period_text = " ".join(period_words)
                else:
                    # Если формат неожиданный, используем первые 2 слова
                    words = base_text.split()
                    period_text = " ".join(words[:2]) if len(words) >= 2 else base_text
                
                # Формируем текст с описанием
                description = promo_descriptions.get(tariff_key, "")
                star = " ⭐" if tariff_key == "3" else ""
                text = f"{period_text} · {description} · {price} ₽{star}"
            else:
                # Для других скидок (VIP, персональная) используем старый формат
                if "·" in base_text:
                    parts = base_text.split("·")
                    full_part = parts[0].strip()
                    words = full_part.split()
                    
                    period_words = []
                    skip_keywords = {
                        "ru": ["Для", "знакомства", "Чаще", "всего", "выбирают", "Реже", "продлевать", "Не", "думать", "о", "доступе"],
                        "en": ["Temporary", "Standard", "Extended", "Priority", "Access"],
                        "uz": ["Vaqtinchalik", "Standart", "Kengaytirilgan", "Ustuvor", "kirish"],
                        "tj": ["муваққатӣ", "стандартӣ", "васеъ", "афзалиятнок", "Дастрасии"]
                    }
                    
                    skip_list = skip_keywords.get(language, skip_keywords["ru"])
                    
                    for word in words:
                        if any(skip_word.lower() in word.lower() for skip_word in skip_list):
                            break
                        period_words.append(word)
                    
                    if not period_words:
                        period_words = words[:2] if len(words) >= 2 else words
                    
                    period_text = " ".join(period_words)
                    text = f"{period_text} {discount_label} · {price} ₽"
                else:
                    text = base_text.replace(str(base_price), str(price))
                    text = f"{text} · {discount_label}"
        else:
            # Если нет скидки - используем полный формат с названием уровня доступа
            text = base_text
        
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"tariff_{tariff_key}")])
    
    # Кнопка ввода промокода
    buttons.append([InlineKeyboardButton(
        text=localization.get_text(language, "enter_promo_button", default="🎟 Ввести промокод"),
        callback_data="enter_promo"
    )])
    
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
            text="💬 Написать в поддержку",
            url="https://t.me/asc_support"
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
        [InlineKeyboardButton(text="📈 Метрики", callback_data="admin:metrics")],
        [InlineKeyboardButton(text="📜 Аудит", callback_data="admin:audit")],
        [InlineKeyboardButton(text="🔑 VPN-ключи", callback_data="admin:keys")],
        [InlineKeyboardButton(text="👤 Пользователь", callback_data="admin:user")],
        [InlineKeyboardButton(text="🚨 Система", callback_data="admin:system")],
        [InlineKeyboardButton(text="📤 Экспорт данных", callback_data="admin:export")],
        [InlineKeyboardButton(text="📣 Уведомления", callback_data="admin:broadcast")],
        [InlineKeyboardButton(text="📊 Статистика промокодов", callback_data="admin_promo_stats")],
    ])
    return keyboard


def get_admin_back_keyboard():
    """Клавиатура с кнопкой 'Назад' для админ-разделов"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:main")],
    ])
    return keyboard


def get_broadcast_test_type_keyboard():
    """Клавиатура выбора типа тестирования"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Обычное уведомление", callback_data="broadcast_test_type:normal")],
        [InlineKeyboardButton(text="🔬 A/B тест", callback_data="broadcast_test_type:ab")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:broadcast")],
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


def get_broadcast_segment_keyboard():
    """Клавиатура выбора сегмента получателей"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 Все пользователи", callback_data="broadcast_segment:all_users")],
        [InlineKeyboardButton(text="🔐 Только активные подписки", callback_data="broadcast_segment:active_subscriptions")],
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


def get_ab_test_list_keyboard(ab_tests: list) -> InlineKeyboardMarkup:
    """Клавиатура списка A/B тестов"""
    buttons = []
    for test in ab_tests[:20]:  # Ограничиваем 20 тестами
        test_id = test["id"]
        title = test["title"][:30] + "..." if len(test["title"]) > 30 else test["title"]
        created_at = test["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        date_str = created_at.strftime("%d.%m.%Y")
        button_text = f"#{test_id} {title} ({date_str})"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"broadcast:ab_stat:{test_id}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin:broadcast")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_export_keyboard():
    """Клавиатура выбора типа экспорта"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:export:users")],
        [InlineKeyboardButton(text="🔑 Активные подписки", callback_data="admin:export:subscriptions")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:main")],
    ])
    return keyboard


def get_admin_user_keyboard(has_active_subscription: bool = False, user_id: int = None, has_discount: bool = False, is_vip: bool = False):
    """Клавиатура для раздела пользователя"""
    buttons = []
    if has_active_subscription:
        callback_data = f"admin:user_reissue:{user_id}" if user_id else "admin:user_reissue"
        buttons.append([InlineKeyboardButton(text="🔁 Перевыпустить ключ", callback_data=callback_data)])
    if user_id:
        buttons.append([InlineKeyboardButton(text="🧾 История подписок", callback_data=f"admin:user_history:{user_id}")])
        # Кнопки выдачи и лишения доступа (всегда доступны)
        buttons.append([
            InlineKeyboardButton(text="🟢 Выдать доступ", callback_data=f"admin:grant:{user_id}"),
            InlineKeyboardButton(text="🔴 Лишить доступа", callback_data=f"admin:revoke:{user_id}")
        ])
        # Кнопки управления скидками
        if has_discount:
            buttons.append([InlineKeyboardButton(text="❌ Удалить скидку", callback_data=f"admin:discount_delete:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text="🎯 Назначить скидку", callback_data=f"admin:discount_create:{user_id}")])
        # Кнопки управления VIP-статусом
        if is_vip:
            buttons.append([InlineKeyboardButton(text="❌ Снять VIP", callback_data=f"admin:vip_revoke:{user_id}")])
        else:
            buttons.append([InlineKeyboardButton(text="👑 Выдать VIP", callback_data=f"admin:vip_grant:{user_id}")])
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


async def format_promo_stats_text(stats: list) -> str:
    """Форматировать статистику промокодов в текст"""
    if not stats:
        return "Промокоды не найдены."
    
    text = "📊 Статистика промокодов\n\n"
    
    for promo in stats:
        code = promo["code"]
        discount_percent = promo["discount_percent"]
        max_uses = promo["max_uses"]
        used_count = promo["used_count"]
        is_active = promo["is_active"]
        
        text += f"{code}\n"
        text += f"— Скидка: {discount_percent}%\n"
        
        if max_uses is not None:
            text += f"— Использовано: {used_count} / {max_uses}\n"
            if is_active:
                text += "— Статус: активен\n"
            else:
                text += "— Статус: исчерпан\n"
        else:
            text += f"— Использовано: {used_count}\n"
            text += "— Статус: без ограничений\n"
        
        text += "\n"
    
    return text


@router.message(Command("promo_stats"))
async def cmd_promo_stats(message: Message):
    """Команда для просмотра статистики промокодов (только для администратора)"""
    telegram_id = message.from_user.id
    
    # Проверяем, что пользователь - администратор
    if telegram_id != config.ADMIN_TELEGRAM_ID:
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await message.answer(localization.get_text(language, "error_access_denied"))
        return
    
    try:
        # Получаем статистику промокодов
        stats = await database.get_promo_stats()
        
        # Формируем текст ответа
        text = await format_promo_stats_text(stats)
        await message.answer(text)
    except Exception as e:
        logger.error(f"Error getting promo stats: {e}")
        await message.answer("Ошибка при получении статистики промокодов.")


@router.message(Command("profile"))
async def cmd_profile(message: Message):
    """Обработчик команды /profile"""
    telegram_id = message.from_user.id
    user = await database.get_user(telegram_id)
    
    if not user:
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await message.answer(localization.get_text(language, "error_start_command"))
        return
    
    language = user.get("language", "ru")
    await show_profile(message, language)


async def check_subscription_expiry(telegram_id: int) -> bool:
    """
    Дополнительная защита: проверка и мгновенное отключение истёкшей подписки
    
    Вызывается в начале критичных handlers для дополнительной безопасности.
    Возвращает True если подписка была отключена, False если активна или отсутствует.
    """
    return await database.check_and_disable_expired_subscription(telegram_id)


async def show_profile(message_or_query, language: str):
    """Показать профиль пользователя"""
    if isinstance(message_or_query, Message):
        telegram_id = message_or_query.from_user.id
        send_func = message_or_query.answer
    else:
        telegram_id = message_or_query.from_user.id
        send_func = message_or_query.message.edit_text
    
    # Дополнительная защита: проверка истечения подписки
    await check_subscription_expiry(telegram_id)
    
    subscription = await database.get_subscription(telegram_id)
    
    # Проверяем VIP-статус
    is_vip = await database.is_vip_user(telegram_id)
    
    if subscription:
        # asyncpg возвращает datetime объекты напрямую, не строки
        expires_at = subscription["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        expires_str = expires_at.strftime("%d.%m.%Y")
        text = localization.get_text(language, "profile_active", date=expires_str, vpn_key=subscription["vpn_key"])
        text += localization.get_text(language, "profile_renewal_hint")
        
        # Добавляем информацию о VIP-статусе, если есть
        if is_vip:
            text += "\n\n" + localization.get_text(language, "vip_status_badge", default="👑 VIP-статус активен")
        
        # Получаем последний утверждённый платёж для определения тарифа
        last_payment = await database.get_last_approved_payment(telegram_id)
        last_tariff = last_payment.get("tariff") if last_payment else None
        
        await send_func(text, reply_markup=get_profile_keyboard_with_copy(language, last_tariff, is_vip))
    else:
        # Проверяем, есть ли pending платеж
        pending_payment = await database.get_pending_payment_by_user(telegram_id)
        if pending_payment:
            text = localization.get_text(language, "profile_payment_check")
        else:
            text = localization.get_text(language, "no_subscription")
        
        # Добавляем информацию о VIP-статусе, если есть
        if is_vip:
            text += "\n\n" + localization.get_text(language, "vip_status_badge", default="👑 VIP-статус активен")
        
        await send_func(text, reply_markup=get_profile_keyboard_with_copy(language, None, is_vip, has_subscription=False))


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
    text = await format_text_with_incident(text, language)
    await callback.message.edit_text(text, reply_markup=get_main_menu_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "menu_main")
async def callback_main_menu(callback: CallbackQuery):
    """Главное меню"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "welcome")
    text = await format_text_with_incident(text, language)
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


@router.callback_query(F.data == "menu_vip_access")
async def callback_vip_access(callback: CallbackQuery):
    """Обработчик кнопки 'VIP-доступ'"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    # Проверяем VIP-статус
    is_vip = await database.is_vip_user(telegram_id)
    
    # Получаем текст VIP-доступа
    text = localization.get_text(language, "vip_access_text")
    
    # Добавляем информацию о статусе, если пользователь VIP
    if is_vip:
        text += "\n\n" + localization.get_text(language, "vip_status_active", default="👑 Ваш VIP-статус активен")
    
    # Клавиатура с кнопками
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=localization.get_text(language, "contact_manager_button", default="💬 Связаться с менеджером"),
            url="https://t.me/asc_support"
        )],
        [InlineKeyboardButton(
            text=localization.get_text(language, "back"),
            callback_data="menu_profile"
        )]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "renew_same_period")
async def callback_renew_same_period(callback: CallbackQuery):
    """Продление подписки на тот же период - сразу вызывает sendInvoice"""
    await callback.answer()
    
    telegram_id = callback.from_user.id
    
    # Дополнительная защита: проверка истечения подписки
    await check_subscription_expiry(telegram_id)
    
    # Проверяем наличие активной подписки
    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await callback.message.answer(localization.get_text(language, "error_no_active_subscription"))
        return
    
    # Получаем тариф из последнего утвержденного платежа
    last_payment = await database.get_last_approved_payment(telegram_id)
    if not last_payment:
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await callback.message.answer(localization.get_text(language, "error_no_active_subscription"))
        return
    
    tariff_key = last_payment.get("tariff")
    if not tariff_key:
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await callback.message.answer(localization.get_text(language, "error_tariff"))
        return
    
    # Получаем цену тарифа
    tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
    price = tariff_data["price"]
    
    # Формируем payload (формат: renew:user_id:tariff:timestamp для уникальности)
    payload = f"renew:{telegram_id}:{tariff_key}:{int(time.time())}"
    
    # Отправляем invoice сразу
    await callback.bot.send_invoice(
        chat_id=telegram_id,
        title="Atlas Secure — продление подписки",
        description=f"Продление доступа на {tariff_key}",
        payload=payload,
        provider_token=config.TG_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Продление подписки", amount=price * 100)]
    )


@router.callback_query(F.data.startswith("renewal_pay:"))
async def callback_renewal_pay(callback: CallbackQuery):
    """Обработчик кнопки оплаты продления - отправляет invoice через Telegram Payments"""
    tariff_key = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    # Проверяем наличие provider_token
    if not config.TG_PROVIDER_TOKEN:
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await callback.answer(localization.get_text(language, "error_payments_unavailable"), show_alert=True)
        return
    
    # Рассчитываем цену с учетом скидки (та же логика, что в create_payment)
    tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
    base_price = tariff_data["price"]
    
    # ПРИОРИТЕТ 1: VIP-статус
    is_vip = await database.is_vip_user(telegram_id)
    
    if is_vip:
        amount = int(base_price * 0.70)  # 30% скидка
    else:
        # ПРИОРИТЕТ 2: Персональная скидка
        personal_discount = await database.get_user_discount(telegram_id)
        
        if personal_discount:
            discount_percent = personal_discount["discount_percent"]
            amount = int(base_price * (1 - discount_percent / 100))
        else:
            # Без скидки
            amount = base_price
    
    # Формируем payload (формат: renew:user_id:tariff:timestamp для уникальности)
    import time
    payload = f"renew:{telegram_id}:{tariff_key}:{int(time.time())}"
    
    # Формируем описание тарифа
    months = tariff_data["months"]
    description = f"Atlas Secure VPN продление подписки на {months} месяц(ев)"
    
    # Формируем prices (цена в копейках)
    prices = [LabeledPrice(label="К оплате", amount=amount * 100)]
    
    try:
        # Отправляем invoice
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
            start_parameter=payload  # Для быстрого доступа к платежу
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice for renewal: {e}")
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await callback.answer(localization.get_text(language, "error_payment_create"), show_alert=True)


@router.callback_query(F.data == "copy_key")
async def callback_copy_key(callback: CallbackQuery):
    """Копировать VPN-ключ"""
    await callback.answer()
    
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    # Дополнительная защита: проверка истечения подписки
    await check_subscription_expiry(telegram_id)
    
    # Проверяем, что у пользователя есть активная подписка
    subscription = await database.get_subscription(telegram_id)
    
    if not subscription:
        text = localization.get_text(language, "no_active_subscription")
        await callback.message.answer(text)
        return
    
    # Отправляем VPN-ключ отдельным сообщением
    vpn_key = subscription["vpn_key"]
    await callback.message.answer(
    f"<code>{vpn_key}</code>",
    parse_mode="HTML"
)


@router.callback_query(F.data == "copy_vpn_key")
async def callback_copy_vpn_key(callback: CallbackQuery):
    """Скопировать VPN-ключ (для экрана выдачи ключа)"""
    await callback.answer()
    
    telegram_id = callback.from_user.id
    
    # Дополнительная защита: проверка истечения подписки
    await check_subscription_expiry(telegram_id)
    
    # Получаем VPN-ключ из активной подписки
    subscription = await database.get_subscription(telegram_id)
    
    if not subscription:
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        text = localization.get_text(language, "no_active_subscription")
        await callback.message.answer(text)
        return
    
    # Отправляем VPN-ключ отдельным сообщением (без форматирования)
    vpn_key = subscription["vpn_key"]
    await callback.message.answer(vpn_key)


@router.callback_query(F.data == "go_profile")
async def callback_go_profile(callback: CallbackQuery):
    """Переход в профиль с экрана выдачи ключа"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    await show_profile(callback, language)
    await callback.answer()


@router.callback_query(F.data == "back_to_main")
async def callback_back_to_main(callback: CallbackQuery):
    """Возврат в главное меню с экрана выдачи ключа"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    text = localization.get_text(language, "welcome")
    text = await format_text_with_incident(text, language)
    await callback.message.edit_text(text, reply_markup=get_main_menu_keyboard(language))
    await callback.answer()


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
async def callback_buy_vpn(callback: CallbackQuery, state: FSMContext):
    """Купить VPN - выбор тарифа"""
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    # Очищаем промокод из состояния при входе в меню
    await state.update_data(promo_code=None)
    
    text = localization.get_text(language, "select_tariff")
    await callback.message.edit_text(text, reply_markup=await get_tariff_keyboard(language, telegram_id, None))
    await callback.answer()


@router.callback_query(F.data == "enter_promo")
async def callback_enter_promo(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки ввода промокода"""
    await callback.answer()
    
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    # Устанавливаем состояние ожидания промокода
    await state.set_state(PromoCodeInput.waiting_for_promo)
    
    text = localization.get_text(language, "enter_promo_text", default="Введите промокод:")
    await callback.message.answer(text)


@router.message(PromoCodeInput.waiting_for_promo)
async def process_promo_code(message: Message, state: FSMContext):
    """Обработчик ввода промокода"""
    telegram_id = message.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    promo_code = message.text.strip().upper()
    
    # Проверяем промокод через базу данных
    promo_data = await database.check_promo_code_valid(promo_code)
    if promo_data:
        # Промокод валиден
        await state.update_data(promo_code=promo_code)  # Сохраняем в верхнем регистре
        await state.set_state(None)  # Сбрасываем состояние
        
        text = localization.get_text(language, "promo_applied", default="✅ Промокод применён")
        await message.answer(text)
        
        # Обновляем экран выбора тарифа
        tariff_text = localization.get_text(language, "select_tariff")
        await message.answer(tariff_text, reply_markup=await get_tariff_keyboard(language, telegram_id, promo_code))
    else:
        # Промокод невалиден
        text = localization.get_text(language, "invalid_promo", default="❌ Промокод недействителен")
        await message.answer(text)


@router.callback_query(F.data.startswith("tariff_"))
async def callback_tariff(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора тарифа - отправляет invoice через Telegram Payments"""
    tariff_key = callback.data.split("_")[1]
    telegram_id = callback.from_user.id
    user = await database.get_user(telegram_id)
    language = user.get("language", "ru") if user else "ru"
    
    # Проверяем наличие provider_token
    if not config.TG_PROVIDER_TOKEN:
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await callback.answer(localization.get_text(language, "error_payments_unavailable"), show_alert=True)
        return
    
    # Получаем промокод из состояния
    state_data = await state.get_data()
    promo_code = state_data.get("promo_code")
    
    # Проверяем промокод через базу данных
    promo_data = None
    if promo_code:
        promo_data = await database.check_promo_code_valid(promo_code.upper())
    
    has_promo = promo_data is not None
    
    tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
    base_price = tariff_data["price"]
    
    # ПРИОРИТЕТ 0: Промокод (высший приоритет, перекрывает все остальные скидки)
    if has_promo:
        discount_percent = promo_data["discount_percent"]
        amount = int(base_price * (100 - discount_percent) / 100)
        payload = f"purchase:promo:{promo_code.upper()}:{telegram_id}:{tariff_key}:{int(time.time())}"
        # Очищаем промокод из состояния после использования
        await state.update_data(promo_code=None)
    else:
        # ПРИОРИТЕТ 1: VIP-статус
        is_vip = await database.is_vip_user(telegram_id)
        
        if is_vip:
            amount = int(base_price * 0.70)  # 30% скидка
            payload = f"{telegram_id}_{tariff_key}_{int(time.time())}"
        else:
            # ПРИОРИТЕТ 2: Персональная скидка
            personal_discount = await database.get_user_discount(telegram_id)
            if personal_discount:
                discount_percent = personal_discount["discount_percent"]
                amount = int(base_price * (1 - discount_percent / 100))
                payload = f"{telegram_id}_{tariff_key}_{int(time.time())}"
            else:
                # Без скидки
                amount = base_price
                payload = f"{telegram_id}_{tariff_key}_{int(time.time())}"
    
    # Формируем описание тарифа
    months = tariff_data["months"]
    if has_promo:
        description = f"Atlas Secure VPN подписка на {months} месяц(ев) (промокод)"
    else:
        description = f"Atlas Secure VPN подписка на {months} месяц(ев)"
    
    # Проверяем, что цена корректна
    if amount <= 0:
        await callback.answer("Ошибка расчета цены", show_alert=True)
        return
    
    # Формируем prices (цена в копейках)
    prices = [LabeledPrice(label="К оплате", amount=amount * 100)]
    
    try:
        # Отправляем invoice
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice: {e}")
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await callback.answer(localization.get_text(language, "error_payment_create"), show_alert=True)


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """Обработчик pre_checkout_query - подтверждение платежа перед списанием"""
    # Всегда подтверждаем платеж
    await pre_checkout_query.answer(ok=True)
    
    # Логируем событие
    payload = pre_checkout_query.invoice_payload
    telegram_id = pre_checkout_query.from_user.id
    
    logger.info(f"Pre-checkout query: user_id={telegram_id}, payload={payload}, amount={pre_checkout_query.total_amount}")
    
    # Логируем в audit_log
    try:
        await database._log_audit_event_atomic_standalone(
            "telegram_payment_pre_checkout",
            telegram_id,
            telegram_id,
            f"Pre-checkout query: payload={payload}, amount={pre_checkout_query.total_amount / 100} RUB"
        )
    except Exception as e:
        logger.error(f"Error logging pre-checkout query: {e}")


@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    """Обработчик successful_payment - успешная оплата"""
    telegram_id = message.from_user.id
    payment = message.successful_payment
    
    # Извлекаем данные из payload
    # Формат для обычной покупки: user_id_tariff_timestamp
    # Формат для покупки с промокодом: purchase:promo:CODE:user_id:tariff:timestamp
    # Формат для продления: renew:user_id:tariff:timestamp
    payload = payment.invoice_payload
    promo_code_used = None  # Инициализируем переменную для промокода
    try:
        if payload.startswith("renew:"):
            # Продление подписки
            parts = payload.split(":")
            if len(parts) < 3:
                logger.error(f"Invalid renewal payload format: {payload}")
                user = await database.get_user(telegram_id)
                language = user.get("language", "ru") if user else "ru"
                await message.answer(localization.get_text(language, "error_payment_processing"))
                return
            
            payload_user_id = int(parts[1])
            tariff_key = parts[2]
        elif payload.startswith("purchase:promo:"):
            # Покупка с промокодом
            parts = payload.split(":")
            if len(parts) < 5:
                logger.error(f"Invalid promo purchase payload format: {payload}")
                user = await database.get_user(telegram_id)
                language = user.get("language", "ru") if user else "ru"
                await message.answer(localization.get_text(language, "error_payment_processing"))
                return
            
            promo_code_used = parts[2]  # Код промокода
            payload_user_id = int(parts[3])
            tariff_key = parts[4]
        else:
            # Обычная покупка (старый формат)
            parts = payload.split("_")
            if len(parts) < 2:
                logger.error(f"Invalid payload format: {payload}")
                user = await database.get_user(telegram_id)
                language = user.get("language", "ru") if user else "ru"
                await message.answer(localization.get_text(language, "error_payment_processing"))
                return
            
            payload_user_id = int(parts[0])
            tariff_key = parts[1]
        
        # Проверяем, что платеж для этого пользователя
        if payload_user_id != telegram_id:
            logger.warning(f"Payload user_id mismatch: payload_user_id={payload_user_id}, telegram_id={telegram_id}")
            user = await database.get_user(telegram_id)
            language = user.get("language", "ru") if user else "ru"
            await message.answer(localization.get_text(language, "error_payment_processing"))
            return
        
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing payload {payload}: {e}")
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await message.answer(localization.get_text(language, "error_payment_processing"))
        return
    
    payment_amount = payment.total_amount // 100  # Конвертируем из копеек
    
    # Создаем платеж в БД
    # Для Telegram Payments создаем платеж при successful_payment
    # (в отличие от СБП, где платеж создается заранее)
    # create_payment может вернуть None если есть pending - в этом случае
    # используем существующий платеж
    existing_payment = await database.get_pending_payment_by_user(telegram_id)
    if existing_payment:
        # Используем существующий pending платеж
        payment_id = existing_payment["id"]
        # Обновляем сумму на актуальную из платежа
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE payments SET amount = $1 WHERE id = $2",
                payment_amount, payment_id
            )
    else:
        # Создаем новый платеж с фактической суммой из платежа
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            payment_id = await conn.fetchval(
                "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
                telegram_id, tariff_key, payment_amount
            )
        if not payment_id:
            logger.error(f"Failed to create payment record for user {telegram_id}, tariff {tariff_key}")
            user = await database.get_user(telegram_id)
            language = user.get("language", "ru") if user else "ru"
            await message.answer(localization.get_text(language, "error_payment_processing"))
            return
    
    # Получаем тариф
    tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
    months = tariff_data["months"]
    
    # Активируем подписку
    expires_at, is_renewal, vpn_key = await database.approve_payment_atomic(
        payment_id,
        months,
        admin_telegram_id=config.ADMIN_TELEGRAM_ID  # Используем системного админа
    )
    
    if expires_at and vpn_key:
        # Успешно активирована подписка
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        
        # Если использован промокод, увеличиваем счетчик использований и логируем
        if promo_code_used:
            try:
                # Получаем данные промокода для логирования
                promo_data = await database.get_promo_code(promo_code_used)
                if promo_data:
                    discount_percent = promo_data["discount_percent"]
                    # Рассчитываем price_before (базовая цена тарифа)
                    base_price = tariff_data["price"]
                    price_before = base_price
                    price_after = payment_amount
                    
                    # Увеличиваем счетчик использований
                    await database.increment_promo_code_use(promo_code_used)
                    
                    # Логируем использование промокода
                    await database.log_promo_code_usage(
                        promo_code=promo_code_used,
                        telegram_id=telegram_id,
                        tariff=tariff_key,
                        discount_percent=discount_percent,
                        price_before=price_before,
                        price_after=price_after
                    )
            except Exception as e:
                logger.error(f"Error processing promo code usage: {e}")
        
        expires_str = expires_at.strftime("%d.%m.%Y")
        text = localization.get_text(language, "payment_approved", vpn_key=vpn_key, date=expires_str)
        
        # Отправляем сообщение с VPN-ключом
        await message.answer(text, reply_markup=get_vpn_key_keyboard(language))
        
        logger.info(f"Payment successful: user_id={telegram_id}, payment_id={payment_id}, tariff={tariff_key}, amount={payment_amount}")
        
        # Логируем событие
        await database._log_audit_event_atomic_standalone(
            "telegram_payment_successful",
            config.ADMIN_TELEGRAM_ID,
            telegram_id,
            f"Telegram payment successful: payment_id={payment_id}, payload={payload}, amount={payment_amount} RUB"
        )
    else:
        logger.error(f"Failed to activate subscription for payment {payment_id}")
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        await message.answer(localization.get_text(language, "error_subscription_activation"))


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
    base_price = tariff_data["price"]
    
    # Рассчитываем цену с учетом скидки (та же логика, что в create_payment)
    # ПРИОРИТЕТ 1: VIP-статус
    is_vip = await database.is_vip_user(telegram_id)
    
    if is_vip:
        amount = int(base_price * 0.70)  # 30% скидка
    else:
        # ПРИОРИТЕТ 2: Персональная скидка
        personal_discount = await database.get_user_discount(telegram_id)
        
        if personal_discount:
            discount_percent = personal_discount["discount_percent"]
            amount = int(base_price * (1 - discount_percent / 100))
        else:
            # Без скидки
            amount = base_price
    
    # Формируем текст с реквизитами
    text = localization.get_text(
        language, 
        "sbp_payment_text",
        amount=amount
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
    
    # Получаем данные платежа, чтобы показать реальную сумму администратору
    payment = await database.get_payment(payment_id)
    actual_amount = payment["amount"] if payment else config.TARIFFS.get(tariff_key, config.TARIFFS["1"])["price"]
    
    # Отправляем сообщение пользователю
    text = localization.get_text(language, "payment_pending")
    await callback.message.edit_text(text, reply_markup=get_pending_payment_keyboard(language))
    await callback.answer()
    
    # Уведомляем администратора с реальной суммой платежа
    tariff_data = config.TARIFFS.get(tariff_key, config.TARIFFS["1"])
    username = callback.from_user.username or "не указан"
    
    # Используем локализацию для админ-уведомления
    admin_text = localization.get_text(
        "ru",  # Админ всегда видит на русском
        "admin_payment_notification",
        username=username,
        telegram_id=telegram_id,
        tariff=tariff_data['months'],
        price=actual_amount
    )
    
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
    
    # Добавляем предупреждение об инциденте, если режим активен
    incident = await database.get_incident_settings()
    if incident["is_active"]:
        incident_text = incident.get("incident_text") or localization.get_text(language, "incident_banner")
        warning = localization.get_text(language, "incident_status_warning", incident_text=incident_text)
        text = text + warning
    
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
    
    text = localization.get_text(language, "support_text")
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
        
        # Атомарно подтверждаем платеж и создаем/продлеваем подписку
        # VPN-ключ создается через Outline API
        admin_telegram_id = callback.from_user.id
        result = await database.approve_payment_atomic(payment_id, tariff_data["months"], admin_telegram_id)
        expires_at, is_renewal, vpn_key = result
        
        if expires_at is None or vpn_key is None:
            logging.error(f"Failed to approve payment {payment_id} atomically")
            await callback.answer("Ошибка создания VPN-ключа. Проверь логи.", show_alert=True)
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
                reply_markup=get_vpn_key_keyboard(language)
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


@router.callback_query(F.data == "admin_promo_stats")
async def callback_admin_promo_stats(callback: CallbackQuery):
    """Обработчик кнопки статистики промокодов в админ-дашборде"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        # Получаем статистику промокодов
        stats = await database.get_promo_stats()
        
        # Формируем текст ответа
        text = await format_promo_stats_text(stats)
        
        await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
        await callback.answer()
    except Exception as e:
        logger.error(f"Error getting promo stats: {e}")
        await callback.answer("Ошибка при получении статистики промокодов.", show_alert=True)


@router.callback_query(F.data == "admin:metrics")
async def callback_admin_metrics(callback: CallbackQuery):
    """Раздел Метрики"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        metrics = await database.get_business_metrics()
        
        text = "📈 Бизнес-метрики\n\n"
        
        # Среднее время подтверждения оплаты
        approval_time = metrics.get('avg_payment_approval_time_seconds')
        if approval_time:
            minutes = int(approval_time / 60)
            seconds = int(approval_time % 60)
            text += f"⏱ Среднее время подтверждения оплаты: {minutes} мин {seconds} сек\n"
        else:
            text += "⏱ Среднее время подтверждения оплаты: нет данных\n"
        
        # Среднее время жизни подписки
        lifetime = metrics.get('avg_subscription_lifetime_days')
        if lifetime:
            text += f"📅 Среднее время жизни подписки: {lifetime:.1f} дней\n"
        else:
            text += "📅 Среднее время жизни подписки: нет данных\n"
        
        # Количество продлений на пользователя
        renewals = metrics.get('avg_renewals_per_user', 0.0)
        text += f"🔄 Среднее количество продлений на пользователя: {renewals:.2f}\n"
        
        # Процент подтвержденных платежей
        approval_rate = metrics.get('approval_rate_percent', 0.0)
        text += f"✅ Процент подтвержденных платежей: {approval_rate:.1f}%\n"
        
        await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
        await callback.answer()
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_metrics", callback.from_user.id, None, "Admin viewed business metrics")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_metrics: {e}")
        await callback.answer("Ошибка при получении метрик. Проверь логи.", show_alert=True)


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
        
        # Получаем расширенную статистику
        stats = await database.get_user_extended_stats(user["telegram_id"])
        
        # Формируем карточку пользователя
        text = "👤 Пользователь\n\n"
        text += f"Telegram ID: {user['telegram_id']}\n"
        username_display = user.get('username') or 'не указан'
        text += f"Username: @{username_display}\n"
        
        # Язык
        user_language = user.get('language') or 'ru'
        language_display = localization.LANGUAGE_BUTTONS.get(user_language, user_language)
        text += f"Язык: {language_display}\n"
        
        # Дата регистрации
        created_at = user.get('created_at')
        if created_at:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            created_str = created_at.strftime("%d.%m.%Y %H:%M")
            text += f"Дата регистрации: {created_str}\n"
        else:
            text += "Дата регистрации: —\n"
        
        text += "\n"
        
        if subscription:
            expires_at = subscription["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            
            now = datetime.now()
            if expires_at > now:
                text += "Статус подписки: ✅ Активна\n"
            else:
                text += "Статус подписки: ⛔ Истекла\n"
            
            text += f"Срок действия: до {expires_str}\n"
            text += f"VPN-ключ: `{subscription['vpn_key']}`\n"
        else:
            text += "Статус подписки: ❌ Нет подписки\n"
            text += "VPN-ключ: —\n"
            text += "Срок действия: —\n"
        
        # Статистика
        text += f"\nКоличество продлений: {stats['renewals_count']}\n"
        text += f"Количество перевыпусков: {stats['reissues_count']}\n"
        
        # Проверяем наличие персональной скидки
        user_discount = await database.get_user_discount(user["telegram_id"])
        has_discount = user_discount is not None
        
        # Проверяем VIP-статус (явно определяем переменную)
        is_vip = await database.is_vip_user(user["telegram_id"])
        
        if user_discount:
            discount_percent = user_discount["discount_percent"]
            expires_at_discount = user_discount.get("expires_at")
            if expires_at_discount:
                if isinstance(expires_at_discount, str):
                    expires_at_discount = datetime.fromisoformat(expires_at_discount.replace('Z', '+00:00'))
                expires_str = expires_at_discount.strftime("%d.%m.%Y %H:%M")
                text += f"\n🎯 Персональная скидка: {discount_percent}% (до {expires_str})\n"
            else:
                text += f"\n🎯 Персональная скидка: {discount_percent}% (бессрочно)\n"
        
        # Добавляем информацию о VIP-статусе
        if is_vip:
            text += f"\n👑 VIP-статус: активен\n"
        
        if subscription:
            expires_at = subscription["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            now = datetime.now()
            has_active = expires_at > now
            await message.answer(text, reply_markup=get_admin_user_keyboard(has_active_subscription=has_active, user_id=user["telegram_id"], has_discount=has_discount, is_vip=is_vip), parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=get_admin_user_keyboard(has_active_subscription=False, user_id=user["telegram_id"], has_discount=has_discount, is_vip=is_vip), parse_mode="HTML")
        
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


def get_admin_grant_days_keyboard(user_id: int):
    """Клавиатура для выбора срока доступа (1/7/14 дней или 10 минут)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1 день", callback_data=f"admin:grant_days:{user_id}:1"),
            InlineKeyboardButton(text="7 дней", callback_data=f"admin:grant_days:{user_id}:7"),
        ],
        [
            InlineKeyboardButton(text="14 дней", callback_data=f"admin:grant_days:{user_id}:14"),
        ],
        [
            InlineKeyboardButton(text="⏱ Доступ на 10 минут", callback_data=f"admin:grant_minutes:{user_id}:10"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data="admin:user"),
        ]
    ])
    return keyboard


@router.callback_query(F.data.startswith("admin:grant:"))
async def callback_admin_grant(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Выдать доступ'"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Сохраняем user_id в состоянии
        await state.update_data(user_id=user_id)
        
        # Показываем клавиатуру выбора срока
        text = "Выберите срок доступа:"
        await callback.message.edit_text(text, reply_markup=get_admin_grant_days_keyboard(user_id))
        await state.set_state(AdminGrantAccess.waiting_for_days)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_grant: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:grant_days:"))
async def callback_admin_grant_days(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработчик выбора срока доступа"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        days = int(parts[3])
        
        # Выдаем доступ
        expires_at, vpn_key = await database.admin_grant_access_atomic(
            telegram_id=user_id,
            days=days,
            admin_telegram_id=callback.from_user.id
        )
        
        if expires_at is None or vpn_key is None:
            # Ошибка создания ключа
            text = "❌ Ошибка создания VPN-ключа"
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("Ошибка создания ключа", show_alert=True)
        else:
            # Успешно
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            text = f"✅ Доступ выдан на {days} дней\nПользователь уведомлён."
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            
            # Уведомляем пользователя
            try:
                user_lang = await database.get_user(user_id)
                language = user_lang.get("language", "ru") if user_lang else "ru"
                
                user_text = localization.get_text(
                    language,
                    "admin_grant_user_notification",
                    days=days,
                    vpn_key=vpn_key,
                    date=expires_str
                )
                await bot.send_message(user_id, user_text)
            except Exception as e:
                logging.exception(f"Error sending notification to user {user_id}: {e}")
        
        await state.clear()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_grant_days: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin:grant_minutes:"))
async def callback_admin_grant_minutes(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработчик выдачи доступа на N минут"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        minutes = int(parts[3])
        
        # Выдаем доступ на минуты
        expires_at, vpn_key = await database.admin_grant_access_minutes_atomic(
            telegram_id=user_id,
            minutes=minutes,
            admin_telegram_id=callback.from_user.id
        )
        
        if expires_at is None or vpn_key is None:
            # Ошибка создания ключа
            text = "❌ Ошибка создания VPN-ключа"
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("Ошибка создания ключа", show_alert=True)
        else:
            # Успешно
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            text = f"✅ Доступ выдан на {minutes} минут\nПользователь уведомлён."
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            
            # Уведомляем пользователя
            try:
                user_lang = await database.get_user(user_id)
                language = user_lang.get("language", "ru") if user_lang else "ru"
                
                # Используем специальное уведомление для 10 минут
                user_text = localization.get_text(
                    language,
                    "admin_grant_user_notification_10m"
                )
                
                # Добавляем кнопку "Перейти к подключению"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=localization.get_text(language, "go_to_connection"),
                        callback_data="menu_instruction"
                    )]
                ])
                
                await bot.send_message(user_id, user_text, reply_markup=keyboard)
            except Exception as e:
                logging.exception(f"Error sending notification to user {user_id}: {e}")
        
        await state.clear()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_grant_minutes: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin:revoke:"))
async def callback_admin_revoke(callback: CallbackQuery, bot: Bot):
    """Обработчик кнопки 'Лишить доступа'"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Лишаем доступа
        revoked = await database.admin_revoke_access_atomic(
            telegram_id=user_id,
            admin_telegram_id=callback.from_user.id
        )
        
        if not revoked:
            # Нет активной подписки
            text = "❌ У пользователя нет активной подписки"
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("Нет активной подписки", show_alert=True)
        else:
            # Успешно
            text = "✅ Доступ отозван\nПользователь уведомлён."
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            
            # Уведомляем пользователя
            try:
                user_lang = await database.get_user(user_id)
                language = user_lang.get("language", "ru") if user_lang else "ru"
                
                user_text = localization.get_text(language, "admin_revoke_user_notification")
                await bot.send_message(user_id, user_text)
            except Exception as e:
                logging.exception(f"Error sending notification to user {user_id}: {e}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_revoke: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:revoke:"))
async def callback_admin_revoke(callback: CallbackQuery, bot: Bot):
    """Обработчик кнопки 'Лишить доступа'"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Лишаем доступа
        revoked = await database.admin_revoke_access_atomic(
            telegram_id=user_id,
            admin_telegram_id=callback.from_user.id
        )
        
        if not revoked:
            # Нет активной подписки
            text = "❌ У пользователя нет активной подписки"
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("Нет активной подписки", show_alert=True)
        else:
            # Успешно
            text = "✅ Доступ отозван\nПользователь уведомлён."
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            
            # Уведомляем пользователя
            try:
                user_lang = await database.get_user(user_id)
                language = user_lang.get("language", "ru") if user_lang else "ru"
                
                user_text = localization.get_text(language, "admin_revoke_user_notification")
                await bot.send_message(user_id, user_text)
            except Exception as e:
                logging.exception(f"Error sending notification to user {user_id}: {e}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_revoke: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


# ==================== ОБРАБОТЧИКИ ДЛЯ УПРАВЛЕНИЯ ПЕРСОНАЛЬНЫМИ СКИДКАМИ ====================

def get_admin_discount_percent_keyboard(user_id: int):
    """Клавиатура для выбора процента скидки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="10%", callback_data=f"admin:discount_percent:{user_id}:10"),
            InlineKeyboardButton(text="15%", callback_data=f"admin:discount_percent:{user_id}:15"),
        ],
        [
            InlineKeyboardButton(text="25%", callback_data=f"admin:discount_percent:{user_id}:25"),
            InlineKeyboardButton(text="Ввести вручную", callback_data=f"admin:discount_percent_manual:{user_id}"),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:main")],
    ])
    return keyboard


def get_admin_discount_expires_keyboard(user_id: int, discount_percent: int):
    """Клавиатура для выбора срока действия скидки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="7 дней", callback_data=f"admin:discount_expires:{user_id}:{discount_percent}:7"),
            InlineKeyboardButton(text="30 дней", callback_data=f"admin:discount_expires:{user_id}:{discount_percent}:30"),
        ],
        [
            InlineKeyboardButton(text="Бессрочно", callback_data=f"admin:discount_expires:{user_id}:{discount_percent}:0"),
            InlineKeyboardButton(text="Ввести вручную", callback_data=f"admin:discount_expires_manual:{user_id}:{discount_percent}"),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:main")],
    ])
    return keyboard


@router.callback_query(F.data.startswith("admin:discount_create:"))
async def callback_admin_discount_create(callback: CallbackQuery):
    """Обработчик кнопки 'Назначить скидку'"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Проверяем, есть ли уже скидка
        existing_discount = await database.get_user_discount(user_id)
        if existing_discount:
            discount_percent = existing_discount["discount_percent"]
            text = f"❌ У пользователя уже есть персональная скидка {discount_percent}%.\n\nСначала удалите существующую скидку."
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("Скидка уже существует", show_alert=True)
            return
        
        text = f"🎯 Назначить скидку\n\nВыберите процент скидки:"
        await callback.message.edit_text(text, reply_markup=get_admin_discount_percent_keyboard(user_id))
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_create: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:discount_percent:"))
async def callback_admin_discount_percent(callback: CallbackQuery):
    """Обработчик выбора процента скидки"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        
        text = f"🎯 Назначить скидку {discount_percent}%\n\nВыберите срок действия скидки:"
        await callback.message.edit_text(text, reply_markup=get_admin_discount_expires_keyboard(user_id, discount_percent))
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_percent: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:discount_percent_manual:"))
async def callback_admin_discount_percent_manual(callback: CallbackQuery, state: FSMContext):
    """Обработчик для ввода процента скидки вручную"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        await state.update_data(discount_user_id=user_id)
        await state.set_state(AdminDiscountCreate.waiting_for_percent)
        
        text = "🎯 Назначить скидку\n\nВведите процент скидки (число от 1 до 99):"
        await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_percent_manual: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.message(AdminDiscountCreate.waiting_for_percent)
async def process_admin_discount_percent(message: Message, state: FSMContext):
    """Обработка введённого процента скидки"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await message.answer("Недостаточно прав доступа")
        await state.clear()
        return
    
    try:
        data = await state.get_data()
        user_id = data.get("discount_user_id")
        
        try:
            discount_percent = int(message.text.strip())
            if discount_percent < 1 or discount_percent > 99:
                await message.answer("Процент скидки должен быть от 1 до 99. Попробуйте снова:")
                return
        except ValueError:
            await message.answer("Введите число от 1 до 99:")
            return
        
        await state.update_data(discount_percent=discount_percent)
        
        text = f"🎯 Назначить скидку {discount_percent}%\n\nВыберите срок действия скидки:"
        await message.answer(text, reply_markup=get_admin_discount_expires_keyboard(user_id, discount_percent))
        await state.set_state(AdminDiscountCreate.waiting_for_expires)
        
    except Exception as e:
        logging.exception(f"Error in process_admin_discount_percent: {e}")
        await message.answer("Ошибка. Проверь логи.")
        await state.clear()


@router.callback_query(F.data.startswith("admin:discount_expires:"))
async def callback_admin_discount_expires(callback: CallbackQuery, bot: Bot):
    """Обработчик выбора срока действия скидки"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        expires_days = int(parts[4])
        
        # Рассчитываем expires_at
        expires_at = None
        if expires_days > 0:
            expires_at = datetime.now() + timedelta(days=expires_days)
        
        # Создаём скидку
        success = await database.create_user_discount(
            telegram_id=user_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=callback.from_user.id
        )
        
        if success:
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "бессрочно"
            text = f"✅ Персональная скидка {discount_percent}% назначена\n\nСрок действия: {expires_str}"
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("Скидка назначена", show_alert=True)
        else:
            text = "❌ Ошибка при создании скидки"
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("Ошибка", show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_expires: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:discount_expires_manual:"))
async def callback_admin_discount_expires_manual(callback: CallbackQuery, state: FSMContext):
    """Обработчик для ввода срока действия скидки вручную"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        discount_percent = int(parts[3])
        
        await state.update_data(discount_user_id=user_id, discount_percent=discount_percent)
        await state.set_state(AdminDiscountCreate.waiting_for_expires)
        
        text = "🎯 Назначить скидку\n\nВведите количество дней действия скидки (или 0 для бессрочной):"
        await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_expires_manual: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.message(AdminDiscountCreate.waiting_for_expires)
async def process_admin_discount_expires(message: Message, state: FSMContext, bot: Bot):
    """Обработка введённого срока действия скидки"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await message.answer("Недостаточно прав доступа")
        await state.clear()
        return
    
    try:
        data = await state.get_data()
        user_id = data.get("discount_user_id")
        discount_percent = data.get("discount_percent")
        
        try:
            expires_days = int(message.text.strip())
            if expires_days < 0:
                await message.answer("Количество дней должно быть неотрицательным. Попробуйте снова:")
                return
        except ValueError:
            await message.answer("Введите число (количество дней или 0 для бессрочной):")
            return
        
        # Рассчитываем expires_at
        expires_at = None
        if expires_days > 0:
            expires_at = datetime.now() + timedelta(days=expires_days)
        
        # Создаём скидку
        success = await database.create_user_discount(
            telegram_id=user_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=message.from_user.id
        )
        
        if success:
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "бессрочно"
            text = f"✅ Персональная скидка {discount_percent}% назначена\n\nСрок действия: {expires_str}"
            await message.answer(text, reply_markup=get_admin_back_keyboard())
        else:
            text = "❌ Ошибка при создании скидки"
            await message.answer(text, reply_markup=get_admin_back_keyboard())
        
        await state.clear()
        
    except Exception as e:
        logging.exception(f"Error in process_admin_discount_expires: {e}")
        await message.answer("Ошибка. Проверь логи.")
        await state.clear()


@router.callback_query(F.data.startswith("admin:discount_delete:"))
async def callback_admin_discount_delete(callback: CallbackQuery):
    """Обработчик кнопки 'Удалить скидку'"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Удаляем скидку
        success = await database.delete_user_discount(
            telegram_id=user_id,
            deleted_by=callback.from_user.id
        )
        
        if success:
            text = "✅ Персональная скидка удалена"
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("Скидка удалена", show_alert=True)
        else:
            text = "❌ Скидка не найдена или уже удалена"
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("Скидка не найдена", show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_discount_delete: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


# ==================== ОБРАБОТЧИКИ ДЛЯ УПРАВЛЕНИЯ VIP-СТАТУСОМ ====================

async def _show_admin_user_card(message_or_callback, user_id: int):
    """Вспомогательная функция для отображения карточки пользователя администратору"""
    # Получаем данные пользователя
    user = await database.find_user_by_id_or_username(telegram_id=user_id)
    if not user:
        if hasattr(message_or_callback, 'edit_text'):
            await message_or_callback.edit_text("❌ Пользователь не найден", reply_markup=get_admin_back_keyboard())
        else:
            await message_or_callback.answer("❌ Пользователь не найден")
        return
    
    # Получаем информацию о подписке
    subscription = await database.get_subscription(user["telegram_id"])
    
    # Получаем расширенную статистику
    stats = await database.get_user_extended_stats(user["telegram_id"])
    
    # Формируем карточку пользователя
    text = "👤 Пользователь\n\n"
    text += f"Telegram ID: {user['telegram_id']}\n"
    username_display = user.get('username') or 'не указан'
    text += f"Username: @{username_display}\n"
    
    # Язык
    user_language = user.get('language') or 'ru'
    language_display = localization.LANGUAGE_BUTTONS.get(user_language, user_language)
    text += f"Язык: {language_display}\n"
    
    # Дата регистрации
    created_at = user.get('created_at')
    if created_at:
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        created_str = created_at.strftime("%d.%m.%Y %H:%M")
        text += f"Дата регистрации: {created_str}\n"
    else:
        text += "Дата регистрации: —\n"
    
    text += "\n"
    
    if subscription:
        expires_at = subscription["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
        
        now = datetime.now()
        if expires_at > now:
            text += "Статус подписки: ✅ Активна\n"
        else:
            text += "Статус подписки: ⛔ Истекла\n"
        
        text += f"Срок действия: до {expires_str}\n"
        text += f"VPN-ключ: `{subscription['vpn_key']}`\n"
    else:
        text += "Статус подписки: ❌ Нет подписки\n"
        text += "VPN-ключ: —\n"
        text += "Срок действия: —\n"
    
    # Статистика
    text += f"\nКоличество продлений: {stats['renewals_count']}\n"
    text += f"Количество перевыпусков: {stats['reissues_count']}\n"
    
    # Проверяем наличие персональной скидки
    user_discount = await database.get_user_discount(user["telegram_id"])
    has_discount = user_discount is not None
    
    # Проверяем VIP-статус (явно определяем переменную)
    is_vip = await database.is_vip_user(user["telegram_id"])
    
    if user_discount:
        discount_percent = user_discount["discount_percent"]
        expires_at_discount = user_discount.get("expires_at")
        if expires_at_discount:
            if isinstance(expires_at_discount, str):
                expires_at_discount = datetime.fromisoformat(expires_at_discount.replace('Z', '+00:00'))
            expires_str = expires_at_discount.strftime("%d.%m.%Y %H:%M")
            text += f"\n🎯 Персональная скидка: {discount_percent}% (до {expires_str})\n"
        else:
            text += f"\n🎯 Персональная скидка: {discount_percent}% (бессрочно)\n"
    
    # Добавляем информацию о VIP-статусе
    if is_vip:
        text += f"\n👑 VIP-статус: активен\n"
    
    # Определяем статус подписки для клавиатуры
    if subscription:
        expires_at = subscription["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        now = datetime.now()
        has_active = expires_at > now
    else:
        has_active = False
    
    # Отображаем карточку
    keyboard = get_admin_user_keyboard(has_active_subscription=has_active, user_id=user["telegram_id"], has_discount=has_discount, is_vip=is_vip)
    
    if hasattr(message_or_callback, 'edit_text'):
        await message_or_callback.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:vip_grant:"))
async def callback_admin_vip_grant(callback: CallbackQuery):
    """Обработчик кнопки 'Выдать VIP'"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Проверяем, есть ли уже VIP-статус
        existing_vip = await database.is_vip_user(user_id)
        if existing_vip:
            # Если уже есть VIP, просто обновляем карточку
            await _show_admin_user_card(callback.message, user_id)
            await callback.answer("VIP уже назначен", show_alert=True)
            return
        
        # Назначаем VIP-статус
        success = await database.grant_vip_status(
            telegram_id=user_id,
            granted_by=callback.from_user.id
        )
        
        if success:
            # После успешного назначения VIP обновляем карточку пользователя
            await _show_admin_user_card(callback.message, user_id)
            await callback.answer("✅ VIP-статус выдан", show_alert=True)
        else:
            text = "❌ Ошибка при назначении VIP-статуса"
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("Ошибка", show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_vip_grant: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@router.callback_query(F.data.startswith("admin:vip_revoke:"))
async def callback_admin_vip_revoke(callback: CallbackQuery):
    """Обработчик кнопки 'Снять VIP'"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Отзываем VIP-статус
        success = await database.revoke_vip_status(
            telegram_id=user_id,
            revoked_by=callback.from_user.id
        )
        
        if success:
            # После успешного снятия VIP обновляем карточку пользователя
            await _show_admin_user_card(callback.message, user_id)
            await callback.answer("✅ VIP-статус снят", show_alert=True)
        else:
            text = "❌ VIP-статус не найден или уже снят"
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            await callback.answer("VIP не найден", show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_vip_revoke: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


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
            await callback.answer("Не удалось перевыпустить ключ. Нет активной подписки или ошибка создания ключа.", show_alert=True)
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
            
            # Проверяем VIP-статус и скидку
            is_vip = await database.is_vip_user(target_user_id)
            has_discount = await database.get_user_discount(target_user_id) is not None
            
            await callback.message.edit_text(text, reply_markup=get_admin_user_keyboard(has_active_subscription=True, user_id=target_user_id, has_discount=has_discount, is_vip=is_vip), parse_mode="HTML")
        
        await callback.answer("Ключ успешно перевыпущен")
        
        # Уведомляем пользователя
        try:
            user_text = f"🔐 Ваш VPN-ключ был перевыпущен администратором.\n\nНовый ключ: `{new_vpn_key}`\nРекомендуем сохранить новый ключ в надёжном месте."
            await callback.bot.send_message(target_user_id, user_text, parse_mode="HTML")
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


@router.callback_query(F.data == "admin:incident")
async def callback_admin_incident(callback: CallbackQuery):
    """Раздел управления инцидентом"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    incident = await database.get_incident_settings()
    is_active = incident["is_active"]
    incident_text = incident.get("incident_text") or "Текст не указан"
    
    status_text = "🟢 Режим инцидента активен" if is_active else "⚪ Режим инцидента выключен"
    text = f"🚨 Инцидент\n\n{status_text}\n\nТекст инцидента:\n{incident_text}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Включить" if not is_active else "❌ Выключить",
            callback_data="admin:incident:toggle"
        )],
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data="admin:incident:edit")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:main")],
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone("admin_view_incident", callback.from_user.id, None, f"Viewed incident settings (active: {is_active})")


@router.callback_query(F.data == "admin:incident:toggle")
async def callback_admin_incident_toggle(callback: CallbackQuery):
    """Переключение режима инцидента"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    incident = await database.get_incident_settings()
    new_state = not incident["is_active"]
    
    await database.set_incident_mode(new_state)
    
    action = "включен" if new_state else "выключен"
    await callback.answer(f"Режим инцидента {action}", show_alert=True)
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone(
        "incident_mode_toggled",
        callback.from_user.id,
        None,
        f"Incident mode {'enabled' if new_state else 'disabled'}"
    )
    
    # Возвращаемся к экрану инцидента
    await callback_admin_incident(callback)


@router.callback_query(F.data == "admin:incident:edit")
async def callback_admin_incident_edit(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования текста инцидента"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    text = "Введите текст инцидента (или отправьте /cancel для отмены):"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:incident")],
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.set_state(IncidentEdit.waiting_for_text)


@router.message(IncidentEdit.waiting_for_text)
async def process_incident_text(message: Message, state: FSMContext):
    """Обработка текста инцидента"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено")
        return
    
    incident_text = message.text
    
    # Включаем режим инцидента и сохраняем текст
    await database.set_incident_mode(True, incident_text)
    
    await message.answer(f"✅ Текст инцидента сохранён. Режим инцидента включён.")
    
    # Логируем действие
    await database._log_audit_event_atomic_standalone(
        "incident_text_updated",
        message.from_user.id,
        None,
        f"Incident text updated: {incident_text[:50]}..."
    )
    
    await state.clear()


@router.callback_query(F.data == "admin:broadcast")
async def callback_admin_broadcast(callback: CallbackQuery):
    """Раздел уведомлений"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    text = "📣 Уведомления\n\nВыберите действие:"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать уведомление", callback_data="broadcast:create")],
        [InlineKeyboardButton(text="📊 A/B статистика", callback_data="broadcast:ab_stats")],
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
    await state.set_state(BroadcastCreate.waiting_for_test_type)
    await message.answer("Выберите тип уведомления:", reply_markup=get_broadcast_test_type_keyboard())


@router.callback_query(F.data.startswith("broadcast_test_type:"))
async def callback_broadcast_test_type(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа тестирования"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    test_type = callback.data.split(":")[1]
    
    await state.update_data(is_ab_test=(test_type == "ab"))
    
    if test_type == "ab":
        await state.set_state(BroadcastCreate.waiting_for_message_a)
        await callback.message.edit_text("Введите текст варианта A:")
    else:
        await state.set_state(BroadcastCreate.waiting_for_message)
        await callback.message.edit_text("Введите текст уведомления:")


@router.message(BroadcastCreate.waiting_for_message_a)
async def process_broadcast_message_a(message: Message, state: FSMContext):
    """Обработка текста варианта A"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    
    await state.update_data(message_a=message.text)
    await state.set_state(BroadcastCreate.waiting_for_message_b)
    await message.answer("Введите текст варианта B:")


@router.message(BroadcastCreate.waiting_for_message_b)
async def process_broadcast_message_b(message: Message, state: FSMContext):
    """Обработка текста варианта B"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    
    await state.update_data(message_b=message.text)
    await state.set_state(BroadcastCreate.waiting_for_type)
    await message.answer("Выберите тип уведомления:", reply_markup=get_broadcast_type_keyboard())


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
    
    await state.update_data(type=broadcast_type)
    await state.set_state(BroadcastCreate.waiting_for_segment)
    
    await callback.message.edit_text(
        "Выберите сегмент получателей:",
        reply_markup=get_broadcast_segment_keyboard()
    )


@router.callback_query(F.data.startswith("broadcast_segment:"))
async def callback_broadcast_segment(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора сегмента получателей"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    segment = callback.data.split(":")[1]
    
    data = await state.get_data()
    title = data.get("title")
    message_text = data.get("message")
    broadcast_type = data.get("type")
    
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
    segment_name = {
        "all_users": "Все пользователи",
        "active_subscriptions": "Только активные подписки"
    }
    
    data_for_preview = await state.get_data()
    is_ab_test = data_for_preview.get("is_ab_test", False)
    
    if is_ab_test:
        message_a = data_for_preview.get("message_a", "")
        message_b = data_for_preview.get("message_b", "")
        preview_text = (
            f"{type_emoji.get(broadcast_type, '📢')} {title}\n\n"
            f"🔬 A/B ТЕСТ\n\n"
            f"Вариант A:\n{message_a}\n\n"
            f"Вариант B:\n{message_b}\n\n"
            f"Тип: {type_name.get(broadcast_type, broadcast_type)}\n"
            f"Сегмент: {segment_name.get(segment, segment)}"
        )
    else:
        message_text = data_for_preview.get("message", "")
        preview_text = (
            f"{type_emoji.get(broadcast_type, '📢')} {title}\n\n"
            f"{message_text}\n\n"
            f"Тип: {type_name.get(broadcast_type, broadcast_type)}\n"
            f"Сегмент: {segment_name.get(segment, segment)}"
        )
    
    await state.update_data(segment=segment)
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
    message_a = data.get("message_a")
    message_b = data.get("message_b")
    is_ab_test = data.get("is_ab_test", False)
    broadcast_type = data.get("type")
    segment = data.get("segment")
    
    # Проверка данных
    if not all([title, broadcast_type, segment]):
        await callback.message.answer("Ошибка: не все данные заполнены. Начните заново.")
        await state.clear()
        return
    
    if is_ab_test:
        if not all([message_a, message_b]):
            await callback.message.answer("Ошибка: не заполнены тексты вариантов A и B. Начните заново.")
            await state.clear()
            return
    else:
        if not message_text:
            await callback.message.answer("Ошибка: не заполнен текст уведомления. Начните заново.")
            await state.clear()
            return
    
    try:
        # Создаем уведомление в БД
        broadcast_id = await database.create_broadcast(
            title, message_text, broadcast_type, segment, callback.from_user.id,
            is_ab_test=is_ab_test, message_a=message_a, message_b=message_b
        )
        
        # Формируем сообщения для отправки
        type_emoji = {
            "info": "ℹ️",
            "maintenance": "🔧",
            "security": "🔒",
            "promo": "🎯"
        }
        emoji = type_emoji.get(broadcast_type, "📢")
        
        if is_ab_test:
            final_message_a = f"{emoji} {title}\n\n{message_a}"
            final_message_b = f"{emoji} {title}\n\n{message_b}"
        else:
            final_message = f"{emoji} {title}\n\n{message_text}"
        
        # Получаем список пользователей по сегменту
        user_ids = await database.get_users_by_segment(segment)
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
                if is_ab_test:
                    # Случайно выбираем вариант A или B (50/50)
                    variant = "A" if random.random() < 0.5 else "B"
                    message_to_send = final_message_a if variant == "A" else final_message_b
                    await bot.send_message(user_id, message_to_send)
                    await database.log_broadcast_send(broadcast_id, user_id, "sent", variant)
                else:
                    await bot.send_message(user_id, final_message)
                    await database.log_broadcast_send(broadcast_id, user_id, "sent")
                
                sent_count += 1
                
                # Задержка между отправками (0.3-0.5 сек)
                await asyncio.sleep(0.4)
                
            except Exception as e:
                logging.error(f"Error sending broadcast to user {user_id}: {e}")
                variant = None
                if is_ab_test:
                    # Для неудачных отправок тоже логируем вариант, если можем определить
                    variant = "A" if random.random() < 0.5 else "B"
                await database.log_broadcast_send(broadcast_id, user_id, "failed", variant)
                failed_count += 1
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone(
            "broadcast_sent",
            callback.from_user.id,
            None,
            f"Broadcast ID: {broadcast_id}, Segment: {segment}, Sent: {sent_count}, Failed: {failed_count}"
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


@router.callback_query(F.data == "broadcast:ab_stats")
async def callback_broadcast_ab_stats(callback: CallbackQuery):
    """Список A/B тестов"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        ab_tests = await database.get_ab_test_broadcasts()
        
        if not ab_tests:
            text = "📊 A/B статистика\n\nA/B тестов не найдено."
            await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard())
            return
        
        text = "📊 A/B статистика\n\nВыберите уведомление для просмотра статистики:"
        keyboard = get_ab_test_list_keyboard(ab_tests)
        await callback.message.edit_text(text, reply_markup=keyboard)
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_ab_stats_list", callback.from_user.id, None, f"Viewed {len(ab_tests)} A/B tests")
    
    except Exception as e:
        logging.exception(f"Error in callback_broadcast_ab_stats: {e}")
        await callback.message.answer("Ошибка при получении списка A/B тестов. Проверь логи.")


@router.callback_query(F.data.startswith("broadcast:ab_stat:"))
async def callback_broadcast_ab_stat_detail(callback: CallbackQuery):
    """Статистика конкретного A/B теста"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        broadcast_id = int(callback.data.split(":")[2])
        
        # Получаем информацию об уведомлении
        broadcast = await database.get_broadcast(broadcast_id)
        if not broadcast:
            await callback.message.answer("Уведомление не найдено.")
            return
        
        # Получаем статистику
        stats = await database.get_ab_test_stats(broadcast_id)
        
        if not stats:
            text = f"📊 A/B статистика\n\nУведомление: #{broadcast_id}\n\nНедостаточно данных для анализа."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="broadcast:ab_stats")],
            ])
            await callback.message.edit_text(text, reply_markup=keyboard)
            return
        
        # Формируем текст статистики
        total_sent = stats["total_sent"]
        variant_a_sent = stats["variant_a_sent"]
        variant_b_sent = stats["variant_b_sent"]
        
        # Проценты
        if total_sent > 0:
            percent_a = round((variant_a_sent / total_sent) * 100)
            percent_b = round((variant_b_sent / total_sent) * 100)
        else:
            percent_a = 0
            percent_b = 0
        
        text = (
            f"📊 A/B статистика\n\n"
            f"Уведомление: #{broadcast_id}\n"
            f"Заголовок: {broadcast.get('title', '—')}\n\n"
            f"Вариант A:\n"
            f"— Отправлено: {variant_a_sent} ({percent_a}%)\n\n"
            f"Вариант B:\n"
            f"— Отправлено: {variant_b_sent} ({percent_b}%)\n\n"
            f"Всего отправлено: {total_sent}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="broadcast:ab_stats")],
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_ab_stat_detail", callback.from_user.id, None, f"Viewed A/B stats for broadcast {broadcast_id}")
    
    except (ValueError, IndexError) as e:
        logging.error(f"Error parsing broadcast ID: {e}")
        await callback.message.answer("Ошибка: неверный ID уведомления.")
    except Exception as e:
        logging.exception(f"Error in callback_broadcast_ab_stat_detail: {e}")
        await callback.message.answer("Ошибка при получении статистики A/B теста. Проверь логи.")


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
            await message.answer(f"❌ Не удалось перевыпустить ключ для пользователя {target_telegram_id}.\nВозможные причины:\n- Нет активной подписки\n- Ошибка создания VPN-ключа")
            return
        
        # Уведомляем пользователя
        user = await database.get_user(target_telegram_id)
        language = user.get("language", "ru") if user else "ru"
        
        # Получаем информацию о подписке для уведомления
        subscription = await database.get_subscription(target_telegram_id)
        expires_str = subscription["expires_at"].strftime("%d.%m.%Y") if subscription else "неизвестно"
        
        user_text = f"🔐 Ваш VPN-ключ был перевыпущен администратором.\n\nНовый ключ: `{new_vpn_key}`\nСрок действия подписки: до {expires_str}\n\nРекомендуем сохранить новый ключ в надёжном месте."
        
        try:
            await message.bot.send_message(target_telegram_id, user_text, parse_mode="HTML")
            logging.info(f"Reissue notification sent to user {target_telegram_id}")
        except Exception as e:
            logging.error(f"Error sending reissue notification to user {target_telegram_id}: {e}")
            await message.answer(f"✅ Ключ перевыпущен, но не удалось отправить уведомление пользователю: {e}")
            return
        
        await message.answer(f"✅ VPN-ключ успешно перевыпущен для пользователя {target_telegram_id}\n\nСтарый ключ: `{old_vpn_key[:20]}...`\nНовый ключ: `{new_vpn_key}`", parse_mode="HTML")
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



