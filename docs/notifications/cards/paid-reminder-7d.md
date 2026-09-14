# Платная: «Подписка заканчивается через 7 дней»

[← к оглавлению](../README.md) · вердикт: ❌

| | |
|---|---|
| Ключ | `ReminderType.REMINDER_7D` · реестр `subscription.reminder_7d` · i18n `reminder.paid_7d` |
| Сегмент | платная активная, истекает |
| Когда | `expires_at − now ∈ [7 д − 3 ч; 7 д + 3 ч]` = 165…171 ч |
| Воркер | reminders, ~45–48 мин |
| Дедупликация | `subscriptions.reminder_7d_sent` — **не сбрасывается** |

## Назначение
Раннее напоминание: продлить заранее, чтобы доступ не прервался.

## Сегмент
Платные подписки (`source` ≠ `trial`, `subscription_type` ≠ `trial`), которые не считаются админ-выдачей.

## Триггер и окно
`is_within_time_window(time_until_expiry, 7 d, 3 h)` — `app/services/notifications/service.py:224-231`. Проверяется первым в цепочке `elif` (`:224-261`). Проверено скриптом: срабатывает на 165,00…171,00 ч ([доказательства](../bugs-and-risks.md#evidence)).

## Воркер и период
`reminders.reminders_task`: первый прогон через 60 с после старта (`reminders.py:326`), затем каждые 45 мин после окончания итерации (`:384`), таймаут итерации 120 с (`:348`). Окно 6 ч — 7–8 тиков.

## Источник данных
Локальная БД: `database.get_subscriptions_for_reminders()` (`database/subscriptions.py:3365-3405`) — все строки `subscriptions` с `expires_at > now` + `last_action_type` из `subscription_history`. Панель Remnawave не опрашивается.

## Фильтр получателей
- `COALESCE(u.is_reachable, TRUE) = TRUE` (`database/subscriptions.py:3390`). Флаг никогда не восстанавливается — [N-02](../bugs-and-risks.md#n-02).
- **Нет** фильтра по `status`, `activation_status`, `auto_renew` ([N-13](../bugs-and-risks.md#n-13)).
- Исключены триалы (`service.py:173-180`) и «админ-выдачи» (`:183-185`) — последнее ошибочно захватывает оплативших ([N-04](../bugs-and-risks.md#n-04)).
- Опциональный `segment_filter` из `trigger_config` (`reminders.py:229-249`). Сегмент грузится целиком на каждого пользователя (`app/services/automated_notifications/helper.py:217-241`).

## Текст
- Выбор текста: `(await get_notification_text("subscription.reminder_7d")) or i18n.get_text(language, "reminder.paid_7d")` — `reminders.py:179-183`.
- RU (реестр `registry.py:156-172` = `app/i18n/ru.py:877`):
  ```
  <tg-emoji emoji-id="5454415424319931791">📅</tg-emoji> Подписка заканчивается через 7 дней. Продлите заранее — доступ не прервётся.
  ```
- EN (`app/i18n/en.py:1017`, **недостижим**, см. [N-03](../bugs-and-risks.md#n-03)):
  ```
  📅 Subscription ends in 7 days. Renew in advance — access won't be interrupted.
  ```
- Кнопки (`reminders.py:49-60`):
  - `reminder.paid_7d_btn` «🔁 Продлить подписку» / «🔁 Renew subscription» → `menu_buy_vpn`;
  - `main.profile` «👤 Личный кабинет» / «👤 Dashboard» → `menu_profile`.

## Переопределение из дашборда
Да: текст (`custom_text_ru`), вкл/выкл, `segment_filter`. **Только RU**, и этот текст получают все языки. Окно в `trigger_config` (`{before_expiry_hours: 168, tolerance_hours: 12}`) показывается, но **не используется** ([N-14](../bugs-and-risks.md#n-14)). Если выключено — флаг ставится и пишется `skipped_disabled` (`reminders.py:211-223`).

## Дедупликация
- Флаг `reminder_7d_sent` (`migrations/036_notification_overhaul.sql:6`). Ставится **после** отправки: `reminders.py:292` → `database/subscriptions.py:2731-2734`, `WHERE telegram_id = $1`, заодно пишется `last_reminder_at`.
- Защита от рестарта: пропуск, если `last_reminder_at` младше 30 мин (`reminders.py:33, 139-150`).
- **Сброс: нигде.** 0 вхождений `reminder_7d_sent = FALSE` ([N-01](../bugs-and-risks.md#n-01)).

## Риски задвоения
- ⚠️ Падение или отмена между `safe_send_message` (`reminders.py:278`) и `mark_reminder_sent` (`:292`) → повтор на следующем тике ([N-18](../bugs-and-risks.md#n-18)). Защита `last_reminder_at` не срабатывает: он пишется тем же UPDATE.
- ✅ Гонки воркеров нет: 7d шлёт только reminders; единственный инстанс обеспечивает advisory lock (`main.py:218-235`).

## Риски потери
- ❌ **Со второго платного периода не приходит никогда** ([N-01](../bugs-and-risks.md#n-01)).
- ❌ Пользователь с «липким» `admin_grant_days` ([N-04](../bugs-and-risks.md#n-04)) или после подарочного ключа из рассылки ([N-07](../bugs-and-risks.md#n-07)).
- ❌ `is_reachable = FALSE` навсегда ([N-02](../bugs-and-risks.md#n-02)).
- ✅ Временная ошибка: флаг не ставится, повтор через 45 мин, шансов 7–8. Но в лог попадает как `blocked` ([N-15](../bugs-and-risks.md#n-15)).

## Вердикт
❌ **Баг.** Корректно работает только в первом платном периоде.

## Рекомендации
- [R-01](../recommendations.md#r-01): сброс флага в `database/subscriptions.py:1530, 1609, 1718, 1941, 2260`.
- [R-03](../recommendations.md#r-03): EN из i18n (`reminders.py:181`).
- [R-21](../recommendations.md#r-21): для `auto_renew` с балансом — «спишем N ₽», а не «продлите».
- [R-08](../recommendations.md#r-08): `notification_log` с `period_key = expires_at`.
