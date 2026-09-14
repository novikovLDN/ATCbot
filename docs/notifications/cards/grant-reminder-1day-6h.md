# Админ-выдача 1 день: «Бесплатный доступ завершается через 6 часов»

[← к оглавлению](../README.md) · вердикт: ⚠️

| | |
|---|---|
| Ключ | `ReminderType.ADMIN_1DAY_6H` · i18n `reminder.admin_1day_6h` (без реестра) |
| Сегмент | админ-выдача, промо или бонус с `admin_grant_days = 1` |
| Когда | `expires_at − now ∈ [5,5 ч; 6,5 ч]` |
| Воркер | reminders, ~45–48 мин |
| Дедупликация | `reminder_6h_sent`, сбрасывается при продлении |

## Назначение
Конвертировать «подарочный» день в покупку.

## Сегмент
`admin_grant_days IS NOT NULL` или последнее действие в истории — `admin_grant` (`app/services/notifications/service.py:183-185`), и `admin_grant_days == 1` (`:189`). Откуда берётся значение: админ-выдача (`database/admin.py:2566`), бонус (`app/handlers/admin/bonus.py:482`), промо-ссылка (`app/handlers/user/start.py:989`). Пишется только на новой выдаче (`database/subscriptions.py:1946, 2265`).

## Триггер и окно
`is_within_time_window(…, 6 h, 0.5 h)` — `service.py:191-203`. Окно 1 ч — **1 тик** ([N-10](../bugs-and-risks.md#n-10)).

## Воркер и период
reminders.

## Источник данных
БД.

## Фильтр получателей
`is_reachable` ([N-02](../bugs-and-risks.md#n-02)); не триал.

## Текст
- `reminders.py:169-172`, **без** реестра: EN работает ✅.
- RU (`app/i18n/ru.py:969`):
  ```
  <tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji> Ваш бесплатный доступ к Atlas Secure завершается через 6 часов

  Понравилось? Оформите подписку и пользуйтесь без ограничений 💙
  ```
- EN (`app/i18n/en.py:647`): «🎁 Your free access to Atlas Secure ends in 6 hours …»
- Кнопка: `main.buy` «🔐 Купить подписку» / «🔐 Buy Subscription» → `menu_buy_vpn` (`reminders.py:104-106`).

## Переопределение из дашборда
Нет: ключа в реестре нет, в `automated_notification_sends` не логируется ([N-15](../bugs-and-risks.md#n-15)).

## Дедупликация
Флаг `reminder_6h_sent` (маппинг `service.py:288`), ставится после отправки (`database/subscriptions.py:2751-2754`), сбрасывается во всех ветках `grant_access` ✅.

## Риски задвоения
⚠️ [N-18](../bugs-and-risks.md#n-18).

## Риски потери
- ⚠️ 1 тик в окне ([N-10](../bugs-and-risks.md#n-10)).
- ❌ «Липкость»: пользователь оплатил, пока была активна выдача на 1 день → `admin_grant_days=1` остаётся → в конце **платной** подписки придёт «Ваш бесплатный доступ…», а платных напоминаний не будет ([N-04](../bugs-and-risks.md#n-04)).

## Вердикт
⚠️ **Риск** — сама по себе работает; ломает сегментацию после оплаты.

## Рекомендации
[R-04](../recommendations.md#r-04), [R-12](../recommendations.md#r-12).
