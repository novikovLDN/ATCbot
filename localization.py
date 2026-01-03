from typing import Dict

# Все тексты для локализации
TEXTS: Dict[str, Dict[str, str]] = {
    "ru": {
        "language_select": "Выберите язык / Choose language / Tilni tanlang / Забони интихоб кунед",
        
        # Главное меню
        "welcome": "Добро пожаловать в Atlas Secure.\n\nЗакрытый VPN-сервис для тех,\nкто ценит конфиденциальность,\nстабильность и контроль соединения.",
        "profile": "👤 Мой профиль",
        "buy_vpn": "🔐 Купить доступ",
        "about": "ℹ️ О сервисе",
        "support": "🛡 Поддержка",
        "back": "🔙 Назад",
        
        # Выбор тарифа
        "select_tariff": "Выберите срок доступа.\n\nКаждый тариф включает:\n— индивидуальный VPN-ключ\n— отсутствие логирования\n— стабильное соединение\n— приоритетную поддержку",
        "months": "{months} месяц",
        "months_3_6": "{months} месяца",
        "months_12": "{months} месяцев",
        
        # Выбор способа оплаты
        "select_payment": "Выберите способ оплаты.",
        "payment_test": "Тест (не работает)",
        "payment_sbp": "СБП",
        
        # Оплата СБП
        "sbp_payment_text": "Оплата доступа производится вручную.\n\nЭто позволяет:\n— исключить автоматические списания\n— сохранить контроль над каждым подключением\n— гарантировать индивидуальный VPN-ключ\n\nПосле перевода нажмите «Я оплатил».",
        "paid_button": "✅ Я оплатил",
        
        # Ожидание подтверждения
        "payment_pending": "⏳ Платёж принят на проверку.\n\nКаждое подключение подтверждается вручную.\nОбычно проверка занимает до 5 минут.\n\nПосле подтверждения доступ будет активирован автоматически.",
        
        # Успешная активация
        "payment_approved": "✅ Доступ активирован.\n\nВаш персональный VPN-ключ:\n{vpn_key}\n\nСрок действия:\nдо {date}\n\nРекомендуем сохранить ключ в надёжном месте.",
        
        # Отклонение
        "payment_rejected": "❌ Платёж не подтверждён.\n\nЕсли вы уверены, что оплатили —\nобратитесь в поддержку.",
        
        # Профиль - активная подписка
        "profile_active": "👤 Профиль доступа\n\nСтатус: Активен\nСрок действия: до {date}\n\nПерсональный VPN-ключ:\n{vpn_key}\n\nПодключение стабильно и защищено.",
        
        # Профиль - платеж на проверке
        "profile_payment_check": "🕒 Платёж на проверке.\n\nЭто стандартная процедура безопасности.\nПосле подтверждения доступ появится автоматически.",
        
        # Профиль - без подписки
        "no_subscription": "❌ Активного доступа нет.\n\nAtlas Secure — приватный VPN-сервис\nс индивидуальными ключами подключения.\n\nВы можете оформить доступ в любое время.",
        
        # О сервисе
        "about_text": "Atlas Secure — закрытый VPN-сервис,\nориентированный на приватность и надёжность.\n\nМы не используем:\n— общие ключи\n— автоматическую выдачу\n— массовые подключения\n\nКаждый доступ подтверждается вручную.",
        "privacy_policy": "Политика конфиденциальности",
        "privacy_policy_text": "Политика конфиденциальности\n\nМы храним только необходимые данные для предоставления услуг VPN.",
        
        # Поддержка
        "support_text": "🛡 Поддержка Atlas Secure\n\nЕсли у вас возникли вопросы по оплате,\nподключению или работе сервиса —\nсвяжитесь с нами напрямую.",
        "support_payment_not_confirmed": "💳 Платёж не подтвердили",
        "support_vpn_not_working": "🔌 VPN не работает",
        "support_other": "✉️ Другой вопрос",
        "change_language": "🌍 Изменить язык",
        
        # Администратор (без изменений)
        "admin_payment_notification": "💰 Новая оплата\nПользователь: @{username}\nTelegram ID: {telegram_id}\nТариф: {tariff} месяцев\nСтоимость: {price} руб.",
        "admin_approve": "Подтвердить",
        "admin_reject": "Отклонить",
    },
    "en": {
        "language_select": "Select language / Выберите язык / Tilni tanlang / Забони интихоб кунед",
        
        # Главное меню
        "welcome": "Welcome to Atlas Secure.\n\nPrivate VPN service for those\nwho value privacy,\nstability and connection control.",
        "profile": "👤 My Profile",
        "buy_vpn": "🔐 Buy Access",
        "about": "ℹ️ About",
        "support": "🛡 Support",
        "back": "🔙 Back",
        
        # Выбор тарифа
        "select_tariff": "Select access period.\n\nEach plan includes:\n— individual VPN key\n— no logging\n— stable connection\n— priority support",
        "months": "{months} month",
        "months_3_6": "{months} months",
        "months_12": "{months} months",
        
        # Выбор способа оплаты
        "select_payment": "Choose payment method.",
        "payment_test": "Test (not working)",
        "payment_sbp": "SBP",
        
        # Оплата СБП
        "sbp_payment_text": "Payment is processed manually.\n\nThis allows:\n— to exclude automatic charges\n— to maintain control over each connection\n— to guarantee individual VPN key\n\nAfter transfer, press «I paid».",
        "paid_button": "✅ I paid",
        
        # Ожидание подтверждения
        "payment_pending": "⏳ Payment accepted for verification.\n\nEach connection is confirmed manually.\nUsually verification takes up to 5 minutes.\n\nAfter confirmation, access will be activated automatically.",
        
        # Успешная активация
        "payment_approved": "✅ Access activated.\n\nYour personal VPN key:\n{vpn_key}\n\nValid until:\n{date}\n\nWe recommend saving the key in a secure place.",
        
        # Отклонение
        "payment_rejected": "❌ Payment not confirmed.\n\nIf you are sure you paid —\ncontact support.",
        
        # Профиль - активная подписка
        "profile_active": "👤 Access Profile\n\nStatus: Active\nValid until: {date}\n\nPersonal VPN key:\n{vpn_key}\n\nConnection is stable and protected.",
        
        # Профиль - платеж на проверке
        "profile_payment_check": "🕒 Payment under verification.\n\nThis is a standard security procedure.\nAfter confirmation, access will appear automatically.",
        
        # Профиль - без подписки
        "no_subscription": "❌ No active access.\n\nAtlas Secure — private VPN service\nwith individual connection keys.\n\nYou can get access at any time.",
        
        # О сервисе
        "about_text": "Atlas Secure — private VPN service,\nfocused on privacy and reliability.\n\nWe do not use:\n— shared keys\n— automatic issuance\n— mass connections\n\nEach access is confirmed manually.",
        "privacy_policy": "Privacy Policy",
        "privacy_policy_text": "Privacy Policy\n\nWe store only necessary data to provide VPN services.",
        
        # Поддержка
        "support_text": "🛡 Atlas Secure Support\n\nIf you have questions about payment,\nconnection or service operation —\ncontact us directly.",
        "support_payment_not_confirmed": "💳 Payment not confirmed",
        "support_vpn_not_working": "🔌 VPN not working",
        "support_other": "✉️ Other question",
        "change_language": "🌍 Change language",
        
        # Администратор
        "admin_payment_notification": "💰 New payment\nUser: @{username}\nTelegram ID: {telegram_id}\nTariff: {tariff} months\nPrice: {price} rub.",
        "admin_approve": "Approve",
        "admin_reject": "Reject",
    },
    "uz": {
        "language_select": "Tilni tanlang / Выберите язык / Choose language / Забони интихоб кунед",
        "welcome": "Atlas Secure-ga xush kelibsiz.\n\nMaxfiylik, barqarorlik va ulanishni nazorat qilishni qadrlaydiganlar uchun yopiq VPN xizmati.",
        "profile": "👤 Mening profilim",
        "buy_vpn": "🔐 Kirishni sotib olish",
        "about": "ℹ️ Xizmat haqida",
        "support": "🛡 Qo'llab-quvvatlash",
        "back": "🔙 Orqaga",
        "select_tariff": "Kirish muddatini tanlang.\n\nHar bir tarif quyidagilarni o'z ichiga oladi:\n— individual VPN kalit\n— jurnallashmaslik\n— barqaror ulanish\n— ustuvor qo'llab-quvvatlash",
        "months": "{months} oy",
        "months_3_6": "{months} oy",
        "months_12": "{months} oy",
        "select_payment": "To'lov usulini tanlang.",
        "payment_test": "Test (ishlamaydi)",
        "payment_sbp": "SBP",
        "sbp_payment_text": "To'lov qo'lda amalga oshiriladi.\n\nBu quyidagilarga imkon beradi:\n— avtomatik to'lovlarni istisno qilish\n— har bir ulanishni nazorat qilish\n— individual VPN kalitni kafolatlash\n\nO'tkazmadan keyin «Men to'ladim» ni bosing.",
        "paid_button": "✅ Men to'ladim",
        "payment_pending": "⏳ To'lov tekshiruvga qabul qilindi.\n\nHar bir ulanish qo'lda tasdiqlanadi.\nOdatda tekshiruv 5 minutgacha davom etadi.\n\nTasdiqlanganidan keyin kirish avtomatik ravishda faollashtiriladi.",
        "payment_approved": "✅ Kirish faollashtirildi.\n\nSizning shaxsiy VPN kalitingiz:\n{vpn_key}\n\nAmal qilish muddati:\n{date} gacha\n\nKalitni xavfsiz joyda saqlashni tavsiya etamiz.",
        "payment_rejected": "❌ To'lov tasdiqlanmadi.\n\nAgar to'laganingizga ishonchingiz komil bo'lsa — qo'llab-quvvatlashga murojaat qiling.",
        "profile_active": "👤 Kirish profili\n\nHolati: Faol\nAmal qilish muddati: {date} gacha\n\nShaxsiy VPN kalit:\n{vpn_key}\n\nUlanish barqaror va himoyalangan.",
        "profile_payment_check": "🕒 To'lov tekshiruvda.\n\nBu standart xavfsizlik protsedurasi.\nTasdiqlanganidan keyin kirish avtomatik ravishda paydo bo'ladi.",
        "no_subscription": "❌ Faol kirish yo'q.\n\nAtlas Secure — individual ulanish kalitlari bilan maxfiy VPN xizmati.\n\nSiz istalgan vaqtda kirish olishingiz mumkin.",
        "about_text": "Atlas Secure — maxfiylik va ishonchlilikka yo'naltirilgan yopiq VPN xizmati.\n\nBiz quyidagilardan foydalanmaymiz:\n— umumiy kalitlar\n— avtomatik berish\n— ommaviy ulanishlar\n\nHar bir kirish qo'lda tasdiqlanadi.",
        "privacy_policy": "Maxfiylik siyosati",
        "privacy_policy_text": "Maxfiylik siyosati\n\nBiz VPN xizmatlarini taqdim etish uchun faqat zarur ma'lumotlarni saqlaymiz.",
        "support_text": "🛡 Atlas Secure qo'llab-quvvatlash\n\nAgar sizda to'lov, ulanish yoki xizmat ishlashi haqida savollar bo'lsa — biz bilan to'g'ridan-to'g'ri bog'laning.",
        "support_payment_not_confirmed": "💳 To'lov tasdiqlanmadi",
        "support_vpn_not_working": "🔌 VPN ishlamaydi",
        "support_other": "✉️ Boshqa savol",
        "change_language": "🌍 Tilni o'zgartirish",
        "admin_payment_notification": "💰 Yangi to'lov\nFoydalanuvchi: @{username}\nTelegram ID: {telegram_id}\nTarif: {tariff} oy\nNarx: {price} so'm",
        "admin_approve": "Tasdiqlash",
        "admin_reject": "Rad etish",
    },
    "tj": {
        "language_select": "Забони интихоб кунед / Выберите язык / Choose language / Tilni tanlang",
        "welcome": "Хуш омадед ба Atlas Secure.\n\nХизмати махфии VPN барои касоне,\nки махфият, устуворӣ ва назорати пайвандро арзанда мешуморанд.",
        "profile": "👤 Профили ман",
        "buy_vpn": "🔐 Хариди дастрасӣ",
        "about": "ℹ️ Дар бораи хизмат",
        "support": "🛡 Дастгирӣ",
        "back": "🔙 Бозгашт",
        "select_tariff": "Муддати дастрасиро интихоб кунед.\n\nҲар як тариф дорои:\n— калиди VPN-и шахсӣ\n— ҷавоб додан намешавад\n— пайванди устувор\n— дастгирии афзалиятнок",
        "months": "{months} моҳ",
        "months_3_6": "{months} моҳ",
        "months_12": "{months} моҳ",
        "select_payment": "Усули пардохтро интихоб кунед.",
        "payment_test": "Тест (коре намекунад)",
        "payment_sbp": "СБП",
        "sbp_payment_text": "Пардохт дастӣ анҷом дода мешавад.\n\nИн имкон медиҳад:\n— пардохтҳои худкорро истисно кардан\n— назорати ҳар як пайвандро нигоҳ доштан\n— калиди VPN-и шахсиро кафолат додан\n\nПас аз интиқол, тугмаи «Ман пардохт кардам»-ро пахш кунед.",
        "paid_button": "✅ Ман пардохт кардам",
        "payment_pending": "⏳ Пардохт барои санҷиш қабул шуд.\n\nҲар як пайванд дастӣ тасдиқ карда мешавад.\nОдаттан санҷиш то 5 дақиқа давом мекунад.\n\nПас аз тасдиқ, дастрасӣ худкор фаъол карда мешавад.",
        "payment_approved": "✅ Дастрасӣ фаъол шуд.\n\nКалиди VPN-и шахсии шумо:\n{vpn_key}\n\nМуддати амал:\nто {date}\n\nТавсия медиҳем, ки калидро дар ҷойи бехатар нигоҳ доред.",
        "payment_rejected": "❌ Пардохт тасдиқ нашуд.\n\nАгар мӯътақид ҳастед, ки пардохт кардед — ба дастгирӣ муроҷиат кунед.",
        "profile_active": "👤 Профили дастрасӣ\n\nҲолат: Фаъол\nМуддати амал: то {date}\n\nКалиди VPN-и шахсӣ:\n{vpn_key}\n\nПайванд устувор ва ҳимояшуда аст.",
        "profile_payment_check": "🕒 Пардохт дар санҷиш аст.\n\nИн процедураи стандартии амният аст.\nПас аз тасдиқ, дастрасӣ худкор пайдо мешавад.",
        "no_subscription": "❌ Дастрасии фаъол нест.\n\nAtlas Secure — хизмати махфии VPN\nбо калидҳои пайванди шахсӣ.\n\nШумо метавонед дар ҳар вақт дастрасӣ гиред.",
        "about_text": "Atlas Secure — хизмати махфии VPN,\nки ба махфият ва эътимоднокӣ равона аст.\n\nМо истифода намебарем:\n— калидҳои умумӣ\n— додани худкор\n— пайвандҳои оммавӣ\n\nҲар як дастрасӣ дастӣ тасдиқ карда мешавад.",
        "privacy_policy": "Сиёсати махфият",
        "privacy_policy_text": "Сиёсати махфият\n\nМо танҳо маълумоти зарурӣ барои таъмини хидматҳои VPN нигоҳ медорем.",
        "support_text": "🛡 Дастгирии Atlas Secure\n\nАгар шумо саволҳо дар бораи пардохт, пайванд ё амали хизмат дошта бошед — бо мо бевосита тавонос шавед.",
        "support_payment_not_confirmed": "💳 Пардохт тасдиқ нашуд",
        "support_vpn_not_working": "🔌 VPN кор намекунад",
        "support_other": "✉️ Саволи дигар",
        "change_language": "🌍 Тағйири забон",
        "admin_payment_notification": "💰 Пардохти нав\nКорбар: @{username}\nTelegram ID: {telegram_id}\nТариф: {tariff} моҳ\nНарх: {price} сом.",
        "admin_approve": "Тасдиқ кардан",
        "admin_reject": "Рад кардан",
    },
}


def get_text(language: str, key: str, **kwargs) -> str:
    """Получить переведенный текст"""
    lang = language if language in TEXTS else "ru"
    text = TEXTS[lang].get(key, TEXTS["ru"].get(key, key))
    return text.format(**kwargs) if kwargs else text


# Кнопки для выбора языка
LANGUAGE_BUTTONS = {
    "ru": "Русский",
    "en": "English",
    "uz": "O'zbek",
    "tj": "Тоҷикӣ",
}
