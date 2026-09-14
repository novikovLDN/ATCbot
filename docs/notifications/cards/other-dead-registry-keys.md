# Мёртвые ключи реестра и i18n (никогда не отправляются)

[← к оглавлению](../README.md) · вердикт: ⚠️

## Назначение карточки
Перечислить то, что выглядит как уведомление (есть в дашборде или в i18n), но не отправляется. Такие ключи вводят админа в заблуждение ([N-14](../bugs-and-risks.md#n-14)).

Метод: поиск строковых литералов `"key"` и `'key'` по `app/`, `database/` и корневым `*.py`, исключая `app/i18n/` и `registry.py`. Динамически собранные ключи (f-строки) так не найти, поэтому для 0-хитов нужна ручная проверка перед удалением.

## Ключи реестра `automated_notifications`, видимые в дашборде

| Ключ | Где объявлен | Почему не отправляется |
|---|---|---|
| `subscription.reminder_24h` | `app/services/automated_notifications/registry.py:205-222` | Ветка `reminders.py:197-201` недостижима: `should_send_reminder` никогда не возвращает `REMINDER_24H` (платная ветка даёт только 7D, 3D, 1D, 3H — `app/services/notifications/service.py:221-261`). Вместо него за сутки уходит `subscription.reminder_1d`. |
| `trial.reminder_6h` | `registry.py:94-112` | 0 использований; триальное расписание пустое (`app/services/trials/service.py:443`). Описание в реестре («работает параллельно с 3h») устарело. |
| `referral.reward_notification` | `registry.py:302-318` | 0 использований. Кэшбэк рефереру шлётся из `app/handlers/notifications.py:26` с текстами `loyalty_pushes.py` и i18n. |

Для сравнения: `payment.success_welcome_basic/plus` и `payment.success_renewal_compact` используются (`app/handlers/callbacks/payments_callbacks.py:794-808`), `gift.activated_welcome` тоже (`app/handlers/user/start.py:257`).

## Ключи i18n без использования (0 хитов)

| Ключ | RU |
|---|---|
| `main.trial_notification_71h` | `app/i18n/ru.py:622` |
| `trial.notification_6h`, `trial.notification_60h` | `ru.py:789-790` |
| `main.reminder_admin_1day_6h`, `main.reminder_admin_7days_24h` | `ru.py:577-578` |
| `main.reminder_paid_24h`, `main.reminder_paid_3d`, `main.reminder_paid_3h` | `ru.py:579-581` |
| `reminder.paid_3h` | `ru.py:973` |
| `reminder.paid_3d_new` | `ru.py:879` |
| `main.auto_renewal_success` | `ru.py:475` |
| `reminder.paid_24h` (1 хит — только в недостижимой ветке `reminders.py:199`) | `ru.py:972` |

Кроме того, из-за [N-03](../bugs-and-risks.md#n-03) **i18n-версии** `reminder.paid_7d/3d/1d/3h_special`, `trial.reminder_24h/3h`, `trial.notification_71h` фактически не используются: всегда побеждает текст из реестра.

## Колонки-флаги без записи
`trial_notif_6h_sent, trial_notif_18h_sent, trial_notif_30h_sent, trial_notif_42h_sent, trial_notif_54h_sent, trial_notif_60h_sent` (`database/core.py:619-624`). Расписание пустое, поэтому они никогда не выставляются. Сбрасываются в `grant_access` (`database/subscriptions.py:1949-1954, 2268-2273`) — безвредно.

## Вердикт
⚠️ **Не баг доставки, но путает**: админ может «править» текст, который никто не получит.

## Рекомендации
[R-18](../recommendations.md#r-18): удалить мёртвые ключи из реестра (или пометить «не используется» в `description`), вычистить i18n после ручной проверки, заодно убрать недостижимую ветку `REMINDER_24H` в `reminders.py:197-201`.
