# ATLAS SECURE — Full Language Hard-Bind Refactor
## Executive Implementation Plan
## STAGE Environment | Crypto: DO NOT TOUCH

---

## 1. AUDIT SUMMARY

### 1.1 Keyboard Builders (✅ All accept `language`)

| Function | Has `language` param | Calls without `language` |
|----------|---------------------|--------------------------|
| get_language_keyboard | ✅ default="ru" | - |
| get_main_menu_keyboard | ✅ | - |
| get_back_keyboard | ✅ | - |
| get_profile_keyboard | ✅ | - |
| get_profile_keyboard_with_copy | ✅ | - |
| get_vpn_key_keyboard | ✅ | - |
| get_tariff_keyboard | ✅ | - |
| get_payment_method_keyboard | ✅ | - |
| get_sbp_payment_keyboard | ✅ | - |
| get_pending_payment_keyboard | ✅ | - |
| get_about_keyboard | ✅ | - |
| get_service_status_keyboard | ✅ | - |
| get_support_keyboard | ✅ | - |
| get_instruction_keyboard | ✅ | - |
| get_admin_dashboard_keyboard | ✅ | - |
| get_admin_back_keyboard | ✅ default="ru" | **~45 calls without `language`** |
| get_broadcast_*_keyboard | ✅ | - |
| get_ab_test_list_keyboard | ✅ | - |
| get_admin_export_keyboard | ✅ | **1 call without `language`** (L10112) |
| get_admin_user_keyboard | ✅ | - |
| get_admin_grant_days_keyboard | ✅ | - |
| get_admin_discount_*_keyboard | ✅ | - |

**Action:** All `get_admin_back_keyboard()` and `get_admin_export_keyboard()` calls must pass `language`. Each call site must have `language` in scope (fetched from DB at handler entry).

---

### 1.2 Prohibited Pattern: `localization.get_text("ru", ...)`

| File | Line | Context |
|------|------|---------|
| handlers.py | 121, 145, 163 | `user_fallback` — display of username/fallback (consider: use `language` when available) |
| handlers.py | 765 | DB init stage warning — system message, OK to keep "ru" for STAGE admin? |
| handlers.py | 1589, 2211 | user_fallback_text |
| handlers.py | 7965 | username_not_set fallback |
| **admin_notifications.py** | 43, 72 | `admin_degraded_mode`, `admin_recovered` — **admin messages should use admin's language from DB** |
| **auto_renewal.py** | 296 | `auto_renewal_success` — **user notification, MUST use user.language** |

**Action:** Admin notifications: fetch admin user, use admin's language. Auto_renewal: use `user.language`. User fallbacks: propagate language where caller has it.

---

### 1.3 Hardcoded Russian Strings (Partial List)

**handlers.py — message.answer / callback.message.answer:**

| Line (approx) | String |
|---------------|--------|
| 4753 | "Пожалуйста, введите промокод текстом." |
| 6576 | "❌ База данных недоступна" |
| 6582 | "❌ Не удалось подключиться к базе данных" |
| 7618 | "📜 Аудит\n\nАудит пуст. Действий не зафиксировано." |
| 7787, 8021 | "❌ Нет активных подписок для перевыпуска" |
| 7899, 8111 | "❌ Ошибка при массовом перевыпуске: ..." |
| 8158 | "👤 Пользователь\n\nВведите Telegram ID или username пользователя:" |
| 8189, 8199, 8207 | "Пользователь не найден.\nПроверьте Telegram ID или username." |
| 8297 | "Ошибка при получении информации о пользователе." |
| 8322 | "🧾 История подписок\n\nИстория подписок пуста." |
| 8507 | "❌ Ошибка выдачи доступа: ..." |
| 8711 | "❌ Введите положительное число" |
| 8732 | "❌ Введите число" |
| 8735 | "Ошибка" |
| 8822 | "❌ Ошибка: ..." |
| 8794, 8894, 8978, 9022, 9162 | "Действие выполнено без уведомления." |
| 9154, 9286 | "❌ У пользователя нет активной подписки" |
| 9294 | "✅ Доступ отозван" |
| 9409 | "❌ У пользователя уже есть персональная скидка..." |
| 9461, 9567 | "🎯 Назначить скидку\n\nВведите процент скидки..." |
| 9487, 9490, 9501 | Promo/discount validation messages |
| 9535, 9615 | "✅ Персональная скидка ... назначена" |
| 9539, 9618 | "❌ Ошибка при создании скидки" |
| 9649 | "✅ Персональная скидка удалена" |
| 9653 | "❌ Скидка не найдена или уже удалена" |
| 9671 | "❌ Пользователь не найден" |
| 9789 | "❌ Ошибка при назначении VIP-статуса" |
| 9824 | "❌ VIP-статус не найден или уже снят" |
| 10111 | "📤 Экспорт данных\n\nВыберите тип данных для экспорта:" |
| 10140 | "Неверный тип экспорта" |
| 10144 | "Нет данных для экспорта" |
| 10205 | "✅ Файл отправлен" |
| 10223 | "Ошибка при экспорте данных." |
| 10321 | "Отменено" |
| 10615-10626 | Broadcast validation errors |
| 10806, 10855, 10858 | Broadcast/AB stats errors |
| 10866, 10874, 10966 | Admin/audit errors |
| 10974 | "Нет доступа" |
| 10981, 10987 | /reissue_key usage |
| 11021 | "Ошибка при перевыпуске ключа." |
| 11162, 11178 | User search errors |
| 11196, 11203 | Credit balance errors |
| 11234, 11237 | Sum validation errors |
| 11319 | "❌ Операция отменена" |

**main.py:**
- L340-342: BotCommand descriptions — currently Russian. Consider: set per user on /start or leave as RU for now (Telegram command list is global).

