# I18N Phase 3 — String Audit
# ATLAS SECURE | STAGE | Crypto: DO NOT TOUCH

## Summary

| File | Cyrillic Lines | localization.get_text | Hardcoded answer()/edit_text |
|------|----------------|----------------------|------------------------------|
| handlers.py | 2063 | 586 | 100+ |
| localization.py | 2042 | (source) | — |
| auto_renewal.py | 86 | 3 | — |
| admin_notifications.py | 23 | 6 | — |
| trial_notifications.py | 49 | 4 | — |
| activation_worker.py | 23 | 8 | — |
| reminders.py | 14 | 8 | — |
| app/handlers/notifications.py | 25 | 3 | — |
| database.py | 1194 | — | (internal) |

## 1. Handlers — Hardcoded answer() / edit_text

### Corporate / Buy Flow
- `"Запрос принят"` (corporate_access_confirm)
- `"Оплата криптовалютой временно недоступна"` (4×)
- `"Пожалуйста, введите промокод текстом."`
- `"У вас уже есть ожидающий платеж"`
- `"Не удалось создать платеж. Попробуйте позже."`

### Admin Panel
- `"Нет доступа"`
- `"Платеж не найден"` (2×)
- `"Платеж уже обработан"` (2×)
- `"Ошибка. Проверь логи."` (20+)
- `"❌ База данных недоступна"`
- `"❌ Не удалось подключить к базе данных"`
- `"Реферер не найден"`
- `"Ошибка при расчете аналитики"`
- `"Ошибка при получении ежемесячной сводки"`
- `"Ошибка при получении audit log"`
- `"Начинаю массовый перевыпуск..."` (2×)
- `"Ошибка: неверный формат команды"` (6×)
- `"Подписка не найдена или не активна"`
- `"У подписки нет UUID для перевыпуска"`
- `"Перевыпускаю ключ..."`
- `"Ключ успешно перевыпущен"` (2×)
- `"Ошибка при перевыпуске ключа"` (2×)
- `"Ошибка при получении статистики ключей"`
- `"Пользователь не найден.\nПроверьте Telegram ID или username."` (4×)
- `"Ошибка при получении информации о пользователе. Проверь логи."`
- `"Ошибка при получении истории подписок"`
- `"Ошибка создания ключа"`
- `"❌ Введите положительное число"`
- `"❌ Введите число"`
- `"Ошибка"`
- `"Ошибка: данные не найдены"` (4×)
- `"Ошибка выдачи доступа"` (2×)
- `"Ошибка: неизвестный тип действия"`
- `"Ошибка: неверный ID пользователя"` (2×)
- `"Ошибка: user_id не найден"`
- `"Нет активной подписки"` (2×)
- `"Процент скидки должен быть от 1 до 99. Попробуйте снова:"`
- `"Введите число от 1 до 99:"`
- `"Количество дней должно быть неотрицательным. Попробуйте снова:"`
- `"Введите число (количество дней или 0 для бессрочной):"`
- `"Скидка назначена"`
- `"Скидка удалена"`
- `"Скидка не найдена"`
- `"Скидка уже существует"`
- `"❌ Пользователь не найден"`
- `"VIP уже назначен"`
- `"✅ VIP-статус выдан"`
- `"✅ VIP-статус снят"`
- `"VIP не найден"`
- `"Не удалось перевыпустить ключ. Нет активной подписки или ошибка создания ключа."`
- `"Ошибка при получении системной информации"`
- `"Неверный тип экспорта"`
- `"Нет данных для экспорта"`
- `"✅ Файл отправлен"`
- `"Ошибка при экспорте данных. Проверь логи."`
- `"Отменено"`
- `"Ошибка: не все данные заполнены. Начните заново."`
- `"Ошибка: не заполнены тексты вариантов A и B. Начните заново."`
- `"Ошибка: не заполнен текст уведомления. Начните заново."`
- `"Уведомление не найдено."`
- `"Ошибка: неверный ID уведомления."`
- `"Ошибка при получении статистики A/B теста. Проверь логи."`
- `"Недостаточно прав"`
- `"Аудит пуст. Действий не зафиксировано."`
- `"Использование: /reissue_key <telegram_id>"`
- `"Неверный формат telegram_id. Используйте число."`
- `"❌ Сумма должна быть положительным числом.\n\nВведите сумму в рублях:"`
- `"Ошибка: пользователь не найден. Начните заново."`
- `"❌ Неверный формат суммы.\n\nВведите число (например: 500 или 100.50):"`
- `"Ошибка при обработке суммы. Проверьте логи."`
- `"✅ Средства начислены"`
- `"❌ Ошибка при начислении средств"`
- `"Эта функция не работает"`

## 2. localization.py — Current Key Count

- **ru**: 446 keys
- **en, uz, tj, ar, kk, de**: 446 keys each (validate_localization.py)

## 3. app/i18n — Current Key Count

- **ru, en, uz, tj, de, kk, ar**: ~20 keys each

## 4. Proposed Key Namespace

