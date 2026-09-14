# Триал: «Последний час пробного доступа»

[← к оглавлению](../README.md) · вердикт: ⚠️

| | |
|---|---|
| Ключ | реестр и i18n `trial.notification_71h` (имя историческое, фактически за 1 ч до конца) |
| Сегмент | триал |
| Когда | `trial_expires_at − now ∈ (0,5; 1]` ч, то есть T+71…71,5 ч |
| Воркер | trial_notifications, 5 мин |
| Дедупликация | `trial_notif_71h_sent` после отправки |

## Назначение
Финальный пуш перед отключением.

## Сегмент
Как у [24 ч](trial-reminder-24h.md). Дополнительно перед отправкой перепроверяется активная платная подписка (`trial_notifications.py:367-373`).

## Триггер и окно
`should_send_final_reminder` (`app/services/trials/service.py:256-316`): окно `(0.5, 1]` захардкожено (`:310-314`); конфиг — `get_final_reminder_config` (`:446-461`). Нижняя граница 0,5 ч нужна, чтобы истечение в том же тике не «съело» сообщение (комментарий `:305-309`). 4–6 тиков.

## Воркер и период
trial_notifications, «legacy»-блок `trial_notifications.py:358-487`.

## Источник данных
БД.

## Фильтр получателей
Как у 24 ч + повторная проверка платной подписки.

## Текст
- Выбор: `_custom = await get_notification_text(_key)` (`:459`) → `send_trial_notification(custom_text=_custom)` → `custom_text or i18n` (`:200`).
- RU (реестр `registry.py:114-131` = `app/i18n/ru.py:791`):
  ```
  🚨 Последний час пробного доступа

  Через час VPN будет отключён.

  Оформите подписку, чтобы оставаться на связи, даже когда глушат связь!
  ```
- EN (`app/i18n/en.py:646`, недостижим — [N-03](../bugs-and-risks.md#n-03)).
- Кнопка: `main.buy` → `menu_buy_vpn` (`has_button: True`, `service.py:459`).

## Переопределение из дашборда
- Текст и вкл/выкл — да; выключено → флаг ставится + `skipped_disabled` (`:450-458`).
- Окно `default_trigger {1 h, ±0.5 h}` **не читается** ([N-14](../bugs-and-risks.md#n-14)).

## Дедупликация
Флаг `trial_notif_71h_sent` (`database/core.py:625`) при `sent` или `failed_permanently` (`trial_notifications.py:465-480`). Если `send_trial_notification` бросает исключение — повтор (`:481-486`).

## Риски задвоения
⚠️ [N-18](../bugs-and-risks.md#n-18).

## Риски потери
- ⚠️ Временная ошибка Telegram → `failed_permanently` → потеря ([N-08](../bugs-and-risks.md#n-08)).
- ❌ [N-02](../bugs-and-risks.md#n-02).

## Вердикт
⚠️ **Риск.**

## Рекомендации
[R-09](../recommendations.md#r-09), [R-03](../recommendations.md#r-03). Переименовать ключ в `trial.reminder_1h` с алиасом ([R-18](../recommendations.md#r-18)).