**Action:** Add localization keys for all above. Replace hardcoded strings with `localization.get_text(language, "key", default="...")`. Ensure `language` is in scope.

---

### 1.4 Handler Entry Pattern

**Required at start of every handler:**
```python
user = await database.get_user(event.from_user.id)
language = user.get("language", "ru") if user else "ru"
```

**Handlers at risk (language used but may not be set for all paths):**
- Already fixed: callback_admin_broadcast, callback_broadcast_create, callback_admin_referral_detail, callback_broadcast_ab_stats
- Admin handlers that call `get_admin_back_keyboard()` without `language` — if `language` is set only in non-admin branch, admin path will use default. Need to ensure ALL admin handlers fetch `user`/`language` at entry.

---

### 1.5 Background Notifications

| Module | Issue | Fix |
|--------|-------|-----|
| admin_notifications.py | Uses get_text("ru", ...) | Fetch admin user, use admin.language |
| auto_renewal.py | Uses get_text("ru", "auto_renewal_success", ...) | Use user.language from DB |
| trial_notifications | Audit | Ensure user.language for all sends |
| reminders | Audit | Ensure user.language |
| activation_worker | Audit | Ensure user.language |
| referral notifications | Audit | Ensure user.language |
| broadcast | Already uses recipient language | OK |

---

### 1.6 Language Selection Screen

- Keyboard: Already has all 7 languages (ru, en, uz, tj, de, kk, ar).
- On selection: Must update `users.language` in DB and immediately show main menu in new language.
- /start: If no language → show selection. If language exists → use it.

---

## 2. PHASED IMPLEMENTATION

### Phase 1 — Critical Fixes (UnboundLocalError + Admin Keyboards)
1. Ensure all admin handlers that use `get_admin_back_keyboard()` fetch `user`/`language` at entry.
2. Pass `language` to every `get_admin_back_keyboard(language)` and `get_admin_export_keyboard(language)` call.
3. Fix callback_admin_export (L10110-10112): fetch language, add localization keys for export screen.

### Phase 2 — Localization Keys + Hardcoded Strings
1. Add ~60 new localization keys for admin/broadcast/export/discount/VIP/user-search messages.
2. Replace hardcoded strings with `localization.get_text(language, "key", default="...")` in handlers.py.
3. Ensure every replacement has `language` in scope.

### Phase 3 — Background Notifications
1. admin_notifications: Fetch admin, use admin.language.
2. auto_renewal: Use user.language for success message.
3. trial_notifications, reminders, activation_worker: Audit and fix.

### Phase 4 — Language Selection + /start Flow
1. Verify language selection updates DB and immediately reloads main menu.
2. Verify /start uses user.language when set.
3. Add any missing keys for language_select_title.

### Phase 5 — Verification
1. Run validate_localization.py.
2. Manual test: switch to each of 7 languages, navigate all screens.
3. Test: admin panel, broadcast, buy, profile, referral, notifications.

---

## 3. NEW LOCALIZATION KEYS TO ADD (RU + 6 langs)

```
admin_db_unavailable
admin_db_connection_failed
admin_audit_empty
admin_no_active_subscriptions_reissue
admin_reissue_bulk_error
admin_user_prompt_enter_id
admin_user_not_found_check_id
admin_user_info_error
admin_subscription_history_empty
admin_grant_access_error
admin_enter_positive_number
admin_enter_number
admin_action_without_notification
admin_no_active_subscription
admin_access_revoked
admin_discount_already_exists
admin_discount_assign_prompt
admin_discount_assign_days_prompt
admin_discount_percent_1_99
admin_discount_created
admin_discount_error
admin_discount_removed
admin_discount_not_found
admin_user_not_found
admin_vip_assign_error
admin_vip_not_found
admin_export_prompt
admin_export_invalid_type
admin_export_no_data
admin_export_file_sent
admin_export_error
admin_operation_cancelled
broadcast_validation_incomplete
broadcast_validation_ab_empty
broadcast_validation_message_empty
broadcast_not_found
broadcast_invalid_id
broadcast_ab_stats_error
admin_no_access
admin_reissue_usage
admin_reissue_invalid_id
admin_reissue_error
admin_credit_positive_sum
admin_credit_user_not_found
admin_credit_sum_format
admin_credit_sum_error
promo_enter_text
```

---

## 4. FILES TO MODIFY

| File | Changes |
|------|---------|
| handlers.py | ~80 edits: pass language to keyboards, replace hardcoded strings, ensure handler entry pattern |
| localization.py | Add ~50 keys × 7 languages |
| admin_notifications.py | 2 places: fetch admin, use admin.language |
| auto_renewal.py | 1 place: use user.language |
| trial_notifications.py | Audit |
| reminders.py | Audit |
| activation_worker.py | Audit |

---

## 5. VERIFICATION CHECKLIST

For each language (ru, en, uz, tj, de, kk, ar):

- [ ] /start
- [ ] Profile
- [ ] Buy
- [ ] Referral screen
- [ ] Admin panel
- [ ] Broadcast menu
- [ ] Export screen
- [ ] Admin user search
- [ ] Admin grant/revoke/discount/VIP
- [ ] Delete→New menu transitions
- [ ] Error responses
- [ ] Trial/reminder/renewal notifications (user language)

---

## 6. get_text() FALLBACK (Section 9)

Current behavior: If key missing in selected language → fallback to ru for that key only. Already implemented in `localization.get_text()`. No change needed.

---

## 7. ESTIMATED EFFORT

- Phase 1: ~30 min
- Phase 2: ~2–3 hours (many keys + edits)
- Phase 3: ~1 hour
- Phase 4: ~30 min
- Phase 5: Manual testing ~1 hour

**Total: ~5–6 hours**
