# Неиспользуемые ключи i18n

- Дата: 2026-09-13 · База: `refactor/audit-2026-09` @ `fe4f1efc`
- Словари: `app/i18n/ru.py` — 1144 ключа, `app/i18n/en.py` — 1151 ключ (объединение — 1151; 7 ключей есть только в EN).

## Итог

| Категория | Ключей |
|---|---|
| **Мёртвые** — строка ключа не встречается нигде в коде | **349** |
| Возможно живые — ключ собирается динамически (`f"buy.tariff_{x}_desc"`, `"payment." + x`, `help.faq_q{n}`) | 65 |
| Используются только в тестах | 0 |

## Как считали

Скрипт брал каждый ключ из `ru.LANG ∪ en.LANG` и искал его в кавычках (`"ns.key"` или `'ns.key'`) во всех `*.py, *.ts, *.tsx, *.js, *.mjs, *.json, *.sql, *.html` репозитория, кроме `app/i18n/`, `docs/`, `.venv/`, `node_modules/`. Ключ, начинающийся с префикса f-строки или конкатенации (`buy.tariff_`, `buy.tariff_button_`, `help.faq_a`, `help.faq_q`, `lang.button_`, `payment.`, `setup.combined_`, `setup.connect_`, `setup.download_`, …), вынесен в «возможно живые» и в список мёртвых **не попал**.

**Удалять ключ нужно из обоих файлов** (`ru.py` и `en.py`). Риск нулевой: `get_text` при отсутствии ключа не падает (fallback на EN, затем возвращает сам ключ). Но если ключ всё-таки используется динамически и мы его пропустили, юзер увидит сырой ключ вместо текста. Поэтому перед удалением стоит один раз прогнать `grep -rn "<префикс>" app/` по неймспейсу.

## Что видно по мёртвым ключам (подсказки для чистки экранов)

