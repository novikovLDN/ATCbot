# Платная: «Подписка активирована!» (отложенная активация)

[← к оглавлению](../README.md) · вердикт: ⚠️

| | |
|---|---|
| Ключ | нет: текст захардкожен в `activation_worker.py:219-223` |
| Сегмент | оплатившие, у которых активация в панели не прошла сразу (`activation_status='pending'`) |
| Когда | на тике, где `attempt_activation` завершилась успешно |
| Воркер | activation_worker, 5 мин (env 1–30 мин) |
| Дедупликация | переход `activation_status` pending → active |

## Назначение
Сообщить, что оплаченная подписка, которая «зависла», наконец активирована, и дать кнопку подключения.

## Сегмент
Строки с `activation_status='pending'`. Их создают ветки отложенной активации в `grant_access` (`database/subscriptions.py:1918-1965`, `:2229`).

## Триггер и окно
`activation_service.get_pending_subscriptions(max_attempts, limit=50)` (`activation_worker.py:106-111`) → `attempt_activation` (`:176-181`) → перепроверка `activation_status == 'active' AND uuid == result.uuid` (`:190-204`) → отправка (`:205-231`).

## Воркер и период
`ACTIVATION_INTERVAL_SECONDS = 300` (`activation_worker.py:41-45`), лимит 15 с на итерацию (`:37`), таймаут 120 с (`:449`). Работает только при `config.VPN_ENABLED` (`:84`), а он равен `REMNAWAVE_ENABLED` (`config.py:409`).

## Источник данных
БД + вызов панели внутри `attempt_activation`.

## Фильтр получателей
Нет `is_reachable`. Истёкшие pending-подписки помечаются `failed` без уведомления пользователю (`:148-168`).

## Текст
- Только RU, захардкожен (`activation_worker.py:219-223`), и для EN-пользователей тоже ([N-17](../bugs-and-risks.md#n-17)):
  ```
  🎉 <b>Подписка активирована!</b>

  ⚡ {Basic|Plus|Business} · до {dd.mm.yyyy}

  Нажмите кнопку ниже чтобы подключить устройство.
  ```
- Дата в UTC (`:208`, [N-19](../bugs-and-risks.md#n-19)).
- Кнопка: `get_connect_keyboard()` (`app/handlers/common/keyboards.py`).

## Переопределение из дашборда
Нет.

## Дедупликация
Подписка переходит в `active` один раз, поэтому повторного выбора в `pending` нет ✅. Проверка `uuid != result.uuid` → «already_notified» (`:200-204`) — косвенная эвристика, отдельного флага нет.

## Риски задвоения
- ✅ В рамках этого воркера — нет.
- ⚠️ Пересечение с новым provisioning outbox (T5–T10, `app/services/provisioning.py`, флаг `USE_NEW_PROVISIONING`, `complete_activation`) в этом аудите **не анализировалось**: отправляет ли провижининг своё сообщение об активации — проверить при раскатке флага.

## Риски потери
- ⚠️ Результат `safe_send_message` не проверяется (`:225-231`): при ошибке в лог всё равно пишется `ACTIVATION_NOTIFICATION_SENT`.
- ⚠️ Если активация прошла, а отправка упала, повтора нет: строка уже `active`.
- ⚠️ При окончательном `failed` пользователю ничего не сообщается, алерт получает только админ (`:283-311`).

## Вердикт
⚠️ **Риск.** Логика однократна, но текст только RU, без проверки доставки; пересечение с новым провижинингом не проверено.

## Рекомендации
[R-17](../recommendations.md#r-17) (i18n), [R-09](../recommendations.md#r-09) (проверка доставки), [R-08](../recommendations.md#r-08) (журнал с `period_key = subscription_id`), [R-16](../recommendations.md#r-16). При раскатке `USE_NEW_PROVISIONING` — один источник сообщения «активировано».
