# Триал → истёк: «Пробный доступ завершён», скидка 30%

[← к оглавлению](../README.md) · вердикт: ❌

| | |
|---|---|
| Ключ | i18n `trial.expired` + кнопка `trial.expired_discount_btn` (без реестра) |
| Сегмент | триал истёк, платной подписки нет |
| Когда | первый тик trial_notifications после `trial_expires_at` (окно до 24 ч) |
| Воркер | trial_notifications → `expire_trial_subscriptions`, 5 мин |
| Дедупликация | `users.trial_completed_sent`, условный UPDATE **до** отправки |

## Назначение
Попрощаться после триала и дать оффер −30% на первую подписку.

## Сегмент
Пользователи с `trial_used_at`, у которых триал закончился в последние 24 ч и нет активной платной подписки.

## Триггер и окно
- Выборка: `trial_expires_at <= now AND trial_expires_at > now − 24h` (`trial_notifications.py:822-835`).
- Затем `should_expire_trial`: истёк, не старше 24 ч, **есть активная строка `source='trial'`** (`app/services/trials/service.py:87-136`).
- Затем двойная проверка «нет активной платной» (`trial_notifications.py:655-663, 681-689`).
- Затем: отключение premium в панели (`:695-705`), перевод строки в `bypass_only` (с отправкой [«обход работает»](expired-bypass-active.md), `:712-747`) или в `expired` (`:749-753`), и только потом `trial.expired` (`:755-791`).

## Воркер и период
`expire_trial_subscriptions` вызывается в том же тике после `process_trial_notifications` (`trial_notifications.py:942-946`), каждые 5 мин.

## Источник данных
БД; единственный HTTP-вызов — отключение premium в Remnawave (`:697`).

## Фильтр получателей
`is_reachable` (`trial_notifications.py:831`, [N-02](../bugs-and-risks.md#n-02)), `trial_completed_sent = FALSE` (`app/services/trials/service.py:352-387`).

## Текст
- RU (`app/i18n/ru.py:787`):
  ```
  🔓 <b>Пробный доступ завершён</b>

  Спасибо что попробовали Atlas Secure. Надеемся вам понравилось.

  Если решите вернуться — нажмите кнопку ниже и получите <b>скидку 30%</b> на первую подписку.
  ```
- EN (`app/i18n/en.py:643`):
  ```
  🔓 <b>Trial access ended</b>

  Your trial period has expired.

  🎟 Use promo code <b>YABX30</b> for 30% discount on your first subscription.

  Subscribe now to continue using secure access.
  ```
- Кнопка: `trial.expired_discount_btn` «🔥 Купить со скидкой 30%» / «🔥 Buy with 30% off» → **`menu_buy_vpn`** (`trial_notifications.py:767-772`). Скидка 30% нигде не создаётся; `YABX30` в коде не встречается ([N-05](../bugs-and-risks.md#n-05)).

## Переопределение из дашборда
Нет.

## Дедупликация
`UPDATE users SET trial_completed_sent = TRUE WHERE … AND trial_completed_sent = FALSE` (`app/services/trials/service.py:341-349`; колонка — `database/core.py:715`) **до** отправки. Отправляет тот, кто выиграл. Не сбрасывается никогда.

## Риски задвоения
- ✅ Самого `trial.expired` — нет.
- ⚠️ Если этот воркер всё-таки выиграл гонку у пользователя с обходом, подряд придут **два** сообщения: «Основная подписка закончилась» (`:745`) и «Пробный доступ завершён» (`:773`).

## Риски потери
- ❌ **Гонка с fast_expiry_cleanup** (тик 60 с против 5 мин): fast_expiry первым переводит строку в `bypass_only` (дальше она считается «платной») или в `expired` (дальше нет активной trial-строки). В обоих случаях сообщение не уходит ([N-05](../bugs-and-risks.md#n-05), доказательство — скрипт §6).
- ⚠️ Флаг ставится до отправки: ошибка Telegram — потеря (`:777-791`).
- ⚠️ Соединение пула удерживается во время HTTP в Remnawave и отправок в Telegram ([N-11](../bugs-and-risks.md#n-11)).

## Вердикт
❌ **Баг**: в большинстве случаев не приходит, а обещанная скидка не применяется.

## Рекомендации
[R-06](../recommendations.md#r-06) (отдельный проход по `trial_completed_sent` независимо от состояния строки, одно объединённое сообщение, реальная скидка), [R-15](../recommendations.md#r-15), [R-17](../recommendations.md#r-17).