```
common.back
common.cancel
main.profile
main.buy
main.referral
main.about
main.instruction
lang.select_title
lang.change
errors.profile_load
errors.db_unavailable
errors.db_connection_failed
errors.check_logs
errors.user_not_found
errors.payment_not_found
errors.payment_already_processed
errors.invalid_format
errors.no_access
admin.user_not_found
admin.no_access
admin.reissue_starting
admin.reissue_key_success
admin.reissue_key_error
admin.subscription_not_found
admin.vip_granted
admin.vip_revoked
admin.discount_assigned
admin.discount_removed
admin.export_file_sent
admin.audit_empty
broadcast.validation_incomplete
payment.crypto_unavailable
payment.pending_exists
payment.create_failed
corporate.request_accepted
promo.enter_text
```

## 5. Migration Order

1. **Phase 3.1** — Main menu + profile (get_main_menu_keyboard, show_profile, cmd_start, callback_main_menu)
2. **Phase 3.2** — Buy flow (tariff selection, invoice, payment screens)
3. **Phase 3.3** — Referral flow (referral screens, share link, stats)
4. **Phase 3.4** — Admin panel (all admin handlers, export, broadcast, discounts, VIP)
5. **Phase 3.5** — Background notifications (admin_notifications, trial_notifications, auto_renewal, activation_worker, reminders)

## 7. BUY FLOW — Phase 3.2

### Handlers
- `_open_buy_screen` — uses i18n: buy.tariff_*, buy.select_*, buy.corporate_*, buy.enter_promo
- `callback_tariff_type` — buy.tariff_basic_desc, buy.tariff_plus_desc, errors.tariff
- `callback_tariff_period` — period button text (hardcoded: "1 месяц", "N месяца", "N месяцев"), localization.get_text(back, error_tariff)
- `show_payment_method_selection` — payment.select_method, payment.balance, payment.card, payment.crypto
- `callback_pay_balance` — localization.get_text: error_payment_processing, action_purchase, action_renewal, payment_pending_activation, profile, support, error_subscription_activation
- `callback_pay_balance` — hardcoded: success_text (renewal/first), fallback_text, transaction_description
- `get_vpn_key_keyboard` — localization.get_text: go_to_connection, copy_key, profile
- `callback_enter_promo` / promo apply — localization.get_text: enter_promo_button
- `callback_corporate_access_confirm` — localization.get_text: username_not_set
- Admin notification (corporate) — hardcoded: f"📩 Новый запрос на корпоративный доступ..."

### Strings migrated (Phase 3.2 ✅)
- Period: buy.period_1, buy.period_2_4, buy.period_5_plus
- Button: buy.button_price, buy.button_price_discount
- common.back, common.go_to_connection, profile.copy_key, main.profile
- payment.success_first, payment.success_renewal, payment.pending_activation
- payment.fallback_first, payment.fallback_renewal
- common.username_not_set, referral.action_purchase, referral.action_renewal
- errors.payment_create, errors.payments_unavailable, errors.invalid_amount

## 9. REFERRAL FLOW — Phase 3.3 ✅ MIGRATED

### Handlers (i18n_get_text)
- `_open_referral_screen` — referral.screen_title, referral.total_invited, referral.active_with_subscription, referral.current_status, referral.cashback_level, referral.rewards_earned, referral.last_activity, referral.next_level_line, referral.max_level_reached, referral.share_button, referral.stats_button, common.back
- `callback_copy_referral_link` — referral.link_copied, errors.profile_load
- `callback_referral_stats` — referral.status_footer, referral.max_level_reached, referral.stats_screen, common.back
- `callback_referral_how_it_works` — referral.how_it_works_text, common.back

### Notifications (i18n_get_text)
- Referral registration — referral.registered_title, referral.registered_user, referral.registered_date, referral.first_payment_notification
- Trial activation — referral.trial_activated_title, referral.trial_activated_user, referral.trial_period, referral.first_payment_notification
- `send_referral_cashback_notification` — action_purchase, action_renewal, action_topup
- `format_referral_notification_text` — friend_singular, friend_dual, friend_plural, referral.cashback_*

### Keys migrated ✅
referral.screen_title, referral.total_invited, referral.active_with_subscription, referral.current_status, referral.cashback_level, referral.rewards_earned, referral.last_activity, referral.next_level_line, referral.max_level_reached, referral.share_button, referral.stats_button, referral.link_copied, referral.stats_screen, referral.status_footer, referral.how_it_works_text, referral.registered_title, referral.registered_user, referral.registered_date, referral.first_payment_notification, referral.trial_activated_title, referral.trial_activated_user, referral.trial_period, common.user, errors.profile_load

## 10. Critical Notes

- **localization.py** remains the primary source until migration complete
- **app/i18n** keys use dot notation (e.g. `admin.user_not_found`)
- Each hardcoded string needs key in all 7 languages
- `get_text()` from app.i18n must receive `language` from `resolve_user_language()`
- DO NOT remove localization.py until ALL call sites migrated
