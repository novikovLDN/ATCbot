# Триал: «Пробный период заканчивается завтра»

[← к оглавлению](../README.md) · вердикт: ⚠️

| | |
|---|---|
| Ключ | реестр `trial.reminder_24h` · i18n `trial.reminder_24h` |
| Сегмент | триал |
| Когда | `trial_expires_at − now ∈ [24 − 1; 24 + 1]` ч (по умолчанию), то есть T+47…49 ч |
| Воркер | trial_notifications, 5 мин |
| Дедупликация | `subscriptions.trial_notif_24h_sent` после отправки |

## Назначение
Ключевой пуш триальной воронки перед отключением.

## Сегмент
Пользователи с активным триалом без платной подписки.

## Триггер и окно
`trial_notifications.py:280-314`. Цель и допуск берутся из `get_trigger_config("trial.reminder_24h")` (реестр `registry.py:74-92`: `before_expiry_hours: 24, tolerance_hours: 1`), **админ может сдвинуть окно из дашборда** ✅. Часы считаются от `users.trial_expires_at` (`:230, 247-248`).

## Воркер и период
`run_trial_scheduler`: 5 мин (`trial_notifications.py:995`), таймаут 120 с на весь проход (`:949`), батчи по 100 (`:141, 613-633`). Окно 2 ч — 17–24 тика ✅.

## Источник данных
БД: `users.trial_expires_at` + строка `subscriptions` триала.

## Фильтр получателей
`trial_notifications.py:561-586`:
- `s.source='trial' AND s.status='active' AND s.expires_at > now`;
- `u.trial_used_at IS NOT NULL AND u.trial_expires_at > now`;
- `is_reachable` ([N-02](../bugs-and-risks.md#n-02));
- нет активной платной (LEFT JOIN, `:575-578`, `:234-241`).

## Текст
- Выбор: `custom = await get_notification_text(...)`, затем `text = custom or i18n...` (`trial_notifications.py:297-299`) → `send_trial_notification(..., custom_text=text)` (`:301-304`).
- RU (реестр `registry.py:83-89` = `app/i18n/ru.py:794`):
  ```
  ⏳ <b>Пробный период заканчивается завтра</b>

  Завтра доступ отключится — сайты и приложения вернутся к блокировкам.

  Оформите подписку сейчас — ключ и настройки сохранятся, ничего не нужно переустанавливать.
  ```
- EN (`app/i18n/en.py:1189`, недостижим — [N-03](../bugs-and-risks.md#n-03)).
- Кнопка: `main.buy` «🔐 Купить подписку» → `menu_buy_vpn` (`trial_notifications.py:145-153, 203-205`). Переменная `keyboard` на `:300` не используется.

## Переопределение из дашборда
Да: RU-текст, вкл/выкл, окно. Выключено → флаг ставится + `skipped_disabled` (`:287-295`).

## Дедупликация
- Флаг ставится при `sent` **или** `failed_permanently` (`trial_notifications.py:305-308`), статичный SQL (`:106-109`). Колонка — `migrations/036_notification_overhaul.sql:10`.
- Сброс: нигде (0 вхождений). Для одноразового триала это ок.

## Риски задвоения
⚠️ Падение между отправкой (`:301`) и флагом (`:306-308`) → повтор через 5 мин, пока окно открыто ([N-18](../bugs-and-risks.md#n-18)).

## Риски потери
- ⚠️ **Временная ошибка Telegram = постоянная**: `safe_send_message` → `None` → `failed_permanently` → флаг → потеря ([N-08](../bugs-and-risks.md#n-08); скрипт подтверждает).
- ❌ [N-02](../bugs-and-risks.md#n-02).

## Вердикт
⚠️ **Риск.** Окно и частота хорошие; теряется на временных ошибках, для EN — русский текст.

## Рекомендации
[R-09](../recommendations.md#r-09), [R-03](../recommendations.md#r-03), [R-02](../recommendations.md#r-02).
