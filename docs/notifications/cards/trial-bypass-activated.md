# Триал: «Обход белых списков подключён» (T+5 мин)

[← к оглавлению](../README.md) · вердикт: ✅

| | |
|---|---|
| Ключ | i18n `trial.bypass_activated` (без реестра) |
| Сегмент | триал, сразу после активации |
| Когда | через 300 с после нажатия «Активировать триал» |
| Воркер | разовая задача + запасной путь trial_notifications (5 мин) |
| Дедупликация | `subscriptions.trial_notif_bypass_activated_sent`, атомарный захват **до** отправки |

## Назначение
Отправить пользователя на экран установки bypass-ключа (Happ или Incy).

## Сегмент
Строка `subscriptions` с `source='trial' AND status='active'`.

## Триггер и окно
- **Основной путь.** `callback_activate_trial` → `schedule_bypass_activated_notification` (`app/handlers/callbacks/subscription.py:260-275`) → `loop.create_task(sleep(300) + try_send)` (`app/services/trials/bypass_activation_delay.py:33, 36-59`).
- **Запасной путь** (задача потерялась при рестарте). В `trial_notifications.py:257-272`, если «часы с активации» ≥ 5/60, до конца > 1 ч и флаг FALSE. «Часы с активации» = `72 − часы_до_конца` (`app/services/trials/service.py:171`) — допущение «триал = 72 ч» ([N-20](../bugs-and-risks.md#n-20)).

## Воркер и период
Разовая задача в event loop; запасной путь — trial_notifications (`trial_notifications.py:874-995`, 5 мин, стартовый джиттер 5–60 с).

## Источник данных
БД.

## Фильтр получателей
- Основной путь: без фильтра `is_reachable`.
- Запасной: запрос trial_notifications (`trial_notifications.py:561-586`): `is_reachable`, `trial_expires_at > now`, нет активной платной.

## Текст
- RU (`app/i18n/ru.py:801-808`):
  ```
  🛡 <b>Обход белых списков подключён</b>

  Мы дали <b>500 МБ трафика обхода</b> в подарок — с ним открываются сайты и сервисы, которые фильтруются по белым спискам.

  Чтобы начать пользоваться — установи ключ в Happ или Incy нажатием кнопки ниже.
  ```
- EN (`app/i18n/en.py:1179-1185`): «🛡 Whitelist bypass connected …»
- Кнопки (`bypass_activation_delay.py:113-122`):
  - `trial.bypass_activated_btn_setup` «🌐 Включить обход» → `bypass_setup_open`;
  - `trial.bypass_activated_btn_help` «💬 Нужна помощь» → `menu_help`.

## Переопределение из дашборда
Нет.

## Дедупликация
`UPDATE subscriptions SET trial_notif_bypass_activated_sent = TRUE WHERE … AND COALESCE(flag, FALSE) = FALSE RETURNING id` (`bypass_activation_delay.py:80-101`; колонка — `migrations/062_trial_notif_bypass_activated.sql`). Отправляет тот, кто выиграл захват. Флаг не сбрасывается (0 вхождений).

## Риски задвоения
✅ Нет: захват атомарный, проигравший (задача или scheduler) тихо выходит.

## Риски потери
- ⚠️ Флаг ставится **до** отправки: любая ошибка, включая временную, — уведомление потеряно (осознанно, `bypass_activation_delay.py:66-73, 124-131`).
- ⚠️ Текст обещает 500 МБ, но отправляется, даже если bypass-сущность не создалась; объём в тексте захардкожен (`config.py:558`) ([N-20](../bugs-and-risks.md#n-20)).
- ⚠️ Повторный триал из подарочного ключа рассылки: флаг уже TRUE, сообщение не придёт. Для однодневного подарка это нормально.

## Вердикт
✅ **Корректно.** Единственное уведомление с правильной схемой «захват → отправка».

## Рекомендации
- Та же схема захвата — образец для [R-08](../recommendations.md#r-08).
- Для временных ошибок — ретрай ([R-09](../recommendations.md#r-09)).
- Подставлять `{mb}` из `TRIAL_BYPASS_MB`, отправлять только при успешном создании bypass ([R-17](../recommendations.md#r-17)).
