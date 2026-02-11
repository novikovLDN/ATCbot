# I18N Phase 3 — String Audit Checklist

## Objective
Remove ALL user-facing hardcoded strings. All text must exist only in `app/i18n/{lang}.py`.

---

## Files with User-Facing Cyrillic/Russian

| File | Est. Occurrences | Priority |
|------|------------------|----------|
| handlers.py | ~500+ | HIGH |
| admin_notifications.py | ~20 | MEDIUM |
| trial_notifications.py | ~15 | MEDIUM |
| activation_worker.py | ~10 | MEDIUM |
| auto_renewal.py | ~15 | MEDIUM |
| reminders.py | ~20 | MEDIUM |
| app/handlers/notifications.py | ~10 | MEDIUM |
| app/services/notifications/service.py | ~5 | LOW |

---

## handlers.py — Categories

### 1. MAIN MENU + PROFILE (Phase 3.1) ✓ DONE
- [x] Line 1209: `text="💬 Написать в поддержку"` → support.write_button
- [x] Line 1633: `text = "🌍 Выберите язык:"` → lang.select_title
- [x] Line 1816: `"Ошибка загрузки профиля. Попробуйте позже."` → errors.profile_load
- [ ] Profile welcome/balance/auto_renew strings (lines 1840–1919) — still use localization
- [x] Keyboard defaults in get_main_menu_keyboard, get_profile_keyboard, get_back_keyboard

### 2. BUY FLOW (Phase 3.2)
- [ ] Lines 3297, 4627–4660: callback.answer hardcoded strings
- [ ] Line 4708: `"Пожалуйста, введите промокод текстом."` → buy.promo_enter_text
- [ ] Invoice, tariff, payment success screens
- [ ] Lines 5805, 5816: payment error strings

### 3. REFERRAL FLOW (Phase 3.3)
- [ ] Referral screen titles, stats, level labels
- [ ] share_referral_link, referral_stats callbacks

### 4. ADMIN PANEL (Phase 3.4)
- [ ] Line 5733: `"Эта функция не работает"`
- [ ] Line 6257: `"Нет доступа"`
- [ ] Lines 6270, 6277: payment not found/processed
- [ ] Lines 6477, 6483: DB unavailable
- [ ] Line 7646, 7889: `"Начинаю массовый перевыпуск..."`
- [ ] Lines 7802–8030: admin reissue, keys, stats errors
- [ ] Lines 8072–9275: admin user, grant, revoke, discount, VIP strings
- [ ] format_promo_stats_text (lines 1640–1662) — admin-only

### 5. BROADCAST + EXPORT
- [ ] Broadcast type labels, segment labels
- [ ] Export prompts, success/error messages

---

## Hardcoded Answer/Message Samples (handlers.py)

```
"Запрос принят"
"Оплата криптовалютой временно недоступна"
"Пожалуйста, введите промокод текстом."
"Эта функция не работает"
"У вас уже есть ожидающий платеж"
"Не удалось создать платеж. Попробуйте позже."
"Нет доступа"
"Платеж не найден"
"Платеж уже обработан"
"Ошибка. Проверь логи."
"❌ База данных недоступна"
"❌ Не удалось подключиться к базе данных"
"Реферер не найден"
"Ошибка при расчете аналитики"
"Ошибка при получении ежемесячной сводки"
"Ошибка при получении audit log"
"Начинаю массовый перевыпуск..."
"Ошибка: неверный формат команды"
"Подписка не найдена или не активна"
"У подписки нет UUID для перевыпуска"
"Перевыпускаю ключ..."
"Ключ успешно перевыпущен"
"Ошибка при перевыпуске ключа"
"Пользователь не найден.\nПроверьте Telegram ID или username."
"❌ Введите положительное число"
"❌ Введите число"
"Ошибка"
"Ошибка: данные не найдены"
"Ошибка формата команды"
"Ошибка выдачи доступа"
"Ошибка: неизвестный тип действия"
"Ошибка: неверный ID пользователя"
"Ошибка: user_id не найден"
"Нет активной подписки"
"Скидка уже существует"
... and more
```

---

## Key Namespace Convention (Target)

```
common.back
common.cancel
main.profile
main.buy
main.referral
main.instruction
support.write_button
lang.select_title
errors.profile_load
errors.db_unavailable
errors.generic
admin.user_not_found
admin.access_denied
admin.reissue_start
admin.reissue_success
admin.reissue_error
payment.crypto_unavailable
payment.promo_enter_text
payment.pending_exists
payment.create_failed
subscription.not_found
subscription.no_uuid
discount.already_exists
...
```

---

## Migration Strategy

1. **Phase 3.1** — Main menu + profile: add keys to app/i18n, replace hardcoded strings
2. **Phase 3.2** — Buy flow
3. **Phase 3.3** — Referral flow
4. **Phase 3.4** — Admin panel
5. **Phase 3.5** — Background notifications (admin_notifications, trial_notifications, activation_worker, auto_renewal, reminders)
6. **Step 5** — Remove localization.py, switch all imports to app.i18n
7. **Step 4** — Enable strict=True in STAGE for get_text()

---

## Notes

- **localization.py** currently has ~446 keys per language. Full migration requires either:
  - Copying keys to app/i18n with same or new names, then switching imports
  - Or gradual replacement: add new keys to app/i18n and replace call-by-call
- **Crypto**: Do NOT touch crypto/payment logic, only presentation strings
- **Docstrings/comments**: Russian docstrings are OK (not user-facing). Only UI strings must be extracted.

---

*Generated for I18N Phase 3. Update as migration progresses.*
