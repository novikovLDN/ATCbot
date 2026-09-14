# Платная: «Ваша подписка активна ещё 3 дня» (с фото)

[← к оглавлению](../README.md) · вердикт: ⚠️

| | |
|---|---|
| Ключ | `ReminderType.REMINDER_3D` · реестр `subscription.reminder_3d` · i18n `reminder.paid_3d` |
| Сегмент | платная активная, истекает |
| Когда | `expires_at − now ∈ [72 ч − 2,4 ч; 72 ч + 2,4 ч]` = 69,6…74,4 ч |
| Воркер | reminders, ~45–48 мин |
| Дедупликация | `reminder_3d_sent`, сбрасывается при каждой выдаче или продлении |

## Назначение
Классическая точка «продлите заранее». На проде — с картинкой-шапкой.

## Сегмент
Как у [7 дней](paid-reminder-7d.md): платные, не триал, не «админ-выдача».

## Триггер и окно
`is_within_time_window(…, 3 d, 2.4 h)` — `app/services/notifications/service.py:234-241`. Скрипт: 69,75…74,25 ч (сетка 15 мин).

## Воркер и период
reminders (`reminders.py:323-384`): 45 мин, таймаут 120 с. Окно 4,8 ч — 6 тиков.

## Источник данных
БД, `database/subscriptions.py:3365-3405`.

## Фильтр получателей
Как у 7d: `is_reachable` ([N-02](../bugs-and-risks.md#n-02)), без `status` и `auto_renew` ([N-13](../bugs-and-risks.md#n-13)), `segment_filter` — опционально.

## Текст
- Выбор: `reminders.py:185-189`.
- RU (реестр `registry.py:174-189` = `app/i18n/ru.py:971`):
  ```
  <tg-emoji emoji-id="5454415424319931791">📅</tg-emoji> Ваша подписка Atlas Secure активна ещё 3 дня

  Продлите заранее — и доступ не прервётся ни на секунду 🤍
  ```
- EN (`app/i18n/en.py:649`, недостижим — [N-03](../bugs-and-risks.md#n-03)):
  ```
  📅 Your Atlas Secure subscription is active for 3 more days

  Renew in advance so your access never stops for a second 🤍
  ```
- Кнопка (`reminders.py:63-70`): `reminder.paid_3d_btn` «🔁 Продлить» / «🔁 Renew» → `menu_buy_vpn`.
- Фото: `file_id` для prod в `reminders.py:27-30`. `bot.send_photo` с caption (`:256-268`), при любой ошибке — фолбэк на текст через `safe_send_message` (`:269-278`).
- Не используется: `reminder.paid_3d_new` (`ru.py:879`).

## Переопределение из дашборда
Да (RU-текст, вкл/выкл, `segment_filter`), только RU. Окно из `trigger_config` (`{72 h, ±6 h}`) не используется ([N-14](../bugs-and-risks.md#n-14)).

## Дедупликация
- Флаг `reminder_3d_sent` после отправки (`reminders.py:292` → `database/subscriptions.py:2735-2738`) + `last_reminder_at`.
- **Сброс:** во всех ветках `grant_access`: `database/subscriptions.py:1530, 1609, 1719, 1942, 2261` ✅.

## Риски задвоения
- ⚠️ Падение между отправкой и флагом ([N-18](../bugs-and-risks.md#n-18)).
- ⚠️ Фото-путь идёт голым `bot.send_photo` (`reminders.py:262`). На Forbidden фолбэк повторит попытку текстом — вторая ошибка, пользователь помечается недоступным. Лишний запрос, но не дубль.

## Риски потери
- ⚠️ `is_reachable` ([N-02](../bugs-and-risks.md#n-02)), `admin_grant_days` ([N-04](../bugs-and-risks.md#n-04)), `source='trial'` после подарка ([N-07](../bugs-and-risks.md#n-07)).
- ✅ Временная ошибка — ретрай в окне (6 тиков).

## Вердикт
⚠️ **Риск.** Сама логика и сброс корректны. Теряется у «липких» сегментов и приходит на RU для EN.

## Рекомендации
[R-02](../recommendations.md#r-02), [R-03](../recommendations.md#r-03), [R-04](../recommendations.md#r-04), [R-07](../recommendations.md#r-07), [R-21](../recommendations.md#r-21).
