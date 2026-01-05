from typing import Dict

# Все тексты для локализации
TEXTS: Dict[str, Dict[str, str]] = {
    "ru": {
        "language_select": "Добро пожаловать в Atlas Secure\n\nЧастный защищённый доступ\nбез сложных настроек.\n\nПожалуйста, выберите язык:",
        
        # Главное меню
        "welcome": "Добро пожаловать в Atlas Secure\n\nЧастная цифровая инфраструктура высшего класса.\nСоздана для тех, кто не обсуждает контроль — он у них есть.\n\nAtlas Secure — это среда, где\nприватность заложена в архитектуре,\nстабильность — в инженерных решениях,\nа предсказуемость — в каждом соединении.",
        "profile": "👤 Мой профиль",
        "buy_vpn": "🔐 Купить доступ",
        "about": "ℹ️ О сервисе",
        "support": "🛡 Поддержка",
        "instruction": "📖 Инструкция",
        "instruction_device_ios": "📱 iOS",
        "instruction_device_android": "🤖 Android",
        "instruction_device_desktop": "💻 Windows / macOS",
        "back": "← Назад",
        "copy_key": "📋 Скопировать ключ",
        "renew_subscription": "🔁 Продлить доступ",
        "no_active_subscription": "Активная подписка не найдена.",
        "subscription_history": "📄 История подписок",
        "subscription_history_empty": "История подписок пуста",
        "subscription_history_action_purchase": "Покупка",
        "subscription_history_action_renewal": "Продление",
        "subscription_history_action_reissue": "Выдача нового ключа",
        "subscription_history_action_manual_reissue": "Перевыпуск ключа",
        
        # Выбор тарифа
        "select_tariff": "🕒 Выберите срок подписки\n\nAtlas Secure — это стабильный доступ,\nкоторый просто работает.\n\nВ любой подписке:\n🔑 Персональный ключ — только для вас\n⚡️ Стабильная скорость без ограничений\n📱💻 Работает на всех устройствах\n💬 Поддержка в Telegram в любой момент\n\nЧем дольше срок — тем меньше вы думаете\nо продлении и проблемах с доступом.\n\nБольшинство пользователей выбирают подписку от 3 месяцев.",
        "enter_promo_button": "🎟 Ввести промокод",
        "enter_promo_text": "Введите промокод:",
        "invalid_promo": "❌ Неверный промокод",
        "promo_applied": "🎁 Промокод применён. Скидка уже учтена в цене.",
        "promo_discount_label": "🎟 Промокод",
        "tariff_button_1": "1 месяц · Для знакомства · 149 ₽",
        "tariff_button_3": "3 месяца · Чаще всего выбирают · 399 ₽ ⭐",
        "tariff_button_6": "6 месяцев · Реже продлевать · 599 ₽",
        "tariff_button_12": "12 месяцев · Не думать о доступе · 899 ₽",
        
        # Выбор способа оплаты
        "select_payment": "Выберите способ оплаты.",
        "payment_test": "Служебный режим Недоступно",
        "payment_sbp": "СБП",
        
        # Оплата СБП
        "sbp_payment_text": "После выполнения перевода подтвердите оплату.\n\n⸻\n\nРеквизиты для перевода\n\nБанк: Ozon\nСчёт карты: 2204321075030551\n\nСумма к подтверждению: {amount} ₽",
        "paid_button": "Подтвердить оплату",
        
        # Продление подписки
        "renewal_payment_text": "Оплатите продление подписки.\n\nПродление будет выполнено\nна тот же период, что и текущий доступ.",
        "renewal_pay_button": "💳 Оплатить",
        
        # Ожидание подтверждения
        "payment_pending": "Подтверждение в процессе\n\nПлатёж зафиксирован.\nВерификация занимает до 5 минут.\nАктивация доступа выполняется автоматически.",
        
        # Успешная активация
        "payment_approved": "✅ Доступ активирован.\n\nВаш персональный VPN-ключ:\n{vpn_key}\n\nСрок действия:\nдо {date}\n\nРекомендуем сохранить ключ в надёжном месте.",
        
        # Отклонение
        "payment_rejected": "❌ Платёж не подтверждён.\n\nЕсли вы уверены, что оплатили —\nобратитесь в поддержку.",
        
        # Профиль - активная подписка
        "profile_active": "👤 Профиль доступа\n\nСтатус доступа: Активен\nДоступ оплачен до {date}\n\nВы подключены. Доступ работает стабильно.\n\nПерсональный ключ доступа\nИспользуется для подключения в приложении Outline.\nПодключение сохраняется, пока действует доступ.\n\n{vpn_key}\n\nПри продлении выбранный срок\nдобавляется к текущему доступу автоматически.\n\nДо окончания срока вы можете\nне возвращаться к настройкам и оплате.",
        "profile_renewal_hint": "",
        
        # Профиль - платеж на проверке
        "profile_payment_check": "🕒 Платёж на проверке.\n\nЭто стандартная процедура безопасности.\nПосле подтверждения доступ появится автоматически.",
        
        # Напоминание об окончании подписки
        "subscription_expiring_reminder": "⏳ Срок доступа скоро истекает.\n\nДо окончания вашей подписки осталось 3 дня.\n\nВы можете продлить доступ в любое время —\nповторная покупка автоматически увеличит срок действия.",
        
        # Умные напоминания - админ-доступ
        "reminder_admin_1day_6h": "⏳ Временный доступ Atlas Secure завершается через 6 часов.\n\nРекомендуем оформить полноценную подписку,\nчтобы сохранить стабильный доступ без перерыва.",
        "reminder_admin_7days_24h": "⏳ Временный доступ Atlas Secure завершается через 24 часа.\n\nРекомендуем оформить подписку на 1 месяц\nдля непрерывного и стабильного подключения.",
        
        # Умные напоминания - оплаченные тарифы
        "reminder_paid_3d": "⏳ Срок вашего доступа Atlas Secure истекает через 3 дня.\n\nВы можете продлить подписку заранее,\nчтобы избежать перерыва в соединении.",
        "reminder_paid_24h": "⏳ Срок вашего доступа Atlas Secure истекает через 24 часа.\n\nРекомендуем продлить подписку заранее,\nчтобы сохранить непрерывное соединение.",
        "reminder_paid_3h": "⏳ Срок вашего доступа Atlas Secure истекает через 3 часа.\n\nПродлите подписку сейчас,\nчтобы избежать перерыва в соединении.",
        
        # Приветственная скидка
        "welcome_discount_label": "🎁 Приветственная скидка",
        "subscribe_1_month_button": "🔐 Подписка на 1 месяц",
        "personal_discount_label": "🎯 Персональная скидка {percent}%",
        "vip_discount_label": "👑 VIP-доступ",
        "vip_access_button": "👑 Улучшить уровень доступа",
        "vip_access_text": "👑 VIP-доступ Atlas Secure\n\nVIP — это расширенный уровень обслуживания\nдля тех, кому важны стабильность и приоритет.\n\nЧто даёт VIP:\n⚡️ Приоритетную инфраструктуру и минимальную задержку\n🛠 Персональную конфигурацию доступа\n💬 Приоритетную поддержку без ожиданий\n🚀 Ранний доступ к обновлениям\n\nVIP подойдёт, если вы:\n• используете доступ ежедневно\n• не хотите разбираться в настройках\n• цените предсказуемую работу\n\nСтоимость:\n1 990 ₽ / месяц\nили 9 990 ₽ / 6 месяцев\n\nVIP подключается при активной подписке.\nОставьте запрос — мы всё сделаем за вас.\n\nVIP — когда доступ просто есть,\nи вы о нём не думаете.",
        "vip_status_badge": "👑 VIP-статус активен",
        "vip_status_active": "👑 Ваш VIP-статус активен",
        "contact_manager_button": "💬 Подключить VIP-доступ",
        
        # Профиль - без подписки
        "no_subscription": "❌ Активного доступа нет.\n\nAtlas Secure — приватный VPN-сервис\nс индивидуальными ключами подключения.\n\nВы можете оформить доступ в любое время.",
        
        # О сервисе
        "about_text": "ℹ️ О сервисе Atlas Secure\n\nAtlas Secure — это частный защищённый доступ\nк интернету, построенный для стабильной\nи предсказуемой работы без лишнего внимания\nк настройкам и деталям.\n\nМы проектируем инфраструктуру так,\nчтобы вы просто пользовались доступом,\nа не думали о том, как он устроен.\n\nЧто это даёт на практике:\n\n🔐 Приватность по умолчанию\nМы не отслеживаем действия пользователей,\nне храним историю подключений\nи не собираем метаданные.\n\n⚡️ Стабильность без ограничений\nОптимизированные серверы без перегрузки\nобеспечивают ровную и предсказуемую работу.\n\n🌍 Надёжная инфраструктура\nВыделенные серверы в разных регионах,\nотобранные по скорости и надёжности.\n\n📱 Все ваши устройства\nОдин доступ работает на iOS, Android,\nmacOS и Windows без доплат и ограничений.\n\nAtlas Secure подходит, если вы:\n• работаете с важной информацией\n• цените стабильность и контроль\n• не хотите разбираться в технологиях\n• предпочитаете, чтобы сервис просто работал\n\nAtlas Secure — это не про функции.\nЭто про спокойствие и предсказуемость.\n\nВы подключены.\nОстальное — работает в фоне.",
        "privacy_policy": "Политика конфиденциальности",
        "privacy_policy_text": "🔐 Политика конфиденциальности Atlas Secure\n\nAtlas Secure построен на принципе\nминимизации данных.\n\nМы не собираем и не храним информацию,\nкоторая не требуется для работы сервиса.\n\nЧто мы НЕ храним:\n• историю подключений\n• IP-адреса и сетевой трафик\n• DNS-запросы\n• данные о посещаемых ресурсах\n• метаданные пользовательской активности\n\nАрхитектура сервиса реализована\nпо принципу Zero-Logs.\n\nЧто может обрабатываться:\n• статус доступа\n• срок действия подписки\n• технический идентификатор ключа\n\nЭти данные не связаны\nс вашей сетевой активностью.\n\nПлатежи:\nAtlas Secure не обрабатывает\nи не хранит платёжные данные.\nОплата проходит через\nбанковские и платёжные системы\nвне нашей инфраструктуры.\n\nПередача данных:\nМы не передаём данные третьим лицам\nи не используем трекеры,\nаналитику или рекламные SDK.\n\nПоддержка:\nМы обрабатываем только ту информацию,\nкоторую вы добровольно предоставляете\nдля решения конкретного запроса.\n\nAtlas Secure.\nКонфиденциальность заложена\nв архитектуре сервиса.",
        "service_status": "📜 Статус сервиса",
        "service_status_text": "📊 Статус сервиса Atlas Secure\n\nТекущий статус: 🟢 Сервис работает стабильно\n\nВсе основные компоненты функционируют\nв штатном режиме:\n• доступ активен\n• выдача ключей работает\n• поддержка на связи\n\nAtlas Secure построен как частная\nцифровая инфраструктура\nс приоритетом на стабильность\nи предсказуемую работу.\n\nНаши принципы:\n• целевой аптайм — 99.9%\n• плановые работы проводятся заранее\n• критические инциденты решаются\n  в приоритетном порядке\n• потеря данных исключена архитектурно\n\nВ случае технических работ\nили изменений пользователи\nуведомляются заранее через бот.\n\nПоследнее обновление статуса:\nавтоматически",
        "incident_banner": "⚠️ Ведутся технические работы",
        "incident_status_warning": "\n\n⚠️ ВНИМАНИЕ: Режим инцидента активен\n{incident_text}",
        "admin_incident_title": "🚨 Инцидент",
        "admin_incident_status_on": "🟢 Режим инцидента активен",
        "admin_incident_status_off": "⚪ Режим инцидента выключен",
        "admin_incident_enable": "✅ Включить",
        "admin_incident_disable": "❌ Выключить",
        "admin_incident_edit_text": "📝 Изменить текст",
        "admin_incident_text_prompt": "Введите текст инцидента (или отправьте /cancel для отмены):",
        
        # Поддержка
        "support_text": "🛡 Поддержка Atlas Secure\n\nЕсли у вас есть вопросы по доступу,\nоплате или работе сервиса —\nнапишите нам напрямую.\n\nМы отвечаем вручную\nи рассматриваем обращения\nв приоритетном порядке.\n\nВы можете обратиться в поддержку\nв любой момент — мы рядом.",
        "change_language": "🌍 Изменить язык",
        
        # Инструкция
        "instruction_text": "🔌 Подключение\n\nДоступ работает через персональный ключ.\nНастройка занимает не более 1 минуты.\n\n1️⃣ 🔑 Получите ключ доступа\nКлюч появляется автоматически после активации подписки.\n\n2️⃣ 📥 Установите приложение Outline\nСкачайте приложение из официального магазина\nдля вашей операционной системы.\n\n3️⃣ ➕ Подключитесь\nОткройте Outline, нажмите «+» и вставьте ключ.\nСоединение включится автоматически.\n\n✅ После подключения ничего настраивать не нужно.\nДоступ будет работать, пока активна подписка.",
        
        # Администратор (без изменений)
        "admin_payment_notification": "💰 Новая оплата\nПользователь: @{username}\nTelegram ID: {telegram_id}\nТариф: {tariff} месяцев\nСтоимость: {price} ₽",
        "admin_approve": "Подтвердить",
        "admin_reject": "Отклонить",
        "admin_grant_access": "🟢 Выдать доступ",
        "admin_revoke_access": "🔴 Лишить доступа",
        "admin_grant_days_prompt": "Выберите срок доступа:",
        "admin_grant_days_1": "1 день",
        "admin_grant_days_7": "7 дней",
        "admin_grant_days_14": "14 дней",
        "admin_grant_success": (
            "✅ Доступ выдан на {days} дней.\n\n"
            "Доступ активирован администратором."
        ),
        "admin_grant_fail_no_keys": "❌ Нет свободных VPN-ключей",
        "admin_revoke_success": (
            "✅ Доступ отозван.\n\n"
            "Пользователь уведомлён."
        ),
        "admin_revoke_fail_no_sub": "❌ У пользователя нет активной подписки",
        "admin_grant_user_notification": (
            "✅ Вам предоставлен доступ к Atlas Secure на {days} дней.\n"
            "VPN-ключ: {vpn_key}\n"
            "Срок действия: до {date}"
        ),
        "admin_revoke_user_notification": "⛔ Ваш доступ к Atlas Secure был отозван администратором.",

    },
    "en": {
        "language_select": "Select language / Выберите язык / Tilni tanlang / Забони интихоб кунед",
        
        # Главное меню
        "welcome": "Welcome to Atlas Secure\n\nPrivate digital infrastructure of the highest class.\nCreated for those who don't discuss control — they have it.\n\nAtlas Secure is an environment where\nprivacy is embedded in architecture,\nstability — in engineering solutions,\nand predictability — in every connection.",
        "profile": "👤 My Profile",
        "buy_vpn": "🔐 Buy Access",
        "about": "ℹ️ About",
        "support": "🛡 Support",
        "instruction": "📖 Instruction",
        "instruction_device_ios": "📱 iOS",
        "instruction_device_android": "🤖 Android",
        "instruction_device_desktop": "💻 Windows / macOS",
        "back": "🔙 Back",
        "copy_key": "📋 Copy Key",
        "renew_subscription": "🔁 Renew for the Same Period",
        "no_active_subscription": "Active subscription not found.",
        "subscription_history": "🧾 Subscription History",
        "subscription_history_empty": "Subscription history is empty",
        "subscription_history_action_purchase": "Purchase",
        "subscription_history_action_renewal": "Renewal",
        "subscription_history_action_reissue": "Key reissue",
        "subscription_history_action_manual_reissue": "Manual key reissue",
        
        # Выбор тарифа
        "select_tariff": "Select access period\n\nAtlas Secure operates on a limited access principle.\nEach period is a private configuration, not a mass tariff.\n\nEach access level includes:\n— individual VPN key assigned exclusively to you\n— zero-logs architecture without session and metadata storage\n— stable connection without limits and speed degradation\n— priority support",
        "tariff_button_1": "1 month Temporary Access · 299 ₽",
        "tariff_button_3": "3 months Standard Access · 799 ₽",
        "tariff_button_6": "6 months Extended Access · 1 199 ₽",
        "tariff_button_12": "12 months Priority Access · 1 699 ₽",
        
        # Выбор способа оплаты
        "select_payment": "Choose payment method.",
        "payment_test": "Service mode Unavailable",
        "payment_sbp": "SBP",
        
        # Продление подписки
        "renewal_payment_text": "Obuna yangilanishi uchun to'lang.\n\nYangilanish joriy davr bilan bir xil muddatga amalga oshiriladi.",
        "renewal_pay_button": "💳 To'lash",
        
        # Продление подписки
        "renewal_payment_text": "Pay for subscription renewal.\n\nRenewal will be performed\nfor the same period as current access.",
        "renewal_pay_button": "💳 Pay",
        
        # Оплата СБП
        "sbp_payment_text": "After making the transfer, confirm payment.\n\n⸻\n\nTransfer details\n\nBank: Ozon\nCard account: 2204321075030551\n\nAmount to confirm: {amount} ₽",
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
        
        # Умные напоминания - админ-доступ
        "reminder_admin_1day_6h": "⏳ Temporary Atlas Secure access expires in 6 hours.\n\nWe recommend purchasing a full subscription\nto maintain stable access without interruption.",
        "reminder_admin_7days_24h": "⏳ Temporary Atlas Secure access expires in 24 hours.\n\nWe recommend purchasing a 1-month subscription\nfor continuous and stable connection.",
        
        # Умные напоминания - оплаченные тарифы
        "reminder_paid_3d": "⏳ Your Atlas Secure access expires in 3 days.\n\nYou can renew your subscription in advance\nto avoid connection interruption.",
        "reminder_paid_24h": "⏳ Your Atlas Secure access expires in 24 hours.\n\nWe recommend renewing your subscription in advance\nto maintain continuous connection.",
        "reminder_paid_3h": "⏳ Your Atlas Secure access expires in 3 hours.\n\nRenew your subscription now\nto avoid connection interruption.",
        
        # Приветственная скидка
        "welcome_discount_label": "🎁 Welcome Discount",
        "subscribe_1_month_button": "🔐 1 Month Subscription",
        "personal_discount_label": "🎯 Personal Discount {percent}%",
        "vip_discount_label": "👑 VIP Access",
        "vip_access_button": "👑 VIP Access",
        "vip_access_text": "VIP Access Atlas Secure\n\nVIP is an access level\nprovided selectively.\n\nIt is not sold and is considered individually\nbased on trust and interaction history\nwith the Atlas Secure infrastructure.\n\n⸻\n\nLevel Privileges\n\n— priority infrastructure and minimal latency\n— personal VPN access configuration\n— extended support and direct contact\n— discretionary terms for renewal\n— early access to infrastructure changes\n— closed communication on key updates\n\nAll decisions are made manually.\nAutomatic mechanisms are not used.",
        "vip_status_badge": "👑 VIP status active",
        "vip_status_active": "👑 Your VIP status is active",
        "contact_manager_button": "💬 Contact Manager",
        
        # Профиль - без подписки
        "no_subscription": "❌ No active access.\n\nAtlas Secure — private VPN service\nwith individual connection keys.\n\nYou can get access at any time.",
        
        # О сервисе
        "about_text": "What stands behind Atlas Secure\n\n🔐 Enterprise-grade cryptography (AES-256)\nThe same architecture used by financial and government systems.\n\n🧬 Zero-Logs philosophy\nWe fundamentally do not store logs, connection history, or metadata.\nNothing to store — nothing to protect — nothing to disclose.\n\n🕶 Privacy by default\nNo tracking, analytics, third-party SDKs, or hidden data collection.\n\n⚡ Unlimited speed and stability\nOptimized servers without oversell and artificial limits.\n\n🌍 Premium global infrastructure\nDedicated servers in 25+ countries, selected by speed,\njurisdiction, and reliability criteria.\n\n📱 Full device ecosystem\niOS · Android · macOS · Windows\nOne access — all your devices.\n\n⸻\n\nWho Atlas Secure is for\n\n• For those who work with sensitive information\n• For entrepreneurs and investors\n• For travelers without digital compromises\n• For those who don't discuss privacy — they just ensure it\n\n⸻\n\nAtlas Secure is not a VPN\n\nIt's private digital infrastructure.\nQuiet. Invisible. Reliable.\n\nYou're connected. The rest is not your concern.",
        "privacy_policy": "Privacy Policy",
        "privacy_policy_text": "Atlas Secure Privacy Policy\n\nAtlas Secure uses the data minimization principle.\nWe do not collect information that is not required for service operation.\n\n⸻\n\nWhat we do not store\n\n— connection history\n— IP addresses and traffic\n— DNS queries\n— data about visited resources\n— activity metadata\n\nZero-Logs architecture is used.\n\n⸻\n\nWhat may be processed\n\n— access status and validity period\n— technical VPN key identifier\n\nThis data is not linked to user activity.\n\n⸻\n\nPayments\n\nPayment data is not processed or stored by Atlas Secure.\nPayment is processed through banking channels outside our infrastructure.\n\n⸻\n\nData sharing\n\nWe do not share data with third parties\nand do not use trackers, analytics, or advertising SDKs.\n\n⸻\n\nSupport\n\nOnly information voluntarily provided by the user\nfor resolving a specific request is processed.\n\n⸻\n\nAtlas Secure\nPrivacy is the foundation of architecture.",
        
        # Поддержка
        "support_text": "🛡 Atlas Secure Support\n\nFor questions about access, payment, or service operation\nyou can contact us directly.\n\nEach request is considered individually\nwith priority.\n\nContacts:\nEmail: 000n999@duck.com\nTelegram: @asc_support",
        "change_language": "🌍 Change language",
        
        # Инструкция
        "instruction_text": "Connecting to Outline\n\nAccess is provided through a personal key.\n\n1. Access Key\nIssued after Atlas Secure activation.\n\n2. Application\nInstall Outline VPN from the official app store\nfor your operating system.\n\n3. Connection\nOpen Outline, press (＋) and enter the issued key.\nConnection is activated automatically.",
        
        # Администратор
        "admin_payment_notification": "💰 New payment\nUser: @{username}\nTelegram ID: {telegram_id}\nTariff: {tariff} months\nPrice: {price} ₽",
        "admin_approve": "Approve",
        "admin_reject": "Reject",
        "admin_grant_access": "🟢 Grant Access",
        "admin_revoke_access": "🔴 Revoke Access",
        "admin_grant_days_prompt": "Select access period:",
        "admin_grant_days_1": "1 day",
        "admin_grant_days_7": "7 days",
        "admin_grant_days_14": "14 days",
        "admin_grant_success": (
            "✅ Access granted for {days} days.\n\n"
            "Access activated by administrator."
        ),
        "admin_grant_fail_no_keys": "❌ No free VPN keys available",
        "admin_revoke_success": (
            "✅ Access revoked.\n\n"
            "User notified."
        ),
        "admin_revoke_fail_no_sub": "❌ User has no active subscription",
        "admin_grant_user_notification": (
            "✅ You have been granted access to Atlas Secure for {days} days.\n"
            "VPN key: {vpn_key}\n"
            "Expires: {date}"
        ),
        "admin_revoke_user_notification": "⛔ Your access to Atlas Secure has been revoked by the administrator.",

        "incident_banner": "⚠️ Technical work in progress",
        "incident_status_warning": "\n\n⚠️ WARNING: Incident mode active\n{incident_text}",
        "admin_incident_title": "🚨 Incident",
        "admin_incident_status_on": "🟢 Incident mode active",
        "admin_incident_status_off": "⚪ Incident mode off",
        "admin_incident_enable": "✅ Enable",
        "admin_incident_disable": "❌ Disable",
        "admin_incident_edit_text": "📝 Edit text",
        "admin_incident_text_prompt": "Enter incident text (or send /cancel to cancel):",
    },
    "uz": {
        "language_select": "Tilni tanlang / Выберите язык / Choose language / Забони интихоб кунед",
        "welcome": "Atlas Secure-ga xush kelibsiz\n\nYuqori darajadagi shaxsiy raqamli infratuzilma.\nNazorat haqida gapirmaydiganlar uchun yaratilgan — ular nazoratga ega.\n\nAtlas Secure — bu muhit, bu yerda\nmaxfiylik arxitekturada qo'yilgan,\nbarqarorlik — muhandislik yechimlarida,\npishiqligi — har bir ulanishda.",
        "profile": "👤 Mening profilim",
        "buy_vpn": "🔐 Kirishni sotib olish",
        "about": "ℹ️ Xizmat haqida",
        "support": "🛡 Qo'llab-quvvatlash",
        "instruction": "📖 Ko'rsatma",
        "back": "🔙 Orqaga",
        "copy_key": "📋 Kalitni nusxalash",
        "renew_subscription": "🔁 Xuddi shu muddatga uzaytirish",
        "no_active_subscription": "Faol obuna topilmadi.",
        "select_tariff": "Kirish muddatini tanlang\n\nAtlas Secure cheklangan kirish printsipi asosida ishlaydi.\nHar bir davr — bu shaxsiy konfiguratsiya, emas ommaviy tarif.\n\nHar bir kirish darajasi quyidagilarni o'z ichiga oladi:\n— sizga xos qilingan individual VPN kalit\n— sessiya va metama'lumotlarni saqlashsiz zero-logs arxitektura\n— cheklovlar va tezlik pasayishi bo'lmagan barqaror ulanish\n— ustuvor qo'llab-quvvatlash",
        "tariff_button_1": "1 oy Vaqtinchalik kirish · 299 ₽",
        "tariff_button_3": "3 oy Standart kirish · 799 ₽",
        "tariff_button_6": "6 oy Kengaytirilgan kirish · 1 199 ₽",
        "tariff_button_12": "12 oy Ustuvor kirish · 1 699 ₽",
        "select_payment": "To'lov usulini tanlang.",
        "payment_test": "Xizmat rejimi Mavjud emas",
        "payment_sbp": "SBP",
        
        # Продление подписки
        "renewal_payment_text": "Obuna yangilanishi uchun to'lang.\n\nYangilanish joriy davr bilan bir xil muddatga amalga oshiriladi.",
        "renewal_pay_button": "💳 To'lash",
        "sbp_payment_text": "O'tkazmadan keyin to'lovni tasdiqlang.\n\n⸻\n\nO'tkazma ma'lumotlari\n\nBank: Ozon\nKarta hisobi: 2204321075030551\n\nTasdiqlash uchun summa: {amount} ₽",
        "paid_button": "To'lovni tasdiqlash",
        "payment_pending": "Tasdiqlash jarayonda\n\nTo'lov ro'yxatga olingan.\nTekshiruv 5 minutgacha davom etadi.\nKirish faollashtirish avtomatik ravishda amalga oshiriladi.",
        "payment_approved": "✅ Kirish faollashtirildi.\n\nSizning shaxsiy VPN kalitingiz:\n{vpn_key}\n\nAmal qilish muddati:\n{date} gacha\n\nKalitni xavfsiz joyda saqlashni tavsiya etamiz.",
        "payment_rejected": "❌ To'lov tasdiqlanmadi.\n\nAgar to'laganingizga ishonchingiz komil bo'lsa — qo'llab-quvvatlashga murojaat qiling.",
        "profile_active": "👤 Kirish profili\n\nHolati: Faol\nAmal qilish muddati: {date} gacha\n\nShaxsiy VPN kalit:\n{vpn_key}\n\nUlanish barqaror va himoyalangan.",
        "profile_renewal_hint": "\n\nHar qanday takroriy xarid obuna muddatini avtomatik ravishda uzaytiradi.",
        "profile_payment_check": "🕒 To'lov tekshiruvda.\n\nBu standart xavfsizlik protsedurasi.\nTasdiqlanganidan keyin kirish avtomatik ravishda paydo bo'ladi.",
        "subscription_expiring_reminder": "⏳ Kirish muddati yaqin orada tugaydi.\n\nObunangiz tugashiga 3 kun qoldi.\n\nSiz istalgan vaqtda kirishni uzaytirishingiz mumkin —\ntakroriy xarid avtomatik ravishda muddatni uzaytiradi.",
        
        # Умные напоминания - админ-доступ
        "reminder_admin_1day_6h": "⏳ Vaqtinchalik Atlas Secure kirishi 6 soatdan keyin tugaydi.\n\nBiz to'liq obunani xarid qilishni tavsiya qilamiz,\nuzilishlarsiz barqaror kirishni saqlash uchun.",
        "reminder_admin_7days_24h": "⏳ Vaqtinchalik Atlas Secure kirishi 24 soatdan keyin tugaydi.\n\nBiz uzluksiz va barqaror ulanish uchun\n1 oylik obunani xarid qilishni tavsiya qilamiz.",
        
        # Умные напоминания - оплаченные тарифы
        "reminder_paid_3d": "⏳ Atlas Secure kirishingiz 3 kundan keyin tugaydi.\n\nSiz obunani oldindan uzaytirishingiz mumkin,\nulanish uzilishini oldini olish uchun.",
        "reminder_paid_24h": "⏳ Atlas Secure kirishingiz 24 soatdan keyin tugaydi.\n\nBiz uzluksiz ulanishni saqlash uchun\nobunani oldindan uzaytirishni tavsiya qilamiz.",
        "reminder_paid_3h": "⏳ Atlas Secure kirishingiz 3 soatdan keyin tugaydi.\n\nHozir obunani uzaytiring,\nulanish uzilishini oldini olish uchun.",
        
        # Приветственная скидка
        "welcome_discount_label": "🎁 Salomlashish chegirmasi",
        "subscribe_1_month_button": "🔐 1 oylik obuna",
        "personal_discount_label": "🎯 Shaxsiy chegirma {percent}%",
        "vip_discount_label": "👑 VIP kirish",
        "vip_access_button": "👑 VIP kirish",
        "vip_access_text": "VIP kirish Atlas Secure\n\nVIP — bu tanlab beriladigan\nkirish darajasi.\n\nU sotilmaydi va individual ravishda ko'rib chiqiladi\nishonch va o'zaro munosabatlar tarixiga asoslanib\nAtlas Secure infratuzilmasi bilan.\n\n⸻\n\nDaraja imtiyozlari\n\n— ustuvor infratuzilma va minimal kechikish\n— shaxsiy VPN kirish konfiguratsiyasi\n— kengaytirilgan qo'llab-quvvatlash va to'g'ridan-to'g'ri aloqa\n— uzaytirish uchun diskretsion shartlar\n— infratuzilma o'zgarishlariga erta kirish\n— asosiy yangilanishlar bo'yicha yopiq aloqa\n\nBarcha qarorlar qo'lda qabul qilinadi.\nAvtomatik mexanizmlar ishlatilmaydi.",
        "vip_status_badge": "👑 VIP holati faol",
        "vip_status_active": "👑 Sizning VIP holatingiz faol",
        "contact_manager_button": "💬 Menejer bilan bog'lanish",
        "no_subscription": "❌ Faol kirish yo'q.\n\nAtlas Secure — individual ulanish kalitlari bilan maxfiy VPN xizmati.\n\nSiz istalgan vaqtda kirish olishingiz mumkin.",
        "about_text": "Atlas Secure orqasida nima bor\n\n🔐 Enterprise darajasidagi kriptografiya (AES-256)\nMoliyaviy va davlat tizimlari ishlatadigan xuddi shu arxitektura.\n\n🧬 Zero-Logs falsafasi\nBiz asosiy ravishda jurnallarni, ulanishlar tarixini yoki metama'lumotlarni saqlamaymiz.\nSaqlash kerak bo'lgan narsa yo'q — himoya qilish kerak bo'lgan narsa yo'q — oshkor qilish kerak bo'lgan narsa yo'q.\n\n🕶 Sukut bo'yicha maxfiylik\nKuzatish, analitika, uchinchi tomon SDK'lari va yashirin ma'lumotlar to'plami yo'q.\n\n⚡ Cheksiz tezlik va barqarorlik\nOversell va sun'iy cheklovlarsiz optimallashtirilgan serverlar.\n\n🌍 Premium global infratuzilma\nTezlik, yurisdiktsiya va ishonchlilik mezonlari bo'yicha tanlangan\n25+ mamlakatdagi ajratilgan serverlar.\n\n📱 To'liq qurilmalar ekotizimi\niOS · Android · macOS · Windows\nBir kirish — barcha qurilmalaringiz.\n\n⸻\n\nAtlas Secure kimlar uchun\n\n• Sezgir ma'lumotlar bilan ishlaydiganlar uchun\n• Tadbirkorlar va investorlar uchun\n• Raqamli kompromisslarsiz sayohat qiladiganlar uchun\n• Maxfiylik haqida gapirmaydiganlar uchun — ular uni ta'minlaydi\n\n⸻\n\nAtlas Secure VPN emas\n\nBu shaxsiy raqamli infratuzilma.\nJimsiz. Ko'rinmas. Ishonchli.\n\nSiz ulangan siz. Qolgani — sizning ishingiz emas.",
        "privacy_policy": "Maxfiylik siyosati",
        "privacy_policy_text": "Atlas Secure maxfiylik siyosati\n\nAtlas Secure ma'lumotlarni minimallashtirish printsipidan foydalanadi.\nXizmat ishlashi uchun zarur bo'lmagan ma'lumotlarni yig'maymiz.\n\n⸻\n\nNimani saqlamaymiz\n\n— ulanishlar tarixi\n— IP-manzillar va trafik\n— DNS so'rovlari\n— tashrif buyurilgan resurslar haqidagi ma'lumotlar\n— faollik metama'lumotlari\n\nZero-Logs arxitektura qo'llaniladi.\n\n⸻\n\nQanday ma'lumotlar qayta ishlanishi mumkin\n\n— kirish holati va amal qilish muddati\n— VPN kalitning texnik identifikatori\n\nUshbu ma'lumotlar foydalanuvchi faolligi bilan bog'liq emas.\n\n⸻\n\nTo'lovlar\n\nTo'lov ma'lumotlari Atlas Secure tomonidan qayta ishlanmaydi va saqlanmaydi.\nTo'lov bizning infratuzilmamizdan tashqari bank kanallari orqali amalga oshiriladi.\n\n⸻\n\nMa'lumotlarni uzatish\n\nBiz ma'lumotlarni uchinchi shaxslarga uzatmaymiz\nva kuzatuvchilar, analitika yoki reklama SDK-laridan foydalanmaymiz.\n\n⸻\n\nQo'llab-quvvatlash\n\nFaqat foydalanuvchi tomonidan ixtiyoriy ravishda taqdim etilgan\nva muayyan so'rovni hal qilish uchun zarur bo'lgan ma'lumotlar qayta ishlanadi.\n\n⸻\n\nAtlas Secure\nMaxfiylik — bu arxitektura asosi.",
        "service_status": "📜 Xizmat holati",
        "service_status_text": "📜 Atlas Secure Xizmat holati\n\nJoriy holat: 🟢 Operativ rejim\n\nBarcha asosiy komponentlar barqaror ishlayapti:\n— VPN-infratuzilma\n— Kalitlar berish tizimi\n— Kirishlarni tasdiqlash\n— Qo'llab-quvvatlash\n\n⸻\n\nSLA va majburiyatlar\n\nAtlas Secure barqarorlik va bashoratlilik ustuvorligi bilan\nshaxsiy raqamli infratuzilma sifatida qurilgan.\n\n• Maqsadli ish vaqti: 99.9%\n• Rejalashtirilgan ishlar oldindan o'tkaziladi\n• Kritik hodisalar ustuvor tartibda ko'rib chiqiladi\n• Ma'lumotlar yo'qolishi arxitektura jihatidan istisno qilingan\n\n⸻\n\nMa'lumot\n\nTexnik ishlar yoki o'zgarishlar holatida\nfoydalanuvchilar bot orqali oldindan xabardor qilinadi.\n\nHolat so'nggi yangilanishi: avtomatik",
        "support_text": "🛡 Atlas Secure qo'llab-quvvatlash\n\nKirish, to'lov yoki xizmat ishlashi haqida savollar bo'yicha\nsiz biz bilan to'g'ridan-to'g'ri bog'lanishingiz mumkin.\n\nHar bir murojaat individual ravishda ko'rib chiqiladi\nustuvor tartibda.\n\nKontaktlar:\nEmail: 000n999@duck.com\nTelegram: @asc_support",
        "change_language": "🌍 Tilni o'zgartirish",
        
        # Инструкция
        "instruction_text": "Outline-ga ulanish\n\nKirish shaxsiy kalit orqali ta'minlanadi.\n\n1. Kirish kaliti\nAtlas Secure faollashtirilgandan keyin beriladi.\n\n2. Ilova\nOperatsion tizimingiz uchun rasmiy ilova do'konidan\nOutline VPN-ni o'rnating.\n\n3. Ulanish\nOutline-ni oching, (＋) tugmasini bosing va berilgan kalitni kiriting.\nUlanish avtomatik ravishda faollashtiriladi.",
        "admin_payment_notification": "💰 Yangi to'lov\nFoydalanuvchi: @{username}\nTelegram ID: {telegram_id}\nTarif: {tariff} oy\nNarx: {price} ₽",
        "admin_approve": "Tasdiqlash",
        "admin_reject": "Rad etish",
        "admin_grant_access": "🟢 Kirish berish",
        "admin_revoke_access": "🔴 Kirishni bekor qilish",
        "admin_grant_days_prompt": "Kirish muddatini tanlang:",
        "admin_grant_days_1": "1 kun",
        "admin_grant_days_7": "7 kun",
        "admin_grant_days_14": "14 kun",
        "admin_grant_success": (
            "✅ {days} kun uchun kirish berildi.\n\n"
            "Kirish administrator tomonidan faollashtirildi."
        ),
        "admin_grant_fail_no_keys": "❌ Bepul VPN kalitlari mavjud emas",
        "admin_revoke_success": (
            "✅ Kirish bekor qilindi.\n\n"
            "Foydalanuvchi xabardor qilindi."
        ),
        "admin_revoke_fail_no_sub": "❌ Foydalanuvchining faol obunasi yo'q",
        "admin_grant_user_notification": (
            "✅ Sizga Atlas Secure ga {days} kun uchun kirish berildi.\n"
            "VPN kalit: {vpn_key}\n"
            "Muddati: {date} gacha"
        ),
        "admin_revoke_user_notification": "⛔ Atlas Secure ga kirishingiz administrator tomonidan bekor qilindi.",

        "incident_banner": "⚠️ Texnik ishlar olib borilmoqda",
        "incident_status_warning": "\n\n⚠️ E'TIBOR: Inson hodisa rejimi faol\n{incident_text}",
        "admin_incident_title": "🚨 Hodisa",
        "admin_incident_status_on": "🟢 Hodisa rejimi faol",
        "admin_incident_status_off": "⚪ Hodisa rejimi o'chirilgan",
        "admin_incident_enable": "✅ Faollashtirish",
        "admin_incident_disable": "❌ O'chirish",
        "admin_incident_edit_text": "📝 Matnni o'zgartirish",
        "admin_incident_text_prompt": "Hodisa matnini kiriting (yoki bekor qilish uchun /cancel yuboring):",
    },
    "tj": {
        "language_select": "Забони интихоб кунед / Выберите язык / Choose language / Tilni tanlang",
        "welcome": "Хуш омадед ба Atlas Secure\n\nИнфрасохтори рақамии хусусии дараҷаи олӣ.\nБарои касоне эҷод шудааст, ки дар бораи назорат суҳбат намекунанд — онҳо назорат доранд.\n\nAtlas Secure — ин муҳитест, ки дар он\nмахфият дар меъморӣ қарор дорад,\nустуворӣ — дар ҳалли муҳандисӣ,\nпешбинии — дар ҳар як пайванд.",
        "profile": "👤 Профили ман",
        "buy_vpn": "🔐 Хариди дастрасӣ",
        "about": "ℹ️ Дар бораи хизмат",
        "support": "🛡 Дастгирӣ",
        "instruction": "📖 Дастур",
        "instruction_device_ios": "📱 iOS",
        "instruction_device_android": "🤖 Android",
        "instruction_device_desktop": "💻 Windows / macOS",
        "back": "🔙 Бозгашт",
        "copy_key": "📋 Калидро нусхабардорӣ кардан",
        "renew_subscription": "🔁 Боз ҳамон муддатро васеъ кардан",
        "no_active_subscription": "Обунаи фаъол ёфт нашуд.",
        "subscription_history": "🧾 Таърихи обунаҳо",
        "subscription_history_empty": "Таърихи обунаҳо холӣ аст",
        "subscription_history_action_purchase": "Харид",
        "subscription_history_action_renewal": "Тоза кардан",
        "subscription_history_action_reissue": "Додани калиди нав",
        "subscription_history_action_manual_reissue": "Аз нав додани калид",
        "select_tariff": "Муддати дастрасиро интихоб кунед\n\nAtlas Secure ба принсипи дастрасии маҳдуд кор мекунад.\nҲар як давра — ин конфигуратсияи хусусӣ аст, на тарифи оммавӣ.\n\nҲар як сатҳи дастрасӣ дорои:\n— калиди VPN-и шахсӣ, ки хусусан ба шумо закреп шудааст\n— меъмории zero-logs бе нигоҳдории сессияҳо ва метамаълумот\n— пайванди устувор бе маҳдудияту коҳиши суръат\n— дастгирии афзалиятнок",
        "tariff_button_1": "1 моҳ Дастрасии муваққатӣ · 299 ₽",
        "tariff_button_3": "3 моҳ Дастрасии стандартӣ · 799 ₽",
        "tariff_button_6": "6 моҳ Дастрасии васеъ · 1 199 ₽",
        "tariff_button_12": "12 моҳ Дастрасии афзалиятнок · 1 699 ₽",
        "select_payment": "Усули пардохтро интихоб кунед.",
        "payment_test": "Реҷаи хизматӣ Дастрас нест",
        "payment_sbp": "СБП",
        "sbp_payment_text": "Пас аз интиқол, пардохтро тасдиқ кунед.\n\n⸻\n\nМаълумоти интиқол\n\nБонк: Ozon\nҲисоби корт: 2204321075030551\n\nМаблағи тасдиқ: {amount} ₽",
        "paid_button": "Пардохтро тасдиқ кардан",
        
        # Продление подписки
        "renewal_payment_text": "Барои васеъ кардани обуна пардохт кунед.\n\nВасеъ кардан ба ҳамон давра, ки дастрасии ҷорӣ, иҷро карда мешавад.",
        "renewal_pay_button": "💳 Пардохт кардан",
        
        "payment_pending": "Тасдиқ дар раванд аст\n\nПардохт ба қайд гирифта шуд.\nСанҷиш то 5 дақиқа давом мекунад.\nФаъолсозии дастрасӣ ба таври худкор иҷро мешавад.",
        "payment_approved": "✅ Дастрасӣ фаъол шуд.\n\nКалиди VPN-и шахсии шумо:\n{vpn_key}\n\nМуддати амал:\nто {date}\n\nТавсия медиҳем, ки калидро дар ҷойи бехатар нигоҳ доред.",
        "payment_rejected": "❌ Пардохт тасдиқ нашуд.\n\nАгар мӯътақид ҳастед, ки пардохт кардед — ба дастгирӣ муроҷиат кунед.",
        "profile_active": "👤 Профили дастрасӣ\n\nҲолат: Фаъол\nМуддати амал: то {date}\n\nКалиди VPN-и шахсӣ:\n{vpn_key}\n\nПайванд устувор ва ҳимояшуда аст.",
        "profile_renewal_hint": "\n\nҲар як хариди такрори обунаро ба таври худкор васеъ мекунад.",
        "profile_payment_check": "🕒 Пардохт дар санҷиш аст.\n\nИн процедураи стандартии амният аст.\nПас аз тасдиқ, дастрасӣ худкор пайдо мешавад.",
        "subscription_expiring_reminder": "⏳ Муддати дастрасӣ ба зудӣ анҷом мешавад.\n\nТо анҷоми обунаи шумо 3 рӯз боқӣ мондааст.\n\nШумо метавонед дар ҳар вақт дастрасиро васеъ кунед —\nхариди такрориҳо муддатро ба таври худкор васеъ мекунад.",
        
        # Умные напоминания - админ-доступ
        "reminder_admin_1day_6h": "⏳ Дастрасии муваққатии Atlas Secure дар 6 соат ба анҷом мерасад.\n\nМо тавсия медиҳем, ки обунаи пурраро тартиб диҳед,\nто дастрасии устуворро бе танаффус нигоҳ доред.",
        "reminder_admin_7days_24h": "⏳ Дастрасии муваққатии Atlas Secure дар 24 соат ба анҷом мерасад.\n\nМо тавсия медиҳем, ки обунаи 1 моҳаро тартиб диҳед\nбарои пайванди муттасил ва устувор.",
        
        # Умные напоминания - оплаченные тарифы
        "reminder_paid_3d": "⏳ Дастрасии шумо ба Atlas Secure дар 3 рӯз ба анҷом мерасад.\n\nШумо метавонед обунаро пеш аз вақт васеъ кунед,\nто аз танаффуси пайванд ҷилавгирӣ кунед.",
        "reminder_paid_24h": "⏳ Дастрасии шумо ба Atlas Secure дар 24 соат ба анҷом мерасад.\n\nМо тавсия медиҳем, ки обунаро пеш аз вақт васеъ кунед,\nто пайванди муттасилро нигоҳ доред.",
        "reminder_paid_3h": "⏳ Дастрасии шумо ба Atlas Secure дар 3 соат ба анҷом мерасад.\n\nҲоло обунаро васеъ кунед,\nто аз танаффуси пайванд ҷилавгирӣ кунед.",
        
        # Приветственная скидка
        "welcome_discount_label": "🎁 Чекрамоии тавзеҳӣ",
        "subscribe_1_month_button": "🔐 Обуна барои 1 моҳ",
        "personal_discount_label": "🎯 Чекрамоии шахсӣ {percent}%",
        "vip_discount_label": "👑 Дастрасии VIP",
        "vip_access_button": "👑 Дастрасии VIP",
        "vip_access_text": "Дастрасии VIP Atlas Secure\n\nVIP — ин сатҳи дастрасӣ аст,\nки ба таври интихобӣ таъмин карда мешавад.\n\nОн фурӯхта намешавад ва ба таври шахсӣ баррасӣ карда мешавад\nдар асоси эътимод ва таърихи муошират\nбо инфрасохтори Atlas Secure.\n\n⸻\n\nИмтиёзҳои сатҳ\n\n— инфрасохтори афзалиятнок ва латентнокӣ ҳадди ақал\n— конфигуратсияи шахсии дастрасии VPN\n— дастгирии васеъ ва тамоси мустақим\n— шартҳои ихтиёрии васеъ кардан\n— дастрасии пеш аз вақт ба тағйироти инфрасохторӣ\n— муоширати пӯшида дар бораи навсозиҳои асосӣ\n\nҲамаи қарорҳо ба таври дастӣ қабул карда мешаванд.\nМеханизмҳои худкор истифода намешаванд.",
        "vip_status_badge": "👑 Ҳолати VIP фаъол аст",
        "vip_status_active": "👑 Ҳолати VIP-и шумо фаъол аст",
        "contact_manager_button": "💬 Бо мудир тамос гиред",
        
        "no_subscription": "❌ Дастрасии фаъол нест.\n\nAtlas Secure — хизмати махфии VPN\nбо калидҳои пайванди шахсӣ.\n\nШумо метавонед дар ҳар вақт дастрасӣ гиред.",
        "about_text": "Чӣ дар пушти Atlas Secure аст\n\n🔐 Криптографияи сатҳи enterprise (AES-256)\nХуди ҳамин меъморӣ, ки системаҳои молиявӣ ва давлатӣ истифода мебаранд.\n\n🧬 Фалсафаи Zero-Logs\nМо асосан журналҳо, таърихи пайвандҳо ё метамаълумотро нигоҳ намедорем.\nЧизе барои нигоҳ доштан нест — чизе барои ҳимоя кардан нест — чизе барои ошкор кардан нест.\n\n🕶 Махфият ба таври сукут\nБе пайгирӣ, аналитика, SDK-ҳои тарафи сеюм ва ҷамъоварии пинҳонии маълумот.\n\n⚡ Суръат ва устувории номаҳдуд\nСерверҳои оптимизатсияшуда бе oversell ва маҳдудияти сунъӣ.\n\n🌍 Инфрасохтори глобалии премиум\nСерверҳои бахшидашуда дар 25+ кишвар, ки бо меъёрҳои суръат,\nюрисдикция ва эътимоднокӣ интихоб шудаанд.\n\n📱 Экосистемаи пурраи дастгоҳҳо\niOS · Android · macOS · Windows\nЯк дастрасӣ — ҳамаи дастгоҳҳои шумо.\n\n⸻\n\nAtlas Secure барои кӣ\n\n• Барои касоне, ки бо маълумоти ҳассос кор мекунанд\n• Барои соҳибкорон ва сармоягузорон\n• Барои саёҳаткунандагони бе компромиссҳои рақамӣ\n• Барои касоне, ки дар бораи махфият суҳбат намекунанд — онҳо танҳо онро таъмин мекунанд\n\n⸻\n\nAtlas Secure VPN нест\n\nИн инфрасохтори рақамии хусусӣ аст.\nОром. Намоён нест. Эътимоднок.\n\nШумо пайванд шудед. Боқимонда — ба шумо тааллуқ надорад.",
        "privacy_policy": "Сиёсати махфият",
        "privacy_policy_text": "Сиёсати махфияти Atlas Secure\n\nAtlas Secure ба принсипи коҳиши маълумот истифода мебарад.\nМо маълумотеро, ки барои амали хизмат зарур нест, ҷамъ намеорем.\n\n⸻\n\nЧӣ чизеро нигоҳ намедорем\n\n— таърихи пайвандҳо\n— суроғаҳои IP ва трафик\n— дархостҳои DNS\n— маълумот дар бораи манбаъҳои ташрифкардашуда\n— метамаълумоти фаъолият\n\nМеъмории Zero-Logs истифода мешавад.\n\n⸻\n\nЧӣ чизеро коркард кардан мумкин аст\n\n— ҳолат ва муддати дастрасӣ\n— идентификатори техникии калиди VPN\n\nИн маълумот бо фаъолияти корбар алоқаманд нест.\n\n⸻\n\nПардохтҳо\n\nМаълумоти пардохтӣ аз ҷониби Atlas Secure коркард ва нигоҳдорӣ намешавад.\nПардохт тавассути каналҳои бонкӣ берун аз инфрасохтори мо амалӣ мешавад.\n\n⸻\n\nИнтиқоли маълумот\n\nМо маълумотро ба шахсони сеюм намегузаронем\nва пайгирӣ, аналитика ё SDK-ҳои рекламавиро истифода намебарем.\n\n⸻\n\nДастгирӣ\n\nТанҳо маълумоте, ки корбар ихтиёриашон пешниҳод кардааст\nбарои ҳалли дархости муайян коркард карда мешавад.\n\n⸻\n\nAtlas Secure\nМахфият — асоси меъморӣ.",
        "service_status": "📜 Вазъияти хизмат",
        "service_status_text": "📜 Вазъияти хизмат Atlas Secure\n\nВазъияти ҷорӣ: 🟢 Реҷаи амалӣ\n\nҲамаи компонентҳои асосӣ устувор кор мекунанд:\n— инфрасохтори VPN\n— системаи додани калидҳо\n— тасдиқи дастрасиҳо\n— дастгирӣ\n\n⸻\n\nSLA ва ваъдаҳо\n\nAtlas Secure ҳамчун инфрасохтори рақамии шахсӣ\nбо афзалияти устуворӣ ва пешбиникунӣ сохта шудааст.\n\n• Вахти кори ҳадаф: 99.9%\n• Корҳои банақшагирифта пеш аз вақт анҷом дода мешаванд\n• Ҳодисаҳои ҷиддӣ дар тартиби афзалиятӣ баррасӣ мешаванд\n• Гум шудани маълумот аз ҷиҳати меъморӣ истисно карда шудааст\n\n⸻\n\nМаълумот\n\nДар сурати корҳои техникӣ ё тағйирот\nкорбарон тавассути бот пеш аз вақт огоҳ карда мешаванд.\n\nНавсозии охирини вазъият: ба таври худкор",
        "support_text": "🛡 Дастгирии Atlas Secure\n\nДар бораи дастрасӣ, пардохт ё амали хизмат саволҳо\nшумо метавонед бо мо бевосита тавонос шавед.\n\nҲар як мурожаат ба таври шахсӣ баррасӣ карда мешавад\nдар тартиби афзалиятнок.\n\nКонтактҳо:\nEmail: 000n999@duck.com\nTelegram: @asc_support",
        "change_language": "🌍 Тағйири забон",
        
        # Инструкция
        "instruction_text": "Пайвастшавӣ ба Outline\n\nДастрасӣ тавассути калиди шахсӣ таъмин карда мешавад.\n\n1. Калиди дастрасӣ\nПас аз фаъолсозии Atlas Secure дода мешавад.\n\n2. Барнома\nOutline VPN-ро аз мағозаи расмии барномаҳо\nбарои системаи оператсионии шумо насб кунед.\n\n3. Пайвастшавӣ\nOutline-ро кушоед, (＋) -ро пахш кунед ва калиди додашударо ворид кунед.\nПайванд ба таври худкор фаъол мешавад.",
        "admin_payment_notification": "💰 Пардохти нав\nКорбар: @{username}\nTelegram ID: {telegram_id}\nТариф: {tariff} моҳ\nНарх: {price} ₽",
        "admin_approve": "Тасдиқ кардан",
        "admin_reject": "Рад кардан",
        "admin_grant_access": "🟢 Дастраси додан",
        "admin_revoke_access": "🔴 Дастраси бекор кардан",
        "admin_grant_days_prompt": "Муддати дастрасиро интихоб кунед:",
        "admin_grant_days_1": "1 рӯз",
        "admin_grant_days_7": "7 рӯз",
        "admin_grant_days_14": "14 рӯз",
        "admin_grant_success": (
            "✅ Дастрасӣ барои {days} рӯз дода шуд.\n\n"
            "Дастрасӣ аз ҷониби мудир фаъол карда шуд."
        ),
        "admin_grant_fail_no_keys": "❌ Калидҳои VPN-и озод нестанд",
        "admin_revoke_success": (
            "✅ Дастрасӣ бекор карда шуд.\n\n"
            "Корбар огоҳ карда шуд."
        ),
        "admin_revoke_fail_no_sub": "❌ Корбар обунаи фаъол надорад",
        "admin_grant_user_notification": (
            "✅ Ба шумо ба Atlas Secure барои {days} рӯз дастрасӣ дода шуд.\n"
            "Калиди VPN: {vpn_key}\n"
            "Муддат: то {date}"
        ),
        "admin_revoke_user_notification": "⛔ Дастрасии шумо ба Atlas Secure аз ҷониби мудир бекор карда шуд.",

        "incident_banner": "⚠️ Корҳои техникӣ иҷро карда мешавад",
        "incident_status_warning": "⚠️ ЭЪТИБОР: Реҷаи ҳодиса фаъол аст\n{incident_text}",
        "admin_incident_title": "🚨 Ҳодиса",
        "admin_incident_status_on": "🟢 Реҷаи ҳодиса фаъол аст",
        "admin_incident_status_off": "⚪ Реҷаи ҳодиса хомӯш аст",
        "admin_incident_enable": "✅ Фаъол кардан",
        "admin_incident_disable": "❌ Хомӯш кардан",
        "admin_incident_edit_text": "📝 Матнро тағйир додан",
        "admin_incident_text_prompt": "Матни ҳодисаро ворид кунед (ё барои бекор кардан /cancel ирсол кунед):",
    },
}


def get_text(language: str, key: str, default: str = None, **kwargs) -> str:
    """Получить переведенный текст"""
    lang = language if language in TEXTS else "ru"
    text = TEXTS[lang].get(key)
    if text is None:
        text = TEXTS["ru"].get(key)
    if text is None:
        text = default if default is not None else key
    return text.format(**kwargs) if kwargs else text


# Кнопки для выбора языка
LANGUAGE_BUTTONS = {
    "ru": "Русский",
    "en": "English",
    "uz": "O'zbek",
    "tj": "Тоҷикӣ",
}
