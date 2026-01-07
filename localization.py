from typing import Dict

# Все тексты для локализации
TEXTS: Dict[str, Dict[str, str]] = {
    "ru": {
        "language_select": "Добро пожаловать в Atlas Secure\n\nЧастный защищённый доступ\nбез сложных настроек.\n\nПожалуйста, выберите язык:",
        
        # Главное меню
        "welcome": "🔐 Atlas Secure\n\n🧩 Частный цифровой доступ\n⚙️ Стабильная работа привычных сервисов\n🛡 Конфиденциальность по умолчанию\n\nВы подключаетесь —\nостальное работает в фоне.",
        "profile": "👤 Мой профиль",
        "buy_vpn": "🔐 Купить доступ",
        "get_access_button": "🔐 Оформить доступ",
        "about": "ℹ️ О сервисе",
        "support": "🛡 Поддержка",
        "instruction": "🔌 Инструкция",
        "instruction_device_ios": "📱 iOS",
        "instruction_device_android": "🤖 Android",
        "instruction_device_desktop": "💻 Windows / macOS",
        "referral_program": "🤝 Пригласить друга",
        "referral_program_text": (
            "🤝 Пригласите друзей\n\n"
            "Вы получаете бонусы за друзей,\n"
            "которые оформляют подписку.\n\n"
            "🎁 За каждого друга:\n"
            "+7 дней доступа\n\n"
            "🔗 Ваша реферальная ссылка:\n"
            "{referral_link}\n\n"
            "📊 Статистика:\n"
            "Приглашено: {total_referred}\n"
            "Бонусов начислено: {total_rewarded}"
        ),
        "copy_referral_link": "📋 Скопировать ссылку",
        "referral_link_copied": "Ссылка отправлена",
        "back": "← Назад",
        "copy_key": "📋 Скопировать ключ",
        "go_to_connection": "🔌 Перейти к подключению",
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
        "payment_approved": "✅ Доступ активирован\n\nВаш персональный ключ доступа готов.\nВы можете подключиться и начать пользоваться.\n\nПерсональный ключ доступа:\n\n{vpn_key}\n\nСрок действия доступа:\nдо {date}\n\nКлюч закреплён за вами\nи будет доступен в профиле.\n\nПодключение занимает не более 1 минуты.\nЕсли понадобится помощь — мы на связи.",
        
        # Отклонение
        "payment_rejected": "❌ Платёж не подтверждён.\n\nЕсли вы уверены, что оплатили —\nобратитесь в поддержку.",
        
        # Профиль - новый формат
        "profile_welcome": "Добро пожаловать в Atlas Secure!\n\n👤 {username}\n\n💰 Баланс: {balance:.2f} ₽",
        "profile_subscription_active": "Подписка:\n— 🟢 Активна до {date}",
        "profile_subscription_inactive": "Подписка:\n— 🔴 Неактивна",
        "profile_renewal_hint_new": "При продлении выбранный срок\nдобавляется к текущему автоматически.",
        "profile_buy_hint": "Нажмите «Купить подписку» в меню, чтобы получить доступ.",
        "access_key_label": "Ключ доступа:",
        
        # Профиль - активная подписка (старая версия, для совместимости)
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
        
        # Умные уведомления на основе трафика
        "smart_notif_no_traffic_20m": "Если вы ещё не подключились —\nобычно это занимает не более минуты.\n\nКлюч уже готов и закреплён за вами.",
        "smart_notif_no_traffic_24h": "Напоминание:\nдоступ активен и готов к использованию.\n\nПодключение не влияет на настройки устройства\nи не требует дополнительных разрешений.",
        "smart_notif_first_connection": "Соединение активно.\n\nДоступ работает стабильно\nи не требует вашего внимания.",
        "smart_notif_3days_usage": "Atlas Secure используется без ограничений\nи не требует продлений вручную\nдо окончания срока доступа.",
        "smart_notif_7days_before_expiry": "Срок действия доступа\nзаканчивается через 7 дней.\n\nВы можете продлить его заранее\nбез прерывания соединения.",
        "smart_notif_3days_before_expiry": "Напоминание:\nдоступ будет активен ещё 3 дня.\n\nПродление занимает менее минуты\nи сохраняет текущую конфигурацию.",
        "smart_notif_expiry_day": "Сегодня истекает срок действия доступа.\n\nПри продлении ключ и настройки сохраняются.",
        "smart_notif_expired_24h": "Доступ приостановлен.\n\nВы можете восстановить его в любой момент —\nбез повторной настройки.",
        "smart_notif_vip_offer": "Для пользователей с активным доступом\nдоступен расширенный уровень сопровождения.\n\nОн не продаётся автоматически\nи рассматривается индивидуально.",
        
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
        "no_subscription": "👤 Профиль доступа\n\nВ данный момент доступ не активирован.\n\nAtlas Secure предоставляет\nчастный защищённый доступ\nс индивидуальным ключом подключения.\n\nВы можете оформить доступ\nв любое удобное время.",
        
        # О сервисе
        "about_text": "ℹ️ О сервисе Atlas Secure\n\nAtlas Secure — это частный защищённый доступ\nк интернету, построенный для стабильной\nи предсказуемой работы без лишнего внимания\nк настройкам и деталям.\n\nМы проектируем инфраструктуру так,\nчтобы вы просто пользовались доступом,\nа не думали о том, как он устроен.\n\nЧто это даёт на практике:\n\n🔐 Приватность по умолчанию\nМы не отслеживаем действия пользователей,\nне храним историю подключений\nи не собираем метаданные.\n\n⚡️ Стабильность без ограничений\nОптимизированные серверы без перегрузки\nобеспечивают ровную и предсказуемую работу.\n\n🌍 Надёжная инфраструктура\nВыделенные серверы в разных регионах,\nотобранные по скорости и надёжности.\n\n📱 Все ваши устройства\nОдин доступ работает на iOS, Android,\nmacOS и Windows без доплат и ограничений.\n\nAtlas Secure подходит, если вы:\n• работаете с важной информацией\n• цените стабильность и контроль\n• не хотите разбираться в технологиях\n• предпочитаете, чтобы сервис просто работал\n\nAtlas Secure — это не про функции.\nЭто про спокойствие и предсказуемость.\n\nВы подключены.\nОстальное — работает в фоне.",
        "privacy_policy": "Политика конфиденциальности",
        "privacy_policy_text": "🔐 Политика конфиденциальности Atlas Secure\n\nAtlas Secure построен на принципе\nминимизации данных.\n\nМы не собираем и не храним информацию,\nкоторая не требуется для работы сервиса.\n\nЧто мы НЕ храним:\n• историю подключений\n• IP-адреса и сетевой трафик\n• DNS-запросы\n• данные о посещаемых ресурсах\n• метаданные пользовательской активности\n\nАрхитектура сервиса реализована\nпо принципу Zero-Logs.\n\nЧто может обрабатываться:\n• статус доступа\n• срок действия подписки\n• технический идентификатор ключа\n\nЭти данные не связаны\nс вашей сетевой активностью.\n\nПлатежи:\nAtlas Secure не обрабатывает\nи не хранит платёжные данные.\nОплата проходит через\nбанковские и платёжные системы\nвне нашей инфраструктуры.\n\nПередача данных:\nМы не передаём данные третьим лицам\nи не используем трекеры,\nаналитику или рекламные SDK.\n\nПоддержка:\nМы обрабатываем только ту информацию,\nкоторую вы добровольно предоставляете\nдля решения конкретного запроса.\n\nAtlas Secure.\nКонфиденциальность заложена\nв архитектуре сервиса.",
        "service_status": "📊 Статус сервиса",
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
        "admin_grant_minutes_10": "⏱ Доступ на 10 минут",
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
        "admin_grant_user_notification_10m": (
            "⏱ Доступ активирован на 10 минут.\n\n"
            "Вы можете подключиться сразу.\n"
            "По окончании доступ будет приостановлен автоматически."
        ),
        "admin_revoke_user_notification": "⛔ Ваш доступ к Atlas Secure был отозван администратором.",
        
        # Ошибки (для пользователей)
        "error_payment_processing": "Ошибка обработки платежа. Обратитесь в поддержку.",
        "error_subscription_activation": "Ошибка активации подписки. Обратитесь в поддержку.",
        "error_tariff": "Ошибка тарифа",
        "error_no_active_subscription": "Активная подписка не найдена",
        "error_payment_create": "Ошибка при создании счета. Попробуйте позже.",
        "error_payments_unavailable": "Платежи временно недоступны",
        "error_access_denied": "Доступ запрещён.",
        "error_start_command": "Пожалуйста, начните с команды /start",

    },
    "en": {
        "language_select": "Welcome to Atlas Secure\n\nPrivate secure access\nwithout complex setup.\n\nPlease select your language:",
        
        # Главное меню
        "welcome": "🔐 Atlas Secure\n\n🧩 Private digital access\n⚙️ Stable operation of familiar services\n🛡 Privacy by default\n\nYou connect —\neverything else works in the background.",
        "profile": "👤 My Profile",
        "buy_vpn": "🔐 Buy Access",
        "get_access_button": "🔐 Get Access",
        "about": "ℹ️ About",
        "support": "🛡 Support",
        "instruction": "🔌 Instruction",
        "instruction_device_ios": "📱 iOS",
        "instruction_device_android": "🤖 Android",
        "instruction_device_desktop": "💻 Windows / macOS",
        "referral_program": "🤝 Invite Friend",
        "referral_program_text": (
            "🤝 Invite Friends\n\n"
            "You get bonuses for friends\n"
            "who subscribe.\n\n"
            "🎁 For each friend:\n"
            "+7 days of access\n\n"
            "🔗 Your referral link:\n"
            "{referral_link}\n\n"
            "📊 Statistics:\n"
            "Invited: {total_referred}\n"
            "Bonuses granted: {total_rewarded}"
        ),
        "copy_referral_link": "📋 Copy Link",
        "referral_link_copied": "Link sent",
        "back": "← Back",
        "copy_key": "📋 Copy Key",
        "go_to_connection": "🔌 Go to Connection",
        "renew_subscription": "🔁 Renew Access",
        "no_active_subscription": "Active subscription not found.",
        "subscription_history": "📄 Subscription History",
        "subscription_history_empty": "Subscription history is empty",
        "subscription_history_action_purchase": "Purchase",
        "subscription_history_action_renewal": "Renewal",
        "subscription_history_action_reissue": "Key reissue",
        "subscription_history_action_manual_reissue": "Manual key reissue",
        
        # Выбор тарифа
        "select_tariff": "🕒 Select subscription period\n\nAtlas Secure is stable access\nthat simply works.\n\nIn any subscription:\n🔑 Personal key — only for you\n⚡️ Stable speed without limits\n📱💻 Works on all devices\n💬 Support in Telegram at any time\n\nThe longer the period — the less you think\nabout renewal and access issues.\n\nMost users choose subscriptions from 3 months.",
        "enter_promo_button": "🎟 Enter promo code",
        "enter_promo_text": "Enter promo code:",
        "invalid_promo": "❌ Invalid promo code",
        "promo_applied": "🎁 Promo code applied. Discount already included in price.",
        "promo_discount_label": "🎟 Promo code",
        "tariff_button_1": "1 month · For trial · 149 ₽",
        "tariff_button_3": "3 months · Most popular · 399 ₽ ⭐",
        "tariff_button_6": "6 months · Renew less often · 599 ₽",
        "tariff_button_12": "12 months · Don't think about access · 899 ₽",
        
        # Выбор способа оплаты
        "select_payment": "Choose payment method.",
        "payment_test": "Service mode Unavailable",
        "payment_sbp": "SBP",
        
        # Продление подписки
        "renewal_payment_text": "Pay for subscription renewal.\n\nRenewal will be performed\nfor the same period as current access.",
        "renewal_pay_button": "💳 Pay",
        
        # Оплата СБП
        "sbp_payment_text": "After making the transfer, confirm payment.\n\n⸻\n\nTransfer details\n\nBank: Ozon\nCard account: 2204321075030551\n\nAmount to confirm: {amount} ₽",
        "paid_button": "Confirm payment",
        
        # Ожидание подтверждения
        "payment_pending": "Confirmation in process\n\nPayment registered.\nVerification takes up to 5 minutes.\nAccess activation is performed automatically.",
        
        # Успешная активация
        "payment_approved": "✅ Access activated\n\nYour personal access key is ready.\nYou can connect and start using it.\n\nPersonal access key:\n\n{vpn_key}\n\nAccess valid until:\nuntil {date}\n\nThe key is assigned to you\nand will be available in your profile.\n\nConnection takes no more than 1 minute.\nIf you need help — we're here.",
        
        # Отклонение
        "payment_rejected": "❌ Payment not confirmed.\n\nIf you are sure you paid —\ncontact support.",
        
        # Профиль - новый формат
        "profile_welcome": "Welcome to Atlas Secure!\n\n👤 {username}\n\n💰 Balance: {balance:.2f} ₽",
        "profile_subscription_active": "Subscription:\n— 🟢 Active until {date}",
        "profile_subscription_inactive": "Subscription:\n— 🔴 Inactive",
        "profile_renewal_hint_new": "When renewing, the selected period\nis automatically added to current access.",
        "profile_buy_hint": "Click «Buy Subscription» in the menu to get access.",
        "access_key_label": "Access Key:",
        
        # Профиль - активная подписка (старая версия, для совместимости)
        "profile_active": "👤 Access Profile\n\nAccess status: Active\nAccess paid until {date}\n\nYou are connected. Access works stably.\n\nPersonal access key\nUsed for connection in Outline app.\nConnection persists while access is active.\n\n{vpn_key}\n\nWhen renewing, the selected period\nis automatically added to current access.\n\nUntil the period ends, you can\nnot return to settings and payment.",
        "profile_renewal_hint": "",
        
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
        
        # Умные уведомления на основе трафика
        "smart_notif_no_traffic_20m": "If you haven't connected yet —\nusually it takes no more than a minute.\n\nThe key is ready and assigned to you.",
        "smart_notif_no_traffic_24h": "Reminder:\naccess is active and ready to use.\n\nConnection doesn't affect device settings\nand doesn't require additional permissions.",
        "smart_notif_first_connection": "Connection is active.\n\nAccess works stably\nand doesn't require your attention.",
        "smart_notif_3days_usage": "Atlas Secure is used without limits\nand doesn't require manual renewals\nuntil access period ends.",
        "smart_notif_7days_before_expiry": "Access period\nends in 7 days.\n\nYou can renew it in advance\nwithout interrupting connection.",
        "smart_notif_3days_before_expiry": "Reminder:\naccess will be active for 3 more days.\n\nRenewal takes less than a minute\nand preserves current configuration.",
        "smart_notif_expiry_day": "Access period expires today.\n\nWhen renewing, key and settings are preserved.",
        "smart_notif_expired_24h": "Access is suspended.\n\nYou can restore it at any time —\nwithout reconfiguration.",
        "smart_notif_vip_offer": "For users with active access\nextended support level is available.\n\nIt's not sold automatically\nand is considered individually.",
        
        # Приветственная скидка
        "welcome_discount_label": "🎁 Welcome Discount",
        "subscribe_1_month_button": "🔐 1 Month Subscription",
        "personal_discount_label": "🎯 Personal Discount {percent}%",
        "vip_discount_label": "👑 VIP Access",
        "vip_access_button": "👑 Upgrade Access Level",
        "vip_access_text": "👑 VIP Access Atlas Secure\n\nVIP is an extended support level\nfor those who value stability and priority.\n\nWhat VIP provides:\n⚡️ Priority infrastructure and minimal delay\n🛠 Personal access configuration\n💬 Priority support without waiting\n🚀 Early access to updates\n\nVIP is suitable if you:\n• use access daily\n• don't want to deal with settings\n• value predictable operation\n\nPrice:\n1,990 ₽ / month\nor 9,990 ₽ / 6 months\n\nVIP is activated with active subscription.\nLeave a request — we'll do everything for you.\n\nVIP — when access simply exists,\nand you don't think about it.",
        "vip_status_badge": "👑 VIP status active",
        "vip_status_active": "👑 Your VIP status is active",
        "contact_manager_button": "💬 Connect VIP Access",
        
        # Профиль - без подписки
        "no_subscription": "👤 Access Profile\n\nAccess is not currently activated.\n\nAtlas Secure provides\nprivate secure access\nwith an individual connection key.\n\nYou can get access\nat any convenient time.",
        
        # О сервисе
        "about_text": "ℹ️ About Atlas Secure Service\n\nAtlas Secure is private secure access\nto the internet, built for stable\nand predictable operation without extra attention\nto settings and details.\n\nWe design infrastructure so\nthat you simply use access,\nnot think about how it works.\n\nWhat this gives in practice:\n\n🔐 Privacy by default\nWe don't track user actions,\ndon't store connection history\nand don't collect metadata.\n\n⚡️ Stability without limits\nOptimized servers without overload\nensure smooth and predictable operation.\n\n🌍 Reliable infrastructure\nDedicated servers in different regions,\nselected by speed and reliability.\n\n📱 All your devices\nOne access works on iOS, Android,\nmacOS and Windows without extra fees and limits.\n\nAtlas Secure is suitable if you:\n• work with important information\n• value stability and control\n• don't want to deal with technologies\n• prefer that service simply works\n\nAtlas Secure is not about features.\nIt's about peace and predictability.\n\nYou're connected.\nEverything else — works in the background.",
        "privacy_policy": "Privacy Policy",
        "privacy_policy_text": "🔐 Atlas Secure Privacy Policy\n\nAtlas Secure is built on the principle\nof data minimization.\n\nWe don't collect and don't store information\nthat is not required for service operation.\n\nWhat we DON'T store:\n• connection history\n• IP addresses and network traffic\n• DNS queries\n• data about visited resources\n• user activity metadata\n\nService architecture is implemented\non Zero-Logs principle.\n\nWhat may be processed:\n• access status\n• subscription validity period\n• technical key identifier\n\nThis data is not linked\nto your network activity.\n\nPayments:\nAtlas Secure doesn't process\nand doesn't store payment data.\nPayment goes through\nbanking and payment systems\noutside our infrastructure.\n\nData sharing:\nWe don't share data with third parties\nand don't use trackers,\nanalytics or advertising SDKs.\n\nSupport:\nWe process only information\nthat you voluntarily provide\nfor resolving a specific request.\n\nAtlas Secure.\nPrivacy is embedded\nin service architecture.",
        "service_status": "📊 Service Status",
        "service_status_text": "📊 Atlas Secure Service Status\n\nCurrent status: 🟢 Service works stably\n\nAll main components function\nin normal mode:\n• access is active\n• key issuance works\n• support is available\n\nAtlas Secure is built as private\ndigital infrastructure\nwith priority on stability\nand predictable operation.\n\nOur principles:\n• target uptime — 99.9%\n• planned work is done in advance\n• critical incidents are resolved\n  in priority order\n• data loss is architecturally excluded\n\nIn case of technical work\nor changes, users\nare notified in advance through bot.\n\nLast status update:\nautomatically",
        
        # Поддержка
        "support_text": "🛡 Atlas Secure Support\n\nIf you have questions about access,\npayment or service operation —\nwrite to us directly.\n\nWe respond manually\nand consider requests\nin priority order.\n\nYou can contact support\nat any time — we're here.",
        "change_language": "🌍 Change language",
        
        # Инструкция
        "instruction_text": "🔌 Connection\n\nAccess works through a personal key.\nSetup takes no more than 1 minute.\n\n1️⃣ 🔑 Get access key\nKey appears automatically after subscription activation.\n\n2️⃣ 📥 Install Outline app\nDownload the app from official store\nfor your operating system.\n\n3️⃣ ➕ Connect\nOpen Outline, press «+» and paste the key.\nConnection will activate automatically.\n\n✅ After connection, nothing needs to be configured.\nAccess will work while subscription is active.",
        
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
        "admin_grant_minutes_10": "⏱ Access for 10 minutes",
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
        "admin_grant_user_notification_10m": (
            "⏱ Access activated for 10 minutes.\n\n"
            "You can connect immediately.\n"
            "Access will be suspended automatically when it expires."
        ),
        "admin_revoke_user_notification": "⛔ Your access to Atlas Secure has been revoked by the administrator.",
        
        # Ошибки (для пользователей)
        "error_payment_processing": "Payment processing error. Please contact support.",
        "error_subscription_activation": "Subscription activation error. Please contact support.",
        "error_tariff": "Tariff error",
        "error_no_active_subscription": "Active subscription not found",
        "error_payment_create": "Error creating invoice. Please try again later.",
        "error_payments_unavailable": "Payments temporarily unavailable",
        "error_access_denied": "Access denied.",
        "error_start_command": "Please start with /start command",

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
        "language_select": "Atlas Secure-ga xush kelibsiz\n\nShaxsiy himoyalangan kirish\nmurakkab sozlashlarsiz.\n\nIltimos, tilni tanlang:",
        "welcome": "🔐 Atlas Secure\n\n🧩 Shaxsiy raqamli kirish\n⚙️ Tanish xizmatlarning barqaror ishlashi\n🛡 Sukut bo'yicha maxfiylik\n\nSiz ulanasiz —\nqolgan hamma narsa fon ishlaydi.",
        "profile": "👤 Mening profilim",
        "buy_vpn": "🔐 Kirishni sotib olish",
        "get_access_button": "🔐 Kirishni rasmiylashtirish",
        "about": "ℹ️ Xizmat haqida",
        "support": "🛡 Qo'llab-quvvatlash",
        "instruction": "🔌 Ko'rsatma",
        "instruction_device_ios": "📱 iOS",
        "instruction_device_android": "🤖 Android",
        "instruction_device_desktop": "💻 Windows / macOS",
        "referral_program": "🤝 Do'stni taklif qilish",
        "referral_program_text": (
            "🤝 Do'stlaringizni taklif qiling\n\n"
            "Siz do'stlaringiz uchun bonuslar olasiz,\n"
            "ular obuna bo'lganda.\n\n"
            "🎁 Har bir do'st uchun:\n"
            "+7 kun kirish\n\n"
            "🔗 Sizning taklif havolangiz:\n"
            "{referral_link}\n\n"
            "📊 Statistika:\n"
            "Taklif qilingan: {total_referred}\n"
            "Berilgan bonuslar: {total_rewarded}"
        ),
        "copy_referral_link": "📋 Havolani nusxalash",
        "referral_link_copied": "Havola yuborildi",
        "back": "← Orqaga",
        "copy_key": "📋 Kalitni nusxalash",
        "go_to_connection": "🔌 Ulanishga o'tish",
        "renew_subscription": "🔁 Kirishni uzaytirish",
        "no_active_subscription": "Faol obuna topilmadi.",
        # Профиль - новый формат
        "profile_welcome": "Atlas Secure-ga xush kelibsiz!\n\n👤 {username}\n\n💰 Balans: {balance:.2f} ₽",
        "profile_subscription_active": "Obuna:\n— 🟢 {date} gacha faol",
        "profile_subscription_inactive": "Obuna:\n— 🔴 Faol emas",
        "profile_renewal_hint_new": "Uzaytirishda tanlangan muddat\njoriy kirishga avtomatik qo'shiladi.",
        "profile_buy_hint": "Kirish olish uchun menyudan «Kirishni sotib olish»ni bosing.",
        "access_key_label": "Kirish kaliti:",
        "subscription_history": "📄 Obuna tarixi",
        "subscription_history_empty": "Obuna tarixi bo'sh",
        "subscription_history_action_purchase": "Xarid",
        "subscription_history_action_renewal": "Uzaytirish",
        "subscription_history_action_reissue": "Yangi kalit berish",
        "subscription_history_action_manual_reissue": "Kalitni qayta berish",
        "select_tariff": "🕒 Obuna muddatini tanlang\n\nAtlas Secure — bu barqaror kirish,\noddiy ishlaydi.\n\nHar qanday obunada:\n🔑 Shaxsiy kalit — faqat siz uchun\n⚡️ Cheklovlarsiz barqaror tezlik\n📱💻 Barcha qurilmalarda ishlaydi\n💬 Telegram orqali istalgan vaqtda qo'llab-quvvatlash\n\nMuddat qancha uzoq bo'lsa — shuncha kam\nuzaytirish va kirish muammolari haqida o'ylaysiz.\n\nKo'pchilik foydalanuvchilar 3 oydan boshlab obunani tanlaydi.",
        "enter_promo_button": "🎟 Promokod kiriting",
        "enter_promo_text": "Promokod kiriting:",
        "invalid_promo": "❌ Noto'g'ri promokod",
        "promo_applied": "🎁 Promokod qo'llandi. Chegirma narxga allaqachon kiritilgan.",
        "promo_discount_label": "🎟 Promokod",
        "tariff_button_1": "1 oy · Sinab ko'rish uchun · 149 ₽",
        "tariff_button_3": "3 oy · Eng ko'p tanlanadi · 399 ₽ ⭐",
        "tariff_button_6": "6 oy · Kamroq uzaytirish · 599 ₽",
        "tariff_button_12": "12 oy · Kirish haqida o'ylamaslik · 899 ₽",
        "select_payment": "To'lov usulini tanlang.",
        "payment_test": "Xizmat rejimi Mavjud emas",
        "payment_sbp": "SBP",
        
        # Продление подписки
        "renewal_payment_text": "Obuna yangilanishi uchun to'lang.\n\nYangilanish joriy davr bilan bir xil muddatga amalga oshiriladi.",
        "renewal_pay_button": "💳 To'lash",
        "sbp_payment_text": "O'tkazmadan keyin to'lovni tasdiqlang.\n\n⸻\n\nO'tkazma ma'lumotlari\n\nBank: Ozon\nKarta hisobi: 2204321075030551\n\nTasdiqlash uchun summa: {amount} ₽",
        "paid_button": "To'lovni tasdiqlash",
        "payment_pending": "Tasdiqlash jarayonda\n\nTo'lov ro'yxatga olingan.\nTekshiruv 5 minutgacha davom etadi.\nKirish faollashtirish avtomatik ravishda amalga oshiriladi.",
        "payment_approved": "✅ Kirish faollashtirildi\n\nSizning shaxsiy kirish kalitingiz tayyor.\nUlanish va foydalanishni boshlashingiz mumkin.\n\nShaxsiy kirish kaliti:\n\n{vpn_key}\n\nKirish amal qilish muddati:\n{date} gacha\n\nKalit sizga biriktirilgan\nva profilingizda mavjud bo'ladi.\n\nUlanish 1 minutdan ko'p vaqt olmaydi.\nAgar yordam kerak bo'lsa — biz yonadasiz.",
        "payment_rejected": "❌ To'lov tasdiqlanmadi.\n\nAgar to'laganingizga ishonchingiz komil bo'lsa — qo'llab-quvvatlashga murojaat qiling.",
        "profile_active": "👤 Kirish profili\n\nKirish holati: Faol\nKirish {date} gacha to'langan\n\nSiz ulangansiz. Kirish barqaror ishlaydi.\n\nShaxsiy kirish kaliti\nOutline ilovasida ulanish uchun ishlatiladi.\nUlanish kirish faol bo'lguncha saqlanadi.\n\n{vpn_key}\n\nUzaytirishda tanlangan muddat\njoriy kirishga avtomatik qo'shiladi.\n\nMuddat tugaguncha siz\nsozlashlar va to'lovga qaytishingiz shart emas.",
        "profile_renewal_hint": "",
        "profile_payment_check": "🕒 To'lov tekshiruvda.\n\nBu standart xavfsizlik protsedurasi.\nTasdiqlanganidan keyin kirish avtomatik ravishda paydo bo'ladi.",
        "subscription_expiring_reminder": "⏳ Kirish muddati yaqin orada tugaydi.\n\nObunangiz tugashiga 3 kun qoldi.\n\nSiz istalgan vaqtda kirishni uzaytirishingiz mumkin —\ntakroriy xarid avtomatik ravishda muddatni uzaytiradi.",
        
        # Умные напоминания - админ-доступ
        "reminder_admin_1day_6h": "⏳ Vaqtinchalik Atlas Secure kirishi 6 soatdan keyin tugaydi.\n\nBiz to'liq obunani xarid qilishni tavsiya qilamiz,\nuzilishlarsiz barqaror kirishni saqlash uchun.",
        "reminder_admin_7days_24h": "⏳ Vaqtinchalik Atlas Secure kirishi 24 soatdan keyin tugaydi.\n\nBiz uzluksiz va barqaror ulanish uchun\n1 oylik obunani xarid qilishni tavsiya qilamiz.",
        
        # Умные напоминания - оплаченные тарифы
        "reminder_paid_3d": "⏳ Atlas Secure kirishingiz 3 kundan keyin tugaydi.\n\nSiz obunani oldindan uzaytirishingiz mumkin,\nulanish uzilishini oldini olish uchun.",
        "reminder_paid_24h": "⏳ Atlas Secure kirishingiz 24 soatdan keyin tugaydi.\n\nBiz uzluksiz ulanishni saqlash uchun\nobunani oldindan uzaytirishni tavsiya qilamiz.",
        "reminder_paid_3h": "⏳ Atlas Secure kirishingiz 3 soatdan keyin tugaydi.\n\nHozir obunani uzaytiring,\nulanish uzilishini oldini olish uchun.",
        
        # Умные уведомления на основе трафика
        "smart_notif_no_traffic_20m": "Agar hali ulanmagan bo'lsangiz —\nodatda bu 1 minutdan ko'p vaqt olmaydi.\n\nKalit tayyor va sizga biriktirilgan.",
        "smart_notif_no_traffic_24h": "Eslatma:\nkirish faol va foydalanishga tayyor.\n\nUlanish qurilma sozlamalariga ta'sir qilmaydi\nva qo'shimcha ruxsatlar talab qilmaydi.",
        "smart_notif_first_connection": "Ulanish faol.\n\nKirish barqaror ishlaydi\nva sizning e'tiboringizni talab qilmaydi.",
        "smart_notif_3days_usage": "Atlas Secure cheklovlarsiz ishlatiladi\nva kirish muddati tugaguncha\nqo'lda uzaytirishni talab qilmaydi.",
        "smart_notif_7days_before_expiry": "Kirish muddati\n7 kundan keyin tugaydi.\n\nSiz uni oldindan uzaytirishingiz mumkin\nulanishni uzmasdan.",
        "smart_notif_3days_before_expiry": "Eslatma:\nkirish yana 3 kun faol bo'ladi.\n\nUzaytirish 1 minutdan kam vaqt oladi\nva joriy konfiguratsiyani saqlaydi.",
        "smart_notif_expiry_day": "Bugun kirish muddati tugaydi.\n\nUzaytirishda kalit va sozlamalar saqlanadi.",
        "smart_notif_expired_24h": "Kirish to'xtatilgan.\n\nSiz uni istalgan vaqtda tiklash mumkin —\nqayta sozlashsiz.",
        "smart_notif_vip_offer": "Faol kirishga ega foydalanuvchilar uchun\nkengaytirilgan qo'llab-quvvatlash darajasi mavjud.\n\nU avtomatik sotilmaydi\nva individual ko'rib chiqiladi.",
        
        # Приветственная скидка
        "welcome_discount_label": "🎁 Salomlashish chegirmasi",
        "subscribe_1_month_button": "🔐 1 oylik obuna",
        "personal_discount_label": "🎯 Shaxsiy chegirma {percent}%",
        "vip_discount_label": "👑 VIP kirish",
        "vip_access_button": "👑 Kirish darajasini yaxshilash",
        "vip_access_text": "👑 VIP kirish Atlas Secure\n\nVIP — bu kengaytirilgan qo'llab-quvvatlash darajasi\nbarqarorlik va ustuvorlikni qadrlaydiganlar uchun.\n\nVIP nima beradi:\n⚡️ Ustuvor infratuzilma va minimal kechikish\n🛠 Shaxsiy kirish konfiguratsiyasi\n💬 Kutishsiz ustuvor qo'llab-quvvatlash\n🚀 Yangilanishlarga erta kirish\n\nVIP sizga mos keladi, agar:\n• kirishdan har kuni foydalanasiz\n• sozlamalar bilan shug'ullanmoqchi emassiz\n• bashoratli ishlashni qadrlaysiz\n\nNarx:\n1 990 ₽ / oy\nyoki 9 990 ₽ / 6 oy\n\nVIP faol obuna bilan faollashtiriladi.\nSo'rov qoldiring — biz hamma narsani qilamiz.\n\nVIP — kirish oddiy mavjud bo'lganda,\nva siz uning haqida o'ylamaysiz.",
        "vip_status_badge": "👑 VIP holati faol",
        "vip_status_active": "👑 Sizning VIP holatingiz faol",
        "contact_manager_button": "💬 VIP kirishni ulash",
        "no_subscription": "👤 Kirish profili\n\nHozirgi vaqtda kirish faollashtirilmagan.\n\nAtlas Secure ta'minlaydi\nshaxsiy himoyalangan kirish\nindividual ulanish kaliti bilan.\n\nSiz istalgan qulay vaqtda\nkirishni rasmiylashtirishingiz mumkin.",
        "about_text": "ℹ️ Atlas Secure xizmati haqida\n\nAtlas Secure — bu shaxsiy himoyalangan kirish\ninternetga, barqaror\nva bashoratli ishlash uchun qurilgan ortiqcha e'tiborsiz\nsozlashlar va tafsilotlarga.\n\nBiz infratuzilmani shunday loyihalashtiramiz,\nsiz oddiy kirishdan foydalanasiz,\nqanday ishlashini o'ylamaysiz.\n\nBu amalda nima beradi:\n\n🔐 Sukut bo'yicha maxfiylik\nBiz foydalanuvchi harakatlarini kuzatmaymiz,\nulanishlar tarixini saqlamaymiz\nva metama'lumotlarni yig'maymiz.\n\n⚡️ Cheklovlarsiz barqarorlik\nOverloadsiz optimallashtirilgan serverlar\nsilliq va bashoratli ishlashni ta'minlaydi.\n\n🌍 Ishonchli infratuzilma\nTurli mintaqalardagi ajratilgan serverlar,\ntezlik va ishonchlilik bo'yicha tanlangan.\n\n📱 Barcha qurilmalaringiz\nBir kirish iOS, Android,\nmacOS va Windows-da qo'shimcha to'lovlar va cheklovlarsiz ishlaydi.\n\nAtlas Secure sizga mos keladi, agar:\n• muhim ma'lumotlar bilan ishlaysiz\n• barqarorlik va nazoratni qadrlaysiz\n• texnologiyalar bilan shug'ullanmoqchi emassiz\n• xizmat oddiy ishlashini afzal ko'rasiz\n\nAtlas Secure funksiyalar haqida emas.\nBu tinchlik va bashoratlilik haqida.\n\nSiz ulangansiz.\nQolgan hamma narsa — fon ishlaydi.",
        "privacy_policy": "Maxfiylik siyosati",
        "privacy_policy_text": "🔐 Atlas Secure maxfiylik siyosati\n\nAtlas Secure asosida\nma'lumotlarni minimallashtirish printsipi qurilgan.\n\nBiz xizmat ishlashi uchun zarur bo'lmagan\nma'lumotlarni yig'maymiz va saqlamaymiz.\n\nNimani biz SAQLAMAYMIZ:\n• ulanishlar tarixi\n• IP-manzillar va tarmoq trafigi\n• DNS so'rovlari\n• tashrif buyurilgan resurslar haqidagi ma'lumotlar\n• foydalanuvchi faolligi metama'lumotlari\n\nXizmat arxitekturasi\nZero-Logs printsipi asosida amalga oshirilgan.\n\nQanday ma'lumotlar qayta ishlanishi mumkin:\n• kirish holati\n• obuna amal qilish muddati\n• kalitning texnik identifikatori\n\nUshbu ma'lumotlar\nsizning tarmoq faolligingiz bilan bog'liq emas.\n\nTo'lovlar:\nAtlas Secure to'lov ma'lumotlarini\nqayta ishlamaydi va saqlamaydi.\nTo'lov bizning infratuzilmamizdan tashqari\nbank va to'lov tizimlari orqali amalga oshiriladi.\n\nMa'lumotlarni uzatish:\nBiz ma'lumotlarni uchinchi shaxslarga uzatmaymiz\nva kuzatuvchilar,\nanalitika yoki reklama SDK-laridan foydalanmaymiz.\n\nQo'llab-quvvatlash:\nBiz faqat siz ixtiyoriy ravishda taqdim etgan\nva muayyan so'rovni hal qilish uchun zarur bo'lgan ma'lumotlarni qayta ishlaymiz.\n\nAtlas Secure.\nMaxfiylik xizmat\narxitekturasi ichiga qo'yilgan.",
        "service_status": "📊 Xizmat holati",
        "service_status_text": "📊 Atlas Secure Xizmat holati\n\nJoriy holat: 🟢 Xizmat barqaror ishlaydi\n\nBarcha asosiy komponentlar\noddiy rejimda ishlaydi:\n• kirish faol\n• kalitlar berish ishlaydi\n• qo'llab-quvvatlash mavjud\n\nAtlas Secure shaxsiy\nraqamli infratuzilma sifatida qurilgan\nbarqarorlik va bashoratlilik ustuvorligi bilan.\n\nBizning tamoyillarimiz:\n• maqsadli ish vaqti — 99.9%\n• rejalashtirilgan ishlar oldindan o'tkaziladi\n• kritik hodisalar\n  ustuvor tartibda hal qilinadi\n• ma'lumotlar yo'qolishi arxitektura jihatidan istisno qilingan\n\nTexnik ishlar\nyoki o'zgarishlar holatida foydalanuvchilar\nbot orqali oldindan xabardor qilinadi.\n\nHolat so'nggi yangilanishi:\navtomatik",
        "support_text": "🛡 Atlas Secure qo'llab-quvvatlash\n\nAgar sizda kirish,\nto'lov yoki xizmat ishlashi haqida savollar bo'lsa —\nbizga to'g'ridan-to'g'ri yozing.\n\nBiz qo'lda javob beramiz\nva murojaatlarni\nustuvor tartibda ko'rib chiqamiz.\n\nSiz istalgan vaqtda qo'llab-quvvatlashga murojaat qilishingiz mumkin — biz yonadasiz.",
        "change_language": "🌍 Tilni o'zgartirish",
        
        # Инструкция
        "instruction_text": "🔌 Ulanish\n\nKirish shaxsiy kalit orqali ishlaydi.\nSozlash 1 minutdan ko'p vaqt olmaydi.\n\n1️⃣ 🔑 Kirish kalitini oling\nKalit obuna faollashtirilgandan keyin avtomatik paydo bo'ladi.\n\n2️⃣ 📥 Outline ilovasini o'rnating\nRasmiy do'kondan ilovani yuklab oling\noperatsion tizimingiz uchun.\n\n3️⃣ ➕ Ulaning\nOutline-ni oching, «+» tugmasini bosing va kalitni qo'ying.\nUlanish avtomatik faollashtiriladi.\n\n✅ Ulanishdan keyin hech narsa sozlash shart emas.\nKirish obuna faol bo'lguncha ishlaydi.",
        "admin_payment_notification": "💰 Yangi to'lov\nFoydalanuvchi: @{username}\nTelegram ID: {telegram_id}\nTarif: {tariff} oy\nNarx: {price} ₽",
        "admin_approve": "Tasdiqlash",
        "admin_reject": "Rad etish",
        "admin_grant_access": "🟢 Kirish berish",
        "admin_revoke_access": "🔴 Kirishni bekor qilish",
        "admin_grant_days_prompt": "Kirish muddatini tanlang:",
        "admin_grant_days_1": "1 kun",
        "admin_grant_days_7": "7 kun",
        "admin_grant_days_14": "14 kun",
        "admin_grant_minutes_10": "⏱ 10 daqiqa uchun kirish",
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
        "admin_grant_user_notification_10m": (
            "⏱ 10 daqiqa uchun kirish faollashtirildi.\n\n"
            "Siz darhol ulanishingiz mumkin.\n"
            "Muddati tugagach, kirish avtomatik ravishda to'xtatiladi."
        ),
        "admin_revoke_user_notification": "⛔ Atlas Secure ga kirishingiz administrator tomonidan bekor qilindi.",
        
        # Ошибки (для пользователей)
        "error_payment_processing": "To'lovni qayta ishlash xatosi. Iltimos, qo'llab-quvvatlashga murojaat qiling.",
        "error_subscription_activation": "Obunani faollashtirish xatosi. Iltimos, qo'llab-quvvatlashga murojaat qiling.",
        "error_tariff": "Tarif xatosi",
        "error_no_active_subscription": "Faol obuna topilmadi",
        "error_payment_create": "Hisobni yaratish xatosi. Iltimos, keyinroq qayta urinib ko'ring.",
        "error_payments_unavailable": "To'lovlar vaqtincha mavjud emas",
        "error_access_denied": "Kirish rad etildi.",
        "error_start_command": "Iltimos, /start buyrug'i bilan boshlang",

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
        "language_select": "Хуш омадед ба Atlas Secure\n\nДастрасии хусусии ҳимояшуда\nбе танзимоти мураккаб.\n\nЛутфан, забонро интихоб кунед:",
        "welcome": "🔐 Atlas Secure\n\n🧩 Дастрасии рақамии хусусӣ\n⚙️ Амали устувори хизматҳои одатии\n🛡 Махфият ба таври сукут\n\nШумо пайванд мешавед —\nҳама чизҳои дигар дар фон кор мекунанд.",
        "profile": "👤 Профили ман",
        "buy_vpn": "🔐 Хариди дастрасӣ",
        "get_access_button": "🔐 Дастрасиро расмият додан",
        "about": "ℹ️ Дар бораи хизмат",
        "support": "🛡 Дастгирӣ",
        "instruction": "🔌 Дастур",
        "instruction_device_ios": "📱 iOS",
        "instruction_device_android": "🤖 Android",
        "instruction_device_desktop": "💻 Windows / macOS",
        "referral_program": "🤝 Дӯстро даъват кардан",
        "referral_program_text": (
            "🤝 Дӯстони худро даъват кунед\n\n"
            "Шумо барои дӯстоне, ки обуна мегиранд,\n"
            "бонусҳо мегиред.\n\n"
            "🎁 Барои ҳар як дӯст:\n"
            "+7 рӯз дастрасӣ\n\n"
            "🔗 Пайванди даъвати шумо:\n"
            "{referral_link}\n\n"
            "📊 Омори:\n"
            "Даъват карда шуд: {total_referred}\n"
            "Бонусҳои дода шуда: {total_rewarded}"
        ),
        "copy_referral_link": "📋 Пайвандро нусхабардорӣ кардан",
        "referral_link_copied": "Пайванд фиристода шуд",
        "back": "← Бозгашт",
        "copy_key": "📋 Калидро нусхабардорӣ кардан",
        "go_to_connection": "🔌 Ба пайвандшавӣ гузаштан",
        "renew_subscription": "🔁 Дастрасиро васеъ кардан",
        "no_active_subscription": "Обунаи фаъол ёфт нашуд.",
        # Профиль - новый формат
        "profile_welcome": "Хуш омадед ба Atlas Secure!\n\n👤 {username}\n\n💰 Балланс: {balance:.2f} ₽",
        "profile_subscription_active": "Обуна:\n— 🟢 То {date} фаъол аст",
        "profile_subscription_inactive": "Обуна:\n— 🔴 Фаъол нест",
        "profile_renewal_hint_new": "Ҳангоми васеъ кардан муддати интихобшуда\nба дастрасии ҷорӣ ба таври худкор илова карда мешавад.",
        "profile_buy_hint": "Барои дастрасӣ гирифтан дар меню «Хариди обуна»-ро пахш кунед.",
        "access_key_label": "Калиди дастрасӣ:",
        "subscription_history": "📄 Таърихи обунаҳо",
        "subscription_history_empty": "Таърихи обунаҳо холӣ аст",
        "subscription_history_action_purchase": "Харид",
        "subscription_history_action_renewal": "Тоза кардан",
        "subscription_history_action_reissue": "Додани калиди нав",
        "subscription_history_action_manual_reissue": "Аз нав додани калид",
        "select_tariff": "🕒 Муддати обунаро интихоб кунед\n\nAtlas Secure — ин дастрасии устувор аст,\nки оддӣ кор мекунад.\n\nДар ҳар як обуна:\n🔑 Калиди шахсӣ — танҳо барои шумо\n⚡️ Суръати устувор бе маҳдудият\n📱💻 Дар ҳамаи дастгоҳҳо кор мекунад\n💬 Дастгирӣ дар Telegram дар ҳар лаҳза\n\nМуддат чӣ қадар дарозтар бошад — шумо камтар\nдар бораи васеъ кардан ва мушкилоти дастрасӣ фикр мекунед.\n\nАксарияти корбарон обунаҳоро аз 3 моҳ интихоб мекунанд.",
        "enter_promo_button": "🎟 Промокодро ворид кунед",
        "enter_promo_text": "Промокодро ворид кунед:",
        "invalid_promo": "❌ Промокоди нодуруст",
        "promo_applied": "🎁 Промокод татбиқ шуд. Чекрамоии аллакай дар нарх дохил карда шудааст.",
        "promo_discount_label": "🎟 Промокод",
        "tariff_button_1": "1 моҳ · Барои ошноӣ · 149 ₽",
        "tariff_button_3": "3 моҳ · Бештар интихоб карда мешавад · 399 ₽ ⭐",
        "tariff_button_6": "6 моҳ · Камтар васеъ кардан · 599 ₽",
        "tariff_button_12": "12 моҳ · Дар бораи дастрасӣ фикр накардан · 899 ₽",
        "select_payment": "Усули пардохтро интихоб кунед.",
        "payment_test": "Реҷаи хизматӣ Дастрас нест",
        "payment_sbp": "СБП",
        "sbp_payment_text": "Пас аз интиқол, пардохтро тасдиқ кунед.\n\n⸻\n\nМаълумоти интиқол\n\nБонк: Ozon\nҲисоби корт: 2204321075030551\n\nМаблағи тасдиқ: {amount} ₽",
        "paid_button": "Пардохтро тасдиқ кардан",
        
        # Продление подписки
        "renewal_payment_text": "Барои васеъ кардани обуна пардохт кунед.\n\nВасеъ кардан ба ҳамон давра, ки дастрасии ҷорӣ, иҷро карда мешавад.",
        "renewal_pay_button": "💳 Пардохт кардан",
        
        "payment_pending": "Тасдиқ дар раванд аст\n\nПардохт ба қайд гирифта шуд.\nСанҷиш то 5 дақиқа давом мекунад.\nФаъолсозии дастрасӣ ба таври худкор иҷро мешавад.",
        "payment_approved": "✅ Дастрасӣ фаъол шуд\n\nКалиди шахсии дастрасии шумо омода аст.\nШумо метавонед пайванд шавед ва истифода бурданро оғоз кунед.\n\nКалиди шахсии дастрасӣ:\n\n{vpn_key}\n\nМуддати амали дастрасӣ:\nто {date}\n\nКалид ба шумо закреп шудааст\nва дар профили шумо дастрас хоҳад буд.\n\nПайванд 1 дақиқа зиёд вақт намегирад.\nАгар кӯмак лозим бошад — мо дар дастрасем.",
        "payment_rejected": "❌ Пардохт тасдиқ нашуд.\n\nАгар мӯътақид ҳастед, ки пардохт кардед — ба дастгирӣ муроҷиат кунед.",
        "profile_active": "👤 Профили дастрасӣ\n\nҲолати дастрасӣ: Фаъол\nДастрасӣ то {date} пардохт шудааст\n\nШумо пайванд шудед. Дастрасӣ устувор кор мекунад.\n\nКалиди шахсии дастрасӣ\nБарои пайванд дар барномаи Outline истифода мешавад.\nПайванд то дастрасӣ фаъол аст, нигоҳ дошта мешавад.\n\n{vpn_key}\n\nҲангоми васеъ кардан муддати интихобшуда\nба дастрасии ҷорӣ ба таври худкор илова карда мешавад.\n\nТо муддат анҷом наёбад, шумо\nба танзимот ва пардохт бозгашт кардан лозим нест.",
        "profile_renewal_hint": "",
        "profile_payment_check": "🕒 Пардохт дар санҷиш аст.\n\nИн процедураи стандартии амният аст.\nПас аз тасдиқ, дастрасӣ худкор пайдо мешавад.",
        "subscription_expiring_reminder": "⏳ Муддати дастрасӣ ба зудӣ анҷом мешавад.\n\nТо анҷоми обунаи шумо 3 рӯз боқӣ мондааст.\n\nШумо метавонед дар ҳар вақт дастрасиро васеъ кунед —\nхариди такрориҳо муддатро ба таври худкор васеъ мекунад.",
        
        # Умные напоминания - админ-доступ
        "reminder_admin_1day_6h": "⏳ Дастрасии муваққатии Atlas Secure дар 6 соат ба анҷом мерасад.\n\nМо тавсия медиҳем, ки обунаи пурраро тартиб диҳед,\nто дастрасии устуворро бе танаффус нигоҳ доред.",
        "reminder_admin_7days_24h": "⏳ Дастрасии муваққатии Atlas Secure дар 24 соат ба анҷом мерасад.\n\nМо тавсия медиҳем, ки обунаи 1 моҳаро тартиб диҳед\nбарои пайванди муттасил ва устувор.",
        
        # Умные напоминания - оплаченные тарифы
        "reminder_paid_3d": "⏳ Дастрасии шумо ба Atlas Secure дар 3 рӯз ба анҷом мерасад.\n\nШумо метавонед обунаро пеш аз вақт васеъ кунед,\nто аз танаффуси пайванд ҷилавгирӣ кунед.",
        "reminder_paid_24h": "⏳ Дастрасии шумо ба Atlas Secure дар 24 соат ба анҷом мерасад.\n\nМо тавсия медиҳем, ки обунаро пеш аз вақт васеъ кунед,\nто пайванди муттасилро нигоҳ доред.",
        "reminder_paid_3h": "⏳ Дастрасии шумо ба Atlas Secure дар 3 соат ба анҷом мерасад.\n\nҲоло обунаро васеъ кунед,\nто аз танаффуси пайванд ҷилавгирӣ кунед.",
        
        # Умные уведомления на основе трафика
        "smart_notif_no_traffic_20m": "Агар шумо ҳанӯз пайванд нашуда бошед —\nоддӣ ин 1 дақиқа зиёд вақт намегирад.\n\nКалид омода аст ва ба шумо закреп шудааст.",
        "smart_notif_no_traffic_24h": "Ёддошт:\nдастрасӣ фаъол аст ва барои истифода омода аст.\n\nПайванд ба танзимоти дастгоҳ таъсир намерасонад\nва иҷозатҳои иловагӣ талаб намекунад.",
        "smart_notif_first_connection": "Пайванд фаъол аст.\n\nДастрасӣ устувор кор мекунад\nва эътибори шуморо талаб намекунад.",
        "smart_notif_3days_usage": "Atlas Secure бе маҳдудият истифода мешавад\nва то анҷоми муддати дастрасӣ\nвасеъ кардани дастӣ талаб намекунад.",
        "smart_notif_7days_before_expiry": "Муддати дастрасӣ\nдар 7 рӯз ба анҷом мерасад.\n\nШумо метавонед онро пеш аз вақт васеъ кунед\nбе танаффуси пайванд.",
        "smart_notif_3days_before_expiry": "Ёддошт:\nдастрасӣ то 3 рӯз дигар фаъол хоҳад буд.\n\nВасеъ кардан камтар аз 1 дақиқа вақт мегирад\nва конфигуратсияи ҷорӣ нигоҳ дошта мешавад.",
        "smart_notif_expiry_day": "Имрӯз муддати дастрасӣ ба анҷом мерасад.\n\nҲангоми васеъ кардан калид ва танзимот нигоҳ дошта мешаванд.",
        "smart_notif_expired_24h": "Дастрасӣ қатъ шудааст.\n\nШумо метавонед онро дар ҳар вақт барқарор кунед —\nбе танзимоти такрорӣ.",
        "smart_notif_vip_offer": "Барои корбароне, ки дастрасии фаъол доранд\nсатҳи кушодаи дастгирӣ дастрас аст.\n\nОн ба таври худкор фурӯхта намешавад\nва ба таври шахсӣ баррасӣ карда мешавад.",
        
        # Приветственная скидка
        "welcome_discount_label": "🎁 Чекрамоии тавзеҳӣ",
        "subscribe_1_month_button": "🔐 Обуна барои 1 моҳ",
        "personal_discount_label": "🎯 Чекрамоии шахсӣ {percent}%",
        "vip_discount_label": "👑 Дастрасии VIP",
        "vip_access_button": "👑 Сатҳи дастрасиро беҳтар кардан",
        "vip_access_text": "👑 Дастрасии VIP Atlas Secure\n\nVIP — ин сатҳи кушодаи дастгирӣ аст\nбарои касоне, ки устуворӣ ва афзалиятро қадр мекунанд.\n\nVIP чӣ медиҳад:\n⚡️ Инфрасохтори афзалиятнок ва таъхир ҳадди ақал\n🛠 Конфигуратсияи шахсии дастрасӣ\n💬 Дастгирии афзалиятнок бе интизорӣ\n🚀 Дастрасии пеш аз вақт ба навсозиҳо\n\nVIP ба шумо мувофиқ аст, агар:\n• шумо ҳар рӯз аз дастрасӣ истифода мебаред\n• бо танзимот шуғл кардан намехоҳед\n• амали пешбинишударо қадр мекунед\n\nНарх:\n1 990 ₽ / моҳ\nyo 9 990 ₽ / 6 моҳ\n\nVIP бо обунаи фаъол фаъол мешавад.\nДархост гузоред — мо ҳама чизро мекунем.\n\nVIP — вақте ки дастрасӣ оддӣ мавҷуд аст,\nва шумо дар бораи он фикр намекунед.",
        "vip_status_badge": "👑 Ҳолати VIP фаъол аст",
        "vip_status_active": "👑 Ҳолати VIP-и шумо фаъол аст",
        "contact_manager_button": "💬 Дастрасии VIP-ро пайванд кардан",
        
        "no_subscription": "👤 Профили дастрасӣ\n\nДар вақти ҷорӣ дастрасӣ фаъол карда нашудааст.\n\nAtlas Secure таъмин мекунад\nдастрасии хусусии ҳимояшуда\nбо калиди пайванди шахсӣ.\n\nШумо метавонед дастрасиро\nдар ҳар вақти мусоид расмият диҳед.",
        "about_text": "ℹ️ Дар бораи хизмати Atlas Secure\n\nAtlas Secure — ин дастрасии хусусии ҳимояшуда\nба интернет аст, ки барои амали устувор\nва пешбинишуда сохта шудааст бе эътибори иловагӣ\nба танзимот ва тафсилот.\n\nМо инфрасохторро чунин тарҳрезӣ мекунем,\nто шумо танҳо аз дастрасӣ истифода баред,\nна дар бораи он, ки чӣ тавр кор мекунад, фикр кунед.\n\nИн дар амал чӣ медиҳад:\n\n🔐 Махфият ба таври сукут\nМо амалҳои корбаронро пайгирӣ намекунем,\nтаърихи пайвандҳоро нигоҳ намедорем\nва метамаълумотро ҷамъ намеорем.\n\n⚡️ Устуворӣ бе маҳдудият\nСерверҳои оптимизатсияшуда бе оверлоад\nамали ҳамвор ва пешбинишударо таъмин мекунанд.\n\n🌍 Инфрасохтори эътимоднок\nСерверҳои бахшидашуда дар минтақаҳои гуногун,\nки бо суръат ва эътимоднокӣ интихоб шудаанд.\n\n📱 Ҳамаи дастгоҳҳои шумо\nЯк дастрасӣ дар iOS, Android,\nmacOS ва Windows бе пардохтҳои иловагӣ ва маҳдудият кор мекунад.\n\nAtlas Secure ба шумо мувофиқ аст, агар:\n• бо маълумоти муҳим кор мекунед\n• устуворӣ ва назоратро қадр мекунед\n• бо технологияҳо шуғл кардан намехоҳед\n• тараҷумӣ мекунед, ки хизмат танҳо кор кунад\n\nAtlas Secure дар бораи функсияҳо нест.\nИн дар бораи оромӣ ва пешбинист.\n\nШумо пайванд шудед.\nҲама чизҳои дигар — дар фон кор мекунанд.",
        "privacy_policy": "Сиёсати махфият",
        "privacy_policy_text": "🔐 Сиёсати махфияти Atlas Secure\n\nAtlas Secure дар асоси принсипи\nкоҳиши маълумот сохта шудааст.\n\nМо маълумотеро, ки барои амали хизмат зарур нест,\nҷамъ намеорем ва нигоҳ намедорем.\n\nЧӣ чизеро мо НИГОҲ НАМЕДОРЕМ:\n• таърихи пайвандҳо\n• суроғаҳои IP ва трафики шабака\n• дархостҳои DNS\n• маълумот дар бораи манбаъҳои ташрифкардашуда\n• метамаълумоти фаъолияти корбар\n\nМеъмории хизмат дар асоси\nпринсипи Zero-Logs амалӣ карда шудааст.\n\nЧӣ чизеро коркард кардан мумкин аст:\n• ҳолати дастрасӣ\n• муддати амали обуна\n• идентификатори техникии калид\n\nИн маълумот бо\nфаъолияти шабакавии шумо алоқаманд нест.\n\nПардохтҳо:\nAtlas Secure маълумоти пардохтӣ\nкоркард ва нигоҳдорӣ намекунад.\nПардохт тавассути\nсистемаҳои бонкӣ ва пардохтӣ\nберун аз инфрасохтори мо амалӣ мешавад.\n\nИнтиқоли маълумот:\nМо маълумотро ба шахсони сеюм намегузаронем\nва пайгирӣ,\nаналитика ё SDK-ҳои рекламавиро истифода намебарем.\n\nДастгирӣ:\nМо танҳо маълумотеро коркард мекунем,\nки шумо ихтиёриашон пешниҳод кардаед\nбарои ҳалли дархости муайян.\n\nAtlas Secure.\nМахфият дар\nмеъмории хизмат ҷойгир карда шудааст.",
        "service_status": "📊 Вазъияти хизмат",
        "service_status_text": "📊 Вазъияти хизмати Atlas Secure\n\nВазъияти ҷорӣ: 🟢 Хизмат устувор кор мекунад\n\nҲамаи компонентҳои асосӣ\nдар реҷаи оддӣ кор мекунанд:\n• дастрасӣ фаъол аст\n• додани калидҳо кор мекунад\n• дастгирӣ дастрас аст\n\nAtlas Secure ҳамчун инфрасохтори\nрақамии шахсӣ сохта шудааст\nбо афзалияти устуворӣ\nва амали пешбинишуда.\n\nТамоюлҳои мо:\n• вахти кори ҳадаф — 99.9%\n• корҳои банақшагирифта пеш аз вақт анҷом дода мешаванд\n• ҳодисаҳои ҷиддӣ\n  дар тартиби афзалиятӣ ҳал карда мешаванд\n• гум шудани маълумот аз ҷиҳати меъморӣ истисно карда шудааст\n\nДар сурати корҳои техникӣ\nё тағйирот корбарон\nтавассути бот пеш аз вақт огоҳ карда мешаванд.\n\nНавсозии охирини вазъият:\nба таври худкор",
        "support_text": "🛡 Дастгирии Atlas Secure\n\nАгар шумо саволҳо дар бораи дастрасӣ,\nпардохт ё амали хизмат дошта бошед —\nба мо бевосита нависед.\n\nМо ба таври дастӣ ҷавоб медиҳем\nва мурожаатҳоро\nдар тартиби афзалиятӣ баррасӣ мекунем.\n\nШумо метавонед дар ҳар вақт ба дастгирӣ муроҷиат кунед — мо дар дастрасем.",
        "change_language": "🌍 Тағйири забон",
        
        # Инструкция
        "instruction_text": "🔌 Пайвастшавӣ\n\nДастрасӣ тавассути калиди шахсӣ кор мекунад.\nТанзимот 1 дақиқа зиёд вақт намегирад.\n\n1️⃣ 🔑 Калиди дастрасиро гиред\nКалид пас аз фаъолсозии обуна ба таври худкор пайдо мешавад.\n\n2️⃣ 📥 Барномаи Outline-ро насб кунед\nБарномаро аз мағозаи расмӣ боргирӣ кунед\nбарои системаи оператсионии шумо.\n\n3️⃣ ➕ Пайванд шавед\nOutline-ро кушоед, «+» -ро пахш кунед ва калидро қошида кунед.\nПайванд ба таври худкор фаъол мешавад.\n\n✅ Пас аз пайванд ҳеч чиз танзим кардан лозим нест.\nДастрасӣ то обуна фаъол аст, кор мекунад.",
        "admin_payment_notification": "💰 Пардохти нав\nКорбар: @{username}\nTelegram ID: {telegram_id}\nТариф: {tariff} моҳ\nНарх: {price} ₽",
        "admin_approve": "Тасдиқ кардан",
        "admin_reject": "Рад кардан",
        "admin_grant_access": "🟢 Дастраси додан",
        "admin_revoke_access": "🔴 Дастраси бекор кардан",
        "admin_grant_days_prompt": "Муддати дастрасиро интихоб кунед:",
        "admin_grant_days_1": "1 рӯз",
        "admin_grant_days_7": "7 рӯз",
        "admin_grant_days_14": "14 рӯз",
        "admin_grant_minutes_10": "⏱ Дастрасӣ барои 10 дақиқа",
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
        "admin_grant_user_notification_10m": (
            "⏱ Дастрасӣ барои 10 дақиқа фаъол карда шуд.\n\n"
            "Шумо метавонед фавран пайванд шавед.\n"
            "Пас аз анҷом, дастрасӣ ба таври худкор боздошта мешавад."
        ),
        "admin_revoke_user_notification": "⛔ Дастрасии шумо ба Atlas Secure аз ҷониби мудир бекор карда шуд.",
        
        # Ошибки (для пользователей)
        "error_payment_processing": "Хатогии коркарди пардохт. Лутфан, ба дастгирӣ муроҷиат кунед.",
        "error_subscription_activation": "Хатогии фаъолсозии обуна. Лутфан, ба дастгирӣ муроҷиат кунед.",
        "error_tariff": "Хатогии тариф",
        "error_no_active_subscription": "Обунаи фаъол ёфт нашуд",
        "error_payment_create": "Хатогии эҷоди ҳисоб. Лутфан, баъдтар такрор кунед.",
        "error_payments_unavailable": "Пардохтҳо барои муддати муайян дастрас нестанд",
        "error_access_denied": "Дастрасӣ рад карда шуд.",
        "error_start_command": "Лутфан, бо фармони /start оғоз кунед",

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
    """
    Получить переведенный текст (STRICT MODE)
    
    STRICT-LOCALIZATION:
    - Если ключ отсутствует в выбранном языке - логируется предупреждение
    - Используется fallback на русский (НЕ английский)
    - Если ключ отсутствует даже в русском - логируется критическая ошибка
    - Используется default или сам ключ только в крайнем случае
    """
    import logging
    logger = logging.getLogger(__name__)
    
    lang = language if language in TEXTS else "ru"
    text = TEXTS[lang].get(key)
    
    # Если ключ не найден в выбранном языке - логируем предупреждение
    if text is None:
        logger.warning(f"Localization key '{key}' not found for language '{lang}', falling back to 'ru'")
        text = TEXTS["ru"].get(key)
        
        # Если ключ не найден даже в русском - критическая ошибка
        if text is None:
            logger.error(f"CRITICAL: Localization key '{key}' not found even in 'ru'! Missing key must be added to localization.py")
            text = default if default is not None else key
    
    try:
        return text.format(**kwargs) if kwargs else text
    except KeyError as e:
        logger.error(f"Localization key '{key}' format error: missing parameter {e}")
        return text  # Возвращаем текст без форматирования


# Кнопки для выбора языка
LANGUAGE_BUTTONS = {
    "ru": "Русский",
    "en": "English",
    "uz": "O'zbek",
    "tj": "Тоҷикӣ",
}
