from typing import Dict

# Все тексты для локализации
TEXTS: Dict[str, Dict[str, str]] = {
    "ru": {
        "language_select": "Выберите язык / Choose language / Tilni tanlang / Забони интихоб кунед",
        
        # Главное меню
        "welcome": "Добро пожаловать в Atlas Secure\n\nЧастная цифровая инфраструктура высшего класса.\nСоздана для тех, кто не обсуждает контроль — он у них есть.\n\nAtlas Secure — это среда, где\nприватность заложена в архитектуре,\nстабильность — в инженерных решениях,\nа предсказуемость — в каждом соединении.",
        "profile": "👤 Мой профиль",
        "buy_vpn": "🔐 Купить доступ",
        "about": "ℹ️ О сервисе",
        "support": "🛡 Поддержка",
        "back": "🔙 Назад",
        
        # Выбор тарифа
        "select_tariff": "Выберите срок доступа\n\nAtlas Secure работает по принципу ограниченного доступа.\nКаждый период — это частная конфигурация, а не массовый тариф.\n\nКаждый уровень доступа включает:\n— индивидуальный VPN-ключ, закреплённый исключительно за вами\n— zero-logs архитектуру без хранения сессий и метаданных\n— стабильное соединение без лимитов и деградации скорости\n— приоритетную поддержку",
        "tariff_button_1": "1 месяц Временный доступ · 299 ₽",
        "tariff_button_3": "3 месяца Стандартный доступ · 799 ₽",
        "tariff_button_6": "6 месяцев Расширенный доступ · 1 499 ₽",
        "tariff_button_12": "12 месяцев Приоритетный доступ · 2 799 ₽",
        
        # Выбор способа оплаты
        "select_payment": "Выберите способ оплаты.",
        "payment_test": "Служебный режим Недоступно",
        "payment_sbp": "СБП",
        
        # Оплата СБП
        "sbp_payment_text": "Финансовое подтверждение доступа\n\nРучное подтверждение исключает автоматические списания\nи позволяет сформировать персональную конфигурацию\nVPN-доступа, закреплённую исключительно за вами.\n\nПосле выполнения перевода подтвердите оплату.\n\n⸻\n\nРеквизиты для перевода\n\nБанк: {bank}\nСчёт: {account}\nПолучатель: {name}\n\nСумма к подтверждению: {price} ₽",
        "paid_button": "Подтвердить оплату",
        
        # Ожидание подтверждения
        "payment_pending": "Подтверждение в процессе\n\nПлатёж зафиксирован.\nВерификация занимает до 5 минут.\nАктивация доступа выполняется автоматически.",
        
        # Успешная активация
        "payment_approved": "✅ Доступ активирован.\n\nВаш персональный VPN-ключ:\n{vpn_key}\n\nСрок действия:\nдо {date}\n\nРекомендуем сохранить ключ в надёжном месте.",
        
        # Отклонение
        "payment_rejected": "❌ Платёж не подтверждён.\n\nЕсли вы уверены, что оплатили —\nобратитесь в поддержку.",
        
        # Профиль - активная подписка
        "profile_active": "👤 Профиль доступа\n\nСтатус: Активен\nСрок действия: до {date}\n\nПерсональный VPN-ключ:\n{vpn_key}\n\nПодключение стабильно и защищено.",
        "profile_renewal_hint": "\n\nЛюбая повторная покупка автоматически продлевает срок действия подписки.",
        
        # Профиль - платеж на проверке
        "profile_payment_check": "🕒 Платёж на проверке.\n\nЭто стандартная процедура безопасности.\nПосле подтверждения доступ появится автоматически.",
        
        # Напоминание об окончании подписки
        "subscription_expiring_reminder": "⏳ Срок доступа скоро истекает.\n\nДо окончания вашей подписки осталось 3 дня.\n\nВы можете продлить доступ в любое время —\nповторная покупка автоматически увеличит срок действия.",
        
        # Профиль - без подписки
        "no_subscription": "❌ Активного доступа нет.\n\nAtlas Secure — приватный VPN-сервис\nс индивидуальными ключами подключения.\n\nВы можете оформить доступ в любое время.",
        
        # О сервисе
        "about_text": "Что стоит за Atlas Secure\n\n🔐 Криптография уровня enterprise (AES-256)\nТа же архитектура, которую используют финансовые и государственные системы.\n\n🧬 Zero-Logs философия\nМы принципиально не храним логи, историю подключений или метаданные.\nНечего хранить — нечего защищать — нечего раскрывать.\n\n🕶 Приватность по умолчанию\nБез трекинга, аналитики, сторонних SDK и скрытых сборов данных.\n\n⚡ Неограниченная скорость и стабильность\nОптимизированные серверы без oversell и искусственных лимитов.\n\n🌍 Глобальная инфраструктура премиум-класса\nВыделенные серверы в 25+ странах, отобранные по критериям скорости,\nюрисдикции и надежности.\n\n📱 Полная экосистема устройств\niOS · Android · macOS · Windows\nОдин доступ — все ваши устройства.\n\n⸻\n\nДля кого Atlas Secure\n\n• Для тех, кто работает с чувствительной информацией\n• Для предпринимателей и инвесторов\n• Для путешествующих без цифровых компромиссов\n• Для тех, кто не обсуждает приватность — а просто её обеспечивает\n\n⸻\n\nAtlas Secure — это не VPN\n\nЭто частная цифровая инфраструктура.\nТихая. Незаметная. Надежная.\n\nВы подключены. Остальное — не ваше дело.",
        "privacy_policy": "Политика конфиденциальности",
        "privacy_policy_text": "Политика конфиденциальности\n\nМы храним только необходимые данные для предоставления услуг VPN.",
        
        # Поддержка
        "support_text": "🛡 Поддержка Atlas Secure\n\nПо вопросам доступа, оплаты или работы сервиса\nвы можете связаться с нами напрямую.\n\nКаждое обращение рассматривается индивидуально\nв приоритетном порядке.\n\nКонтакты:\nEmail: {email}\nTelegram: {telegram}",
        "change_language": "🌍 Изменить язык",
        
        # Администратор (без изменений)
        "admin_payment_notification": "💰 Новая оплата\nПользователь: @{username}\nTelegram ID: {telegram_id}\nТариф: {tariff} месяцев\nСтоимость: {price} руб.",
        "admin_approve": "Подтвердить",
        "admin_reject": "Отклонить",
    },
    "en": {
        "language_select": "Select language / Выберите язык / Tilni tanlang / Забони интихоб кунед",
        
        # Главное меню
        "welcome": "Welcome to Atlas Secure\n\nPrivate digital infrastructure of the highest class.\nCreated for those who don't discuss control — they have it.\n\nAtlas Secure is an environment where\nprivacy is embedded in architecture,\nstability — in engineering solutions,\nand predictability — in every connection.",
        "profile": "👤 My Profile",
        "buy_vpn": "🔐 Buy Access",
        "about": "ℹ️ About",
        "support": "🛡 Support",
        "back": "🔙 Back",
        
        # Выбор тарифа
        "select_tariff": "Select access period\n\nAtlas Secure operates on a limited access principle.\nEach period is a private configuration, not a mass tariff.\n\nEach access level includes:\n— individual VPN key assigned exclusively to you\n— zero-logs architecture without session and metadata storage\n— stable connection without limits and speed degradation\n— priority support",
        "tariff_button_1": "1 month\nTemporary access · {price} ₽",
        "tariff_button_3": "3 months\nStandard access · {price} ₽",
        "tariff_button_6": "6 months\nExtended access · {price} ₽",
        "tariff_button_12": "12 months\nPriority access · {price} ₽",
        
        # Выбор способа оплаты
        "select_payment": "Choose payment method.",
        "payment_test": "Service mode\nUnavailable",
        "payment_sbp": "SBP",
        
        # Оплата СБП
        "sbp_payment_text": "Financial access confirmation\n\nManual confirmation excludes automatic charges\nand allows creating a personal VPN access configuration\nassigned exclusively to you.\n\nAfter making the transfer, confirm payment.\n\n⸻\n\nTransfer details\n\nBank: {bank}\nAccount: {account}\nRecipient: {name}\n\nAmount to confirm: {price} ₽",
        "paid_button": "Confirm payment",
        
        # Ожидание подтверждения
        "payment_pending": "Confirmation in process\n\nPayment registered.\nVerification takes up to 5 minutes.\nAccess activation is performed automatically.",
        
        # Успешная активация
        "payment_approved": "✅ Access activated.\n\nYour personal VPN key:\n{vpn_key}\n\nValid until:\n{date}\n\nWe recommend saving the key in a secure place.",
        
        # Отклонение
        "payment_rejected": "❌ Payment not confirmed.\n\nIf you are sure you paid —\ncontact support.",
        
        # Профиль - активная подписка
        "profile_active": "👤 Access Profile\n\nStatus: Active\nValid until: {date}\n\nPersonal VPN key:\n{vpn_key}\n\nConnection is stable and protected.",
        "profile_renewal_hint": "\n\nAny repeated purchase automatically extends the subscription period.",
        
        # Профиль - платеж на проверке
        "profile_payment_check": "🕒 Payment under verification.\n\nThis is a standard security procedure.\nAfter confirmation, access will appear automatically.",
        
        # Напоминание об окончании подписки
        "subscription_expiring_reminder": "⏳ Access period expires soon.\n\n3 days left until your subscription expires.\n\nYou can renew access at any time —\na repeated purchase will automatically extend the period.",
        
        # Профиль - без подписки
        "no_subscription": "❌ No active access.\n\nAtlas Secure — private VPN service\nwith individual connection keys.\n\nYou can get access at any time.",
        
        # О сервисе
        "about_text": "What stands behind Atlas Secure\n\n🔐 Enterprise-grade cryptography (AES-256)\nThe same architecture used by financial and government systems.\n\n🧬 Zero-Logs philosophy\nWe fundamentally do not store logs, connection history, or metadata.\nNothing to store — nothing to protect — nothing to disclose.\n\n🕶 Privacy by default\nNo tracking, analytics, third-party SDKs, or hidden data collection.\n\n⚡ Unlimited speed and stability\nOptimized servers without oversell and artificial limits.\n\n🌍 Premium global infrastructure\nDedicated servers in 25+ countries, selected by speed,\njurisdiction, and reliability criteria.\n\n📱 Full device ecosystem\niOS · Android · macOS · Windows\nOne access — all your devices.\n\n⸻\n\nWho Atlas Secure is for\n\n• For those who work with sensitive information\n• For entrepreneurs and investors\n• For travelers without digital compromises\n• For those who don't discuss privacy — they just ensure it\n\n⸻\n\nAtlas Secure is not a VPN\n\nIt's private digital infrastructure.\nQuiet. Invisible. Reliable.\n\nYou're connected. The rest is not your concern.",
        "privacy_policy": "Privacy Policy",
        "privacy_policy_text": "Privacy Policy\n\nWe store only necessary data to provide VPN services.",
        
        # Поддержка
        "support_text": "🛡 Atlas Secure Support\n\nFor questions about access, payment, or service operation\nyou can contact us directly.\n\nEach request is considered individually\nwith priority.\n\nContacts:\nEmail: {email}\nTelegram: {telegram}",
        "change_language": "🌍 Change language",
        
        # Администратор
        "admin_payment_notification": "💰 New payment\nUser: @{username}\nTelegram ID: {telegram_id}\nTariff: {tariff} months\nPrice: {price} rub.",
        "admin_approve": "Approve",
        "admin_reject": "Reject",
    },
    "uz": {
        "language_select": "Tilni tanlang / Выберите язык / Choose language / Забони интихоб кунед",
        "welcome": "Atlas Secure-ga xush kelibsiz\n\nYuqori darajadagi shaxsiy raqamli infratuzilma.\nNazorat haqida gapirmaydiganlar uchun yaratilgan — ular nazoratga ega.\n\nAtlas Secure — bu muhit, bu yerda\nmaxfiylik arxitekturada qo'yilgan,\nbarqarorlik — muhandislik yechimlarida,\npishiqligi — har bir ulanishda.",
        "profile": "👤 Mening profilim",
        "buy_vpn": "🔐 Kirishni sotib olish",
        "about": "ℹ️ Xizmat haqida",
        "support": "🛡 Qo'llab-quvvatlash",
        "back": "🔙 Orqaga",
        "select_tariff": "Kirish muddatini tanlang\n\nAtlas Secure cheklangan kirish printsipi asosida ishlaydi.\nHar bir davr — bu shaxsiy konfiguratsiya, emas ommaviy tarif.\n\nHar bir kirish darajasi quyidagilarni o'z ichiga oladi:\n— sizga xos qilingan individual VPN kalit\n— sessiya va metama'lumotlarni saqlashsiz zero-logs arxitektura\n— cheklovlar va tezlik pasayishi bo'lmagan barqaror ulanish\n— ustuvor qo'llab-quvvatlash",
        "tariff_button_1": "1 oy\nVaqtinchalik kirish · {price} so'm",
        "tariff_button_3": "3 oy\nStandart kirish · {price} so'm",
        "tariff_button_6": "6 oy\nKengaytirilgan kirish · {price} so'm",
        "tariff_button_12": "12 oy\nUstuvor kirish · {price} so'm",
        "select_payment": "To'lov usulini tanlang.",
        "payment_test": "Xizmat rejimi\nMavjud emas",
        "payment_sbp": "SBP",
        "sbp_payment_text": "Kirishni moliyaviy tasdiqlash\n\nQo'lda tasdiqlash avtomatik to'lovlarni istisno qiladi\nva sizga xos qilingan shaxsiy VPN kirish konfiguratsiyasini yaratishga imkon beradi.\n\nO'tkazmadan keyin to'lovni tasdiqlang.\n\n⸻\n\nO'tkazma ma'lumotlari\n\nBank: {bank}\nHisob: {account}\nQabul qiluvchi: {name}\n\nTasdiqlash uchun summa: {price} so'm",
        "paid_button": "To'lovni tasdiqlash",
        "payment_pending": "Tasdiqlash jarayonda\n\nTo'lov ro'yxatga olingan.\nTekshiruv 5 minutgacha davom etadi.\nKirish faollashtirish avtomatik ravishda amalga oshiriladi.",
        "payment_approved": "✅ Kirish faollashtirildi.\n\nSizning shaxsiy VPN kalitingiz:\n{vpn_key}\n\nAmal qilish muddati:\n{date} gacha\n\nKalitni xavfsiz joyda saqlashni tavsiya etamiz.",
        "payment_rejected": "❌ To'lov tasdiqlanmadi.\n\nAgar to'laganingizga ishonchingiz komil bo'lsa — qo'llab-quvvatlashga murojaat qiling.",
        "profile_active": "👤 Kirish profili\n\nHolati: Faol\nAmal qilish muddati: {date} gacha\n\nShaxsiy VPN kalit:\n{vpn_key}\n\nUlanish barqaror va himoyalangan.",
        "profile_renewal_hint": "\n\nHar qanday takroriy xarid obuna muddatini avtomatik ravishda uzaytiradi.",
        "profile_payment_check": "🕒 To'lov tekshiruvda.\n\nBu standart xavfsizlik protsedurasi.\nTasdiqlanganidan keyin kirish avtomatik ravishda paydo bo'ladi.",
        "subscription_expiring_reminder": "⏳ Kirish muddati yaqin orada tugaydi.\n\nObunangiz tugashiga 3 kun qoldi.\n\nSiz istalgan vaqtda kirishni uzaytirishingiz mumkin —\ntakroriy xarid avtomatik ravishda muddatni uzaytiradi.",
        "no_subscription": "❌ Faol kirish yo'q.\n\nAtlas Secure — individual ulanish kalitlari bilan maxfiy VPN xizmati.\n\nSiz istalgan vaqtda kirish olishingiz mumkin.",
        "about_text": "Atlas Secure orqasida nima bor\n\n🔐 Enterprise darajasidagi kriptografiya (AES-256)\nMoliyaviy va davlat tizimlari ishlatadigan xuddi shu arxitektura.\n\n🧬 Zero-Logs falsafasi\nBiz asosiy ravishda jurnallarni, ulanishlar tarixini yoki metama'lumotlarni saqlamaymiz.\nSaqlash kerak bo'lgan narsa yo'q — himoya qilish kerak bo'lgan narsa yo'q — oshkor qilish kerak bo'lgan narsa yo'q.\n\n🕶 Sukut bo'yicha maxfiylik\nKuzatish, analitika, uchinchi tomon SDK'lari va yashirin ma'lumotlar to'plami yo'q.\n\n⚡ Cheksiz tezlik va barqarorlik\nOversell va sun'iy cheklovlarsiz optimallashtirilgan serverlar.\n\n🌍 Premium global infratuzilma\nTezlik, yurisdiktsiya va ishonchlilik mezonlari bo'yicha tanlangan\n25+ mamlakatdagi ajratilgan serverlar.\n\n📱 To'liq qurilmalar ekotizimi\niOS · Android · macOS · Windows\nBir kirish — barcha qurilmalaringiz.\n\n⸻\n\nAtlas Secure kimlar uchun\n\n• Sezgir ma'lumotlar bilan ishlaydiganlar uchun\n• Tadbirkorlar va investorlar uchun\n• Raqamli kompromisslarsiz sayohat qiladiganlar uchun\n• Maxfiylik haqida gapirmaydiganlar uchun — ular uni ta'minlaydi\n\n⸻\n\nAtlas Secure VPN emas\n\nBu shaxsiy raqamli infratuzilma.\nJimsiz. Ko'rinmas. Ishonchli.\n\nSiz ulangan siz. Qolgani — sizning ishingiz emas.",
        "privacy_policy": "Maxfiylik siyosati",
        "privacy_policy_text": "Maxfiylik siyosati\n\nBiz VPN xizmatlarini taqdim etish uchun faqat zarur ma'lumotlarni saqlaymiz.",
        "support_text": "🛡 Atlas Secure qo'llab-quvvatlash\n\nKirish, to'lov yoki xizmat ishlashi haqida savollar bo'yicha\nsiz biz bilan to'g'ridan-to'g'ri bog'lanishingiz mumkin.\n\nHar bir murojaat individual ravishda ko'rib chiqiladi\nustuvor tartibda.\n\nKontaktlar:\nEmail: {email}\nTelegram: {telegram}",
        "change_language": "🌍 Tilni o'zgartirish",
        "admin_payment_notification": "💰 Yangi to'lov\nFoydalanuvchi: @{username}\nTelegram ID: {telegram_id}\nTarif: {tariff} oy\nNarx: {price} so'm",
        "admin_approve": "Tasdiqlash",
        "admin_reject": "Rad etish",
    },
    "tj": {
        "language_select": "Забони интихоб кунед / Выберите язык / Choose language / Tilni tanlang",
        "welcome": "Хуш омадед ба Atlas Secure\n\nИнфрасохтори рақамии хусусии дараҷаи олӣ.\nБарои касоне эҷод шудааст, ки дар бораи назорат суҳбат намекунанд — онҳо назорат доранд.\n\nAtlas Secure — ин муҳитест, ки дар он\nмахфият дар меъморӣ қарор дорад,\nустуворӣ — дар ҳалли муҳандисӣ,\nпешбинии — дар ҳар як пайванд.",
        "profile": "👤 Профили ман",
        "buy_vpn": "🔐 Хариди дастрасӣ",
        "about": "ℹ️ Дар бораи хизмат",
        "support": "🛡 Дастгирӣ",
        "back": "🔙 Бозгашт",
        "select_tariff": "Муддати дастрасиро интихоб кунед\n\nAtlas Secure ба принсипи дастрасии маҳдуд кор мекунад.\nҲар як давра — ин конфигуратсияи хусусӣ аст, на тарифи оммавӣ.\n\nҲар як сатҳи дастрасӣ дорои:\n— калиди VPN-и шахсӣ, ки хусусан ба шумо закреп шудааст\n— меъмории zero-logs бе нигоҳдории сессияҳо ва метамаълумот\n— пайванди устувор бе маҳдудияту коҳиши суръат\n— дастгирии афзалиятнок",
        "tariff_button_1": "1 моҳ\nДастрасии муваққатӣ · {price} сом.",
        "tariff_button_3": "3 моҳ\nДастрасии стандартӣ · {price} сом.",
        "tariff_button_6": "6 моҳ\nДастрасии васеъ · {price} сом.",
        "tariff_button_12": "12 моҳ\nДастрасии афзалиятнок · {price} сом.",
        "select_payment": "Усули пардохтро интихоб кунед.",
        "payment_test": "Реҷаи хизматӣ\nДастрас нест",
        "payment_sbp": "СБП",
        "sbp_payment_text": "Тасдиқи молиявии дастрасӣ\n\nТасдиқи дастӣ пардохтҳои худкорро истисно мекунад\nва имкон медиҳад конфигуратсияи шахсии дастрасии VPN-ро\nэҷод кунед, ки хусусан ба шумо закреп шудааст.\n\nПас аз интиқол, пардохтро тасдиқ кунед.\n\n⸻\n\nМаълумоти интиқол\n\nБонк: {bank}\nҲисоб: {account}\nҚабулкунанда: {name}\n\nМаблағи тасдиқ: {price} сом.",
        "paid_button": "Пардохтро тасдиқ кардан",
        "payment_pending": "Тасдиқ дар раванд аст\n\nПардохт ба қайд гирифта шуд.\nСанҷиш то 5 дақиқа давом мекунад.\nФаъолсозии дастрасӣ ба таври худкор иҷро мешавад.",
        "payment_approved": "✅ Дастрасӣ фаъол шуд.\n\nКалиди VPN-и шахсии шумо:\n{vpn_key}\n\nМуддати амал:\nто {date}\n\nТавсия медиҳем, ки калидро дар ҷойи бехатар нигоҳ доред.",
        "payment_rejected": "❌ Пардохт тасдиқ нашуд.\n\nАгар мӯътақид ҳастед, ки пардохт кардед — ба дастгирӣ муроҷиат кунед.",
        "profile_active": "👤 Профили дастрасӣ\n\nҲолат: Фаъол\nМуддати амал: то {date}\n\nКалиди VPN-и шахсӣ:\n{vpn_key}\n\nПайванд устувор ва ҳимояшуда аст.",
        "profile_renewal_hint": "\n\nҲар як хариди такрори обунаро ба таври худкор васеъ мекунад.",
        "profile_payment_check": "🕒 Пардохт дар санҷиш аст.\n\nИн процедураи стандартии амният аст.\nПас аз тасдиқ, дастрасӣ худкор пайдо мешавад.",
        "subscription_expiring_reminder": "⏳ Муддати дастрасӣ ба зудӣ анҷом мешавад.\n\nТо анҷоми обунаи шумо 3 рӯз боқӣ мондааст.\n\nШумо метавонед дар ҳар вақт дастрасиро васеъ кунед —\nхариди такрориҳо муддатро ба таври худкор васеъ мекунад.",
        "no_subscription": "❌ Дастрасии фаъол нест.\n\nAtlas Secure — хизмати махфии VPN\nбо калидҳои пайванди шахсӣ.\n\nШумо метавонед дар ҳар вақт дастрасӣ гиред.",
        "about_text": "Чӣ дар пушти Atlas Secure аст\n\n🔐 Криптографияи сатҳи enterprise (AES-256)\nХуди ҳамин меъморӣ, ки системаҳои молиявӣ ва давлатӣ истифода мебаранд.\n\n🧬 Фалсафаи Zero-Logs\nМо асосан журналҳо, таърихи пайвандҳо ё метамаълумотро нигоҳ намедорем.\nЧизе барои нигоҳ доштан нест — чизе барои ҳимоя кардан нест — чизе барои ошкор кардан нест.\n\n🕶 Махфият ба таври сукут\nБе пайгирӣ, аналитика, SDK-ҳои тарафи сеюм ва ҷамъоварии пинҳонии маълумот.\n\n⚡ Суръат ва устувории номаҳдуд\nСерверҳои оптимизатсияшуда бе oversell ва маҳдудияти сунъӣ.\n\n🌍 Инфрасохтори глобалии премиум\nСерверҳои бахшидашуда дар 25+ кишвар, ки бо меъёрҳои суръат,\nюрисдикция ва эътимоднокӣ интихоб шудаанд.\n\n📱 Экосистемаи пурраи дастгоҳҳо\niOS · Android · macOS · Windows\nЯк дастрасӣ — ҳамаи дастгоҳҳои шумо.\n\n⸻\n\nAtlas Secure барои кӣ\n\n• Барои касоне, ки бо маълумоти ҳассос кор мекунанд\n• Барои соҳибкорон ва сармоягузорон\n• Барои саёҳаткунандагони бе компромиссҳои рақамӣ\n• Барои касоне, ки дар бораи махфият суҳбат намекунанд — онҳо танҳо онро таъмин мекунанд\n\n⸻\n\nAtlas Secure VPN нест\n\nИн инфрасохтори рақамии хусусӣ аст.\nОром. Намоён нест. Эътимоднок.\n\nШумо пайванд шудед. Боқимонда — ба шумо тааллуқ надорад.",
        "privacy_policy": "Сиёсати махфият",
        "privacy_policy_text": "Сиёсати махфият\n\nМо танҳо маълумоти зарурӣ барои таъмини хидматҳои VPN нигоҳ медорем.",
        "support_text": "🛡 Дастгирии Atlas Secure\n\nДар бораи дастрасӣ, пардохт ё амали хизмат саволҳо\nшумо метавонед бо мо бевосита тавонос шавед.\n\nҲар як мурожаат ба таври шахсӣ баррасӣ карда мешавад\nдар тартиби афзалиятнок.\n\nКонтактҳо:\nEmail: {email}\nTelegram: {telegram}",
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
