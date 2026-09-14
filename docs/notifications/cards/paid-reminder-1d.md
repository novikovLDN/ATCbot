# Платная: «Подписка заканчивается завтра»

[← к оглавлению](../README.md) · вердикт: ❌

| | |
|---|---|
| Ключ | `ReminderType.REMINDER_1D` · реестр `subscription.reminder_1d` · i18n `reminder.paid_1d` |
| Сегмент | платная активная, истекает |
| Когда | `expires_at − now ∈ [23 ч; 25 ч]` |
| Воркер | reminders, ~45–48 мин |
| Дедупликация | `reminder_1d_sent` — **не сбрасывается** |

## Назначение
Финальное «продлите сейчас» за сутки.

## Сегмент
Платные, не триал, не «админ-выдача» (у админ-выдачи 7 д в это же окно своё сообщение — [grant-reminder-7days-24h](grant-reminder-7days-24h.md)).

## Триггер и окно
`is_within_time_window(…, 24 h, 1 h)` — `app/services/notifications/service.py:244-251`. Окно 2 ч — **2 тика** reminders ([N-10](../bugs-and-risks.md#n-10)).

## Воркер и период
reminders (`reminders.py:323-384`).

## Источник данных
БД (`database/subscriptions.py:3365-3405`).

## Фильтр получателей
Как у [7 дней](paid-reminder-7d.md).

## Текст
- Выбор: `reminders.py:191-195`.
- RU (реестр `registry.py:191-203` = `app/i18n/ru.py:881`):
  ```
  <tg-emoji emoji-id="5190806721286657692">🔴</tg-emoji> Подписка заканчивается завтра. Продлите сейчас, чтобы VPN продолжил работать.
  ```
- EN (`app/i18n/en.py:1021`, недостижим — [N-03](../bugs-and-risks.md#n-03)):
  ```
  🔴 Subscription ends tomorrow. Renew now to keep VPN running.
  ```
- Кнопка (`reminders.py:73-80`): `reminder.paid_1d_btn` «🔁 Продлить» / «🔁 Renew» → `menu_buy_vpn`.

## Переопределение из дашборда
Да, только RU; окно `{24 h, ±2 h}` в `trigger_config` не используется ([N-14](../bugs-and-risks.md#n-14)).

## Дедупликация
- Флаг `reminder_1d_sent` (`migrations/036_notification_overhaul.sql:7`), ставится после отправки (`reminders.py:292` → `database/subscriptions.py:2739-2742`).
- **Сброс: нигде** ([N-01](../bugs-and-risks.md#n-01)).

## Риски задвоения
⚠️ [N-18](../bugs-and-risks.md#n-18). Пересечения с `REMINDER_24H` нет: он в платной ветке не выдаётся вообще ([dead-keys](other-dead-registry-keys.md)).

## Риски потери
- ❌ Со второго периода — никогда ([N-01](../bugs-and-risks.md#n-01)).
- ⚠️ Всего 2 тика в окне: два неудачных или отменённых подряд — и пропуск ([N-10](../bugs-and-risks.md#n-10)).
- ❌ [N-02](../bugs-and-risks.md#n-02), [N-04](../bugs-and-risks.md#n-04), [N-07](../bugs-and-risks.md#n-07).

## Вердикт
❌ **Баг** (то же, что у 7d).

## Рекомендации
[R-01](../recommendations.md#r-01), [R-12](../recommendations.md#r-12) (догоняние), [R-03](../recommendations.md#r-03), [R-08](../recommendations.md#r-08).
