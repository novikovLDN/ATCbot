# Баги и риски автоуведомлений

[← к оглавлению](README.md) · [рекомендации →](recommendations.md)

База: `eac672ba`, 2026-09-13. Отсортировано по критичности: ❌ баг на обычном пути → ⚠️ риск в отдельных сценариях. ✅ — исправлено (P0, `2cba0b11`, тесты `tests/services/test_notifications_p0.py`); `file:line` в исправленных пунктах указаны на базу `eac672ba`. У каждого пункта есть сценарий, `file:line` и ссылка на рекомендацию `R-xx`.

| ID | Статус | Коротко | Затронутые карточки |
|---|---|---|---|
| [N-01](#n-01) | ✅ `2cba0b11` | `reminder_7d_sent` и `reminder_1d_sent` никогда не сбрасываются | [7d](cards/paid-reminder-7d.md), [1d](cards/paid-reminder-1d.md) |
| [N-02](#n-02) | ✅ `2cba0b11` | `is_reachable` не восстанавливается, автопродление тоже выключается | все воркерные |
| [N-03](#n-03) | ✅ `2cba0b11` | EN-пользователи получают RU-тексты из реестра | 7d, 3d, 1d, 3h, trial 24h, 3h, 1h |
| [N-04](#n-04) | ✅ `2cba0b11` | «Липкий» `admin_grant_days`: неверные напоминания или никаких | [6h](cards/grant-reminder-1day-6h.md), [24h](cards/grant-reminder-7days-24h.md), все платные |
| [N-05](#n-05) | ✅ `2cba0b11` | «Пробный доступ завершён, −30%» почти не доходит; скидка не применяется | [trial-expired](cards/trial-expired.md) |
| [N-06](#n-06) | ✅ `2cba0b11` | Автопродление: «Списано с баланса: 0.00 ₽» | [auto-renewal](cards/paid-auto-renewal-success.md) |
| [N-07](#n-07) | ✅ `2cba0b11` | Подарочный пробный ключ из рассылки переводит платного пользователя в `source='trial'` | все платные |
| [N-08](#n-08) | ⚠️ | Триал: временная ошибка Telegram считается постоянной, уведомление теряется | trial 24h, 1h |
| [N-09](#n-09) | ⚠️ | Трафик: каскад до 6 сообщений; флаг ставится даже при неудачной отправке | [traffic](cards/traffic-thresholds.md) |
| [N-10](#n-10) | ⚠️ | Окно «3 часа» (платная) = 1 тик воркера; таймаут итерации 120 с | 3h, 1d, 6h |
| [N-11](#n-11) | ⚠️ | Telegram или HTTP внутри открытой транзакции или удерживаемого соединения → повтор при откате | [expired-bypass](cards/expired-bypass-active.md), trial-expired |
| [N-12](#n-12) | ⚠️ | Отсутствующие уведомления: истечение без обхода, нехватка баланса, win-back | — |
| [N-13](#n-13) | ⚠️ | «Продлите» уходит и тем, у кого включено автопродление или СБП-рекуррент | 7d, 3d, 1d |
| [N-14](#n-14) | ⚠️ | Окна в дашборде (`trigger_config`) для `subscription.*` игнорируются; мёртвые ключи | [dead-keys](cards/other-dead-registry-keys.md) |
| [N-15](#n-15) | ⚠️ | `automated_notification_sends` — только лог: без UNIQUE, статусы неточные | все реестровые |
| [N-16](#n-16) | ⚠️ | Тексты скидок («3 часа», «до конца триала») не совпадают с механикой (7 дней) | 3h (оба) |
| [N-17](#n-17) | ⚠️ | Захардкоженные RU-тексты вне i18n | activation, Platega, auto-renew, traffic |
| [N-18](#n-18) | ⚠️ | Падение между отправкой и записью флага → дубль | reminders, trial |
| [N-19](#n-19) | ⚠️ | Даты в текстах в UTC; нет «тихих часов» | auto-renew, activation, Platega |
| [N-20](#n-20) | ⚠️ | Мелочи триала: допущение «72 ч», текст про 500 МБ без проверки | [bypass-activated](cards/trial-bypass-activated.md) |

---

<a id="n-01"></a>
## N-01 ✅ Флаги `reminder_7d_sent` и `reminder_1d_sent` никогда не сбрасываются

**✅ Исправлено (`2cba0b11`).** `grant_access` сбрасывает `reminder_7d_sent` и `reminder_1d_sent` во всех 5 местах записи: Basic→Plus, Plus→Basic, продление, `ON CONFLICT` обеих новых выдач. Outbox/`defer_panel` идут через те же ветки. Тесты: `test_n01_*`.

**Суть.** При продлении, смене тарифа и новой покупке `grant_access` сбрасывает `reminder_sent, reminder_3d_sent, reminder_24h_sent, reminder_3h_sent, reminder_6h_sent`, но **не** `reminder_7d_sent` и `reminder_1d_sent`.

**Где.**
- Сбросы: `database/subscriptions.py:1530-1531` (Basic→Plus), `:1609-1610` (Plus→Basic), `:1718-1722` (продление), `:1941-1945` (новая выдача с отложенной активацией), `:2260-2264` (новая выдача).
- Флаги ставятся после отправки: `reminders.py:292` → `app/services/notifications/service.py:335-360` → `database/subscriptions.py:2731-2742`.
- Проверка флагов: `app/services/notifications/service.py:225,245`.
- Колонки: `migrations/036_notification_overhaul.sql:6-7`.

**Сценарий.** Пользователь купил месяц → за 7 дней и за сутки пришли напоминания (флаги = TRUE) → продлил → в следующем периоде приходят только «3 дня» и «3 часа». Так будет в каждом следующем периоде, включая повторную покупку после полного истечения (ветки `ON CONFLICT` в `:1936-1963` и `:2252-2280` тоже не трогают 7d/1d).

**Доказательство** ([вывод скрипта](#evidence)): в прод-коде 0 вхождений `reminder_7d_sent = FALSE` и `reminder_1d_sent = FALSE`. Таймлайн `should_send_reminder` для второго периода: `reminder_3h @ 2.50..3.50h, reminder_3d @ 69.75..74.25h` — 7d и 1d не срабатывают.

**Влияние.** Все продлевающиеся клиенты (самая ценная когорта) теряют 2 из 4 напоминаний, включая «завтра» — одно из самых конверсионных.

**Исправление:** [R-01](recommendations.md#r-01), стратегически — [R-08](recommendations.md#r-08).

---

<a id="n-02"></a>
## N-02 ✅ `is_reachable` только выключается — и выключает автопродление

**✅ Исправлено (`2cba0b11`).** `database.mark_user_reachable` (пишет только если FALSE) вызывается из `LastSeenMiddleware` на любое сообщение или нажатие кнопки, включая /start. Автопродление больше не фильтрует по `is_reachable`: списание и продление идут, а уведомление об успехе может не дойти само по себе. Тесты: `test_n02_*`.

**Суть.** `mark_user_unreachable` ставит `users.is_reachable = FALSE` (`database/subscriptions.py:2790-2806`). Вызывается на Forbidden и «chat not found» из `safe_send_message` (`app/utils/telegram_safe.py:46-66`) и из фото-отправки триала (`trial_notifications.py:60-75`). Кода, возвращающего TRUE, **нет**: 0 записей `is_reachable = TRUE` в прод-коде. Вхождение в `database/admin.py:1453` — это docstring.

**Кого отсекает этот флаг.**
- платные напоминания: `database/subscriptions.py:3390`;
- все триальные уведомления: `trial_notifications.py:582`;
- завершение триала с `trial.expired`: `trial_notifications.py:831`;
- **автопродление с баланса**: `auto_renewal.py:83`.

**Сценарий.** Пользователь заблокировал бота на день (или Telegram разово ответил «chat not found»), любое уведомление ставит FALSE → пользователь разблокирует бота и жмёт /start → флаг остаётся FALSE. Больше нет напоминаний, а подписка с `auto_renew` и достаточным балансом **не продлевается** и истекает.

**Влияние.** Потеря выручки (автопродление) и тихий отток. Масштаб — `SELECT count(*) FROM users WHERE is_reachable = FALSE` на проде.

**Исправление:** [R-02](recommendations.md#r-02).

---

<a id="n-03"></a>
## N-03 ✅ EN-пользователи получают русские тексты напоминаний

**✅ Исправлено (`2cba0b11`).** `get_notification_text(key, language=…)`: RU-оверрайд применяется только когда i18n и так ответит по-русски (`ru` и legacy-коды). Для `en` возвращается `None`, и вызывающий код берёт i18n-ключ на английском. Передаётся из `reminders.py` (7d/3d/1d/24h/3h) и `trial_notifications.py` (24h, 3h, 71h, legacy). Колонки EN в схеме нет, полное решение (`custom_text_en`) остаётся за R-03. Тесты: `test_n03_*`.

**Суть.** Для ключей реестра текст берётся так: `(await get_notification_text(key)) or i18n.get_text(language, ...)`: `reminders.py:181,187,193,199,205`, `trial_notifications.py:297-299,334-336`, а для последнего часа `:459` → `send_trial_notification(custom_text=...)`, `:200`. `get_notification_text` возвращает `custom_text_ru or default_text_ru` (`app/services/automated_notifications/helper.py:142,185-203`). Строка есть в БД для каждого ключа (upsert при старте, `main.py:300-305`), поэтому результат **никогда не None**, пока уведомление включено. При пустом кэше берётся `default_text_ru` из `REGISTRY` (`helper.py:190-192`). Ветка i18n с EN недостижима.

**Затронуты:** `subscription.reminder_7d/3d/1d/3h`, `trial.reminder_24h`, `trial.reminder_3h`, `trial.notification_71h`.

**Доказательство:** скрипт: EN-пользователь получает `'<tg-emoji …>📅</tg-emoji> Подписка заканчи…'`, `'⏳ <b>Пробный период заканчивается завтра</b>…'`, `'🚨 Последний час пробного доступа…'`. При пустом кэше — тоже RU.

**Побочно.** Правки этих ключей в `app/i18n/ru.py` ни на что не влияют: побеждает `default_text_ru` из `registry.py`.

**Исправление:** [R-03](recommendations.md#r-03).

---

<a id="n-04"></a>
## N-04 ✅ «Липкий» `admin_grant_days`: неверные напоминания или никаких

**✅ Исправлено (`2cba0b11`).** Платные источники (`payment`, `auto_renew`, `gift`) обнуляют `admin_grant_days` во всех ветках продления. Для admin/promo/game выдач колонка не меняется. Правило «выдачи не на 1 и не на 7 дней остаются без напоминаний» из R-04 не трогали, это P1. Тесты: `test_n04_*`.

**Суть.** `should_send_reminder` считает подписку админ-выдачей, если `admin_grant_days IS NOT NULL` или последняя запись истории — `admin_grant` (`app/services/notifications/service.py:183-185`). Для админ-выдачи есть только два правила: `days == 1` → «6 часов», `days == 7` → «24 часа» (`:188-219`). При любом другом значении напоминаний нет вообще.

`admin_grant_days` пишется только на новой выдаче (`database/subscriptions.py:1946`, `:2265`); во всех ветках продления (`:1526-1532`, `:1605-1611`, `:1711-1727`) колонка **не трогается**. Значения ставят: админ-выдача (`database/admin.py:2566`), админ-бонус (`app/handlers/admin/bonus.py:482`), промо-ссылки с наградой «дни подписки» (`app/handlers/user/start.py:989`).

**Сценарии.**
1. Промо-ссылка на 30 дней новому пользователю → `admin_grant_days=30` → за весь период **ни одного** напоминания. Пользователь купил месяц до истечения (ветка продления) → `admin_grant_days` всё ещё 30 → платных напоминаний нет ни сейчас, ни в следующих периодах, пока подписка не истечёт полностью.
2. Админ выдал 7 дней → пользователь заплатил → за сутки до конца **платной** подписки приходит «Бесплатный доступ к Atlas Secure истекает через 24 часа… Подключите подписку от 199₽/мес», а 7d, 3d и 3h не приходят.

**Доказательство:** скрипт: `admin_grant_days=7` → только `admin_7days_24h @ 23..25h`; `admin_grant_days=30` → `NOTHING`.

**Исправление:** [R-04](recommendations.md#r-04).

---

<a id="n-05"></a>
## N-05 ✅ «Пробный доступ завершён, −30%» почти не доходит, а скидка не применяется

**✅ Исправлено (`2cba0b11`).** `fast_expiry_cleanup` после COMMIT истечения строки `source='trial'` шлёт уведомление через общий атомарный захват `users.trial_completed_sent` (`trial_notifications.claim_trial_expired_notice` + `send_trial_expired_notice`). Кто первым истёк триал, тот и шлёт, ровно один раз и без удерживаемого соединения. Скидка теперь настоящая: персональная 30% на 7 дней (`user_discounts`, тот же механизм, что у кнопок 15%), при оплате её применяет `calculate_final_price`. Бо́льшая существующая скидка сохраняется. RU/EN тексты называют срок 7 дней, EN больше не ссылается на промокод `YABX30`. Тесты: `test_n05_*`.

**Суть (гонка).** Строка триала создаётся через `grant_access(source="trial")` (`app/handlers/callbacks/subscription.py:221-226`) с непустым `uuid`, поэтому её подбирает `fast_expiry_cleanup`: запрос `status='active' AND expires_at < now AND uuid IS NOT NULL` (`fast_expiry_cleanup.py:163-173`), период 60 с (`:42-44`). trial_notifications тикает раз в 5 мин (`trial_notifications.py:995`).

Кто первым увидел истёкший триал:
- **fast_expiry** (почти всегда):
  - если есть обход, строка → `source='bypass_only', expires_at=+10 лет` (`fast_expiry_cleanup.py:269-283`). Тогда trial_notifications считает её «активной платной» (`get_active_paid_subscription` фильтрует по `source != 'trial'`, `database/subscriptions.py:662-666`) и выходит на `trial_notifications.py:655-663`;
  - если обхода нет, `status='expired'` (`:309-316`), и `should_expire_trial` возвращает `no_active_trial_subscription` (`app/services/trials/service.py:124-134`).

  В обоих случаях `trial.expired` **не отправляется**.
- **trial_notifications**: только если его тик пришёлся на первые ≤60 с после истечения.

**Суть (обещание).** RU-текст: «нажмите кнопку ниже и получите **скидку 30%**» (`app/i18n/ru.py:787`), кнопка «🔥 Купить со скидкой 30%» (`:788`) ведёт на обычный `menu_buy_vpn` (`trial_notifications.py:767-772`). Кода, создающего скидку 30%, нет. EN-текст ссылается на промокод `YABX30` (`app/i18n/en.py:643`), который в коде не встречается. Существует ли он в БД промокодов — проверить.

**Доказательство:** скрипт: `should_expire_trial → (False, 'no_active_trial_subscription')`; для строки `bypass_only` предикат «активная платная» = `True`.

**Исправление:** [R-06](recommendations.md#r-06).

---

<a id="n-06"></a>
## N-06 ✅ Автопродление пишет «Списано с баланса: 0.00 ₽»

**✅ Исправлено (`2cba0b11`).** Текст берёт `item["amount_rubles"]` и в legacy-, и в outbox-пути. Эвристика «> 1000 → копейки» удалена. Метка «Комбо» и захардкоженная кнопка «👤 Мой профиль» не трогались (N-17/R-05, P1–P2). Тест: `test_n06_*`.

**Суть.** Payload собирается с ключом `amount_rubles` (`auto_renewal.py:347-358`), а текст читает `item.get("amount", 0)` (`:418-421`). Итог — всегда 0. Ключа `is_combo` в payload тоже нет (`:407`), поэтому метка «Комбо» никогда не показывается. Кнопка захардкожена по-русски: `"👤 Мой профиль"` (`:430`).

**Доказательство:** скрипт рендерит `purchase.auto_renewal_success` с тем же payload: `💳 Списано с баланса: 0.00 ₽`.

**Влияние.** Пользователь видит «0 ₽», но баланс уменьшился — повод для тикета в поддержку и недоверия.

**Исправление:** [R-05](recommendations.md#r-05).

---

<a id="n-07"></a>
## N-07 ✅ Подарок «пробный ключ» из рассылки выключает напоминания платному пользователю

**✅ Исправлено (`2cba0b11`).** В ветке продления `grant_access` входящий `source='trial'` на активной не-триальной и не-bypass-only строке сохраняет её `source` и `subscription_type`: добавляются только дни, Plus не превращается в Basic. Работает и для legacy-пути, и для `grant_outbox`. Тесты: `test_n07_*`.

**Суть.** Кнопка рассылки вызывает `grant_access(duration=1 день, source="trial")` для любого нажавшего (`app/handlers/callbacks/broadcast_trial_key.py:180-185`). Для активной подписки это ветка продления, которая пишет `source = $2` (`database/subscriptions.py:1716`), то есть `'trial'`. Дальше:
- reminders пропускает `source='trial'` (`app/services/notifications/service.py:173-180`);
- trial_notifications требует `users.trial_expires_at > now` (`trial_notifications.py:579-581`), а рассылка его не ставит.

**Сценарий.** Платный пользователь с подпиской до 30.10 жмёт «🎁 Получить пробный ключ» 10.10 → `source='trial'` → 7d, 3d, 1d, 3h не приходят. Кроме того, для `get_active_paid_subscription` он больше не «платный». Статус восстановится только после следующей оплаты.

**Доказательство:** скрипт: платная подписка с `source='trial'` → `NOTHING`.

**Исправление:** [R-07](recommendations.md#r-07).

---

<a id="n-08"></a>
## N-08 ⚠️ Триал: временная ошибка Telegram считается постоянной

**Суть.** `safe_send_message` возвращает `None` на **любую** ошибку, включая RetryAfter, сеть и 5xx (`app/utils/telegram_safe.py:68-70`). `send_trial_notification` превращает `None` в `failed_permanently` (`trial_notifications.py:208-210`), и вызывающий код ставит флаг (`:305-308` для 24h, `:474-480` для последнего часа). Уведомление потеряно. Для «3 часов» логика другая: без флага и с ретраем (`:342-356`).

**Доказательство:** скрипт: бот бросает `Too Many Requests` → `(False, 'failed_permanently')`.

**Исправление:** [R-09](recommendations.md#r-09).

---

<a id="n-09"></a>
## N-09 ⚠️ Трафик: каскад сообщений и флаг при неудачной отправке

1. За тик уходит **один** порог — первый сверху из неотмеченных (`app/workers/traffic_monitor.py:51-66`). Если остаток упал сразу через несколько порогов (или до нуля), пользователь получает по сообщению на каждом тике: 8 ГБ → 5 → 3 → 1 → 500 МБ → 0. Это 6 сообщений за 30 мин, и в каждом «осталось 0 КБ».
2. `_send_traffic_notification` глотает исключения (`:103-104`), а флаг ставится безусловно (`:64-65`). Уведомление теряется.
3. Отправка голым `bot.send_message` (`:101`), не `safe_send_message`. Нет `is_reachable` ни в выборке (`database/traffic.py:394-401`), ни в отметке.

**Доказательство:** скрипт: 7 тиков при остатке 0 → `['8gb','5gb','3gb','1gb','500mb','0']`; при исключении отправки флаг `traffic_notified_8gb` всё равно `True`.

**Исправление:** [R-11](recommendations.md#r-11).

---

<a id="n-10"></a>
## N-10 ⚠️ Окна платных напоминаний против периода воркера

Период reminders — 45 мин + длительность итерации (`reminders.py:384`, таймаут `:348`), первый прогон через 60 с (`:326`). Окна (`app/services/notifications/service.py:191,224,234,244,254`):

| Напоминание | Ширина окна | Тиков в окне |
|---|---|---|
| 7 дней ±3 ч | 6 ч | 7–8 |
| 3 дня ±2,4 ч | 4,8 ч | 6 |
| завтра ±1 ч | 2 ч | 2 |
| 3 часа ±0,5 ч | 1 ч | **1** |
| админ 6 ч ±0,5 ч | 1 ч | **1** |

Один упавший или отменённый по таймауту тик, рестарт или деплой в этом часу — и «3 часа» со скидкой не придёт. Таймаут 120 с распространяется на **весь** проход по всем активным подпискам. При пиковой когорте (массовая акция → сотни истечений в одном часе, ≈0,1–0,3 с на отправку) проход отменяется посреди отправок, и хвост получит своё только через 45 мин — для «3 часов» это уже вне окна.

Для сравнения, у триала окна 2 ч при тике 5 мин (17–24 шанса).

**Исправление:** [R-12](recommendations.md#r-12).

---

<a id="n-11"></a>
## N-11 ⚠️ Отправка в Telegram внутри открытой транзакции или при удерживаемом соединении

- `fast_expiry_cleanup` отправляет «Основная подписка закончилась» внутри `conn.transaction()` (`fast_expiry_cleanup.py:244-307`), **до** записи аудита (`:323`). Если запись аудита упадёт или сработает таймаут итерации 120 с (`:393`), транзакция откатится: строка останется `active` с `uuid`, и через минуту сообщение уйдёт повторно.
- trial_notifications держит соединение пула на всём `_process_single_trial_expiration` (`trial_notifications.py:652-799`): HTTP в Remnawave (`:695-705`), две отправки в Telegram (`:745`, `:773`).

Это нарушение правила «не держать соединение или транзакцию во время HTTP» (корневой `CLAUDE.md`).

**Исправление:** [R-15](recommendations.md#r-15).

---

<a id="n-12"></a>
## N-12 ⚠️ Отсутствующие уведомления

1. **Истечение платной подписки без обхода** — `status='expired'` без сообщения (`fast_expiry_cleanup.py:308-316`). Пользователи с обходом получают только «обход работает» ([expired-bypass-active](cards/expired-bypass-active.md)).
2. **Не хватило баланса на автопродление** — только debug-лог (`auto_renewal.py:361-362`). `last_auto_renewal_at` уже записан (`:157-165`) и транзакция коммитится, поэтому после пополнения в том же окне повторной попытки не будет: условие `last_auto_renewal_at < expires_at - 12h` (`:84`) уже ложно.
3. **Win-back** (через 3 или 7 дней после истечения) — не реализован.

**Исправление:** [R-13](recommendations.md#r-13).

---

<a id="n-13"></a>
## N-13 ⚠️ «Продлите заранее» тем, у кого продление автоматическое

`should_send_reminder` не смотрит на `auto_renew`, баланс и активную СБП-подписку Platega (`app/services/notifications/service.py:221-261`). Пользователь с включённым автопродлением и достаточным балансом получает «Продлите сейчас» и может купить повторно вручную.

**Исправление:** [R-21](recommendations.md#r-21).

---

<a id="n-14"></a>
## N-14 ⚠️ Настройки окон в дашборде частично фиктивные; мёртвые ключи

- Для `subscription.reminder_7d/3d/1d/3h` в реестре есть `default_trigger` (`registry.py:171,188,202,338`), и админ может его менять, но окна захардкожены (`service.py:224-261`). Из `trigger_config` читается только `segment_filter` (`reminders.py:229-249`).
- `trial.notification_71h`: `default_trigger {1h ±0.5}` не читается, окно захардкожено (`app/services/trials/service.py:310-314`).
- `subscription.reminder_24h`, `trial.reminder_6h` и `referral.reward_notification` видны в дашборде, но не отправляются — [other-dead-registry-keys](cards/other-dead-registry-keys.md).

**Исправление:** [R-18](recommendations.md#r-18).

---

<a id="n-15"></a>
## N-15 ⚠️ `automated_notification_sends` — лог, а не дедупликация

- Таблица без UNIQUE (`migrations/068_automated_notifications.sql:59-73`), вставка — простой INSERT (`helper.py:244-265`). Для дедупликации не используется.
- В reminders любой `None` от отправки пишется как `blocked` (`reminders.py:279-288`), хотя это может быть временная ошибка. Статистика дашборда (`helper.py:268-290`) завышает «заблокировавших».
- Напоминания админ-выдачи, «обход подключён», «триал завершён», «обход работает», трафик и автопродление в лог не пишутся. Посчитать конверсию по ним нельзя.

**Исправление:** [R-08](recommendations.md#r-08), [R-19](recommendations.md#r-19).

---

<a id="n-16"></a>
## N-16 ⚠️ Тексты скидок не совпадают с механикой

- Платная, «3 часа»: «скидка 15% на продление. **Действует 3 часа**» (`app/i18n/ru.py:883`, `registry.py:330-335`).
- Триал, «3 часа»: «скидка 15% действует **до конца триала**» (`ru.py:795`).
- Фактически обе кнопки создают скидку 15% **на 7 дней** (`app/handlers/callbacks/navigation.py:319-326`, `:351-358`) и пишут «Действует 7 дней» (`ru.py:1278`).

**Исправление:** [R-17](recommendations.md#r-17).

---

<a id="n-17"></a>
## N-17 ⚠️ RU-тексты вне i18n

- `activation_worker.py:219-223` — «🎉 Подписка активирована!»
- `platega_service.py:813-838, 965-971, 1212-1216` — все 6 сообщений СБП-подписки.
- `auto_renewal.py:409-416, 430` — метки тарифа и кнопка «👤 Мой профиль».
- `app/workers/traffic_monitor.py:24-29` — единицы «ГБ/МБ/КБ» и в EN-текстах.
- Ферма: `app/workers/farm_notifications.py:70-106`.

**Исправление:** [R-17](recommendations.md#r-17).

---

<a id="n-18"></a>
## N-18 ⚠️ Падение между отправкой и флагом → дубль

Везде, кроме «обход подключён» и «триал завершён», флаг пишется **после** отправки: `reminders.py:278 → 292`, `trial_notifications.py:301 → 306`, `:339 → 344-346`, `:460 → 468`, `app/workers/traffic_monitor.py:64 → 65`. Сюда относятся рестарт, деплой, отмена `wait_for` по таймауту и ошибка БД на записи флага. Пока пользователь в окне, следующий тик отправит повторно. Защита `last_reminder_at` на 30 мин (`reminders.py:33,139-150`) не помогает: `last_reminder_at` пишется тем же UPDATE, что и флаг (`database/subscriptions.py:2731-2762`).

Вероятность низкая. Правильная схема — «захват → отправка → статус» ([R-08](recommendations.md#r-08)).

---

<a id="n-19"></a>
## N-19 ⚠️ Время и часовые пояса

- Сравнения времени в воркерах корректны: aware-UTC, `_to_db_utc`/`_from_db_utc` (`database/core.py:88-97`, `trial_notifications.py:230-232`, `fast_expiry_cleanup.py:172,192`). `last_reminder_at` пишется как `NOW() AT TIME ZONE 'UTC'` — согласовано. Ошибок naive/aware в пользовательских воркерах не найдено ✅.
- **Отображаемые даты** форматируются в UTC без пояса: `auto_renewal.py:343`, `activation_worker.py:208`, `platega_service.py:1208` (с пометкой «UTC»). Для МСК около полуночи дата «до» на день раньше.
- «Тихих часов» нет. «3 часа до отключения» может прийти ночью по МСК.

**Исправление:** [R-16](recommendations.md#r-16).

---

<a id="n-20"></a>
## N-20 ⚠️ Мелочи триала

- `calculate_trial_timing` вычисляет «часы с активации» как `72 − часы_до_конца` (`app/services/trials/service.py:171`). Запасной путь «обход подключён» (`trial_notifications.py:258`) опирается на это, и для триала другой длины сработает не через 5 мин.
- «Обход подключён» обещает «500 МБ трафика обхода» (`ru.py:801-808`), но отправляется независимо от того, создалась ли bypass-сущность. Объём задаётся `TRIAL_BYPASS_MB` (`config.py:558`), а в тексте захардкожен.
- Запасной путь делает `return` (`trial_notifications.py:272`): проверки 24h и 3h в том же тике пропускаются. Безвредно, окна далеко.

---

## Что проверено и работает ✅

| Что | Где |
|---|---|
| Триал и платное не дублируют друг друга: reminders пропускает `source='trial'` (исправлено после инцидента с двумя «завтра» подряд) | `app/services/notifications/service.py:161-180` |
| «Обход подключён» — атомарный захват `UPDATE … WHERE flag = FALSE RETURNING`, fast-task и scheduler не дублируют | `app/services/trials/bypass_activation_delay.py:80-109` |
| «Обход работает» не дублируется между fast_expiry и trial_notifications | `fast_expiry_cleanup.py:168,279-281`, `trial_notifications.py:655-663` |
| После оплаты триальные напоминания прекращаются | `database/subscriptions.py:1763-1771`, `trial_notifications.py:572-581` |
| Флаги трафика сбрасываются при создании, продлении и докупке в панели | `app/services/remnawave_service.py:167,191,290,445` |
| Автопродление не спишет дважды: `FOR UPDATE SKIP LOCKED` внутри транзакции + `last_auto_renewal_at` | `auto_renewal.py:84-87,115,157-165` |
| Один инстанс в PROD (advisory lock, иначе `exit(1)`) | `main.py:218-235` |
| Окна триала не пересекаются: 24h [23;25], 3h [2;4], последний час (0,5;1] | `trial_notifications.py:280-323`, `app/services/trials/service.py:310-316` |
| Существующие тесты триальной логики проходят (14 passed); сценарии N-01…N-09 ими не покрыты | `tests/services/test_trials.py` |

---

<a id="evidence"></a>
## Доказательства: вывод throwaway-скрипта

Скрипт запускался локально на `eac672ba` (Python из `.venv`, ENV-стабы как в `tests/conftest.py`), в репозиторий не коммитился. Он импортирует реальные функции (`should_send_reminder`, `get_notification_text`, `should_expire_trial`, `_check_user_traffic`, `send_trial_notification`) и подменяет только сеть и БД.

```text
=== 1. Flag reset coverage (production code, tests/.venv excluded)
reminder_3d_sent = FALSE     -> 5 hits [subscriptions.py:1530, 1609, 1719, 1942, 2261]
reminder_7d_sent = FALSE     -> 0 hits
reminder_1d_sent = FALSE     -> 0 hits
trial_notif_24h_sent = FALSE -> 0 hits
trial_notif_3h_sent = FALSE  -> 0 hits
is_reachable = TRUE          -> 1 hit  [database/admin.py:1453 — это docstring, не запись]

=== 2. should_send_reminder over 0..8 days before expiry (15-min grid)
- fresh paid (first period): reminder_3h @ 2.50..3.50h, reminder_1d @ 23.00..25.00h,
                             reminder_3d @ 69.75..74.25h, reminder_7d @ 165.00..171.00h
- paid, 2nd+ period (7d/1d flags never reset): reminder_3h @ 2.50..3.50h, reminder_3d @ 69.75..74.25h
- paid after admin grant 7d (admin_grant_days=7 kept on renewal): admin_7days_24h @ 23.00..25.00h
- gift/bonus 30d (admin_grant_days=30): NOTHING
- paid user after broadcast trial-key (source='trial'): NOTHING

=== 3. Window width vs worker period (chances per window)
  paid 7d ±3h 7..8 ticks · paid 3d ±2.4h 6 · paid 1d ±1h 2 · paid 3h ±0.5h 1
  trial 24h ±1h 17..24 · trial 3h ±1h 17..24 · trial 1h (0.5,1] 4..6

=== 4. automated_notifications override -> which text an EN user gets
  subscription.reminder_7d  EN user gets: '<tg-emoji …>📅</tg-emoji> Подписка заканчи'
  subscription.reminder_3h  EN user gets: '<tg-emoji …>🚨</tg-emoji> <b>3 часа до отк'
  trial.reminder_24h        EN user gets: '⏳ <b>Пробный период заканчивается завтра</b>…'
  trial.notification_71h    EN user gets: '🚨 Последний час пробного доступа…'
  cache empty (DB down) ->  '<tg-emoji …>📅</tg-emo'

=== 5. auto_renewal success text with the payload built at auto_renewal.py:347-358
  💳 Списано с баланса: 0.00 ₽
  is_combo in payload: False

=== 6. Trial expiry after fast_expiry_cleanup already processed the row
  no-bypass user: should_expire_trial -> (False, 'no_active_trial_subscription')
  bypass user: get_active_paid_subscription predicate -> True => trial worker returns early

=== 7. traffic_monitor: user drops from 10 GB to 0 between ticks
  messages over 7 ticks (5 min each): ['traffic_notified_8gb', '…5gb', '…3gb', '…1gb', '…500mb', '…0']
  send raised -> flags now: {'traffic_notified_8gb': True}

=== 8. trial send_trial_notification on a transient Telegram error
  result: (False, 'failed_permanently') -> caller sets trial_notif_24h_sent
```
