# -*- coding: utf-8 -*-
"""Russian (ru) strings. Canonical language."""

LANG = {
    'admin.access_denied': "Недостаточно прав доступа",
    'admin.activation_error_action': "Требуется ручная активация.",
    'admin.activation_error_attempts': "Попыток: {attempts}/{max_attempts}",
    'admin.activation_error_error': "Ошибка: <code>{error_msg}</code>",
    'admin.activation_error_status': "Подписка помечена как <code>failed</code>.",
    'admin.activation_error_subscription_id': "Подписка ID: <code>{subscription_id}</code>",
    'admin.activation_error_title': "⚠️ <b>ОШИБКА АКТИВАЦИИ VPN ПОДПИСКИ</b>",
    'admin.activation_error_user': "Пользователь: <code>{telegram_id}</code>",
    'admin.copy_key': "📋 Скопировать ключ",
    'admin.degraded_mode': "⚠️ <b>БОТ РАБОТАЕТ В ДЕГРАДИРОВАННОМ РЕЖИМЕ</b>\n\nБаза данных недоступна.\n\n• Бот запущен и отвечает на команды\n• Критические операции блокируются\n• Пользователи получают сообщения о временной недоступности\n\nБот будет автоматически пытаться восстановить соединение с БД каждые 30 секунд.\n\nПроверьте:\n• Доступность PostgreSQL\n• Правильность DATABASE_URL\n• Сетевые настройки",
    'admin.go_to_instruction': "🔌 Перейти к инструкции",
    'admin.my_profile': "👤 Мой профиль",
    'admin.pending_activations_row': "{idx}. ID: <code>{subscription_id}</code> | User: <code>{telegram_id}</code> | Попыток: {attempts} | С {pending_since}\n   Ошибка: <code>{error}</code>\n",
    'admin.pending_activations_title': "⚠️ <b>ОТЛОЖЕННЫЕ АКТИВАЦИИ VPN</b>\n",
    'admin.pending_activations_top': "\n<b>Топ-5 старейших:</b>\n",
    'admin.pending_activations_total': "Всего pending подписок: <b>{count}</b>\n",
    'admin.recovered': "✅ <b>СЛУЖБА ВОССТАНОВЛЕНА</b>\n\nБаза данных стала доступна.\n\n• Бот работает в полнофункциональном режиме\n• Все операции восстановлены\n• Фоновые задачи запущены",
    'buy.button_price': "{period} — {price} ₽",
    'buy.button_price_badge': "{period} — {price} ₽ {badge}",
    'buy.button_price_discount': "{period} — {base} → {final} ₽",
    'buy.button_price_discount_badge': "{period} — {base} → {final} ₽ {badge}",
    # --- Бизнес-тарифы ---
    'buy.period_24_months': "24 месяца",
    # --- Бизнес-экраны ---
    'buy.enter_promo_text': "Введите промокод:",
    'buy.invoice_description': "Atlas Secure VPN тариф {tariff_name}, подписка {months} мес.",
    'buy.invoice_label': "К оплате",
    'buy.period_1': "1 мес",
    'buy.period_2_4': "{months} мес",
    'buy.period_5_plus': "{months} мес",
    'buy.promo_applied': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Промокод применён. Скидка уже учтена в цене.",
    'buy.promo_enter_text_hint': "Пожалуйста, введите промокод текстом.",
    'buy.renew_button': "🔐 Купить / Продлить подписку",
    'buy.select_basic_new': "⚡️ Выбрать Basic",
    'buy.select_basic_renew': "⚡️ Продлить Basic",
    'buy.select_basic_switch': "⚡️ Перейти на Basic",
    'buy.select_plus_new': "👑 Выбрать Plus",
    'buy.select_plus_renew': "👑 Продлить Plus",
    'buy.select_plus_switch': "👑 Перейти на Plus",
    'buy.tariff_basic': "<tg-emoji emoji-id=\"5354810459801295109\">⚡</tg-emoji> <b>Basic</b> — для себя и семьи\n<blockquote>Без блокировок, без тормозов, без нервов\n10 устройств · от 199 ₽/мес</blockquote>",
    'buy.tariff_basic_desc': "⚡️ <b>Тариф: Basic</b>\n\n<blockquote>🚀 Канал до 25 Гбит/с — YouTube 4K без тормозов\n🌐 10 ГБ обхода белых списков — в подарок к подписке\n👨‍👩‍👧‍👦 Одна подписка на всю семью — до 10 устройств\n➕ Подключение в одно нажатие</blockquote>",
    'buy.tariff_basic_description': "⚡️ <b>Тариф: Basic</b>\n\n<blockquote>🚀 Канал до 25 Гбит/с — YouTube 4K без тормозов\n🌐 10 ГБ обхода белых списков — в подарок к подписке\n👨‍👩‍👧‍👦 Одна подписка на всю семью — до 10 устройств\n➕ Подключение в одно нажатие</blockquote>",
    'buy.tariff_plus': "<tg-emoji emoji-id=\"5217822164362739968\">👑</tg-emoji> <b>Plus</b> — когда важна скорость\n<blockquote>Приоритетный канал · 3x быстрее Basic\n14 устройств · от 349 ₽/мес</blockquote>",
    'buy.tariff_plus_desc': "👑 <b>Тариф: Plus</b>\n\n<blockquote>⚡️ Канал до 75 Гбит/с — стримы и игры без лагов\n🔄 Резервные каналы — соединение работает всегда\n🌐 10 ГБ обхода белых списков — в подарок к подписке\n👨‍👩‍👧‍👦 Одна подписка на всю семью — до 14 устройств</blockquote>",
    'buy.tariff_plus_description': "👑 <b>Тариф: Plus</b>\n\n<blockquote>⚡️ Канал до 75 Гбит/с — стримы и игры без лагов\n🔄 Резервные каналы — соединение работает всегда\n🌐 10 ГБ обхода белых списков — в подарок к подписке\n👨‍👩‍👧‍👦 Одна подписка на всю семью — до 14 устройств</blockquote>",
    'buy.tariff_basic_selected': "⚡ <b>Basic</b> — выберите срок",
    'buy.tariff_button_1': "1 месяц · Для знакомства · 149 ₽",
    'buy.tariff_button_12': "12 месяцев · Не думать о доступе · 899 ₽",
    'buy.tariff_button_3': "3 месяца · Чаще всего выбирают · 399 ₽ ⭐",
    'buy.tariff_button_6': "6 месяцев · Реже продлевать · 599 ₽",
    'buy.tariff_label_basic': "🪙 Basic",
    'buy.tariff_label_plus': "👑 Plus",
    'buy.tariff_plus_selected': "👑 <b>Plus</b> — выберите срок",
    'buy.tariff_select_basic_button': "⚡️ Выбрать Basic",
    'buy.tariff_select_plus_button': "👑 Выбрать Plus",
    'common.back': "Назад",
    'common.button_outdated': "Эта кнопка устарела. Откройте меню заново.",
    'common.rate_limit_message': "Слишком много запросов. Попробуйте позже.",
    'common.user': "Пользователь",
    'errors.db_init_stage_warning': "⚠️ База данных ещё инициализируется (STAGE). Некоторые функции могут быть недоступны.",
    'errors.generic': "Ошибка",
    'errors.insufficient_balance': "Недостаточно средств на балансе.\n\nСтоимость: {amount:.2f} ₽\nНа балансе: {balance:.2f} ₽\nНе хватает: {shortage:.2f} ₽",
    'errors.invalid_amount': "Неверная сумма",
    'errors.payment_create': "Ошибка при создании счета. Попробуйте позже.",
    'errors.payment_min_amount': "Сумма после скидки ниже минимальной для оплаты картой (64 ₽).\nПожалуйста, выберите другой тариф.",
    'errors.payment_processing': "Ошибка обработки платежа. Обратитесь в поддержку.",
    'errors.payments_unavailable': "Платежи временно недоступны",
    'errors.profile_load': "Ошибка загрузки профиля. Попробуйте позже.",
    'errors.session_expired': "⏳ Сессия устарела. Пожалуйста, попробуйте ещё раз.",
    'errors.session_expired_processing': "Оплата уже обрабатывается. Пожалуйста, подождите.",
    'errors.database_unavailable': "⚠️ Сервис временно недоступен. Попробуйте через минуту.",
    'errors.start_command': "Пожалуйста, начните с команды /start",
    'errors.subscription_activation': "Ошибка активации подписки. Обратитесь в поддержку.",
    'errors.tariff': "Ошибка тарифа",
    'errors.try_later': "⚠️ Произошла ошибка. Попробуйте позже.",
    'get_key.no_subscription': "❌ У вас нет активной подписки.",
    'setup.connect_ios': "📋 <b>Ручная установка — 3 простых шага</b>\n\n1. Нажмите на ключ ниже — он скопируется\n2. Откройте Happ или Incy\n3. Нажмите <b>+</b> в правом верхнем углу или <b>«Вставить»</b> (Incy) и вставьте из буфера (📋)\n\nПовторите для второго ключа.",
    'setup.connect_android': "📋 <b>Ручная установка — 3 простых шага</b>\n\n1. Нажмите на ключ ниже — он скопируется\n2. Откройте Happ\n3. Нажмите <b>+</b> в правом верхнем углу и вставьте из буфера (📋)\n\nПовторите для второго ключа.",
    'setup.connect_macos': "📋 <b>Ручная установка — 3 простых шага</b>\n\n1. Нажмите на ключ ниже — он скопируется\n2. Откройте Happ\n3. Нажмите <b>+</b> в правом верхнем углу и вставьте из буфера (📋)\n\nПовторите для второго ключа.",
    'setup.connect_windows': "📋 <b>Ручная установка — 3 простых шага</b>\n\n1. Нажмите на ключ ниже — он скопируется\n2. Откройте Happ\n3. Нажмите <b>+</b> в правом верхнем углу и вставьте из буфера (📋)\n\nПовторите для второго ключа.",
    'setup.manual_button': "📖 Ручная установка",
    'setup.key_vpn_label': "🔑 <b>VPN ключ Happ</b> (обычные безлимитные сервера):",
    'setup.key_bypass_label': "🔑 <b>Обход ключ Happ</b> (белые списки РФ):",
    'setup.key_vpn_incy_label': "💚 <b>VPN ключ Incy</b> (обычные безлимитные сервера):",
    'setup.key_bypass_incy_label': "💚 <b>Обход ключ Incy</b> (белые списки РФ):",
    'setup.manual_alt_hint': "❓ <b>Не получается?</b>\nПопробуй альтернативный ключ ниже — подходит для любого клиента (V2Box, Incy, Happ и другие). Просто скопируй и вставь в приложение:",
    'setup.alt_key_premium': "🔑 <b>Премиум ключ</b> (без шифрования):",
    'setup.alt_key_bypass': "🌐 <b>Обход ключ</b> (без шифрования):",
    'setup.done_button': "✅ Готово",
    'setup.qr_button': "📲 Добавить устройство",
    'setup.qr_choose_type': "📲 <b>Добавить устройство</b>\n\nВыберите тип подключения:",
    'setup.qr_choose_app': "📲 <b>Добавить устройство</b>\n\nВыберите приложение, в котором будете подключаться:",
    'setup.qr_app_btn_incy': "💚 Incy",
    'setup.qr_app_btn_happ': "Happ",
    # ── Поделиться скидкой (broadcast share-discount feature) ────────
    'share_discount.screen': (
        "🎁 <b>Подари другу скидку 30%</b>\n\n"
        "Отправь ссылку другу — он получит скидку <b>30%</b> на любой "
        "тариф (Basic / Plus / Комбо).\n\n"
        "⏱ Скидка действует <b>24 часа</b> с момента активации.\n"
        "👤 Один человек может активировать скидку только один раз.\n\n"
        "<b>Твоя ссылка:</b>\n"
        "<blockquote expandable><code>{link}</code></blockquote>"
    ),
    'share_discount.send_button': "📤 Отправить ссылку другу",
    'share_discount.share_text': (
        "🎁 Держи скидку 30% на Atlas Secure — действует 24 часа после "
        "перехода по ссылке."
    ),
    'share_discount.activated': (
        "🎉 <b>Скидка 30% активирована!</b>\n\n"
        "Действует <b>24 часа</b> на тарифы Basic, Plus и Комбо.\n"
        "Выбирай тариф и оформляй подписку 👇"
    ),
    'share_discount.already_claimed': (
        "ℹ️ Ты уже активировал эту скидку раньше — повторно использовать "
        "её нельзя."
    ),
    'share_discount.self_blocked': (
        "🚫 Нельзя активировать скидку по собственной ссылке — отправь её "
        "другу."
    ),
    'setup.qr_standard_btn': "Обычные сервера (безлимит)",
    'setup.qr_bypass_btn': "Обход белых списков",
    'setup.qr_bypass_unavailable': "❌ Обход белых списков недоступен.\n\nУбедитесь, что у вас есть активная подписка и трафик обхода.",
    'setup.qr_instruction': "📲 <b>Добавить устройство</b>\n\n<b>По QR-коду:</b>\nОтсканируйте код выше в Happ → 📸 иконка камеры.\n\n<b>Вручную:</b>\nНажмите на ключ ниже — он скопируется, затем в Happ → иконка буфера внизу.",
    'setup.qr_instruction_incy': "📲 <b>Добавить устройство</b>\n\n<b>По QR-коду:</b>\nОтсканируйте код выше в Incy → 📸 иконка камеры.\n\n<b>Вручную:</b>\nНажмите на ключ ниже — он скопируется, затем в Incy → <b>«Вставить»</b>.",
    'setup.download_happ': "📲 Скачать Happ",
    'setup.select_device': "📱 <b>Выберите устройство для подключения</b>\n\nМы подберём приложение и проведём через установку — займёт 1 минуту.",
    'setup.install_app': "📲 <b>Осталось скачать приложение</b>\n\nIncy — бесплатное приложение для VPN.\nАльтернатива — Happ.\nНажмите кнопку — откроется магазин приложений.\n\n<blockquote>Уже установлен Happ или Incy? Нажмите «Дальше».</blockquote>",
    'setup.install_happ_ru': "📲 Скачать Happ (Россия)",
    'setup.install_happ_global': "📲 Скачать Happ (другой регион)",
    'setup.next_step': "➡️ Дальше",
    'setup.key_install_title': "⚡️ <b>Подключитесь в одно нажатие</b>\n\nНажмите кнопки ниже — ключи добавятся автоматически.\nДобавьте оба для полноценной работы.\n\n<blockquote>🔑 VPN — основные сервера, весь интернет без блокировок\n🌐 Обход — белые списки РФ, интернет работает в любой точке мира</blockquote>",
    'setup.key_install_title_agg': "⚡️ <b>Подключитесь в одно нажатие</b>\n\nНажмите <b>«Добавить ключ»</b> в вашем приложении — подписка импортируется автоматически.\n\nЕсли по каким-то причинам не открылось — нажмите <b>«Настроить вручную»</b> ниже.\n\n<blockquote>🌐 Трафик расходуется <b>только</b> на серверах с пометкой <b>LTE</b>.\n🚀 Безлимитные серверы работают без расхода ГБ.</blockquote>",
    'setup.btn_add_happ': "📥 Добавить ключ в Happ",
    'setup.btn_add_incy': "💚 Добавить ключ в Incy",
    'setup.btn_add_v2raytun': "🚀 Добавить ключ в V2RayTun",
    'setup.btn_manual_setup': "⚙️ Настроить вручную",
    'setup.key_happ_label': "🔑 <b>Ключ Happ</b> (скопируйте и вставьте в приложение):",
    'setup.key_incy_label': "💚 <b>Ключ Incy</b> (скопируйте и вставьте в приложение):",
    'setup.btn_done': "✅ Готово",
    'setup.btn_manual': "📋 Установить вручную",
    'setup.btn_need_help': "💬 Нужна помощь",
    'setup.combined_ios': "📱 <b>Подключение на iOS</b>\n\n1️⃣ Скачайте приложение, если ещё не установлено\n2️⃣ Нажмите на название приложения:\n\n🌐 — стандартные сервера (безлимит)\n🤍 — новые сервера (обход белых списков)\n\n<blockquote>Если ваш регион РФ и нет установленных приложений — скачивайте <b>Happ</b> ⚡️\nЕсли уже установлено одно из приложений — просто нажмите на 🌐 название и 🤍 название</blockquote>",
    'setup.combined_android': "🤖 <b>Подключение на Android</b>\n\n1️⃣ Скачайте приложение, если ещё не установлено\n2️⃣ Нажмите на название приложения:\n\n🌐 — стандартные сервера (безлимит)\n🤍 — новые сервера (обход белых списков)\n\n<blockquote>Если ваш регион РФ и нет установленных приложений — скачивайте <b>Happ</b> ⚡️\nЕсли уже установлено одно из приложений — просто нажмите на 🌐 название и 🤍 название</blockquote>",
    'setup.combined_macos': "🍎 <b>Подключение на macOS</b>\n\n1️⃣ Скачайте приложение, если ещё не установлено\n2️⃣ Нажмите на название приложения:\n\n🌐 — стандартные сервера (безлимит)\n🤍 — новые сервера (обход белых списков)\n\n<blockquote>Если ваш регион РФ и нет установленных приложений — скачивайте <b>Happ</b> ⚡️\nЕсли уже установлено одно из приложений — просто нажмите на 🌐 название и 🤍 название</blockquote>",
    'setup.combined_windows': "🪟 <b>Подключение на Windows</b>\n\n1️⃣ Скачайте приложение, если ещё не установлено\n2️⃣ Нажмите на название приложения:\n\n🌐 — стандартные сервера (безлимит)\n🤍 — новые сервера (обход белых списков)\n\n<blockquote>Если уже установлено одно из приложений — просто нажмите на 🌐 название и 🤍 название</blockquote>",
    'incident.banner': "⚠️ Ведутся технические работы",
    'instruction._open_guide': "📖 Инструкция по установке",
    'instruction._text': "📖 Инструкция по установке\n\nДля настройки подключения перейдите\nв мини-приложение — там вы найдёте\nпошаговую инструкцию по установке\nи подключению на вашем устройстве.",
    'lang.button_en': "🇺🇸 English",
    'lang.button_ru': "🇷🇺 Русский",
    'start_lang.title': "🌍 Выберите язык / Select language",
    'lang.changed_toast': "✅ Язык изменён",
    'lang.select': "🌍 Выбери язык:\n🇷🇺 Русский\n🇺🇸 English\n🇺🇿 O'zbek\n🇹🇯 Тоҷикӣ",
    'main.about_text': "Atlas Secure — цифровая экосистема,\nразвёрнутая внутри Telegram.\n\n🔐 Архитектура без хранения логов\n⚡ Высокая и стабильная скорость соединения\n📶 Корректная работа в LTE / 5G / Wi-Fi\n🧩 Персональные ключи доступа\n🇪🇺 Серверы в нескольких странах\n🛡 Конфиденциальность по умолчанию\n\n🌍 Многоязычный интерфейс\n💳 Безопасные способы оплаты\n\nЭкосистема выстроена так,\nчтобы соединение оставалось стабильным,\nа управление — простым и прозрачным.",
    'main.about_title': "🔎 О сервисе Atlas Secure",
    'main.balance_topup_success': "✅ Баланс успешно пополнен на {amount:.2f} ₽",
    'main.buy': "🔐 Купить подписку",
    'main.invalid_promo': "❌ Неверный промокод",
    'main.our_channel': "Наш канал",
    'main.pay_balance': "💰 Баланс (доступно: {balance:.2f} ₽)",
    'main.pay_with_card': "💳 Оплатить картой",
    'main.privacy_policy': "Политика конфиденциальности",
    'main.privacy_policy_text': "🔐 Политика конфиденциальности Atlas Secure\n\nAtlas Secure построен на принципе\nминимизации данных.\n\nМы не собираем и не храним информацию,\nкоторая не требуется для работы сервиса.\n\nЧто мы НЕ храним:\n• историю подключений\n• IP-адреса и сетевой трафик\n• DNS-запросы\n• данные о посещаемых ресурсах\n• метаданные пользовательской активности\n\nАрхитектура сервиса реализована\nпо принципу Zero-Logs.\n\nЧто может обрабатываться:\n• статус доступа\n• срок действия подписки\n• технический идентификатор ключа\n\nЭти данные не связаны\nс вашей сетевой активностью.\n\nПлатежи:\nAtlas Secure не обрабатывает\nи не хранит платёжные данные.\nОплата проходит через\nбанковские и платёжные системы\nвне нашей инфраструктуры.\n\nПередача данных:\nМы не передаём данные третьим лицам\nи не используем трекеры,\nаналитику или рекламные SDK.\n\nПоддержка:\nМы обрабатываем только ту информацию,\nкоторую вы добровольно предоставляете\nдля решения конкретного запроса.\n\n🔒 Политика конфиденциальности: <a href=\"https://telegra.ph/Politika-konfidencialnosti-02-16-32\">читать</a>\n📜 Пользовательское соглашение: <a href=\"https://telegra.ph/Polzovatelskoe-soglashenie-04-10-27\">читать</a>\n\nAtlas Secure.\nКонфиденциальность заложена\nв архитектуре сервиса.",
    'main.profile': "👤 Личный кабинет",
    'games.menu_title': "<tg-emoji emoji-id=\"5319247469165433798\">🎮</tg-emoji> Добро пожаловать в Игровой зал!\nЗдесь вы можете отвлечься и попытать удачу — а заодно выиграть призы и бонусы.\n\n<tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji> Боулинг — сбей кегли и получи бонусные дни подписки\n<tg-emoji emoji-id=\"5280816565657300091\">🎲</tg-emoji> Кубики — брось кубик и получи столько дней, сколько выпало\n<tg-emoji emoji-id=\"5226813248900187912\">💣</tg-emoji> Бомбер — стратегическая игра на выживание\n<tg-emoji emoji-id=\"5474417568053745249\">🌱</tg-emoji> Ферма — выращивай растения и получай рубли на баланс\n\nВыбирай игру и испытай удачу! <tg-emoji emoji-id=\"5258040062028822951\">🍀</tg-emoji>",
    'games.button_bowling': "🎳 Боулинг",
    'games.button_dice': "🎲 Кубики",
    'games.button_bomber': "💣 Бомбер",
    'games.back_to_games': "🔙 К играм",
    'games.bowling_cooldown': "Боулинг-клуб закрыт <tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji>\nСледующая игра доступна через: {days}д {hours}ч",
    'games.bowling_paywall': "<tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji> Боулинг-клуб только для подписчиков!\n\nПриобретите подписку, чтобы играть.",
    'games.bowling_strike_success': "<tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji> <b>Страйк!</b> Все кегли сбиты!\n\n🎉 Поздравляем! Вы выиграли +7 дней подписки.\n\nДоступ до: {date}",
    'games.bowling_strike_error': "<tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji> <b>Страйк!</b> Все кегли сбиты!\n\n🎉 Поздравляем! Вы выиграли +7 дней подписки.\n\n⚠️ Ошибка при начислении. Обратитесь в поддержку.",
    'games.bowling_no_strike': "<tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji> Вы сбили {value} кеглей из 6.\n\nУвы, не страйк 😔 Попробуйте снова через 7 дней!",
    'games.dice_cooldown': "⏳ Вы уже бросали кубик!\nСледующий бросок доступен через: {days} дней {hours} часов",
    'games.dice_paywall': "<tg-emoji emoji-id=\"5280816565657300091\">🎲</tg-emoji> Игра в кубики только для подписчиков!\n\nПриобретите подписку, чтобы играть.",
    'games.dice_success': "<tg-emoji emoji-id=\"5280816565657300091\">🎲</tg-emoji> Выпало: {value}!\n\n🎉 Вам начислено {value} дней подписки!\n\nВаша подписка действует до: {date}",
    'games.dice_error': "<tg-emoji emoji-id=\"5280816565657300091\">🎲</tg-emoji> Выпало: {value}!\n\n🎉 Вам начислено {value} дней подписки!\n\n⚠️ Ошибка при начислении. Обратитесь в поддержку.",
    'games.bomber_rules': "<tg-emoji emoji-id=\"5226813248900187912\">💣</tg-emoji> Бомбер\n\nПравила:\n• Размещайте бомбы на поле, избегая мин бота\n• Если наступите на свою бомбу — взрыв! 💥\n• Если наступите на мину бота — взрыв! 💥\n• Нажмите 'Завершить' чтобы безопасно выйти\n\nУдачи! <tg-emoji emoji-id=\"5258040062028822951\">🍀</tg-emoji>",
    'games.bomber_finish': "🚩 Завершить",
    'games.bomber_self_destruct': "🧨 БУМ! Вы подорвались на своей бомбе!\n\nИгра окончена. Попробуйте ещё раз!",
    'games.bomber_mine_exploded': "💥 БУМ! Вы подорвались на мине бота!\n\nИгра окончена. Попробуйте ещё раз!",
    'games.bomber_safe_exit': "😮‍💨 Вы вышли из игры целым!\n\nВыжило бомб: {count}",
    'games.button_farm': "🌾 Ферма",
    'main.promo_applied': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Промокод применён. Скидка уже учтена в цене.",
    'main.reissue_notification_text': "Ваш VPN-ключ обновлён\nи переведён на новую версию сервера.\n\nДля корректной работы:\n— удалите старый ключ из VPN-приложения\n— добавьте новую ссылку подписки\n\nСсылка подписки:\n\n{sub_url}\n\nОбновление необходимо для сохранения\nстабильности и производительности соединения.",
    'main.reissue_notification_title': "🔐 Обновление VPN-ключа",
    'main.service_unavailable': "⚠️ Сервис временно недоступен. Попробуйте позже.",
    'main.service_unavailable_payment': "✅ <b>Оплата получена.</b>\n\nСервис сейчас временно недоступен, поэтому оплаченное выдадим вручную в ближайшее время — мы уже получили уведомление.\n\n<b>Повторно платить не нужно.</b>\n\nЕсли есть вопросы, напишите в поддержку. Код обращения: <code>{ref}</code>",
    'main.support': "🛡 Поддержка",
    'main.support_button': "🆘 Поддержка",
    'main.topup_amount_invalid': "Пожалуйста, введите число.",
    'main.topup_amount_too_high': "Максимальная сумма пополнения: 100 000 ₽. Пожалуйста, введите меньшую сумму.",
    'main.topup_amount_too_low': "Минимальная сумма пополнения: 100 ₽. Пожалуйста, введите сумму не менее 100 ₽.",
    'main.topup_balance_select_amount': "💳 <b>Пополнение баланса</b>\n\nТекущий баланс: {balance} ₽\n\nПополните баланс — и оплачивайте подписку, трафик и подарки с него в один тап.",
    'main.topup_balance_success': "✅ <b>Баланс пополнен на {amount:.2f} ₽</b>\n\nНа счёте: {balance:.2f} ₽",
    'main.topup_custom_amount': "Другая сумма",
    'main.topup_enter_amount': "Введите свою сумму от 100 ₽",
    'main.topup_invoice_description': "Пополнение баланса на {amount} ₽",
    'main.topup_invoice_label': "Пополнение баланса",
    'main.topup_invoice_title': "Пополнение баланса Atlas Secure",
    'main.topup_select_payment_method': "Пополнение баланса на {amount} ₽\n\nВыберите способ оплаты:",
    'main.trial_activation_error': "❌ Ошибка активации пробного периода. Попробуйте позже или обратитесь в поддержку.",
    'main.trial_not_available': "❌ Пробный период недоступен. Вы уже использовали его ранее или имеете активную подписку.",
    'main.welcome': "<tg-emoji emoji-id=\"5462902520215002477\">💎</tg-emoji> <b>Atlas Secure</b>\n\nИнтернет без блокировок, нервов и ограничений.\n\n<tg-emoji emoji-id=\"5258203794772085854\">⚡️</tg-emoji> Молниеносное соединение\n<tg-emoji emoji-id=\"5226928895189598791\">⭐️</tg-emoji> Трафик под защитой 24/7\n🏆 Премия «Надежный VPN 2026»\n\n<tg-emoji emoji-id=\"5193065010795911968\">🛍</tg-emoji> В нашем магазине — пополняй Steam, покупай Telegram Premium и многое другое",
    'main.welcome_no_sub': "<tg-emoji emoji-id=\"5462902520215002477\">💎</tg-emoji> <b>Atlas Secure</b> — это свободный, быстрый и безопасный интернет\n\n<tg-emoji emoji-id=\"5447410659077661506\">🌐</tg-emoji> Обход белых списков\n<tg-emoji emoji-id=\"5188481279963715781\">🚀</tg-emoji> Скорость 75 Гбит/с\n<tg-emoji emoji-id=\"5456140674028019486\">⚡️</tg-emoji> Подключение за минуту — без настроек и нервов\n📱 Одна подписка — вся семья подключена\n\n<tg-emoji emoji-id=\"5193065010795911968\">🛍</tg-emoji> В нашем магазине — пополняй Steam, покупай Telegram Premium и многое другое\n\n100 000+ пользователей нам доверяют.",
    'main.welcome_expired': "<tg-emoji emoji-id=\"5462902520215002477\">💎</tg-emoji> <b>Atlas Secure</b>\n\nПодписка закончилась — но всё легко вернуть.\n\nВаш ключ и настройки сохранены.\nНажмите кнопку — VPN заработает снова.\n\n<tg-emoji emoji-id=\"5193065010795911968\">🛍</tg-emoji> В нашем магазине — пополняй Steam, покупай Telegram Premium и многое другое\n\n<blockquote>100 000+ пользователей нам доверяют</blockquote>",
    'main.welcome_bypass': "<tg-emoji emoji-id=\"5462902520215002477\">💎</tg-emoji> <b>Atlas Secure</b>\n\n<tg-emoji emoji-id=\"5447410659077661506\">🌐</tg-emoji> Обход блокировок — активен\n\nИнтернет работает в любой точке мира.\nРоссийские сервисы — без сбоев.",
    'payment.already_processed': "✅ Этот платёж уже был обработан ранее.",
    'payment.approved': "✅ Доступ активирован\n\nВаш персональный ключ доступа готов.\n\n🔑 Персональный ключ доступа будет отправлен в следующем сообщении.\n\n🟢 Срок действия доступа:\nдо {date}\n\nКлюч закреплён за вами\nи будет доступен в профиле.\n\n👉 Подключение занимает не более 1 минуты.\nЕсли понадобится помощь — мы на связи.",
    'payment.balance': "Баланс (доступно: {balance:.2f} ₽)",
    'payment.card': "Карта резерв",
    'payment.stars': "Telegram Stars",
    'payment.stars_invoice_label': "Оплата звёздами",
    'payment.stars_invoice_description': "Atlas Secure VPN {tariff_name} — {months} мес. (⭐ Stars)",
    'payment.invoice_timeout': "⏱ Оплатите в течение 15 минут. После истечения времени инвойс будет отменён.",
    'payment.expired': "❌ Срок действия платежа истёк. Пожалуйста, создайте новый платеж.",
    'payment.fallback_first': "🎉 Подписка успешно активирована\n\n📅 Срок действия: до {date}\n\n🔐 Ваш ключ подключения будет отправлен в следующем сообщении.",
    'payment.fallback_renewal': "🔄 Подписка продлена\n\n📅 Новый срок действия: до {date}\n\n🔐 Ваш текущий ключ (тот же UUID) будет отправлен в следующем сообщении.",
    'payment.paid_button': "Подтвердить оплату",
    'payment.pending': "Подтверждение в процессе\n\nПлатёж зафиксирован.\nВерификация занимает до 5 минут.\nАктивация доступа выполняется автоматически.",
    'payment.pending_activation': "✅ Подписка оформлена!\n\n📅 Срок действия: до {date}\n\n⏳ Активация выполняется автоматически. VPN ключ будет отправлен вам в ближайшее время.\n\nЕсли ключ не пришёл в течение часа, обратитесь в поддержку.",
    'payment.rejected': "❌ Платёж не подтверждён.\n\nЕсли вы уверены, что оплатили —\nобратитесь в поддержку.",
    'payment.sbp': "СБП",
    'payment.sbp_waiting': "🏦 <b>Оплата через СБП</b>\n\nСумма: {amount:.2f} ₽\n\nНажмите кнопку ниже — откроется форма оплаты.\n\nЖдём платёж <tg-emoji emoji-id=\"5886538930148350129\">⏳</tg-emoji>\n<i>Обработка занимает до 5 минут — зависит от банка.</i>",
    'payment.sbp_pay_button': "🏦 Оплатить через СБП",
    'payment.sbp_unavailable': "Оплата через СБП временно недоступна",
    'payment.wata_check_button': "🔄 Проверить платёж",
    'payment.wata_check_cooldown': "Попробуйте через {seconds} сек.",
    'payment.wata_check_not_paid': "⌛ Платёж ещё не поступил. Обычно занимает до 1 минуты.",
    'payment.wata_check_paid': "✅ Оплата подтверждена! Активируем доступ…",
    'payment.wata_check_already': "✅ Оплата уже была подтверждена.",
    'payment.wata_check_error': "Не удалось проверить платёж, попробуйте позже.",
    'payment.crypto': "CryptoBot",
    'payment.crypto_waiting': "₿ <b>Оплата криптовалютой</b>\n\nСумма: {amount:.2f} ₽\n\nНажмите кнопку ниже — откроется @CryptoBot.\n\nЖдём платёж <tg-emoji emoji-id=\"5886538930148350129\">⏳</tg-emoji>\n<i>Обработка занимает до 5 минут — зависит от банка.</i>",
    'payment.crypto_pay_button': "₿ Оплатить через CryptoBot",
    'payment.crypto_unavailable': "Оплата криптовалютой временно недоступна",
    'payment.lava': "💳 Карта / СБП",
    'payment.lava_pay_button': "📱 Оплатить по СБП",
    'payment.card_pl': "Банковская карта",
    'payment.card_pl_waiting': "💳 <b>Оплата банковской картой</b>\n\nСумма: {amount:.2f} ₽\n\nНажмите кнопку ниже — откроется форма оплаты.\n\nЖдём платёж <tg-emoji emoji-id=\"5886538930148350129\">⏳</tg-emoji>\n<i>Обработка занимает до 5 минут — зависит от банка.</i>",
    'payment.card_pl_pay_button': "💳 Оплатить картой",
    'payment.card_pl_unavailable': "Оплата картой временно недоступна",
    'payment.intl_pl': "🌎 Международные платежи",
    'payment.intl_pl_waiting': "🌎 <b>Международный платёж</b>\n\nСумма: {amount:.2f} ₽\n\nНажмите кнопку ниже — откроется форма оплаты.\n\nЖдём платёж <tg-emoji emoji-id=\"5886538930148350129\">⏳</tg-emoji>\n<i>Обработка занимает до 5 минут — зависит от банка.</i>",
    'payment.intl_pl_pay_button': "🌎 Оплатить",
    'payment.intl_pl_unavailable': "Международные платежи временно недоступны",
    'payment.crypto_success': "🎉 Оплата получена!\n{tariff_icon} Тариф: {tariff}\n📅 До: {date}",
    'payment.select_method': "💳 К оплате: <b>{price:.2f} ₽</b>",
    'payment.success': "✅ Платёж успешно обработан!",
    'payment.success_first': "🎉 <b>Подписка успешно активирована</b>\n\n📅 <b>Срок действия:</b> до {date}\n\n🔗 <b>Ссылка подписки:</b>\n<code>{sub_url}</code>\n\nИспользуйте её в приложении VPN.",
    'payment.success_renewal': "🔄 <b>Подписка продлена</b>\n\n📅 <b>Новый срок действия:</b> до {date}\n\n🔗 <b>Ссылка подписки:</b>\n<code>{sub_url}</code>\n\nВы можете продолжить использовать текущую ссылку подключения.",
    'payment.success_renewal_compact': "✅ <b>Подписка продлена!</b>\n\n{tariff_icon} Тариф: {tariff}\n📅 До: {date}\n\nVPN продолжает работать.\n\n🤍 Atlas Secure",
    'payment.success_welcome_basic': "🎉 <b>Добро пожаловать в Atlas Secure!</b>\n\n📦 Тариф: <b>Basic</b>\n📅 До: {date}\n\nПодписка активна — VPN готов к работе.\n\n🤍 Atlas Secure",
    'payment.success_welcome_plus': "🎉 <b>Добро пожаловать в Atlas Secure!</b>\n\n⭐️ Тариф: <b>Plus</b>\n📅 До: {date}\n\nПодписка активна — VPN готов к работе.\n\n🤍 Atlas Secure",
    'payment.test': "Служебный режим Недоступно",
    'referral.cashback_amount': "💳 Сумма покупки: {amount:.2f} ₽",
    'referral.cashback_balance_auto': "Баланс пополнен автоматически.",
    'referral.cashback_level': "📊 Ваш уровень: {percent}%",
    'referral.cashback_max_level': "🎯 Вы достигли максимального уровня!",
    'referral.cashback_progress': "👥 До следующего: осталось {needed} {friend}",
    'referral.cashback_invite_button': "👥 Пригласить ещё друзей",
    'referral.cashback_reward': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Кешбэк: {amount:.2f} ₽ ({percent}%)",
    'referral.cashback_subscription_period': "⏰ Подписка: {period}",
    'referral.cashback_title': "💰 Вам начислен кешбэк!",
    'referral.friend_dual': "друга",
    'referral.friend_plural': "друзей",
    'referral.friend_singular': "друг",
    'referral.how_it_works': "❓ Как работает",
    'referral.how_it_works_text': "❓ <b>Как заработать с Atlas Secure</b>\n\nЗа каждую покупку подписки по твоей ссылке ты получаешь <b>процент на баланс</b>.\n\n<blockquote><b>1️⃣ Размести свою ссылку</b>\nДаже маленький блог или чат друзей подходит. Главное — твоя аудитория видит её:\n• в шапке Telegram-канала или профиля\n• в описании ролика на TikTok, YouTube, Reels\n• в чате, форуме, Discord-сервере\n• в личке другу — да, одного человека уже достаточно</blockquote>\n\n<blockquote><b>2️⃣ Друг оплачивает подписку</b>\nКэшбэк начисляется автоматически на твой баланс — и при первой покупке, и при каждом продлении. Без срока действия.</blockquote>\n\n<blockquote><b>💰 Сколько платим</b>\n<tg-emoji emoji-id=\"5425141507050973573\">🏄</tg-emoji> 10% → <tg-emoji emoji-id=\"5399988331729664856\">👀</tg-emoji> 20% → <tg-emoji emoji-id=\"5413623448440160154\">👨‍💻</tg-emoji> 30% → <tg-emoji emoji-id=\"5278467510604160626\">💰</tg-emoji> 40% → <tg-emoji emoji-id=\"5229011542011299168\">👑</tg-emoji> <b>45%</b> навсегда\n\nУровень растёт автоматически с каждым новым купившим. <b>И не падает.</b> Один раз поднялся — закрепился навсегда.</blockquote>\n\n💡 Чем больше аудитория видит твою ссылку — тем выше уровень. Это рабочий канал дохода: просто оставь ссылку там, где тебя смотрят.",
    'referral.max_level_reached': "🏆 У вас максимальный уровень программы\n\n<blockquote><b>Не работает Телеграм у друга?</b>\nПригласи через сайт! Нажми кнопку «🌐 Веб-клиент»</blockquote>",
    'referral.reward_notification': "🔥 Вам начислен реферальный кешбэк!\n\nВаш друг оформил подписку.\n💰 Начислено: {amount:.2f} ₽\nБаланс: {balance:.2f} ₽",
    'referral.share_button': "📤 Поделиться ссылкой",
    'referral.stats_button': "📚 Подробнее",
    'referral.stats_screen': "🎖 <b>Круг Амбассадоров</b>\n<i>От проводника до амбассадора</i>\n\n<b>У тебя уже есть аудитория — даже маленький канал, чат друзей или подписчики в TikTok.</b>\n\n<blockquote><b>💡 Где разместить ссылку</b>\n• Шапка Telegram-канала или профиля\n• Описание ролика TikTok / YouTube / Reels\n• Твой блог, форум, Discord-сервер\n• Просто отправь личным сообщением — даже одному человеку</blockquote>\n\n<blockquote><b>💰 Сколько ты получаешь</b>\n<tg-emoji emoji-id=\"5425141507050973573\">🏄</tg-emoji> <b>Проводник</b> · 10% (0–24 купивших)\n<tg-emoji emoji-id=\"5399988331729664856\">👀</tg-emoji> <b>Хранитель</b> · 20% (25–49)\n<tg-emoji emoji-id=\"5413623448440160154\">👨‍💻</tg-emoji> <b>Инсайдер</b> · 30% (50–74)\n<tg-emoji emoji-id=\"5278467510604160626\">💰</tg-emoji> <b>Лидер</b> · 40% (75–99)\n<tg-emoji emoji-id=\"5229011542011299168\">👑</tg-emoji> <b>Амбассадор</b> · 45% <i>навсегда</i> (от 100)</blockquote>\n\n🔗 <b>Твоя ссылка</b> <i>(нажми — скопируется)</i>\n<blockquote expandable><code>{referral_link}</code></blockquote>\n\n📊 Текущий статус: <b>{current_status_name}</b>\n{status_footer}",
    'referral.status_footer': "🚀 До следующего уровня: осталось {remaining_invites} приглашений",
    'subscription.auto_renew_disabled_toast': "⏸ Автопродление отключено",
    'subscription.auto_renew_enabled_toast': "✅ Автопродление включено",
    'subscription.renew': "🔁 Продлить доступ",
    'trial.expired': "🔓 <b>Пробный доступ завершён</b>\n\nСпасибо что попробовали Atlas Secure. Надеемся вам понравилось.\n\nЕсли решите вернуться — для вас уже действует <b>скидка 30%</b> на подписку. Она продержится 7 дней и применится автоматически при оплате — нажмите кнопку ниже.",
    'trial.expired_discount_btn': "🔥 Купить со скидкой 30%",
    'trial.notification_71h': "🚨 Последний час пробного доступа\n\nЧерез час VPN будет отключён.\n\nОформите подписку, чтобы оставаться на связи, даже когда глушат связь!",

    # === TRIAL NOTIFICATIONS (new schedule) ===
    # Trial user who bought bypass GB (e.g. the 3-day gift of a «Только обход» purchase):
    # the GB keep working after the premium ends — never «VPN перестанет работать».
    'trial.reminder_24h_gb': "⏳ <b>Пробный период заканчивается завтра</b>\n\nЗавтра отключатся основные серверы. Обход блокировок продолжит работать на ваших ГБ.\n\nОформите подписку, чтобы основные серверы остались — ключ и настройки сохранятся.",
    'trial.reminder_3h_gb': "🔥 <b>3 часа до конца пробного периода</b>\n\nПотом основные серверы отключатся, а обход блокировок продолжит работать на ваших ГБ.\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Успейте — <b>скидка 15%</b> действует до {deadline}.",
    'trial.notification_71h_gb': "🚨 Последний час пробного периода\n\nЧерез час основные серверы отключатся. Обход блокировок продолжит работать на ваших ГБ.\n\nОформите подписку, чтобы основные серверы остались.",
    'trial.reminder_24h': "⏳ <b>Пробный период заканчивается завтра</b>\n\nЗавтра доступ отключится — сайты и приложения вернутся к блокировкам.\n\nОформите подписку сейчас — ключ и настройки сохранятся, ничего не нужно переустанавливать.",
    'trial.reminder_3h': "🔥 <b>3 часа до отключения</b>\n\nПосле этого VPN перестанет работать. Сайты, стриминг, мессенджеры — всё вернётся к блокировкам.\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Успейте — <b>скидка 15%</b> действует до {deadline}.",
    'trial.reminder_3h_discount_btn': "🔥 Купить со скидкой 15%",

    # Bypass-activated (~5 минут после активации триала) — гоним юзера
    # на экран установки в Happ / Incy, где он одной кнопкой добавит
    # bypass-ключ в клиент.
    'trial.bypass_activated': (
        "🛡 <b>Обход белых списков подключён</b>\n\n"
        "Мы дали <b>500 МБ трафика обхода</b> в подарок — с ним "
        "открываются сайты и сервисы, которые фильтруются по белым "
        "спискам.\n\n"
        "Чтобы начать пользоваться — установи ключ в Happ или Incy "
        "нажатием кнопки ниже."
    ),
    'trial.bypass_activated_btn_setup': "🌐 Включить обход",
    'trial.bypass_activated_btn_help': "💬 Нужна помощь",

    # === BYPASS SETUP SCREEN ===
    'bypass_setup.title': (
        "🌐 <b>Установка обхода блокировок</b>\n\n"
        "Открой один из клиентов ниже — установка занимает 30 секунд, "
        "ключ уже сгенерирован и закреплён за тобой.\n\n"
        "<b>📲 Happ</b> — универсальный, работает на iOS / Android / Mac / Windows.\n"
        "<b>💚 Incy</b> — быстрый iOS-клиент с классным UX.\n\n"
        "<b>Как подключиться:</b>\n"
        "1. Убедись что клиент установлен (если нет — установи из App Store / Play Market).\n"
        "2. Нажми <b>«➕ Добавить обход»</b> — Telegram откроет клиент "
        "и импортирует ключ автоматически.\n"
        "3. Не открылось? Нажми <b>«🔑 Показать ключ вручную»</b>, "
        "скопируй ключ и вставь в клиент из буфера."
    ),
    'bypass_setup.add_happ_btn': "➕ Добавить обход в Happ",
    'bypass_setup.add_incy_btn': "➕ Добавить обход в Incy",
    'bypass_setup.manual_btn': "🔑 Показать ключ вручную",
    'bypass_setup.no_key_yet': (
        "⏳ <b>Ключ обхода ещё не готов</b>\n\n"
        "Обычно это занимает не более минуты — загляни сюда чуть позже."
    ),

    # Manual-экран собирается из блоков в bypass_setup.py:
    # header + happ_block [+ incy_block] + footer.  Incy-блок опционален —
    # sidecar может быть недоступен.
    'bypass_setup.manual_screen_header': (
        "🔑 <b>Ключи обхода</b>\n\n"
        "Тапни по ключу ниже — он <b>скопируется в буфер</b>. Затем "
        "открой клиент → кнопка вставки / буфера."
    ),
    'bypass_setup.manual_screen_happ_block': (
        "📲 <b>Ключ для Happ</b> (crypt4)\n"
        "<blockquote expandable><code>{happ_key}</code></blockquote>"
    ),
    'bypass_setup.manual_screen_incy_block': (
        "💚 <b>Ключ для Incy</b> (crypt1)\n"
        "<blockquote expandable><code>{incy_key}</code></blockquote>"
    ),
    'bypass_setup.manual_screen_footer': (
        "💡 Если ключ не импортировался — вернись назад и попробуй "
        "кнопку <b>«➕ Добавить обход»</b>. Она открывает клиент напрямую "
        "через deeplink."
    ),

    # === TRIAL ACTIVATION ===
    'trial.activating': "⏳ <b>Активирую пробный период…</b>\n\nСоздаём тебе доступ. Обычно занимает 2–3 секунды.",
    'trial.activated': "🎉 <b>Доступ активирован!</b>\n\nУ вас 3 дня бесплатно. Без ограничений, без карты.\n\nНажмите кнопку ниже — мы проведём вас через установку шаг за шагом.",
    'trial.activated_btn_connect': "⚡️ Подключиться — это быстро",
    'trial.activated_btn_support': "💬 Нужна помощь",

    # === BROADCAST: «🎁 Получить пробный ключ» ===
    'broadcast.trial_key_activated': "🎁 <b>Подарок активирован!</b>\n\nВам активировано: доступ на <b>1 день</b> подписки и <b>1 ГБ</b> трафика обхода белых списков.\n\nСейчас поможем подключить устройство 👇",
    'broadcast.trial_key_already': "🎁 Подарок уже получен",
    'broadcast.trial_key_error': "Не удалось активировать подарок, попробуйте позже.",

    # === PURCHASE CONFIRMATION ===
    'purchase.success_first': "🎉 <b>Подписка оформлена!</b>\n\n📦 Тариф: <b>{tariff_name}</b>\n⏳ Срок: {period}\n📅 До: {expires_date}{gb_line}\n\nОсталось подключиться — нажмите «Подключиться» ниже, это займёт минуту.\n\n🤍 Ваш трафик под защитой Atlas Secure.",

    # === RENEWAL CONFIRMATION ===
    'purchase.success_renewal': "✅ <b>Подписка продлена!</b>\n\n📦 Тариф: <b>{tariff_name}</b>\n⏳ Срок: {period}\n📅 До: {expires_date}{gb_line}\n\nVPN продолжает работать — ничего настраивать не нужно.\n\n🤍 Спасибо, что остаётесь с Atlas Secure!",
    'purchase.success_tariff_changed': "✅ <b>Тариф изменён на {tariff_name}</b>\n\n⏳ Оплачено: {period}\n📅 До: {expires_date}{gb_line}\n\nНовый тариф уже действует — VPN продолжает работать.",
    'purchase.success_gb_line': "\n🌐 Обход белых списков: +{gb} ГБ",
    'purchase.activated_later': "🎉 <b>Подписка активирована!</b>\n\n📦 Тариф: <b>{tariff_name}</b>\n📅 До: {expires_date}\n\nНажмите «Подключиться» ниже, чтобы подключить устройство.",
    'autorenew.insufficient_balance': "⚠️ <b>Не хватает денег на автопродление</b>\n\nПродление стоит {amount} ₽, на балансе {balance} ₽ — не хватает <b>{missing} ₽</b>.\n\nПополните баланс до {deadline} — и мы продлим подписку автоматически.",
    'autorenew.failed_debit':"⚠️ <b>Не удалось автоматически продлить подписку</b>\n\nСписать {amount:.2f} ₽ с баланса не получилось — деньги не списаны.\n\nПродлите подписку вручную, чтобы VPN не отключился.",
    'autorenew.failed_refunded': "⚠️ <b>Автопродление не выполнено</b>\n\n{amount:.2f} ₽ возвращены на баланс — мы уже разбираемся.\n\nПродлите подписку вручную, чтобы VPN не отключился.",
    'tariff.name_basic': "Basic",
    'tariff.name_plus': "Plus",
    'tariff.name_combo_basic': "Комбо Basic",
    'tariff.name_combo_plus': "Комбо Plus",
    'buy.manage_title': "📦 <b>Управление подпиской</b>\n\nВаш текущий тариф:\n\n{tariff_desc}\n\nВыберите действие:",
    'buy.manage_renew': "Продлить {name}",
    'buy.manage_switch': "Сменить тарифный план",
    'buy.manage_buy_gb': "Купить ГБ обхода",
    'tariff_switch.title': "{icon} <b>Переход на {name}</b>",
    'tariff_switch.choose_period': "Выберите период:",
    'tariff_switch.combo_benefits': "💡 <b>Преимущества комбо:</b>\n✅ Трафик обхода уже включён в стоимость\n✅ Не нужно покупать ГБ отдельно\n✅ Экономия до 30% по сравнению с раздельной покупкой",
    'payment.wata_waiting': "💳 <b>Оплата картой или СБП</b>\n\nСумма: {amount} ₽\n\nНажмите кнопку ниже — откроется форма оплаты (карта, СБП, T-Pay).\n\nЖдём платёж <tg-emoji emoji-id=\"5886538930148350129\">⏳</tg-emoji>\n<i>Обработка занимает до 5 минут — зависит от банка.</i>",
    'payment.wata_pay_button': "💳 Оплатить {amount} ₽",
    'payment.wata_beta_only': "Этот способ оплаты пока недоступен.",
    'payment.markup_suffix': " (+{percent}%)",
    'payment.btn_cancel': "❌ Отмена",
    'payment.wata_declined': "❌ <b>Оплата не прошла</b>\n\nЗаказ <code>{order}</code> не был подтверждён банком. Подписка/товар не активирован.\n\n🎫 <b>Код обращения:</b> <code>{ticket}</code>\n\n<b>Если деньги были списаны</b> — сохраните этот код и напишите в поддержку, мы вернём средства.\n\nЕсли списания не было — можно попробовать оплатить ещё раз.",
    'payment.wata_retry_button': "🔄 Попробовать снова",
    'errors.insufficient_balance_topup_hint': "Пополните баланс — и оплатите с него в один тап:",
    'errors.promo_no_longer_valid': "⚠️ Промокод больше не действует — оплата не прошла, баланс не списан.\n\nВыберите тариф заново: цена будет без промокода.",
    'gift.btn_my_gifts': "🎁 Мои подарки",
    'common.msk': "МСК",
    'common.unit_gb': "ГБ",
    'common.unit_mb': "МБ",
    'common.unit_kb': "КБ",
    'subscription.expired_paid': "⌛️ <b>Ваша подписка Atlas Secure закончилась.</b>\n\nVPN отключён. Продлите подписку, чтобы снова пользоваться сервисом.",
    'subscription.expired_offer_line': "\n\n🎁 Для вас <b>скидка 15%</b> на продление — действует до {deadline}.",
    'subscription.expired_free': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Бесплатный доступ к Atlas Secure закончился.</b>\n\nVPN отключён. Понравилось? Оформите подписку — от {price} ₽/мес 🤍",
    'subscription.expired_gb_left':"⚠️ <b>Ваша основная подписка закончилась</b>\n\nОсновные серверы отключены. Обход блокировок продолжает работать — осталось <b>{remaining}</b>.\n\nПродлите подписку, чтобы вернуть основные серверы, или докупите ГБ, когда они будут заканчиваться 👇",
    'subscription.expired_gb_works': "⚠️ <b>Ваша основная подписка закончилась</b>\n\nОсновные серверы отключены. Обход блокировок продолжает работать на оставшихся ГБ — остаток виден в «Моя подписка».\n\nПродлите подписку, чтобы вернуть основные серверы 👇",
    'subscription.expired_gb_spent': "⌛️ <b>Ваша подписка Atlas Secure закончилась.</b>\n\nVPN отключён: трафик обхода тоже израсходован.\n\nПродлите подписку или купите ГБ обхода, чтобы снова пользоваться сервисом.",
    'subscription.btn_renew_discount_15': "🔥 Продлить со скидкой 15%",
    'reminder.paid_3h_no_offer': "🚨 <b>3 часа до отключения</b>\n\nПосле этого VPN перестанет работать. Продлите подписку, чтобы не остаться без доступа.",
    'purchase.period_days': "{days} дн.",
    'purchase.tariff_basic': "⚡️ Basic",
    'purchase.tariff_plus': "👑 Plus",
    'purchase.tariff_combo_basic': "🚀 Комбо Basic",
    'purchase.tariff_combo_plus': "🚀 Комбо Plus",
    'purchase.link_premium': "🌍 <b>Premium</b> (основные серверы):\n<code>{url}</code>",
    'purchase.link_bypass': "🚧 <b>Bypass</b> (обход белых списков):\n<code>{url}</code>",

    # === AUTO-RENEWAL NOTIFICATION ===
    'purchase.auto_renewal_success': "<tg-emoji emoji-id=\"5456140674028019486\">🔄</tg-emoji> <b>Подписка автоматически продлена</b>\n\n📦 Тариф: {tariff_name}\n<tg-emoji emoji-id=\"5454415424319931791\">⏳</tg-emoji> Срок: {period}\n📅 Действует до: {expires_date}\n💳 Списано с баланса: {amount:.2f} ₽\n\nВаш VPN продолжает работать без перерыва.\n\n🛡 Спасибо за доверие Atlas Secure!",

    # === PAID SUBSCRIPTION REMINDERS (7d, 3d, 1d, 3h) ===
    'reminder.paid_7d': "<tg-emoji emoji-id=\"5454415424319931791\">📅</tg-emoji> Подписка заканчивается через 7 дней. Продлите заранее — доступ не прервётся.",
    'reminder.paid_7d_btn': "🔁 Продлить подписку",
    'reminder.paid_1d_gb': "<tg-emoji emoji-id=\"5190806721286657692\">🔴</tg-emoji> Подписка заканчивается завтра.\n\nЗавтра основные серверы отключатся, а обход блокировок продолжит работать на ваших ГБ — осталось {remaining}.\n\nПродлите сейчас, чтобы основные серверы остались.",
    'reminder.paid_3h_special_gb': "<tg-emoji emoji-id=\"5190806721286657692\">🚨</tg-emoji> <b>3 часа до конца подписки</b>\n\nПотом основные серверы отключатся, а обход блокировок продолжит работать на ваших ГБ — осталось {remaining}.\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Успейте — <b>скидка 15%</b> на продление. Действует до {deadline}.",
    'reminder.paid_3h_no_offer_gb': "🚨 <b>3 часа до конца подписки</b>\n\nПотом основные серверы отключатся, а обход блокировок продолжит работать на ваших ГБ — осталось {remaining}.\n\nПродлите подписку, чтобы основные серверы остались.",
    'reminder.paid_autorenew_ok':"🔄 <b>Подписка продлится автоматически</b>\n\nПодписка действует до {date}. Автопродление включено: перед окончанием спишем <b>{amount} ₽</b> с баланса — на балансе {balance} ₽, этого хватает.\n\nНичего делать не нужно, VPN продолжит работать.",
    'reminder.paid_autorenew_topup': "📅 <b>Подписка действует до {date}</b>\n\nАвтопродление включено, но на балансе {balance} ₽, а продление стоит {amount} ₽ — не хватает <b>{missing} ₽</b>.\n\nПополните баланс на {missing} ₽ до {deadline} — и подписка продлится сама.",
    'reminder.paid_3d_btn': "🔁 Продлить",
    'reminder.paid_1d': "<tg-emoji emoji-id=\"5190806721286657692\">🔴</tg-emoji> Подписка заканчивается завтра. Продлите сейчас, чтобы VPN продолжил работать.",
    'reminder.paid_1d_btn': "🔁 Продлить",
    'reminder.paid_3h_special': "<tg-emoji emoji-id=\"5190806721286657692\">🚨</tg-emoji> <b>3 часа до отключения</b>\n\nПосле этого VPN перестанет работать. Сайты и приложения вернутся к блокировкам.\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Успейте — <b>скидка 15%</b> на продление. Действует до {deadline}.",
    'reminder.paid_3h_discount_btn': "🔥 Купить со скидкой 15%",

    # === REFERRAL x2 CASHBACK PROMO ===

    # === 10 PROMO TEMPLATES ===

    # === RETENTION & ENGAGEMENT TEMPLATES ===

    # === ADMIN NOTIFICATION CATEGORIES ===

    # Admin promo section

    # Admin retention section

    # Admin referral promo

    # Admin subscription notifications info
    'reminder.admin_1day_6h': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Ваш бесплатный доступ к Atlas Secure завершается через 6 часов\n\nПонравилось? Оформите подписку и пользуйтесь без ограничений 💙",
    'reminder.admin_7days_24h': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Бесплатный доступ к Atlas Secure истекает через 24 часа\n\nОцените — за время использования ваш трафик был под надёжной защитой. Подключите подписку от {price} ₽/мес 🤍",
    'reminder.paid_3d': "<tg-emoji emoji-id=\"5454415424319931791\">📅</tg-emoji> Ваша подписка Atlas Secure активна ещё 3 дня\n\nПродлите заранее — и доступ не прервётся ни на секунду 🤍",
    'reminder.paid_24h': "<tg-emoji emoji-id=\"5456140674028019486\">⚡️</tg-emoji> Осталось менее 24 часов подписки\n\nПродлите сейчас одним нажатием, чтобы VPN продолжил работать без перерыва 🛡",
    'connect.press_button': "📲 <b>Подключение</b>\n\nНажмите <b>«Подключиться»</b> и следуйте инструкциям — настройка займёт пару минут.",

    # Gift subscription
    'main.gift_subscription': "🎁 Подарить подписку",
    'gift.intro': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Подарить подписку</b>\n\nИнтернет без блокировок — лучший подарок.\nВыберите тариф, оплатите — мы сгенерируем ссылку.\nПолучатель нажмёт на неё и всё заработает.",
    'gift.choose_period': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Подарок — {tariff_name}</b>\n\nВыберите срок подписки для подарка:",
    'gift.choose_payment': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Подарок — {tariff_name}</b>\n\n📦 Тариф: {tariff_name}\n⏳ Срок: {period}\n💰 Стоимость: {price} ₽\n\nВыберите способ оплаты:",
    'gift.success': "🎉 <b>Подарок создан!</b>\n\n⚡ {tariff_name} · {period}\n\n🔗 Ссылка для активации:\n<code>{gift_link}</code>\n\nОсталось отправить её получателю — нажмите «📤 Отправить ссылку».\nПолучатель нажмёт — и интернет без блокировок его.\n\n<blockquote>⚠️ Ссылка работает только 1 раз — не переходите по ней сами</blockquote>",
    # Plain text only: уходит в t.me/share/url (поле ввода пользователя), где
    # HTML и <tg-emoji> не рендерятся. Обычные эмодзи — можно.
    'gift.share_text': "🎁 Привет! Дарю тебе подписку Atlas Secure — {tariff_name} на {period}.\n\nИнтернет без блокировок: нажми на ссылку, чтобы активировать подарок.",
    'gift.btn_share': "📤 Отправить",
    'gift.activated': "🎉 <b>Подарок активирован!</b>\n\n📦 Тариф: {tariff_name}\n⏳ Срок: {period}\n\nВаша подписка уже активна.\nОткройте «Моя подписка» → «Подключиться», чтобы начать пользоваться.",
    'gift.activated_welcome': "🎉 <b>Добро пожаловать в Atlas Secure!</b>\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Вам подарили подписку:\n📦 Тариф: {tariff_name}\n⏳ Срок: {period}\n\nПодписка уже активна.\nОткройте «Моя подписка» → «Подключиться», чтобы настроить VPN.",
    'gift.error_not_found': "❌ Подарочная ссылка не найдена.\n\nПроверьте правильность ссылки или обратитесь к отправителю.",
    'gift.error_already_activated': "⚠️ Эта подарочная подписка уже была активирована.\n\nКаждый подарок можно использовать только один раз.",
    'gift.error_expired': "⏰ Срок действия подарочной ссылки истёк.\n\nПопросите отправителя приобрести новый подарок.",
    'gift.error_self_activation': "🚫 Вы не можете активировать свой собственный подарок.\n\nОтправьте ссылку другому человеку.",
    'gift.error_invalid': "❌ Подарочная ссылка недействительна.",
    'gift.my_gifts_title': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Мои подарки</b>",
    'gift.my_gifts_empty': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> У вас пока нет подарков.\n\nВы можете приобрести подарок в главном меню.",
    'gift.buy_gift_btn': "🎁 Подарить подписку",
    'gift.back_to_profile': "👤 Вернуться в профиль",
    'gift.back_to_gifts': "🎁 Назад к подаркам",
    'gift.page_prev': "⬅️ Назад",
    'gift.page_next': "Дальше ➡️",
    'gift.status_activated': "✅ Активирован",
    'gift.status_pending': "❌ Не активирован",
    'gift.detail_activated': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>{tariff_name} — {period}</b>\n\n✅ Этот подарок уже активирован.",
    'gift.detail_pending': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Отправьте подарок близкому!</b>\n\n📦 Тариф: {tariff_name}\n⏳ Срок: {period}\n\n❌ Не активирован\n\n🔗 Ссылка для активации:\n<code>{gift_link}</code>",

    # --- Telegram Premium ---
    'premium.main_button': "⚡️ Купить Telegram Premium ⚡️",

    # --- Help / FAQ ---
    'help.menu_title': "❓ <b>Помощь</b>\n\nВыберите подходящий вариант ниже:\n\n<blockquote>📖 <b>Ответы на частые вопросы</b>\nКороткие решения типичных проблем</blockquote>\n\n<blockquote>📲 <b>Инструкции по сервису</b>\nКак настроить VPN на вашем устройстве</blockquote>\n\n<blockquote>📞 <b>Контакты</b>\nПочта поддержки и отдела продаж</blockquote>\n\n<blockquote>💬 <b>Помощь</b>\nНаписать живому оператору в Telegram</blockquote>",
    'help.contacts_title': "📞 <b>Контакты</b>\n\nСвяжитесь с нами по электронной почте — <b>нажмите на адрес, и он скопируется</b>.\n\n<blockquote>📧 <b>Техническая поддержка</b></blockquote>\n<code>support@atlassecure.uk</code>\nПроекты Atlas Secure и QoDev\n\n<blockquote>💼 <b>Отдел продаж</b></blockquote>\n<code>sales@atlassecure.uk</code>\nИнфраструктурные решения Atlas Secure",
    'help.faq_title': "📖 <b>Ответы на частые вопросы</b>\n\nВыберите свой вопрос — покажем короткое решение.",
    'help.faq_q1': "🚫 Не работает VPN",
    'help.faq_q2': "📲 Как подключиться / настроить",
    'help.faq_q3': "🐌 Низкая скорость",
    'help.faq_q4': "💳 Не проходит оплата",
    'help.faq_q5': "📱 Как добавить ещё устройство",
    'help.faq_q6': "🔑 Как обновить",
    'help.faq_q7': "🌐 Как работают сервера обхода",
    'help.faq_q8': "📊 Как работают гигабайты обхода",
    'help.faq_q9': "⚠️ Happ — Ошибка Xray-ядра",
    'help.faq_a1': "🚫 <b>Не работает VPN</b>\n\nПройдитесь по шагам — обычно помогает один из них.\n\n<blockquote>1️⃣ <b>Проверьте интернет без VPN</b>\nОтключите VPN, откройте любой сайт. Если не работает — проблема у провайдера, не у нас.</blockquote>\n\n<blockquote>2️⃣ <b>В регионе глушат связь?</b>\nВ приложении выберите сервер с пометкой <b>LTE</b> — он специально для обхода блокировок мобильных операторов (МТС, МегаФон, Билайн).</blockquote>\n\n<blockquote>3️⃣ <b>Перезапустите приложение</b>\nПолностью закройте <b>Happ</b> (смахните из меню) → откройте заново → включите подключение.</blockquote>\n\n<blockquote>4️⃣ <b>Импортируйте ключ заново</b>\nВ боте: <b>«📲 Подключиться»</b> → ваше устройство → <b>«Импортировать ключ»</b>. Возможно, ссылка обновилась.</blockquote>\n\n<i>Не помогло? Напишите оператору — ответим за 5–10 минут.</i> 💬",
    'help.faq_a2': "📲 <b>Как подключиться</b>\n\nЭто занимает меньше минуты.\n\n<blockquote>1️⃣ В боте нажмите <b>«📲 Подключиться»</b>\n\n2️⃣ Выберите устройство — <b>iPhone · Android · Mac · Windows</b>\n\n3️⃣ Установите приложение по нашей ссылке\n\n4️⃣ Нажмите <b>«Импортировать ключ»</b> — подписка добавится автоматически\n\n5️⃣ Включите VPN в приложении 🚀</blockquote>\n\n<i>💡 Все шаги показаны с картинками — заблудиться сложно. Если что-то не получается — напишите оператору.</i> 💬",
    'help.faq_a3': "🐌 <b>Низкая скорость</b>\n\nПройдитесь по шагам.\n\n<blockquote>1️⃣ <b>Проверьте скорость без VPN</b>\nИзмерьте на <a href=\"https://yandex.ru/internet\">yandex.ru/internet</a> с выключенным VPN. Если базовый интернет медленный — ускорить через VPN физически невозможно.</blockquote>\n\n<blockquote>2️⃣ <b>Смените сервер</b>\nВ приложении переключитесь на другой — иногда соседние страны быстрее (например, Германия вместо Нидерландов).</blockquote>\n\n<blockquote>3️⃣ <b>Используете LTE / 5G?</b>\nСкорость мобильного интернета зависит от загрузки вышки и времени суток. Вечером — медленнее, ночью — быстрее.</blockquote>\n\n<blockquote>4️⃣ <b>Перезагрузите Wi-Fi-роутер</b>\nИногда зависает именно он, а не VPN. Отключите от розетки на 30 секунд → включите.</blockquote>\n\n<i>Стабильно медленно на всех серверах? Напишите оператору.</i> 💬",
    'help.faq_a4': "💳 <b>Не проходит оплата</b>\n\nПройдитесь по шагам.\n\n<blockquote>1️⃣ <b>Смените способ оплаты</b>\n<b>СБП → карта → Telegram Stars → баланс бота</b>. Если один не работает — попробуйте другой.</blockquote>\n\n<blockquote>2️⃣ <b>Платёж завис?</b>\nПодождите 10–15 минут. Деньги либо спишутся и подписка активируется, либо вернутся на карту автоматически — мы ничего не «зажимаем».</blockquote>\n\n<blockquote>3️⃣ <b>Списали, а подписки нет?</b>\nПросто напишите оператору — обязательно поможем.</blockquote>\n\n<i>💬 Поддержка ответит за 5–10 минут.</i>",
    'help.faq_a5': "📱 <b>Как добавить ещё устройство</b>\n\nОдна подписка — несколько устройств одновременно:\n\n<blockquote>• <b>Basic</b> — до 10 устройств\n• <b>Plus</b> — до 14 устройств</blockquote>\n\n<b>Как добавить новое устройство</b>\n\n<blockquote>1️⃣ В боте на <b>основном</b> устройстве нажмите кнопку <b>«Меню»</b> (синяя иконка слева от поля ввода) → <b>«📲 Добавить устройство»</b>\n\n2️⃣ Выберите тип устройства, которое хотите добавить\n\n3️⃣ Бот пришлёт <b>QR-код</b> и короткую инструкцию</blockquote>\n\n<blockquote>4️⃣ На <b>новом</b> устройстве установите приложение <b>Happ</b> и откройте его\n\n5️⃣ Нажмите <b>«+»</b> в правом верхнем углу → <b>«Отсканировать QR-код»</b>\n\n6️⃣ Наведите камеру на QR с основного устройства</blockquote>\n\nГотово 🚀\n\n<i>💡 Доплачивать не нужно — это <b>та же</b> подписка, просто на другом устройстве.</i>",
    'help.faq_a6': "🔑 <b>Как обновить ключ</b>\n\nЕсли в приложении <b>Happ</b> вы видите сообщение <i>«Обновите ключ в боте»</i> или <i>«Данная версия не поддерживается»</i> — нужно переустановить ключ.\n\n<blockquote>1️⃣ Нажмите кнопку <b>«Меню»</b> 🔵 (синяя иконка слева от поля ввода)\n\n2️⃣ Выберите <b>«📲 Подключиться»</b>\n\n3️⃣ Выберите своё устройство\n\n4️⃣ Установите приложение <b>Happ</b>\n<i>Если приложение уже установлено — нажмите «Дальше» и пропустите этот шаг.</i>\n\n5️⃣ Импортируйте ключ — пройдите стандартную процедуру установки</blockquote>\n\n<b>Какой ключ выбрать?</b>\n\n<blockquote>🌐 <b>Добавить VPN</b> — основные безлимитные сервера\n\n🛡 <b>Добавить обход</b> — сервера с обходом белых списков</blockquote>\n\n<i>Не получилось? Напишите оператору — поможем.</i> 💬",
    'help.faq_a7': "🌐 <b>Как работают сервера обхода</b>\n\nСервера обхода белых списков бывают двух типов:\n\n<blockquote>🇷🇺 <b>С российским флагом</b>\n🇪🇺 <b>С европейским флагом</b></blockquote>\n\n<b>Какой выбрать?</b>\n\n<blockquote>Рекомендуем <b>сервера с европейским флагом</b> — они стабильнее и лучше работают с <b>Telegram</b>, <b>Instagram</b> и другими сервисами, заблокированными в РФ.</blockquote>\n\n<b>Подключились, но сервер не работает?</b>\n\n<blockquote>1️⃣ Выберите другой сервер и попробуйте снова\n\n2️⃣ Включите и выключите авиарежим ✈️\n\n3️⃣ Закройте приложение и откройте заново\n\n4️⃣ Проверьте подключение к мобильной сети и корректность соединения</blockquote>\n\n<i>Не помогло? Напишите оператору.</i> 💬",
    'help.faq_a8': "📊 <b>Как работают гигабайты обхода</b>\n\nГигабайты для серверов обхода — это <b>отдельный пакет трафика</b>. Покупаете один раз и тратите в своём темпе.\n\n<blockquote>♾ <b>Без срока годности</b>\nНе сгорают. Не привязаны к месяцу, дню или подписке. Сколько использовали — столько и списалось, остаток остаётся вам.</blockquote>\n\n<blockquote>🔓 <b>Независимо от подписки</b>\nЭто <b>не</b> часть основной подписки на VPN. Подписка закончилась — гигабайты обхода остаются на счёте и ждут вас.</blockquote>\n\n<b>Пример</b>\n\n<blockquote>💰 Купили <b>30 ГБ</b>\n📉 За неделю израсходовали <b>5 ГБ</b>\n✅ Остаток — <b>25 ГБ</b>\n\nЭти 25 ГБ никуда не денутся: используйте завтра, через месяц или через полгода.</blockquote>\n\n<b>Где посмотреть остаток</b>\n\n<blockquote>📱 В приложении <b>Happ</b> — рядом с подключением к серверу обхода\n\n👤 В <b>боте</b> — раздел <b>«👤 Профиль»</b></blockquote>\n\n<i>Закончились? Докупите в любой момент — новые ГБ просто прибавятся к остатку.</i> 💬",
    'help.faq_a9': "⚠️ <b>Happ — Ошибка Xray-ядра</b>\n\nВстречается часто, лечится за минуту.\n\n<b>Почему возникает</b>\n\n<blockquote>Многие VPN-сервисы автоматически подсовывают на устройство <b>файлы маршрутизации</b> — без вашего ведома. Мы так не делаем: <b>все настройки живут на наших серверах</b>, а не на телефоне.\n\nНо иногда файлы, оставшиеся от других VPN, конфликтуют с нашим ядром — и Happ показывает ошибку Xray.</blockquote>\n\n<b>Как исправить</b>\n\n<blockquote>1️⃣ Откройте <b>Happ</b>\n\n2️⃣ Нажмите на <b>шестерёнку ⚙️</b> в левом верхнем углу\n\n3️⃣ Выберите <b>«Маршрутизация»</b>\n\n4️⃣ Удалите <b>каждый</b> файл маршрутизации из списка\n\n5️⃣ Перезапустите приложение</blockquote>\n\nПодключение заработает штатно 🚀\n\n<i>Не помогло? Напишите оператору — разберёмся вместе за 5–10 минут.</i> 💬",

    # --- Mini Shop ---
    'shop.title': "<tg-emoji emoji-id=\"5193065010795911968\">🛍</tg-emoji> <b>Мини-магазинчик</b>\n\nЗдесь можно купить полезные цифровые товары — быстро, без нервов и лишних сложностей.\n\n<blockquote><tg-emoji emoji-id=\"5456140674028019486\">⚡️</tg-emoji> <b>Telegram Premium</b> — расширьте возможности мессенджера\n<tg-emoji emoji-id=\"5422545633112249830\">🍎</tg-emoji> <b>Пополнение Apple ID</b> — пополните баланс App Store в любом регионе\n<tg-emoji emoji-id=\"5319247469165433798\">🎮</tg-emoji> <b>Пополнить Steam</b> — кошелёк Steam без VPN\n<tg-emoji emoji-id=\"5226639745106330551\">🧠</tg-emoji> <b>Claude Pro/Max</b> <i>(скоро)</i> — самый мощный AI-ассистент для работы и творчества</blockquote>\n\nВыберите что вас интересует:",
    'shop.claude_coming_soon': "💪 Собрали все свои суперсилы, чтобы быстрее реализовать",
    'shop.apple_title': "🍎 <b>Пополнение Apple ID</b>\n\nВыберите регион вашего Apple ID:\n\n<blockquote>Не знаете свой регион?\nОткройте <b>Настройки → Apple ID → Медиаматериалы и покупки → Просмотреть</b> — там будет указана страна/регион.</blockquote>",
    'shop.apple_amount_title': "🍎 <b>Пополнение Apple ID</b>\n\nРегион: {region}\n\nВыберите номинал пополнения:",
    'shop.apple_confirm': "🍎 <b>Пополнение Apple ID</b>\n\nРегион: {region}\nНоминал: {nominal}\nК оплате: <b>{price:.2f} ₽</b>\n\n<blockquote>Код пополнения будет отправлен в этот чат в течение 15 минут после оплаты.</blockquote>",
    'shop.apple_success': "✅ <b>Оплата прошла успешно!</b>\n\n🍎 Товар: Пополнение Apple ID\n🌍 Регион: {region}\n💰 Номинал: {nominal}\n💳 Сумма: {price} ₽\n\n⏳ Ожидайте получения кода пополнения в течение <b>5–15 минут</b>.\n\nЕсли вы не получили код, напишите нам:",
    'shop.apple_admin': "🍎 <b>ПОКУПКА APPLE ID</b>\n\n👤 Покупатель: <code>{buyer_id}</code>\n📛 Username: {buyer_username}\n🌍 Регион: {region}\n💰 Номинал: {nominal}\n💳 Сумма: {price} ₽\n🕐 Дата: {date}\n\n💬 <i>Отправьте код через «Написать пользователю»</i>",

    # ── Shop: Пополнить Steam ─────────────────────────────────────────
    'shop.steam_main_button': "🎮 Пополнить Steam",
    'shop.steam_disclaimer': (
        "🎮 <b>Пополнение Steam</b>\n\n"
        "Это пополнение доступно только для России и стран СНГ:\n"
        "🇷🇺 Российская Федерация\n"
        "🇦🇲 Республика Армения\n"
        "🇧🇾 Республика Беларусь\n"
        "🇰🇿 Республика Казахстан\n"
        "🇰🇬 Кыргызская Республика\n"
        "🇲🇩 Республика Молдова\n"
        "🇹🇯 Республика Таджикистан\n"
        "🇹🇲 Туркменистан\n"
        "🇺🇿 Республика Узбекистан\n\n"
        "<blockquote>Конвертация выполняется по внутреннему курсу Steam. "
        "Мы не используем калькулятор валют, чтобы не вводить в заблуждение.</blockquote>"
    ),
    'shop.steam_disclaimer_ack_btn': "✅ Ознакомлен",
    'shop.steam_amount_title': (
        "🎮 <b>Пополнение Steam</b>\n\n"
        "Выберите сумму пополнения (₽):"
    ),
    'shop.steam_login_prompt': (
        "🎮 <b>Пополнение Steam</b>\n\n"
        "Сумма пополнения: <b>{amount} ₽</b>\n\n"
        "Введите <b>логин</b> вашего аккаунта Steam:"
    ),
    'shop.steam_invalid_login': (
        "❌ Неверный логин Steam.\n\n"
        "Логин должен содержать 3–32 символа: латинские буквы, цифры, "
        "дефис и подчёркивание. Попробуйте ещё раз."
    ),
    'shop.steam_choose_payment': (
        "🎮 <b>Пополнение Steam</b>\n\n"
        "📥 Сумма на счёт: <b>{amount} ₽</b>\n"
        "👤 Логин Steam: <code>{login}</code>\n"
        "💳 К оплате: <b>{price} ₽</b> <i>(включая комиссию сервиса {fee} ₽)</i>\n\n"
        "Выберите способ оплаты:"
    ),
    'shop.steam_success': (
        "✅ <b>Оплата прошла успешно!</b>\n\n"
        "🎮 Товар: Пополнение Steam\n"
        "👤 Логин: <code>{login}</code>\n"
        "💰 Сумма пополнения: {amount} ₽\n"
        "💳 Сумма оплаты: {price} ₽\n\n"
        "⏳ Ожидайте зачисления на аккаунт Steam в течение <b>5–15 минут</b>.\n\n"
        "Если средства не поступили, напишите нам:"
    ),
    'shop.steam_admin': (
        "🎮 <b>ПОКУПКА STEAM</b>\n\n"
        "👤 Покупатель: <code>{buyer_id}</code>\n"
        "📛 Username: {buyer_username}\n"
        "🎮 Логин Steam: <code>{login}</code>\n"
        "💰 Сумма пополнения: {amount} ₽\n"
        "💳 Сумма оплаты: {price} ₽\n"
        "🕐 Дата: {date}\n\n"
        "💬 <i>Пополните Steam-аккаунт и подтвердите пользователю через «Написать пользователю»</i>"
    ),
    'premium.enter_username': "💎 <b>Купить Telegram Premium</b>\n\nВведите свой username Telegram, если покупаете для себя, или username друга, если покупаете другу.\n\n⚠️ Обязательно через <b>@</b>\nПример: <code>@username</code>",
    'premium.invalid_username': "❌ Неверный username. Введите корректный username через @\n\nПример: <code>@username</code>\n\nОсталось попыток: {attempts}",
    'premium.attempts_exhausted': "❌ Вы исчерпали все попытки ввода username. Попробуйте ещё раз.",
    'premium.choose_period': "💎 <b>Выберите срок подписки Telegram Premium</b>\n\n👤 Username: <code>{username}</code>",
    'premium.period_3m': "3 месяца | 1 590 ₽",
    'premium.period_6m': "6 месяцев | 2 690 ₽",
    'premium.period_12m': "12 месяцев | 3 790 ₽",
    'premium.choose_payment': "💎 <b>Оплата Telegram Premium</b>\n\n👤 Username: <code>{username}</code>\n📅 Срок: {period}\n💰 Сумма: {price} ₽\n\nВыберите способ оплаты:",
    'premium.success': "✅ <b>Оплата прошла успешно!</b>\n\n💎 Товар: Telegram Premium\n👤 Username: <code>{username}</code>\n📅 Срок: {period}\n💰 Сумма: {price} ₽\n\n⏳ Ожидайте получения Telegram Premium в течение <b>5-15 минут</b>.\n\nЕсли вы не получили Premium, напишите нам:",
    'premium.support_button': "💬 Поддержка",
    'premium.back_button': "🔙 Назад",
    'premium.admin_notification': "💎 <b>ПОКУПКА TELEGRAM PREMIUM</b>\n\n👤 Покупатель: {buyer_id}\n🎯 Username: <code>{username}</code>\n📅 Срок: {period}\n💰 Сумма: {price} ₽\n🕐 Дата: {date}",

    # --- Traffic / Bypass (Remnawave) ---
    'traffic.info': "<tg-emoji emoji-id=\"5190806721286657692\">📊</tg-emoji> <b>Обход блокировок</b> 🇷🇺\n\n<tg-emoji emoji-id=\"5443127283898405358\">📥</tg-emoji> {used} / {limit}\n{bar} {pct}%\n\n<tg-emoji emoji-id=\"5454415424319931791\">⏳</tg-emoji> До: {expires}\n\n<tg-emoji emoji-id=\"5271604874419647061\">🔗</tg-emoji> <b>Ключ для Happ</b> <i>(нажми — скопируется)</i>\n<blockquote expandable><code>{happ_url}</code></blockquote>\n\n<tg-emoji emoji-id=\"5271604874419647061\">🔗</tg-emoji> <b>Ключ для Incy</b> <i>(нажми — скопируется)</i>\n<blockquote expandable><code>{incy_url}</code></blockquote>\n\n<b>Установка в один клик</b>\nНажмите кнопку ниже — приложение откроется и импортирует ключ автоматически.\n\n<i>Если авто-импорт не сработал:</i>\n└ <b>Happ:</b> Главная → <b>+</b> → Вставить из буфера\n└ <b>Incy:</b> Настройки → Импорт → Из буфера",
    'traffic.no_subscription': "📊 <b>Обход блокировок</b> 🇷🇺\n\n🔒 Нет активной подписки.",
    'traffic.trial_upgrade_hint': "Купите подписку Basic или Plus, чтобы разблокировать больше ГБ и покупку трафика",
    'traffic.not_provisioned': "📊 Обход пока не настроен. Попробуйте позже.",
    'traffic.fetch_error': "⚠️ Не удалось загрузить данные о трафике. Попробуйте позже.",
    'traffic.warning_low': "Осталось {remaining} трафика обхода",
    'traffic.warning_critical': "Трафик обхода почти закончился!",
    'traffic.subscription_expired_bypass_active': "⚠️ <b>Ваша основная подписка закончилась</b>\n\nОбход блокировок продолжает работать — ваши ГБ на месте.\n\nНе забудьте докупить трафик, если он заканчивается 👇",
    'traffic.buy_subscription': "📈 Купить подписку",
    'traffic.buy_traffic_btn': "💳 Купить трафик",
    'traffic.install_insy_btn': "📥 Установить в Incy",
    'traffic.install_incy_btn': "📥 Установить в Incy",
    'traffic.install_happ_btn': "📥 Установить в Happ",
    'traffic.buy_gb_btn': "📈 Докупить ГБ обхода",
    'traffic.main_menu_btn': "🏠 Главное меню",
    'traffic.back_to_traffic': "📊 К трафику",
    'traffic.buy_title': "<tg-emoji emoji-id=\"5447410659077661506\">📦</tg-emoji> <b>Купить трафик 🇷🇺</b>\n\nДобавляется к текущему остатку.\n\n<tg-emoji emoji-id=\"5203993413346680064\">📊</tg-emoji> <b>Пакет — это ваш личный запас ГБ</b>\nНе сгорает по времени и не привязан к подписке — тратится только когда вы реально пользуетесь.\n\n<tg-emoji emoji-id=\"5325547803936572038\">✨</tg-emoji> Возьмите столько, сколько нужно — и пользуйтесь спокойно.\nЗакончится — пополните, когда удобно <tg-emoji emoji-id=\"5289944036881230584\">⭐️</tg-emoji>\n\n💎 Чем больше пакет — тем выгоднее за ГБ",
    'traffic.buy_title_extended': "<tg-emoji emoji-id=\"5447410659077661506\">🌐</tg-emoji> <b>Больше объёма 🇷🇺</b>\n\nДобавляется к текущему остатку.\n\n<blockquote><tg-emoji emoji-id=\"5190806721286657692\">📊</tg-emoji> Примерный расход:\n├ 300 ГБ — ~5 месяцев\n├ 600 ГБ — ~10 месяцев\n├ 1 200 ГБ — ~1.5 года\n├ 2 200 ГБ — ~3 чел. на год\n├ 5 000 ГБ — ~7 чел. на год\n└ 8 000 ГБ — ~11 чел. на год</blockquote>",
    'traffic.confirm_purchase': "💳 <b>Оплата: {gb} ГБ — {price} ₽</b>\n\n💰 Ваш баланс: {balance} ₽",
    'traffic.pay_wata': "Оплатить",
    'traffic.pay_reserve': "Резерв",
    'traffic.purchase_success': "✅ <b>Трафик добавлен!</b>\n\n📦 +{gb} ГБ\n💰 {price} ₽",
    'traffic.bypass_provisioning': "⏳ Настраиваем обход блокировок...\nНажмите 🔄 через несколько секунд.",
    'traffic.left_info': "<tg-emoji emoji-id=\"5456140674028019486\">💡</tg-emoji> Осталось {remaining} трафика обхода.\n\nЕго можно докупить заранее — ГБ не сгорают.",
    'traffic.left_warn': "<tg-emoji emoji-id=\"5190806721286657692\">📉</tg-emoji> Осталось {remaining} трафика обхода.\n\nДокупите ГБ, чтобы обход работал без перебоев.",
    'traffic.left_last': "<tg-emoji emoji-id=\"5190806721286657692\">🔴</tg-emoji> Осталось {remaining} трафика обхода — скоро обход отключится.\n\nДокупите ГБ, чтобы не остаться без него.",
    'traffic.zero_premium': "<tg-emoji emoji-id=\"5190806721286657692\">🚫</tg-emoji> <b>Трафик обхода исчерпан.</b>\n\nОсновные серверы по подписке продолжают работать. Чтобы обход снова заработал, докупите ГБ.",
    'traffic.zero_no_premium': "<tg-emoji emoji-id=\"5190806721286657692\">🚫</tg-emoji> <b>Трафик обхода закончился — доступ отключён.</b>\n\nКупите ГБ обхода или оформите подписку, чтобы снова пользоваться сервисом.",
    'traffic.notify_8gb':"<tg-emoji emoji-id=\"5456140674028019486\">💡</tg-emoji> Осталось {remaining} трафика обхода.\n\nМожно докупить заранее — трафик не сгорает.",
    'traffic.notify_5gb': "<tg-emoji emoji-id=\"5190806721286657692\">📉</tg-emoji> Осталось {remaining} трафика обхода.\n\nРекомендуем пополнить, чтобы обход работал без перебоев.",
    'traffic.notify_3gb': "<tg-emoji emoji-id=\"5190806721286657692\">⚠️</tg-emoji> Осталось {remaining} трафика обхода 🇷🇺\n\nКупите дополнительный трафик, чтобы обход продолжал работать.",
    'traffic.notify_1gb': "<tg-emoji emoji-id=\"5190806721286657692\">🔴</tg-emoji> Менее 1 ГБ трафика обхода! Скоро обход отключится.",
    'traffic.notify_500mb': "<tg-emoji emoji-id=\"5190806721286657692\">❗️</tg-emoji> Осталось {remaining} трафика обхода!",
    'traffic.notify_zero': "<tg-emoji emoji-id=\"5190806721286657692\">🚫</tg-emoji> Трафик обхода закончился.\n\nAtlas Fast 🇩🇪 продолжает работать без ограничений.",

    # --- Bypass-only purchase ---
    'bypass.buy_title': "🌐 <b>Обход белых списков 🇷🇺</b>\n\nДоступ к заблокированным сайтам через новейший протокол обхода.\nОплата только за трафик — без ежемесячных платежей.\n\n<blockquote>📊 Примерный расход:\n├ 15 ГБ — ~1.5 мес.\n├ 25 ГБ — ~2.5 мес.\n├ 45 ГБ — ~4 мес.\n├ 60 ГБ — ~6 мес.\n└ 120 ГБ — ~1 год</blockquote>\n\n💡 Текст и фото — мало. Видео и Reels — больше.",
    'bypass.buy_title_trial': "\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Бонус:</b> 3 дня бесплатного доступа к основным серверам!",
    'bypass.purchase_success': "✅ <b>Обход белых списков активирован!</b>\n\n📦 +{gb} ГБ трафика начислено",
    'bypass.gift_premium_granted': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Подарок: 3 дня premium</b> — до {until} МСК.\n\nКлюч основных серверов — в личном кабинете. Когда подарок закончится, обход продолжит работать на купленных ГБ.",
    'bypass.activation_delayed': "⚠️ Начисление ГБ задерживается. Если они не появятся в течение часа — напишите в поддержку.",
    'bypass.btn_profile': "👤 Личный кабинет",
    'bypass.btn_buy_more_gb': "🌐 Купить ещё ГБ",
    'bypass.btn_main_menu': "← На главную",

    # --- Combo subscription ---
    'combo.screen_title': "🚀 <b>Комбо-подписка</b>\n\nОсновные сервера + обход белых списков в одном пакете.\nВыберите тариф:",
    'combo.tariff_basic': "⚡ <b>Комбо Basic</b>\n\n<blockquote>🌐 Основные сервера (безлимит) · до 25 Гбит/с\n🌐 Обход белых списков + трафик в комплекте\n👨‍👩‍👧‍👦 Одна подписка на всю семью — до 10 устройств</blockquote>\n\n📊 <b>Пакет — это ваш личный запас ГБ</b>\n<blockquote>Не сгорает по времени и не привязан к подписке — тратится только когда вы реально пользуетесь серверами с пометкой <b>LTE</b>.</blockquote>\n\n<i>Закончится — пополните, когда удобно</i> ⭐️",
    'combo.tariff_plus': "👑 <b>Комбо Plus</b>\n\n<blockquote>🌐 Приоритетные сервера (безлимит) · до 75 Гбит/с\n🔄 Резервные каналы · соединение всегда работает\n🌐 Обход белых списков + трафик в комплекте\n👨‍👩‍👧‍👦 Одна подписка на всю семью — до 14 устройств</blockquote>\n\n📊 <b>Пакет — это ваш личный запас ГБ</b>\n<blockquote>Не сгорает по времени и не привязан к подписке — тратится только когда вы реально пользуетесь серверами с пометкой <b>LTE</b>.</blockquote>\n\n<i>Закончится — пополните, когда удобно</i> ⭐️",
    'combo.select_basic': "⚡ Комбо Basic",
    'combo.select_plus': "👑 Комбо Plus",
    'combo.period_1': "1 мес · {gb} ГБ обхода · {price} ₽",
    'combo.period_3': "3 мес · {gb} ГБ обхода · {price} ₽",
    'combo.period_6': "6 мес · {gb} ГБ обхода · {price} ₽",
    'combo.period_12': "12 мес · {gb} ГБ обхода · {price} ₽",
    'combo.period_24': "24 мес · {gb} ГБ обхода · {price} ₽",
    'tariff_switch.menu_title': "📦 <b>Сменить тарифный план</b>",
    'tariff_switch.applies_now': "Новый тариф включится сразу после оплаты и будет действовать на весь срок подписки, включая оставшиеся дни.",
    'tariff_switch.available': "Доступные тарифы:",

    # Bypass gift links — user-facing redemption messages
    'bypass_gift.activated': "🎁 <b>Подарок активирован!</b>\n\nВам начислено <b>{gb} ГБ</b> трафика обхода.\n\nГБ уже доступны и работают независимо от срока подписки. Откройте раздел «Включить обход», чтобы использовать их.",
    'bypass_gift.error_already_redeemed': "🎁 <b>Эта ссылка уже была активирована вашим аккаунтом.</b>\n\nПо каждой ссылке можно получить ГБ только один раз. Если вам нужен ещё трафик — напишите тому, кто прислал ссылку.",
    'bypass_gift.error_not_found': "❌ <b>Гифт-ссылка не найдена.</b>\n\nПроверьте правильность ссылки или обратитесь к отправителю.",
    'bypass_gift.error_expired': "⏰ <b>Срок действия ссылки истёк.</b>\n\nПопросите отправителя создать новую.",
    'bypass_gift.error_max_uses': "🚫 <b>Лимит активаций исчерпан.</b>\n\nПо этой ссылке уже активировали ГБ максимальное число раз. Попросите отправителя создать новую ссылку.",
    'bypass_gift.error_remnawave': "⚠️ <b>Не удалось начислить ГБ — попробуйте позже.</b>\n\nЕсли проблема повторится, напишите в поддержку.",
    'bypass_gift.connect_btn': "🌐 Подключить Обход",

    # Bypass gift — dedicated setup flow (only reachable from gift link)
    'bgift_setup.select_device': "📱 <b>Выберите устройство</b>\n\nПодключим обход в две короткие шага. Сначала выберите, на каком устройстве будете пользоваться.",
    'bgift_setup.connect_screen': (
        "⚡️ <b>Подключитесь в одно нажатие</b>\n\n"
        "Добавьте ключ для полноценной работы.\n\n"
        "🌐 <b>Обход</b> — белые списки РФ, интернет работает в любой точке мира.\n\n"
        "<b>Ваш ключ обхода:</b>\n"
        "<blockquote><code>{sub_url}</code></blockquote>\n"
        "<i>Нажмите на ключ, чтобы скопировать.</i>\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<b>📲 Как добавить вручную</b>\n\n"
        "<b>В Happ:</b>\n"
        "1. Откройте Happ\n"
        "2. Нажмите <b>+</b> в правом верхнем углу\n"
        "3. Выберите <b>«Добавить из буфера обмена»</b>\n"
        "4. Включите соединение переключателем\n\n"
        "<blockquote>💡 Кнопка «Добавить ключ» сверху сделает это автоматически — без копирования.</blockquote>"
    ),
    'bgift_setup.connect_no_key': (
        "⚡️ <b>Подключитесь в одно нажатие</b>\n\n"
        "🌐 <b>Обход</b> — белые списки РФ, интернет работает в любой точке мира.\n\n"
        "⚠️ Не удалось получить ваш ключ обхода. "
        "Подождите минуту и нажмите «Назад → Дальше» ещё раз. "
        "Если ошибка повторится, напишите в поддержку."
    ),

    # === Batch 2: hardcoded strings extracted from handlers ===

    # common buttons / toasts
    'common.support_short': "💬 Поддержка",
    'common.help_button': "💬 Помощь",
    'common.back_arrow': "🔙 Назад",

    # main menu / settings

    # main menu — main buttons (keyboards.py)
    'main.btn_connect_short': "⚡️ Подключиться",
    'main.btn_need_help': "💬 Нужна помощь",
    'main.btn_buy_vpn': "Купить VPN",
    'main.btn_renew_vpn': "Продлить VPN",
    'main.btn_buy_gb': "Докупить ГБ обхода",
    'main.btn_trial_free': "Попробовать бесплатно — 3 дня",
    'main.btn_renew_discount_15': "Продлить со скидкой 15% | ⏳ {remaining}",
    'main.btn_bypass_only': "🌐 Только обход блокировок",
    'main.btn_my_subscription': "Моя подписка",
    'main.btn_invite_friends': "Пригласить друзей",
    'main.btn_my_profile': "Мой профиль",
    'main.btn_shop': "Магазин",
    'main.btn_games': "Игры",
    'main.btn_help': "Помощь",
    'main.btn_devices': "Мои устройства",
    'main.btn_topup_balance': "Пополнить баланс",
    'main.btn_auto_renew_on': "🔁 Автопродление с баланса ✅",
    'main.btn_auto_renew_off': "🔁 Автопродление с баланса",
    'main.btn_change_language_full': "Сменить язык / Change language",
    'main.btn_legal': "Правила",

    # shop buttons
    'shop.premium_button': "⚡️ Telegram Premium",
    'shop.apple_id_button': "🍎 Пополнить Apple ID",
    'shop.steam_top_up_button': "🎮 Пополнить Steam",
    'shop.spotify_button': "🎧 Spotify Premium",
    'shop.claude_coming_soon_button': "🧠 Claude Pro/Max (скоро)",
    'shop.mt_proxy_button': "Купить Telegram MT Прокси",

    # setup buttons (auto-install)
    'setup.install_incy_btn': "📲 Скачать Incy",
    'setup.install_happ_btn': "📲 Установить Happ",
    'setup.download_happ_btn': "📲 Скачать Happ",
    'setup.happ_vpn_label': "Happ VPN",
    'setup.incy_vpn_label': "Incy VPN",
    'setup.happ_bypass_label': "Happ Обход",
    'setup.incy_bypass_label': "Incy Обход",
    'setup.download_incy': "📲 Скачать Incy",
    'setup.install_karing_btn': "📲 Скачать Karing",
    'setup.karing_vpn_label': "Karing VPN",
    'setup.karing_bypass_label': "Karing Обход",
    'setup.btn_add_karing': "🔷 Добавить ключ в Karing",
    # «Другие клиенты»: два обычных (не зашифрованных) ключа подписки —
    # Premium и Обход — для v2RayTun / Karing / Stash / Clash Verge.
    'setup.other_clients_btn': "🧩 Другие клиенты",
    'setup.other_title': "🧩 <b>Другие клиенты</b>\n\nКлючи ниже подходят для v2RayTun, Karing, Stash, Clash Verge (Clash Meta / Mihomo) и других приложений, которые умеют добавлять подписку по ссылке.",
    'setup.other_clients_header': "<b>Для вашего устройства:</b>",
    'setup.other_about_v2raytun': "• <a href=\"{url}\">v2RayTun</a> — простой и лёгкий клиент",
    'setup.other_about_karing': "• <a href=\"{url}\">Karing</a> — Clash, sing-box и V2Ray в одном приложении",
    'setup.other_about_stash': "• <a href=\"{url}\">Stash</a> — клиент на ядре Clash для iPhone и iPad",
    'setup.other_about_clash': "• <a href=\"{url}\">Clash Verge</a> — клиент Clash Meta (Mihomo) для компьютера",
    'setup.other_howto': "<b>Как добавить ключ</b>\n<blockquote>1. Нажмите на ключ ниже — он скопируется\n2. В приложении нажмите «+», «Добавить подписку» или «Импорт из буфера»\n3. Вставьте ссылку и обновите подписку\n4. Выберите сервер и подключитесь</blockquote>\nИли нажмите кнопку с названием приложения — ключ добавится сам.",
    'setup.other_key_premium': "🔑 <b>Premium</b> — основной VPN, безлимитные сервера:",
    'setup.other_key_bypass': "🌐 <b>Обход</b> — для обхода белых списков и блокировок, расходует ГБ:",
    'setup.other_only_bypass_note': "<i>Сейчас у вас только ключ обхода. Ключ Premium появится после оформления подписки.</i>",
    'setup.other_only_premium_note': "<i>Ключ обхода появится, когда станет доступен трафик обхода.</i>",
    'setup.other_no_keys': "<i>Ключей пока нет — оформите подписку, и они появятся здесь.</i>",
    'setup.other_btn_premium': "{client} · Premium",
    'setup.other_btn_bypass': "{client} · Обход",
    'setup.other_copy_premium': "📋 Скопировать Premium",
    'setup.other_copy_bypass': "📋 Скопировать Обход",
    'setup.other_profile_premium': "Atlas Secure",
    'setup.other_profile_bypass': "Atlas Secure Обход",
    'setup.key_hint_press': "Установка ключа в одно нажатие 👇",

    # errors — payment / general
    'errors.card_payment_unavailable': "Оплата картой временно недоступна",
    'errors.sbp_unavailable_toast': "СБП временно недоступен",
    'errors.min_card_amount': "Сумма ниже минимальной для оплаты картой (64₽)",
    'errors.payment_creation': "Ошибка создания платежа",
    'errors.sbp_creation': "Ошибка создания платежа СБП",
    'errors.special_offer_expired': "⏰ Срок спецпредложения истёк. Вы можете приобрести подписку по обычной цене.",

    # main — 15% auto-discount notification
    'main.discount_applied_choose_tariff': "🎁 Скидка 15% применена — действует до {deadline}.\n\nВыберите тариф:",
    # the user already has a bigger discount — it stays (the −15 % did not replace it)
    'main.discount_bigger_kept': "🎁 У вас уже есть скидка {percent}% — она больше, поэтому остаётся в силе и применится при оплате.\n\nВыберите тариф:",

    # combo flow
    'combo.promo_period_prompt': "\n\n🎁 Промокод: скидка {discount_pct}%\nВыберите период:",
    'combo.choose_period_prompt': "\n\nВыберите период:",

    # traffic / bypass buttons
    'traffic.btn_more_volume': "Больше объёма →",
    'traffic.promo_active_line': "\n\n🎁 Промо-скидка {discount_pct}% активна!",
    'traffic.refresh_btn': "🔄 Обновить",

    # buy screen
    'buy.combo_button': "🚀 Комбо (VPN + обход)",
    'buy.have_promo_button': "У меня промокод",
    'buy.select_tariff_new': '<tg-emoji emoji-id="5427168083074628963">💎</tg-emoji> <b>Выберите тариф</b>\n\n{tariffs}\n\n<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji> <b>Комбо</b> — VPN + обход в одном пакете\n<blockquote>Трафик обхода включён · от 329 ₽/мес</blockquote>',
    'buy.select_tariff_bypass_active': "🌐 <b>У вас активен обход блокировок</b>\n\nДля основной подписки выберите тариф:\n\n{tariffs}\n\n<tg-emoji emoji-id=\"5445284980978621387\">🚀</tg-emoji> <b>Комбо</b> — VPN + обход в одном пакете\n<blockquote>Трафик обхода включён · от 329 ₽/мес</blockquote>",

    # profile screen (personal cabinet)
    'profile.info_active_until': "📆 Подписка: активна до {date}",
    'profile.info_tariff': "⭐️ Тариф: {tariff}",
    'profile.info_inactive': "📆 Подписка: не активна",
    'profile.info_tariff_none': "⭐️ Тариф: —",
    'profile.info_bypass_none': "💎 Трафик: —",
    'profile.info_bypass_left': "💎 Осталось трафика: {remaining} из {limit}",
    'profile.info_auto_renew_on': "🔁 Автопродление: включено",
    'profile.info_auto_renew_none': "🔁 Автопродление: —",
    'profile.info_balance': "💰 Баланс: {balance} ₽",
    'profile.info_invited_friends': "👥 Приглашено друзей: {count}",

    # my subscription screen
    'main.my_sub_title': "<b>Информация о подписке</b>",
    'main.my_sub_active_until': "Активна до: {date}",
    'main.my_sub_active_until_none': "Активна до: —",
    'main.my_sub_bypass_none': "Трафик: —",
    'main.my_sub_bypass_left': "Осталось трафика: {remaining} из {limit}",
    'main.my_sub_bypass_unlimited': "Трафик: безлимит",
    'main.my_sub_btn_connect': "Подключить VPN",
    'main.my_sub_btn_renew': "Продлить подписку",
    'main.my_sub_btn_buy_vpn': "Купить VPN",
    'main.my_sub_btn_topup_gb': "Пополнить ГБ Обхода",
    'main.my_sub_btn_my_proxy': "Мой прокси",
    'main.my_sub_btn_mt_proxy': "Telegram MT Прокси",

    # legal
    'main.legal_title': "📰 <b>Правовые документы</b>\n\nВыберите документ для ознакомления:",
    'main.legal_terms_btn': "Пользовательское соглашение",
    'main.legal_privacy_btn': "Политика конфиденциальности",

    # help / faq screen buttons
    'help.faq_button': "📖 Ответы на частые вопросы",
    'help.instructions_button': "📲 Инструкции по сервису",
    'help.contacts_button': "📞 Контакты",

    # referral screen

    # Invoice descriptions & minor payment strings
    'buy.period_text_1': "1 месяц",
    'buy.period_text_2_4': "{months} месяца",
    'buy.period_text_5_plus': "{months} месяцев",
    'trial.degradation_notice': "\n\n⏳ Возможны небольшие задержки",

    # Plus→Basic downgrade confirmation
    'buy.downgrade_confirm_text': "⚠️ Вы переходите с Plus на Basic.\n\nКлюч будет ротирован с выделенного сервера на базовый.\n\nПодтвердить переход?",
    'buy.downgrade_confirm_yes': "⚡️ Да, перейти на Basic",
    'buy.downgrade_confirm_no': "❌ Отмена",

    # global discount notice
    'buy.global_discount_default_reason': "Спец-цены",
    'buy.global_discount_notice': "\n\n🎁 <b>Скидка −{pct}%</b> · {reason}",
    'buy.global_discount_notice_dated': "\n\n🎁 <b>Скидка −{pct}%</b> · {reason} · до {date}",

    # start.py — site link + promo/gift link errors
    'promo_link.not_found': "⚠️ <b>Ссылка не найдена</b>\n\nВозможно, она удалена или адрес введён неправильно.",
    'promo_link.activation_failed': "⚠️ <b>Не получилось активировать ссылку</b>\n\nПопробуй ещё раз чуть позже.",
    'promo_link.error_inactive': "🚫 <b>Ссылка выключена</b>\n\nАдмин её деактивировал.",
    'promo_link.error_expired': "⏳ <b>Срок действия ссылки истёк</b>",
    'promo_link.error_exhausted': "🚫 <b>Ссылка полностью использована</b>\n\nЛимит активаций исчерпан.",
    'promo_link.error_already_redeemed_by_user': "ℹ️ <b>Ты уже использовал эту ссылку</b>\n\nОдна активация на пользователя.",
    'promo_link.error_not_found': "⚠️ <b>Ссылка не найдена</b>",
    'promo_link.error_db_not_ready': "⚠️ <b>Сервис перезапускается</b>\n\nПопробуй через минуту.",
    'promo_link.error_generic': "⚠️ <b>Активация не прошла</b>",
    'promo_link.reward_not_applied': "⚠️ <b>Награда пока не применилась</b>\n\nПопробуй ещё раз через минуту или напиши в поддержку — мы всё выдадим.",
    'promo_link.header_activated': "🎉 <b>Награда активирована!</b>\n\n",
    'promo_link.reward_subscription': "📦 <b>Подписка</b> · {tariff}\n⏳ <b>{days} дн.</b>\n📅 До: <b>{end}</b>",
    'promo_link.reward_discount_subscription': "🎁 <b>Твой подарок активирован</b>\n\n<blockquote>— Скидка <b>{percent}%</b> на любой тариф\n— Действует ещё <b>{hours} часов</b></blockquote>\n\nВыбери подходящий тариф ниже ↓",
    'promo_link.reward_discount_traffic': "🎁 <b>Твой подарок активирован</b>\n\n<blockquote>— Скидка <b>{percent}%</b> на пакеты ГБ обхода\n— Действует ещё <b>{hours} часов</b></blockquote>\n\nВыбери подходящий тариф ниже ↓",
    'promo_link.reward_bypass_gb': "📊 <b>+{gb} ГБ</b> обхода начислено\n\nПакет ГБ не сгорает — тратится только при работе на LTE-серверах.",
    'promo_link.fallback_success_hint': "Открой «Купить подписку» — скидка применится автоматически.",

    # stage-only user picker
    'stage.user_role_prompt': "Привет 👋\n\nТы разработчик Atlas Secure или пользователь?\nВыбери вариант ниже 👇",
    'stage.role_user_btn': "👤 Пользователь",
    'stage.role_dev_btn': "💻 Разработчик",
    # --- Воронка продаж (docs/audit/SCOPE.md «Воронка продаж», app/services/sales_funnel) ---
    # Цепочка 1: нажали /start, без пробного и подписки
    'funnel.start_1h': "👋 <b>Вы в одном шаге от Atlas Secure</b>\n\nВключите <b>пробный период на 3 дня</b> — это бесплатно, платить ничего не нужно.\n\nYouTube, Instagram, Telegram и любимые сайты — снова без блокировок и тормозов 🚀",
    'funnel.start_1d': "🛡 <b>Что даёт Atlas Secure</b>\n\n<blockquote>🚀 Скорость до 25 Гбит/с — видео в 4K без буферизации\n🌐 Обход белых списков — работает, даже когда мобильный интернет ограничен\n👨‍👩‍👧‍👦 Несколько устройств на одной подписке\n➕ Подключение в одно нажатие</blockquote>\n\nПроверьте сами: <b>3 дня бесплатно</b>, без оплаты.",
    'funnel.start_3d': "🎁 <b>Два способа начать</b>\n\n1️⃣ <b>Бесплатно</b> — пробный период на 3 дня.\n2️⃣ <b>Сразу подписка со скидкой {percent}%</b> — действует до <b>{deadline}</b>.\n\nСкидка уже закреплена за вами и применится автоматически при оплате.",
    'funnel.start_7d': "🔥 <b>−{percent}% на первый месяц</b>\n\nМы придержали для вас скидку <b>{percent}%</b> на подписку Atlas Secure — первый месяц обойдётся заметно дешевле, а подключение займёт минуту.\n\n⏰ Скидка действует до <b>{deadline}</b> и применится автоматически.",
    'funnel.start_30d': "💎 <b>Скидка {percent}% — специально для вас</b>\n\nВы так и не попробовали Atlas Secure. Самое время: <b>−{percent}%</b> на любую подписку до <b>{deadline}</b>.\n\nНе хотите платить сразу — начните с бесплатного пробного периода.",
    # Цепочка 2: пробный закончился, не купил
    'funnel.trial_1d': "⏳ <b>Ваша скидка {percent}% ещё действует</b>\n\nПосле пробного периода за вами закреплена скидка <b>{percent}%</b> на подписку. Осталось {days_left} — до <b>{deadline}</b>.\n\nСкидка применится автоматически при оплате.",
    'funnel.trial_6d': "⏰ <b>Скидка {percent}% скоро сгорит</b>\n\nОна действует до <b>{deadline}</b> — потом цена вернётся к обычной.\n\nОформите подписку сейчас и снова пользуйтесь интернетом без блокировок.",
    'funnel.trial_14d': "👋 <b>Возвращайтесь в Atlas Secure — −{percent}%</b>\n\nСкучаете по быстрому интернету без блокировок? Для вас скидка <b>{percent}%</b> на любую подписку.\n\n⏰ Действует до <b>{deadline}</b>, применится автоматически.",
    'funnel.trial_30d': "💎 <b>−{percent}% на подписку Atlas Secure</b>\n\nМесяц назад вы пробовали Atlas Secure. Возвращайтесь со скидкой <b>{percent}%</b> — она действует до <b>{deadline}</b>.\n\nБыстрые серверы, обход белых списков и подключение в одно нажатие — всё на месте.",
    'funnel.trial_90d': "🎁 <b>Наша лучшая скидка — {percent}%</b>\n\nТакой скидки на Atlas Secure ещё не было: <b>−{percent}%</b> на любую подписку до <b>{deadline}</b>.\n\nВозвращайтесь — будем рады 🤍",
    # Цепочка 3: платная закончилась, не продлил
    'funnel.paid_6h': "🔌 <b>Подписка Atlas Secure закончилась</b>\n\nПродлите её — VPN заработает сразу после оплаты.\n\n🎁 Для вас действует скидка <b>{percent}%</b> на продление — до <b>{deadline}</b>.",
    'funnel.paid_1d': "⏳ <b>Скидка {percent}% на продление — осталось {days_left}</b>\n\nОна действует до <b>{deadline}</b> и применится автоматически при оплате.\n\nПродлите подписку и снова пользуйтесь интернетом без блокировок.",
    'funnel.paid_3d': "🔥 <b>−{percent}% на продление</b>\n\nВернитесь в Atlas Secure со скидкой <b>{percent}%</b> на любой тариф и срок. Действует до <b>{deadline}</b>.",
    'funnel.paid_7d': "💎 <b>−{percent}% на продление Atlas Secure</b>\n\nНеделя без быстрого интернета — достаточно 🙂 Продлите подписку со скидкой <b>{percent}%</b> до <b>{deadline}</b>.",
    'funnel.paid_30d': "👋 <b>Мы скучаем! −{percent}% на возвращение</b>\n\nСкидка <b>{percent}%</b> на любую подписку Atlas Secure действует до <b>{deadline}</b>. Подключение займёт минуту.",
    'funnel.paid_90d': "🎁 <b>Наша лучшая скидка — {percent}%</b>\n\n<b>−{percent}%</b> на любую подписку Atlas Secure до <b>{deadline}</b>.\n\nВозвращайтесь — будем рады 🤍",
    'funnel.btn_trial': "🎁 Попробовать бесплатно",
    'funnel.btn_buy_discount': "🔥 Купить со скидкой",
    'funnel.btn_renew_discount': "🔄 Продлить со скидкой",
    'funnel.discount_active_note': "🎁 Ваша скидка <b>{percent}%</b> уже учтена в ценах — действует до <b>{deadline}</b>.",
    'funnel.discount_expired_note': "⏰ Срок скидки истёк — сейчас действуют обычные цены.",
    'funnel.no_deadline': "без ограничения по сроку",
    'funnel.days_one': "{n} день",
    'funnel.days_few': "{n} дня",
    'funnel.days_many': "{n} дней",
}