- `main.*` (83) — старые версии главного экрана, профиля и напоминаний: `main.profile_*`, `main.smart_notif_*`, `main.reminder_paid_*`, `main.trial_notification_*`, `main.vip_*`, `main.service_status*`, `main.home_welcome_text`. Экранов с этими текстами больше нет.
- `admin.*` (60) — тексты старой админки в боте: скидки, VIP, выдача доступа, экспорт, перевыпуск. Подтверждает, что часть админ-экранов переписана без i18n или удалена.
- `referral.*` (38) — прежние версии экрана «Круг Амбассадоров».
- `farm.*` (31) — ферма перешла на захардкоженные тексты (`game.py:798` «💧 Полить #…»), а i18n-версии остались.
- `profile.*` (22), `subscription.*` (15) — старый профиль и история подписок.
- `setup.*` (17), `instruction.*` (8), `connect.*` (5), `get_key.*` (3) — старые экраны подключения и «Авто настройки». Среди них ключи сирот `setup_device`: `setup.device_button`, `connect.setup_device_button`.
- `buy.corporate*` — удалённая кнопка «🏢 Для бизнеса» (см. сирота `corporate_access_request`).
- `withdraw.*`, `profile.withdraw_funds` — удалённый вход в «Вывод средств».
- `traffic.pay_card` и соседние — удалённая кнопка «Оплатить картой» у пакета ГБ.
- `shop.steam_main_button`, `premium.main_button`, `premium.invalid_username` — 🔒 магазин. Удаление ключей магазина — только с разрешения владельца (SCOPE.md).

## Полный список мёртвых ключей по неймспейсам


### `main.*` — 83

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `main.auto_renew_disable` | ru+en | ⏸ Отключить автопродление |
| `main.auto_renew_disabled` | ru+en | ⏸ Автопродление отключено |
| `main.auto_renew_enable` | ru+en | 🔄 Включить автопродление |
| `main.auto_renew_enabled` | ru+en | ✅ Автопродление включено |
| `main.auto_renewal_insufficient_balance` | ru+en | ⚠️ Недостаточно средств для автопродления подписки.  Требуется: {amoun |
| `main.auto_renewal_success` | ru+en | ✅ Подписка автоматически продлена на {days} дней.  Действует до: {expi |
| `main.balance_topup_waiting` | ru+en | ₿ Пополнение баланса через криптовалюту  Сумма: {amount} ₽  ⏳ Ожидаем  |
| `main.buy_new` | ru+en | 🔐 Купить подписку |
| `main.buy_renew` | ru+en | 🔄 Продлить подписку |
| `main.change_language` | ru+en | 🌍 Изменить язык |
| `main.check_payment` | ru+en | ✅ Проверить оплату |
| `main.contact_manager_button` | ru+en | 💬 Подключить VIP-доступ |
| `main.db_init_stage_warning` | ru+en | ⚠️ База данных ещё инициализируется (STAGE). Некоторые функции могут б |
| `main.enter_promo_text` | ru+en | Введите промокод: |
| `main.friend_dual` | ru+en | друга |
| `main.friend_plural` | ru+en | друзей |
| `main.friend_singular` | ru+en | друг |
| `main.game` | ru+en | Игры 🎮 |
| `main.game_club` | ru+en | 🎮 Игровой клуб |
| `main.get_access` | ru+en | 🔐 Оформить подписку |
| `main.help` | ru+en | 🛡 Поддержка |
| `main.home_welcome_text` | ru+en | 🔐 Atlas Secure  Рады видеть Вас в Atlas Secure 🤝  Мы обеспечиваем: ⚙️  |
| `main.incident_banner` | ru+en | ⚠️ Ведутся технические работы |
| `main.incident_status_warning` | ru+en |   ⚠️ ВНИМАНИЕ: Режим инцидента активен {incident_text} |
| `main.insufficient_balance` | ru+en | Недостаточно средств на балансе.  Стоимость: {amount:.2f} ₽ На балансе |
| `main.insufficient_balance_for_subscription` | ru+en | Недостаточно средств на балансе.  Стоимость: {amount:.2f} ₽ На балансе |
| `main.legal_footer` | ru+en |   <a href="https://telegra.ph/Politika-konfidencialnosti-09-03-71">Пол |
| `main.no` | ru+en | НЕТ |
| `main.no_subscription` | ru+en | 👤 Профиль подписки  В данный момент подписка не активирована.  Atlas S |
| `main.pay_card` | ru+en | 💳 Банковская карта |
| `main.personal_discount_label` | ru+en | 🎯 Персональная скидка {percent}% |
| `main.profile_active` | ru+en | 👤 Профиль подписки  Статус подписки: Активен Подписка оплачена до {dat |
| `main.profile_auto_renew_disabled` | ru+en | 🔁 Автопродление: выключено |
| `main.profile_auto_renew_enabled` | ru+en | 🔁 Автопродление: {next_billing_date} |
| `main.profile_buy_hint` | ru+en | Нажмите «Купить подписку» в меню, чтобы оформить подписку. |
| `main.profile_payment_check` | ru+en | 🕒 Платёж на проверке.  Это стандартная процедура безопасности. После п |
| `main.profile_renewal_hint` | ru+en |  |
| `main.profile_renewal_hint_new` | ru+en | При продлении выбранный срок добавляется к текущему автоматически. |
| `main.profile_subscription_active` | ru+en | 📆 Подписка: активна до {date} |
| `main.profile_subscription_inactive` | ru+en | 📆 Подписка: неактивна |
| `main.profile_subscription_pending` | ru+en | 📆 Подписка оформлена, активация выполняется автоматически  Срок действ |
| `main.profile_welcome` | ru+en | 👤 {username} 📊 Баланс: {balance:.2f} ₽ |
| `main.profile_welcome_full` | ru+en | Добро пожаловать в Atlas Secure!  👤 {username}  💰 Баланс: {balance:.2f |
| `main.promo_discount_label` | ru+en | 🎟 Промокод |
| `main.referral` | ru+en | 💎 Программа лояльности |
| `main.reminder_admin_1day_6h` | ru+en | ⏳ Временный доступ Atlas Secure завершается через 6 часов.  Рекомендуе |
| `main.reminder_admin_7days_24h` | ru+en | ⏳ Временный доступ Atlas Secure завершается через 24 часа.  Рекомендуе |
| `main.reminder_paid_24h` | ru+en | ⏳ Срок вашего доступа Atlas Secure истекает через 24 часа.  Рекомендуе |
| `main.reminder_paid_3d` | ru+en | ⏳ Срок вашего доступа Atlas Secure истекает через 3 дня.  Вы можете пр |
| `main.reminder_paid_3h` | ru+en | ⏳ Срок вашего доступа Atlas Secure истекает через 3 часа.  Продлите по |
| `main.sbp_payment_text` | ru+en | После выполнения перевода подтвердите оплату.  ⸻  Реквизиты для перево |
| `main.select_payment` | ru+en | Выберите способ оплаты. |
| `main.select_payment_method` | ru+en | Выберите способ оплаты:  Сумма: {price:.2f} ₽ |
| `main.service_status` | ru+en | 📊 Статус сервиса |
| `main.service_status_text` | ru+en | 📊 Статус сервиса Atlas Secure  Текущий статус: 🟢 Сервис работает стаби |
| `main.smart_notif_3days_before_expiry` | ru+en | Напоминание: доступ будет активен ещё 3 дня.  Продление занимает менее |
| `main.smart_notif_3days_usage` | ru+en | Atlas Secure используется без ограничений и не требует продлений вручн |
| `main.smart_notif_7days_before_expiry` | ru+en | Срок действия доступа заканчивается через 7 дней.  Вы можете продлить  |
| `main.smart_notif_expired_24h` | ru+en | Доступ приостановлен.  Вы можете восстановить его в любой момент — без |
| `main.smart_notif_expiry_day` | ru+en | Сегодня истекает срок действия доступа.  При продлении ключ и настройк |
| `main.smart_notif_first_connection` | ru+en | Соединение активно.  Доступ работает стабильно и не требует вашего вни |
| `main.smart_notif_no_traffic_20m` | ru+en | Если вы ещё не подключились — обычно это занимает не более минуты.  Кл |
| `main.smart_notif_no_traffic_24h` | ru+en | Напоминание: доступ активен и готов к использованию.  Подключение не в |
| `main.smart_notif_vip_offer` | ru+en | Для пользователей с активным доступом доступен расширенный уровень соп |
| `main.subscribe_1_month_button` | ru+en | 🔐 Подписка на 1 месяц |
| `main.support_text` | ru+en | 🛡 Поддержка Atlas Secure  Если у вас есть вопросы по доступу, оплате и |
| `main.title` | ru+en | Главное меню |
| `main.topup_balance` | ru+en | ➕ Пополнить баланс |
| `main.traffic_btn` | ru+en | 📊 Мой трафик |
| `main.trial_activated_text` | ru+en | ✅ <b>Пробный доступ активирован</b>  📦 Тариф: Basic · 3 дня 📅 До: {exp |
| `main.trial_button` | ru+en | 🎁 Пробный период 3 дня |
| `main.trial_expired_text` | ru+en | 🔓 <b>Пробный доступ завершён</b>  Ваш пробный период истёк.  🎟 Использ |
| `main.trial_notification_60h` | ru+en | ⏰ До конца пробного периода осталось 24 часа  За это время вы уже оцен |
| `main.trial_notification_6h` | ru+en | 👋 Рады что вы с нами!  Ваш VPN активен и защищает вас прямо сейчас. По |
| `main.trial_notification_71h` | ru+en | 🔔 Через 6 часов пробный доступ завершится и VPN отключится  Не теряйте |
| `main.user_fallback` | ru+en | пользователь |
| `main.vip_access_button` | ru+en | 👑 Улучшить уровень доступа |
| `main.vip_access_text` | ru+en | 👑 VIP-доступ Atlas Secure  VIP — это расширенный уровень обслуживания  |
| `main.vip_discount_label` | ru+en | 👑 VIP-доступ |
| `main.vip_status_active` | ru+en | 👑 Ваш VIP-статус активен |
| `main.vip_status_badge` | ru+en | 👑 VIP-статус активен |
| `main.welcome_discount_label` | ru+en | <tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji> Приветственная с |
| `main.yes` | ru+en | ДА |

### `admin.*` — 60

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `admin.access_revoked` | ru+en | ✅ Доступ отозван |
| `admin.action_without_notification` | ru+en | Действие выполнено без уведомления. |
| `admin.approve` | ru+en | Подтвердить |
| `admin.audit_empty` | ru+en | 📜 Аудит  Аудит пуст. Действий не зафиксировано. |
| `admin.back_to_keys` | ru+en | 🔙 Назад |
| `admin.back_to_stats` | ru+en | 🔙 Назад к статистике |
| `admin.credit_positive_sum` | ru+en | ❌ Сумма должна быть положительным числом.  Введите сумму в рублях: |
| `admin.credit_sum_error` | ru+en | Ошибка при обработке суммы. Проверьте логи. |
| `admin.credit_sum_format` | ru+en | ❌ Неверный формат суммы.  Введите число (например: 500 или 100.50): |
| `admin.credit_user_not_found` | ru+en | Ошибка: пользователь не найден. Начните заново. |
| `admin.db_connection_failed` | ru+en | ❌ Не удалось подключиться к базе данных |
| `admin.db_unavailable` | ru+en | ❌ База данных недоступна |
| `admin.debit_balance` | ru+en | ➖ Снять |
| `admin.discount_already_exists` | ru+en | ❌ У пользователя уже есть персональная скидка {percent}%.  Сначала уда |
| `admin.discount_assign_days_prompt` | ru+en | 🎯 Назначить скидку  Введите количество дней действия скидки (или 0 для |
| `admin.discount_assign_prompt` | ru+en | 🎯 Назначить скидку  Введите процент скидки (число от 1 до 99): |
| `admin.discount_created` | ru+en | ✅ Персональная скидка {percent}% назначена  Срок действия: {expires} |
| `admin.discount_days_nonnegative` | ru+en | Количество дней должно быть неотрицательным. Попробуйте снова: |
| `admin.discount_enter_1_99` | ru+en | Введите число от 1 до 99: |
| `admin.discount_enter_days` | ru+en | Введите число (количество дней или 0 для бессрочной): |
| `admin.discount_error` | ru+en | ❌ Ошибка при создании скидки |
| `admin.discount_not_found` | ru+en | ❌ Скидка не найдена или уже удалена |
| `admin.discount_percent_1_99` | ru+en | Процент скидки должен быть от 1 до 99. Попробуйте снова: |
| `admin.discount_removed` | ru+en | ✅ Персональная скидка удалена |
| `admin.enter_number` | ru+en | ❌ Введите число |
| `admin.enter_positive_number` | ru+en | ❌ Введите положительное число |
| `admin.export_error` | ru+en | Ошибка при экспорте данных. Проверь логи. |
| `admin.export_file_sent` | ru+en | ✅ Файл отправлен |
| `admin.export_invalid_type` | ru+en | Неверный тип экспорта |
| `admin.export_no_data` | ru+en | Нет данных для экспорта |
| `admin.grant_access_error` | ru+en | ❌ Ошибка выдачи доступа: {error} |
| `admin.grant_days_prompt` | ru+en | Выберите срок доступа: |
| `admin.grant_fail_no_keys` | ru+en | ❌ Нет свободных VPN-ключей |
| `admin.grant_success` | ru+en | ✅ Доступ выдан на {days} дней.  Доступ активирован администратором. |
| `admin.grant_user_notification` | ru+en | ✅ Вам предоставлен доступ к Atlas Secure на {days} дней. Ссылка подпис |
| `admin.grant_user_notification_10m` | ru+en | ⏱ Доступ активирован на 10 минут.  Вы можете подключиться сразу. По ок |
| `admin.incident_edit_text` | ru+en | 📝 Изменить текст |
| `admin.no_access` | ru+en | Нет доступа |
| `admin.notif_promo_period_1d` | ru+en | 1 день |
| `admin.notif_promo_period_3d` | ru+en | 3 дня |
| `admin.notif_promo_period_7d` | ru+en | 7 дней |
| `admin.payment_notification` | ru+en | 💰 Новая оплата Пользователь: @{username} Telegram ID: {telegram_id} Та |
| `admin.promo_enter_text` | ru+en | Пожалуйста, введите промокод текстом. |
| `admin.reissue_error` | ru+en | Ошибка при перевыпуске ключа. Проверь логи. |
| `admin.reissue_invalid_id` | ru+en | Неверный формат telegram_id. Используйте число. |
| `admin.reissue_success` | ru+en | Ключ успешно перевыпущен |
| `admin.reissue_usage` | ru+en | Использование: /reissue_key <telegram_id> |
| `admin.reject` | ru+en | Отклонить |
| `admin.revoke_fail_no_sub` | ru+en | ❌ У пользователя нет активной подписки |
| `admin.revoke_success` | ru+en | ✅ Доступ отозван.  Пользователь уведомлён. |
| `admin.revoke_user_notification` | ru+en | ⛔ Ваш доступ к Atlas Secure был отозван администратором. |
| `admin.send` | ru+en | ✅ Отправить |
| `admin.subscription_history_empty` | ru+en | 🧾 История подписок  История подписок пуста. |
| `admin.unlimited` | ru+en | бессрочно |
| `admin.user_info_error` | ru+en | Ошибка при получении информации о пользователе. Проверь логи. |
| `admin.vip_already_assigned` | ru+en | VIP уже назначен |
| `admin.vip_assign_error` | ru+en | ❌ Ошибка при назначении VIP-статуса |
| `admin.vip_granted` | ru+en | ✅ VIP-статус выдан |
| `admin.vip_not_found` | ru+en | ❌ VIP-статус не найден или уже снят |
| `admin.vip_revoked` | ru+en | ✅ VIP-статус снят |

### `referral.*` — 38

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `referral.action_purchase` | ru+en | покупка |
| `referral.action_renewal` | ru+en | продление |
| `referral.action_topup` | ru+en | пополнение |
| `referral.active_paid` | ru+en | 💎 Активных с подпиской: {count} |
| `referral.active_with_subscription` | ru+en | 💎 Активных с подпиской: {count} |
| `referral.cashback_referred` | +en | 👤 Referral: {referred} |
| `referral.cashback_x2_active` | ru+en | 🔥 Сейчас действует x2 кешбэк! Каждая покупка друга = двойная награда. |
| `referral.copy_link` | ru+en | 📋 Скопировать ссылку |
| `referral.current_status` | ru+en | 🏆 Текущий статус: {status} |
| `referral.first_payment_notification` | ru+en | Когда ваш реферал совершит первую оплату, вам будет начислен кешбэк! |
| `referral.header` | ru+en | 🎖 <b>Круг Амбассадоров</b> <i>От проводника до амбассадора</i>   |
| `referral.hero_beginner` | ru+en | Ты на первой ступени. Делись ссылкой → друг покупает подписку → ты пол |
| `referral.hero_top` | ru+en | Это вершина. <b>Зафиксировано бессрочно.</b> Тебя меньше 1%.   |
| `referral.last_activity` | ru+en | 📅 Последняя активность: {date} |
| `referral.level_progress` | ru+en |   📈 Ваш уровень: {current_level}% До уровня {next_level}% осталось {re |
| `referral.link_copied` | ru+en | Ссылка отправлена |
| `referral.max_level` | ru+en |   🎉 Вы достигли максимального уровня {current_level}%! |
| `referral.next_level_line` | ru+en | 🚀 До уровня {next_status_name}: осталось {remaining_invites} подключен |
| `referral.program` | ru+en | 🎖 Круг Амбассадоров |
| `referral.program_screen` | ru+en | 📊 Активность и статус доступа  👤 Подключённых аккаунтов: {total_referr |
| `referral.program_status_footer` | ru+en | 🚀 До следующего уровня: осталось {remaining_invites} приглашений |
| `referral.program_text` | ru+en | 🎖 Круг Амбассадоров  Приглашайте друзей и получайте кешбэк на баланс с |
| `referral.progress_to_next` | ru+en | 📈 До <b>{next_name}</b> ({next_pct}%) — <b>{remaining}</b> купивших. У |
| `referral.registered_date` | ru+en | 📅 {date} |
| `referral.registered_notification` | ru+en | 🎉 У вас новый реферал!  📅 {date}  {first_payment_msg} |
| `referral.registered_title` | ru+en | 🎉 У вас новый реферал! |
| `referral.registered_user` | +en | 👤 User: {user} |
| `referral.rewards_earned` | ru+en | 💎 Начислено вознаграждений: {amount:.2f} ₽ |
| `referral.screen_title` | ru+en | 🎖 Круг Амбассадоров |
| `referral.share_link_button` | ru+en | 📤 Поделиться ссылкой |
| `referral.stats_next_level_line` | ru+en | 🚀 До уровня {next_status_name}: осталось {remaining_invites} подключен |
| `referral.status_block` | ru+en | 🏆 Уровень: <b>{status}</b> · {percent}% 💵 <b>{percent}%</b> кэшбэка с  |
| `referral.total_invited` | ru+en | 👤 Всего приглашено: {count} |
| `referral.trial_activated_notification` | ru+en | 🚀 Ваш реферал начал пробный период!  ⏰ 3 дня до первой оплаты  Как тол |
| `referral.trial_activated_title` | ru+en | 🚀 Ваш реферал начал пробный период! |
| `referral.trial_activated_user` | +en | 👤 User: {user} |
| `referral.trial_period` | ru+en | ⏰ 3 дня до первой оплаты |
| `referral.your_link_hint` | ru+en | 🔗 <b>Твоя ссылка</b> <i>(нажми — скопируется)</i>  |

### `farm.*` — 31

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `farm.back` | ru+en | 🔙 Назад |
| `farm.back_to_games` | ru+en | 🔙 К играм |
| `farm.balance` | ru+en |  💰 Баланс: {balance:.2f} ₽ |
| `farm.button_buy_plot` | ru+en | ➕ Купить грядку — 50 ₽ |
| `farm.button_buy_plot_disabled` | ru+en | ➕ Грядка (нужно 50 ₽) |
| `farm.button_fertilize` | ru+en | 🌿 Удобрить #{num} |
| `farm.button_growing` | ru+en | ⏳ Растёт #{num} |
| `farm.button_harvest` | ru+en | 🌾 Собрать {emoji} #{num} (+{reward} ₽) |
| `farm.button_plant` | ru+en | 🌱 Посадить на грядку {num} |
| `farm.button_remove` | ru+en | ☠️ Убрать #{num} |
| `farm.button_water` | ru+en | 💧 Полить #{num} |
| `farm.buy_plot_error` | ru+en | Ошибка при списании средств |
| `farm.choose_plant_title` | ru+en | <tg-emoji emoji-id="5474417568053745249">🌱</tg-emoji> <b>Выберите раст |
| `farm.error_already_fertilized` | ru+en | Вы уже удобряли сегодня! |
| `farm.error_already_watered` | ru+en | Вы уже поливали сегодня! |
| `farm.error_harvest_failed` | ru+en | Ошибка при начислении награды |
| `farm.error_not_ready` | ru+en | Растение не готово к сбору |
| `farm.error_plot_unavailable` | ru+en | Грядка недоступна |
| `farm.error_unknown_plant` | ru+en | Неизвестный тип растения |
| `farm.harvest_success` | ru+en | 🌾 Урожай собран! +{reward} ₽ |
| `farm.insufficient_funds` | ru+en | Недостаточно средств |
| `farm.max_plots_reached` | ru+en | Максимальное количество грядок достигнуто |
| `farm.plant_info` | ru+en | {emoji} {name} — {days} дн. → +{reward} ₽ |
| `farm.plot_dead` | ru+en | Грядка {num}: ☠️ {name} — сгнило |
| `farm.plot_empty` | ru+en | Грядка {num}: ⬜ Пусто |
| `farm.plot_growing` | ru+en | Грядка {num}: <tg-emoji emoji-id="5474417568053745249">🌱</tg-emoji> {n |
| `farm.plot_ready` | ru+en | Грядка {num}: {emoji} {name} — ✅ Готово к сбору! |
| `farm.remove_confirm` | ru+en | Хотите убрать погибшее растение? |
| `farm.remove_no` | ru+en | ❌ Нет |
| `farm.remove_yes` | ru+en | ✅ Да, убрать |
| `farm.title` | ru+en | 🌾 <b>Ваша ферма</b> |

### `profile.*` — 22

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `profile.access_key_label` | ru+en | Ключ подключения: |
| `profile.auto_renew_disabled` | ru+en | 🔁 Автопродление: выключено |
| `profile.auto_renew_enabled` | ru+en | 🔁 Автопродление: {next_billing_date} |
| `profile.auto_renew_none` | ru+en | 🔁 Автопродление: — |
| `profile.auto_renew_off` | ru+en | 🔁 Автопродление: выкл |
| `profile.auto_renew_on` | ru+en | 🔁 Автопродление: {date} |
| `profile.balance` | ru+en | <tg-emoji emoji-id="5224257782013769471">💰</tg-emoji> Баланс: {amount} |
| `profile.buy_hint` | ru+en | Нажмите «Купить подписку» в меню, чтобы оформить подписку. |
| `profile.default_name` | ru+en | Пользователь |
| `profile.key_atlas` | ru+en | 🇩🇪 Скопировать Atlas DE |
| `profile.key_whitelist` | ru+en | ⚪️ Скопировать White List |
| `profile.renewal_hint` | ru+en | При продлении выбранный срок добавляется к текущему автоматически |
| `profile.subscription_active` | ru+en | 📆 Подписка: активна до {date} |
| `profile.subscription_inactive` | ru+en | 📆 Подписка: не активна |
| `profile.subscription_pending` | ru+en | 📆 Подписка оформлена, активация выполняется автоматически  Срок действ |
| `profile.tariff` | ru+en | <tg-emoji emoji-id="5463289097336405244">⭐️</tg-emoji> Тариф: {tariff} |
| `profile.tariff_none` | ru+en | <tg-emoji emoji-id="5463289097336405244">⭐️</tg-emoji> Тариф: — |
| `profile.topup_balance` | ru+en | ➕ Пополнить баланс |
| `profile.vpn_key_copied_toast` | ru+en | 🔑 VPN-ключ скопирован |
| `profile.welcome` | ru+en | Добро пожаловать в Atlas Secure! |
| `profile.welcome_full` | ru+en | Добро пожаловать в Atlas Secure!  👤 {username}  💰 Баланс: {balance:.2f |
| `profile.withdraw_funds` | ru+en | 💸 Вывести средства |

### `setup.*` — 17

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `setup.auto_install_header` | ru+en |  |
| `setup.autosetup_android` | ru+en | 🤖 <b>Авто настройка — Android</b>  ⚡️ Нажмите кнопку ниже с приложение |
| `setup.autosetup_ios` | ru+en | 📱 <b>Авто настройка — iOS</b>  ⚡️ Нажмите кнопку ниже с приложением, к |
| `setup.autosetup_macos` | ru+en | 🍎 <b>Авто настройка — macOS</b>  ⚡️ Нажмите кнопку ниже с приложением, |
| `setup.autosetup_windows` | ru+en | 🪟 <b>Авто настройка — Windows</b>  ⚡️ Нажмите кнопку ниже с приложение |
| `setup.copy_key_label` | ru+en | 👇 Скопируйте ключ одним нажатием: |
| `setup.device_button` | ru+en | 📲 Настроить устройство |
| `setup.help_button` | ru+en | ❓ Нужна помощь |
| `setup.install_happ_android` | ru+en | 📲 Скачать Happ |
| `setup.instruction_android` | ru+en | 🤖 <b>Настройка на Android</b>  Скачайте одно из приложений ниже и пере |
| `setup.instruction_ios` | ru+en | 📱 <b>Настройка на iOS</b>  Скачайте одно из приложений ниже и переходи |
| `setup.instruction_macos` | ru+en | 🍎 <b>Настройка на macOS</b>  Скачайте одно из приложений ниже и перехо |
| `setup.instruction_windows` | ru+en | 🪟 <b>Настройка на Windows</b>  Скачайте одно из приложений ниже и пере |
| `setup.key_bypass` | ru+en | 🔑 <b>Обход ключ</b> (белые списки РФ — для российских сайтов): |
| `setup.key_vpn` | ru+en | 🔑 <b>VPN ключ</b> (обычные сервера — безлимит): |
| `setup.manual_install_header` | ru+en |  |
| `setup.next_button` | ru+en | Далее ➡️ |

### `buy.*` — 16

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `buy.back_to_tariffs` | ru+en | ← Назад |
| `buy.corporate` | ru+en | 🧩 Корпоративный доступ Индивидуальная конфигурация под задачи компании |
| `buy.corporate_access_button` | ru+en | 🧩 Корпоративный доступ |
| `buy.corporate_back` | ru+en | ◀️ Назад |
| `buy.corporate_button` | ru+en | 🏢 Для бизнеса |
| `buy.corporate_confirm` | ru+en | ✅ Подтвердить |
| `buy.corporate_consent` | ru+en | Отправляя запрос, вы подтверждаете согласие на обработку вашего Telegr |
| `buy.corporate_request_accepted` | ru+en | Запрос принят.  Он передан на индивидуальное рассмотрение. С Вами свяж |
| `buy.enter_promo_button` | ru+en | 🎟 Ввести промокод |
| `buy.promo_applied_with_ttl` | ru+en | <tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji> Промокод применё |
| `buy.renewal_payment_label` | ru+en | Продление подписки |
| `buy.select_basic_button` | ru+en | ⚡️ Выбрать Basic |
| `buy.select_plus_button` | ru+en | 👑 Выбрать Plus |
| `buy.select_tariff` | ru+en | 📊 Тарифы  🪙 Тариф: Basic  🔹 Для повседневного использования 📲 Отлично  |
| `buy.select_tariff_type` | ru+en | Выберите тариф: |
| `buy.vpn` | ru+en | 🔐 Купить подписку |

### `subscription.*` — 15

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `subscription.auto_renew_disable` | ru+en | ⏸ Отключить автопродление |
| `subscription.auto_renew_enable` | ru+en | 🔄 Включить автопродление |
| `subscription.auto_renew_success` | ru+en | ✅ Подписка автоматически продлена на {days} дней.  Действует до: {expi |
| `subscription.expiring_reminder` | ru+en | ⏳ Срок доступа скоро истекает.  До окончания вашей подписки осталось 3 |
| `subscription.history` | ru+en | 📄 История подписок |
| `subscription.history_action_manual_reissue` | ru+en | Перевыпуск ключа |
| `subscription.history_action_purchase` | ru+en | Покупка |
| `subscription.history_action_reissue` | ru+en | Выдача нового ключа |
| `subscription.history_action_renewal` | ru+en | Продление |
| `subscription.history_empty` | ru+en | История подписок пуста |
| `subscription.history_expires` | ru+en | До: |
| `subscription.history_key_label` | ru+en | Ключ: |
| `subscription.tariff_basic` | ru+en | ⚡️ Тариф: Basic |
| `subscription.tariff_business` | ru+en | 🏢 Тариф: Business |
| `subscription.tariff_plus` | ru+en | 👑 Тариф: Plus |

### `errors.*` — 10

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `errors.check_logs` | ru+en | Ошибка. Проверь логи. |
| `errors.details` | ru+en | Ошибка при получении деталей |
| `errors.discount_too_low` | ru+en | Сумма после скидки ниже минимальной для оплаты картой (64 ₽). Пожалуйс |
| `errors.function_disabled` | ru+en | Эта функция недоступна. |
| `errors.invalid_tariff` | ru+en | Ошибка: неверный тариф |
| `errors.no_active_subscription` | ru+en | Активная подписка не найдена. |
| `errors.payment_already_processed` | ru+en | Платёж уже обработан. |
| `errors.payment_not_found` | ru+en | Платёж не найден. |
| `errors.pending_payment_exists` | ru+en | У вас уже есть ожидающий платёж. |
| `errors.vpn_key_creation` | ru+en | Ошибка создания VPN-ключа. Проверь логи. |

### `traffic.*` — 9

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `traffic.bypass_activated` | ru+en | ✅ <b>Обход блокировок активирован!</b>  📦 +{gb} ГБ трафика начислено   |
| `traffic.bypass_activated_trial` | ru+en | ✅ <b>Обход блокировок активирован!</b>  📦 +{gb} ГБ трафика начислено   |
| `traffic.gb_price_label` | ru+en | {gb} ГБ — {price} ₽ |
| `traffic.insufficient_balance` | ru+en | ❌ Недостаточно средств на балансе |
| `traffic.pay_balance` | ru+en | 💰 Оплатить с баланса ({price} ₽) |
| `traffic.pay_card` | ru+en | 💳 Оплатить картой ({price} ₽) |
| `traffic.pay_sbp` | ru+en | 🏦 Оплатить через СБП ({price} ₽) |
| `traffic.purchase_failed` | ru+en | ❌ Ошибка при добавлении трафика. Средства возвращены на баланс. |
| `traffic.trial_no_bypass` | ru+en | 📊 <b>Обход блокировок</b> 🇷🇺  🔒 Обход доступен на тарифах Basic и Plus |

### `instruction.*` — 8

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `instruction._device_android` | ru+en | 🤖 Android |
| `instruction._device_desktop` | ru+en | 💻 Windows / macOS |
| `instruction._device_ios` | ru+en | 📱 iOS |
| `instruction._download_android` | ru+en | 🤖 Android |
| `instruction._download_desktop` | ru+en | 💻 Windows |
| `instruction._download_ios` | ru+en | 📱 iOS |
| `instruction._download_macos` | ru+en | 🍎 MacOS |
| `instruction._download_tv` | ru+en | 📺 TV |

### `broadcast.*` — 7

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `broadcast._ab_stats_detail_error` | ru+en | Ошибка при получении статистики A/B теста. Проверь логи. |
| `broadcast._invalid_id` | ru+en | Ошибка: неверный ID уведомления. |
| `broadcast._no_sub_completed` | ru+en | ✅ Отправка завершена.  Получателей: {total} Доставлено: {sent} Не дост |
| `broadcast._not_found` | ru+en | Уведомление не найдено. |
| `broadcast._select_segment` | ru+en | Выберите сегмент получателей: |
| `broadcast._validation_ab_empty` | ru+en | Ошибка: не заполнены тексты вариантов A и B. Начните заново. |
| `broadcast._validation_incomplete` | ru+en | Ошибка: не все данные заполнены. Начните заново. |

### `connect.*` — 5

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `connect.autosetup_btn` | ru+en | 📲 Подключиться |
| `connect.autosetup_screen` | ru+en | 📱 <b>Авто настройка</b>  Нажмите кнопку с приложением, которое вы уста |
| `connect.instruction_screen` | ru+en | 📖 <b>Инструкция по подключению</b>  Нажмите «Подключиться» или «Открыт |
| `connect.open_miniapp_btn` | ru+en | 🌐 Открыть мини апп |
| `connect.setup_device_button` | ru+en | 📲 Настроить устройство |

### `trial.*` — 4

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `trial.activated_btn_profile` | +en | 👤 My Profile |
| `trial.button` | ru+en | 🎁 Пробный период 3 дня |
| `trial.notification_60h` | ru+en | 🛡 VPN скоро отключится  Осталось 12 часов пробного доступа.  Продолжит |
| `trial.notification_6h` | ru+en | ✨ Просто напоминание  VPN лучше включать всегда, чтобы защитить ваши д |

### `withdraw.*` — 4

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `withdraw.admin_btn_approve` | ru+en | ✅ Подтвердить |
| `withdraw.admin_btn_reject` | ru+en | ❌ Отклонить |
| `withdraw.admin_new_request` | ru+en | 💸 Новая заявка на вывод #{wid}  👤 Пользователь: @{username} (ID: {tele |
| `withdraw.amount_prompt` | ru+en | 💸 Вывод средств  Введите сумму (мин. 500 ₽): |

### `common.*` — 3

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `common.cancel` | ru+en | ❌ Отмена |
| `common.go_to_connection` | ru+en | 🔌 Перейти к подключению |
| `common.username_not_set` | ru+en | не указан |

### `get_key.*` — 3

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `get_key.download_happ` | ru+en | 📲 Скачать Happ |
| `get_key.download_v2raytun` | +en | 📲 Download V2RayTun |
| `get_key.instruction_text` | ru+en | 📖 <b>Инструкция по подключению</b>  Нажмите кнопку «📲 Настроить устрой |

### `lang.*` — 3

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `lang.change` | ru+en | 🌍 Изменить язык |
| `lang.changed` | ru+en | Язык изменён |
| `lang.select_title` | ru+en | 🌍 Выберите язык: |

### `gift.*` — 2

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `gift.btn_share_chat` | ru+en | 💬 Отправить в чат |
| `gift.my_gifts_btn` | ru+en | 🎁 Мои подарки |

### `premium.*` — 2

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `premium.invalid_username` | ru+en | ❌ Неверный username. Введите корректный username через @  Пример: <cod |
| `premium.main_button` | ru+en | ⚡️ Купить Telegram Premium ⚡️ |

### `reminder.*` — 2

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `reminder.paid_3d_new` | ru+en | <tg-emoji emoji-id="5456140674028019486">⚡</tg-emoji> Подписка истекае |
| `reminder.paid_3h` | ru+en | <tg-emoji emoji-id="5190806721286657692">🚨</tg-emoji> Подписка истекае |

### `biz.*` — 1

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `biz.profile_inactive` | ru+en | ━━━━━━━━━━━━━━━━━━━━━ 📋 Подписка: не активна 💎 Баланс: {balance} ₽ ━━━ |

### `combo.*` — 1

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `combo.purchase_success` | ru+en | ✅ <b>Комбо-подписка активирована!</b>  📦 Тариф: <b>Комбо {tariff}</b>  |

### `share_discount.*` — 1

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `share_discount.broadcast_btn` | ru+en | 🎁 Поделиться скидкой |

### `shop.*` — 1

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `shop.steam_main_button` | ru+en | 🎮 Пополнить Steam |

### `support.*` — 1

| Ключ | Где есть | Текст (RU, начало) |
|---|---|---|
| `support.write_button` | ru+en | 💬 Написать в поддержку |

## Возможно живые (динамические) — не удалять без ручной проверки

`buy.tariff_basic_description`, `buy.tariff_basic_selected`, `buy.tariff_biz_business_desc`, `buy.tariff_biz_enterprise_desc`, `buy.tariff_biz_pro_desc`, `buy.tariff_biz_starter_desc`, `buy.tariff_biz_team_desc`, `buy.tariff_biz_ultimate_desc`, `buy.tariff_business`, `buy.tariff_button_1`, `buy.tariff_button_12`, `buy.tariff_button_3`, `buy.tariff_button_6`, `buy.tariff_corporate`, `buy.tariff_label_basic`, `buy.tariff_label_plus`, `buy.tariff_plus_description`, `buy.tariff_plus_selected`, `buy.tariff_select_basic_button`, `buy.tariff_select_plus_button`, `help.faq_a1`, `help.faq_a2`, `help.faq_a3`, `help.faq_a4`, `help.faq_a5`, `help.faq_a6`, `help.faq_a7`, `help.faq_a8`, `help.faq_a9`, `help.faq_q1`, `help.faq_q2`, `help.faq_q3`, `help.faq_q4`, `help.faq_q5`, `help.faq_q6`, `help.faq_q7`, `help.faq_q8`, `help.faq_q9`, `payment.already_processed`, `payment.approved`, `payment.card_pl_unavailable`, `payment.card_pl_waiting`, `payment.crypto_success`, `payment.expired`, `payment.fallback_first`, `payment.fallback_renewal`, `payment.intl_pl_pay_button`, `payment.intl_pl_unavailable`, `payment.intl_pl_waiting`, `payment.paid_button`, `payment.pending`, `payment.rejected`, `payment.success_first`, `payment.success_renewal`, `payment.test`, `setup.combined_android`, `setup.combined_ios`, `setup.combined_macos`, `setup.combined_windows`, `setup.connect_android`, `setup.connect_ios`, `setup.connect_macos`, `setup.connect_windows`, `setup.download_v2rayn`, `setup.download_v2raytun`
