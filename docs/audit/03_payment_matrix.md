# Фаза 2. Матрица платежей

- Дата: 2026-09-13
- База: `refactor/audit-2026-09` (`fa0c2774`) + коммиты ниже.
- Запрос владельца: «Самое главное — корректность платежей… Покрой тестами корректно, всё досконально перепроверь, алерты админу обязательно». Пример бага, которого он боится: подписка до 20 окт., купил месяц, а в панели по-прежнему 20 окт.
- Тест: `tests/services/test_payment_matrix.py` — **1201 ячейка, ~9 с** (`pytest tests/services/test_payment_matrix.py`). На 2026-09-14 с §2.1b («Только обход» × состояние) и слитыми правками: 1409 ячеек, 1293 passed, 116 xfailed (strict), ~12 с.
- Итог прогона: **1074 passed, 127 xfailed (strict)**. Все ячейки с включённым флагом проходят. Полный набор тестов (перепроверено ревью 04 на `d622c538`): 2 641 passed, 139 xfailed; ruff — 0 ошибок. Хеши коммитов ниже — после ребейза в `refactor/audit-2026-09`.

## 1. Как устроен тест

**Что настоящее.** Код каждой точки входа вплоть до клиента панели:

- `database.subscriptions.grant_access` — сам код, а не контрактный фейк харнеса;
- `finalize_purchase`, `finalize_balance_purchase`, выдача админом, подарок, автопродление, триал, `grant_outbox`;
- `confirmation`;
- `provisioning`: `enqueue`, `run_now`, `apply`, тик воркера;
- legacy-слой Remnawave: `purchase_flow`, `remnawave_bypass`, `remnawave_service`.

**Что подменено.**

- asyncpg — `MatrixConn`. Держит одну строку `subscriptions`, pending-покупку, подарок, `payments` и баланс, с откатом транзакции **по соединению**. Запись без транзакции автокоммитится, как в Postgres. Именно так поймана M-RENEW-OUTSIDE-TX.
- Панель — legacy `payment_core_harness.FakePanel` с выключателем «панель лежит» и outbox `tests/fakes/panel.FakePanel`.
- Таблица outbox — `FakeJobs`.
- Telegram.

**Измерения.**

| Измерение | Значения |
|---|---|
| Точка входа | вебхук Platega / WATA / CryptoBot / WATA-reconciler, Telegram-карта, Telegram Stars, баланс, автопродление, выдача админом, подарок (`/start gift_…`), бонус админа (дни / ГБ), триал, пакет трафика, пополнение баланса |
| Тариф | basic/plus × 30/90/180/365, combo_basic/combo_plus × 30/90/180/365/730, пакеты 15/50/200 (стандарт) и 300/8000 (расширенные), legacy `biz_team` (только автопродление) |
| Состояние | `new`; `active` (premium T+10д, bypass 3 ГБ); `expired` (T−5д, bypass 0); `bypass_only` (bypass 5 ГБ, premium нет); `trial` (T+2д, bypass 500 МБ) |
| Флаг | `USE_NEW_PROVISIONING` off / on, включена **только** точка входа ячейки |

**Одна функция проверки `check()` на каждую ячейку** (правила владельца, SCOPE.md):

- **Дата в БД.** `subscriptions.expires_at == max(now, current_expires_at) + period`, где оплаченный период — **календарные месяцы** (решение владельца 2026-09-14: 20 окт. + 1 мес. = 20 ноя., 31 янв. + 1 мес. = 28/29 фев.; в матрице — независимый оракул `Months`, пример владельца — `test_owner_example_paid_month_is_a_calendar_month`), а выдача днями (админ, триал) — дни. Текущая дата учитывается, только если подписка действительно активна; bypass-only и истёкшая начинаются от now.
- **Дата в панели.** `expireAt` в панели равен дате в БД **с точностью до секунды**. Legacy PATCH отбрасывает доли секунды, outbox округляет вверх.
- **Bypass.** Лимит = было + ожидаемое:
  - basic/plus: +10 ГБ;
  - combo: только ГБ combo;
  - пакет: +N;
  - выдача днями: 0;
  - триал: 500 МБ.

  Premium не трогаем, если дни не положены.
- **Платежи.** Ровно одна закоммиченная строка `payments`, если были деньги; 0 для выдач.
- **Поля подписки.** `subscription_type`, `is_combo`, `is_bypass_only`.
- **Флаг on.** Ни один legacy-писатель панели не вызван.

## 2. Матрица (happy path)

✅N — прошли N ячеек. `xfail ID` — известный legacy-баг (strict xfail, см. §4). «—» — сочетания нет.

### 2.1 Покупки

Колонки — группы тарифов по режиму флага. Вебхук Platega — полный набор тарифов. WATA, CryptoBot, WATA-reconciler и Stars — сокращённый набор: basic_30, plus_365, combo_basic_30, combo_plus_90.

| Вход | Состояние | basic/plus off | basic/plus on | combo off | combo on | пакеты off | пакеты on |
|---|---|---|---|---|---|---|---|
| platega | new / active / expired / bypass_only / trial | ✅8 | ✅8 | ✅10 | ✅10 | ✅5 | ✅5 |
| wata, cryptobot, wata_reconciler | все 5 | ✅2 | ✅2 | ✅2 | ✅2 | — | — |
| telegram (карта) | new | xfail T0-TG-NEW-20 | ✅8 | xfail T0-TG-COMBO-NEW | ✅10 | ✅5 | ✅5 |
| telegram (карта) | active / expired / bypass_only / trial | ✅8 | ✅8 | ✅10 | ✅10 | ✅5 | ✅5 |
| stars | new | xfail T0-TG-NEW-20 | ✅2 | xfail T0-TG-COMBO-NEW | ✅2 | ✅5 | ✅5 |
| stars | active / expired / bypass_only / trial | ✅2 | ✅2 | ✅2 | ✅2 | ✅5 | ✅5 |
| баланс | new | xfail T0-BAL-NEW-20 | ✅8 | xfail T0-BAL-COMBO-NEW | ✅10 | — | — |
| баланс | active / expired / bypass_only / trial | ✅8 | ✅8 | xfail T0-BAL-COMBO-RENEW | ✅10 | — | — |

Пакеты трафика с баланса бот не продаёт, поэтому этих ячеек нет.

### 2.1b Покупка ГБ с экрана «🌐 Только обход блокировок» (`bypass_{N}gb`) × состояние

Правило владельца 2026-09-14 (SCOPE): нет активной premium-подписки и триал не использован → **ровно купленные ГБ + 3 дня premium в подарок** (один раз, без 500 МБ триала, при любом флаге `trial`); иначе только ГБ, premium не трогается. Флаг `trial` в ячейках выключен — подарок от него не зависит. Состояние `trial_used` — строки нет, триал взят давно.

| Вход | Пакет | Подарок (`new`, `expired`, `bypass_only`) | Только ГБ (`active`, `trial`, `trial_used`) | off | on |
|---|---|---|---|---|---|
| platega, wata, wata_reconciler, telegram (карта) | 15, 300 ГБ | premium = now + 3 дня (БД = панель до секунды), bypass +N, `source='trial'`, `trial_used_at` | premium и дата в БД не меняются, bypass +N, строка bypass-only (или прежняя активная), `trial_used_at` не меняется | ✅48 | ✅48 |

| Проверка | off | on |
|---|---|---|
| Повтор той же покупки (platega, telegram): второго подарка и вторых ГБ нет | ✅2 | ✅2 |
| После подарка — Basic 30 / Plus 90 / Combo basic 30: подарок идёт → от конца подарка, закончился (строка bypass-only) → от now; календарные месяцы; ГБ тарифа к остатку; `source='payment'` | ✅6 | ✅6 |
| Покупка на bypass-only строке с оставшимся ключом закончившегося premium (M-BYPASS-STALE-KEY) — от now, никогда от заглушки 10 лет | ✅3 | ✅3 |

Весь цикл на настоящем PostgreSQL — `tests/e2e/test_17_bypass_only_gift.py`.

### 2.2 Автопродление

Воркер выбирает только активные подписки, поэтому состояния — `active` и `trial`.

| Состояние | basic off | plus off | basic/plus on | combo off | combo on | biz_team off | biz_team on |
|---|---|---|---|---|---|---|---|
| active | ✅4 | xfail T0-AUTORENEW-PLUS | ✅8 | xfail T0-AUTORENEW-COMBO | ✅8 | ✅1 | ✅1 |
| trial | ✅4 | xfail T0-AUTORENEW-PLUS | ✅8 | xfail T0-AUTORENEW-COMBO | ✅8 | ✅1 | ✅1 |

### 2.3 Выдачи без денег

| Вход | Состояние | off | on |
|---|---|---|---|
| админ, 7 дней (basic, plus) | все 5 | xfail T0-ADMIN-GB | ✅2 |
| подарок basic/plus × 4 периода | new | xfail T0-GIFT-20 | ✅8 |
| подарок basic/plus × 4 периода | active / expired / bypass_only / trial | ✅8 | ✅8 |
| бонус, 7 дней | new | xfail M-BONUS-DAYS-GB | ✅1 |
| бонус, 7 дней | active / expired / bypass_only / trial | ✅1 | ✅1 |
| бонус, 10 ГБ | все 5 | ✅1 | ✅1 |
| триал | new | ✅1 | ✅1 |
| триал | expired / bypass_only | xfail M-TRIAL-NO-MB | ✅1 |

### 2.4 Кроме happy path

| Раздел | Что проверяется | off | on |
|---|---|---|---|
| **Пример владельца.** Панель лежит, пока продлевает активный юзер (`test_owner_example_renewal_while_panel_down`) | 7 точек входа: Platega, Telegram, баланс, автопродление, админ, подарок, бонус. Фаза 1: БД продлена ровно один раз, биллинг ровно один раз, панель не тронута, **forced-алерт**, строка `payment_errors`. Фаза 2: панель вернулась → панель = БД = старая дата + период, ГБ ровно один раз (legacy — или forced-алерт о недоставленных ГБ). У вебхука повтор отвечает `already_processed` | ✅7 | ✅7 |
| Новая покупка при лежащей панели | Platega, Telegram, баланс: деньги не потеряны, forced-алерт | ✅3 | ✅3 |
| Повтор того же вебхука или того же `successful_payment` | 4 провайдера + Telegram + Stars × basic/combo × new/active/expired: ничего не меняется, алерта нет | ✅36 | ✅36 |
| Повтор кода подарка, повтор триала | начисление один раз | ✅3 | ✅3 |
| Повтор прогона бонуса | начисление один раз | xfail M-BONUS-NO-KEY ×2 | ✅2 |
| Недоплата по вебхуку (4 провайдера), недоплата в Telegram | отказ + forced-алерт, ничего не выдано | ✅5 | ✅5 |
| Допуск / точная сумма / переплата (комиссия) | принято | ✅3 | ✅3 |
| Чужой провайдер (3) | `provider_mismatch` + forced-алерт + `payment_errors` | ✅3 | — |
| Пополнение баланса (3 провайдера × комиссия / точно / в допуске / недоплата) | зачисляется ожидаемая сумма, недоплата отклоняется с алертом | ✅12 | ✅12 |
| Покрытие алертов | см. §5 | ✅ | ✅ |

## 3. Поиск бага владельца: «активный продлил, а premium в панели не продлён»

Если все вызовы проходят, расхождения нет ни на одном пути ни в одном режиме: 1074 ячейки happy path, дата в панели = дате в БД. **Баг возникает, когда вызов в панель падает.** Такие пути разобраны ниже.

| Путь продления (флаг off) | Было | Стало |
|---|---|---|
| Вебхук, `finalize_purchase` | `grant_access(conn=None)` продлевал на **своём автокоммит-соединении** и синхронизировал панель inline. При сбое панели транзакция биллинга откатывалась, продление оставалось, провайдер ретраил → **продление второй раз**. Если панель лежала долго, каждый ретрай прибавлял ещё период | Продление в транзакции биллинга, синхронизация после commit. Сбой → `remnawave_sync_failed` → юзер уведомлён, ГБ доставлены → 5xx. Premium догоняет фоновый re-sync (M-RENEW-OUTSIDE-TX, M-RENEW-SYNC) |
| Telegram, `finalize_purchase` | То же, но Telegram не ретраит: **деньги списаны, строки `payments` нет, pending не оплачен, в БД дата продлена, в панели нет**, алерта нет (только запись в аудит-логе) | Биллинг закоммичен, forced-алерт + re-sync (M-RENEW-OUTSIDE-TX, M-RENEW-SYNC) |
| Баланс, `finalize_balance_purchase` | Синхронизация после commit падала → только `logger.critical` | Forced-алерт + `payment_errors` + re-sync (M-RENEW-SYNC) |
| Автопродление | Синхронизация inline в транзакции батча; сбой ловил обработчик юзера → батч коммитил **списание и продление без строки `payments`** + алерт с кулдауном | Синхронизация в Phase B после commit + forced-алерт + re-sync (M-AUTORENEW-SYNC-IN-TX, M-RENEW-SYNC) |
| Выдача админом (дни / минуты), подарок (починен в `4d6a0963`) | Синхронизация после commit падала → только лог | Forced-алерт + re-sync (M-RENEW-SYNC) |
| Промо, игра, бонус, ключ из рассылки, триал, выдача с кастомным сроком (standalone `grant_access`) | inline-синхронизация падала: БД продлена, панель нет, только лог | Forced-алерт + re-sync (M-RENEW-SYNC, центральная обёртка) |
| Разовый платёж Platega | = вебхук | = вебхук |
| Все пути под флагом (T8–T16) | outbox: задача `pending`, forced-алерт, воркер доводит | без изменений, проверено матрицей |

**Почему нужен фоновый re-sync.** Ретрай вебхука после commit упирается в `lookup_pending_purchase` → `already_processed` и панель больше не трогает. Поэтому `purchase_flow.sync_renewal_to_remnawave` при сбое сама делает одну пересинхронизацию в фоне:

- попытки через 60 с, 5 мин и 15 мин;
- дата каждый раз перечитывается из БД, поэтому панель никогда не ставится ниже или выше БД; тест `test_legacy_resync_never_lowers_a_later_db_date`;
- если все попытки неудачны — второй forced-алерт;
- рестарт процесса теряет re-sync, но первый алерт уже ушёл.

## 4. Найденные баги

| ID | Серьёзность | Где | Суть | Статус |
|---|---|---|---|---|
| **M-RENEW-OUTSIDE-TX** | высокая (двойное продление; «деньги списаны — записи нет») | `database/subscriptions.py:5406` (`_finalize_purchase_locked`, вызов `grant_access`), `:4959` | Продление через `finalize_purchase` шло вне транзакции биллинга (см. §3) | исправлено `213e2620` |
| **M-AUTORENEW-SYNC-IN-TX** | высокая (списание без строки `payments`) | `auto_renewal.py:421` | Legacy `grant_access` без `_caller_holds_transaction` → HTTP в транзакции, сбой после списания | исправлено `213e2620` |
| **M-RENEW-SYNC** (пример владельца) | высокая | `app/services/purchase_flow.py:347` + места вызова: `database/admin.py:2668,3019,3568,5092`, `database/subscriptions.py:2670,5590`, `auto_renewal.py:586` | При сбое синхронизации premium БД продлена, а панель нет, без алерта и без повтора | исправлено `f0611cc5`: `payment_errors`, forced-алерт, фоновый re-sync |
| **M-CONFIRM-SYNC-5XX** | средняя (ГБ и уведомление терялись) | `app/services/payments/confirmation.py:250` | При `remnawave_sync_failed` confirmation отвечал 5xx **до** уведомления и доставки ГБ, а ретрай до них не доходил | исправлено `f0611cc5` |
| **M-RENEW-GB-SILENT** | средняя (ГБ молча не начислялись) | `app/services/remnawave_service.py:235–318` | `renew_remnawave_user` игнорировал `None` от `update_user` и логировал `REMNAWAVE_RENEWED`; сбой пересоздания не был виден | исправлено `5395293a` |
| Пробелы в алертах (§5) | средняя | `confirmation.py`, `payments_messages.py`, `payments_callbacks.py`, `auto_renewal.py` | см. §5 | исправлено `f0611cc5`, `078e6596`, `213e2620` |
| T0-TG-NEW-20, T0-TG-COMBO-NEW, T0-BAL-NEW-20, T0-BAL-COMBO-NEW, T0-BAL-COMBO-RENEW, T0-ADMIN-GB, T0-GIFT-20, T0-AUTORENEW-PLUS, T0-AUTORENEW-COMBO | средняя (лишние или недостающие ГБ, неверный тариф) | legacy-пути, список T0 в `02_payment_core_plan.md` | Известны по T0 | **остаются xfail**; чинит включение флага своей точки входа (T9, T10, T12–T14) |
| **M-BONUS-DAYS-GB** (новый) | низкая | `app/handlers/admin/bonus.py` (legacy `grant_access`) | Бонус днями новому юзеру создаёт bypass на 10 ГБ; правило — 0 ГБ | xfail, чинит флаг `grants` (T16) |
| **M-TRIAL-NO-MB** (новый) | низкая | `purchase_flow.provision_subscription` (bypass уже есть → пропуск) | Триал юзеру с существующей bypass-сущностью (истёкшему или bypass-only) даёт 0 МБ | xfail, чинит флаг `trial` (T15) |
| **M-BONUS-NO-KEY** (новый) | низкая | legacy-бонус | Нет ключа идемпотентности: повторный прогон выдаёт второй раз | xfail, чинит флаг `grants` |
| **M-BYPASS-GIFT-FLAG** | средняя (обещание на экране не выполнялось) | `confirmation._handle_traffic_pack_confirmation`, экран `buy_bypass_only` | Покупка ГБ с «Только обход» без подписки давала 3 дня только при флаге `trial` (прод — off), и с лишними 500 МБ; экран обещал подарок по правилу кнопки меню (bypass-only строку считал подпиской) | исправлено: подарок при любом флаге, один раз, 0 байт, экран = правило выдачи (§2.1b) |
| **M-BYPASS-CACHE** | высокая (флаг on: через 3 дня отключался бы bypass купленных ГБ) | `finalize_purchase`, ветка пакета | Строку bypass-only создавала confirmation уже после `run_now` задачи пакета: у нового юзера кеш bypass (`remnawave_uuid/id`) не записывался (E2E-CACHE на этом пути) → при истечении строка `expired` и отключение bypass | исправлено: строка создаётся в транзакции биллинга (`ensure_bypass_only_subscription(conn=…)`) |
| **M-BYPASS-STALE-KEY** | высокая (деньги без выдачи / premium на ~10 лет) | `ensure_bypass_only_subscription`, `grant_access` (ветки продления) | ГБ куплены, когда premium уже кончился, а очистка ещё не прошла: строка становилась bypass-only с заглушкой на 10 лет **и старым ключом**. Дальше Basic → «Invalid renewal» (PERMANENT, оплата не зачтена), Plus → заглушка + 3 мес.; очистка строку больше не выбирала. Такие строки могли остаться в проде | исправлено: `ensure` снимает ключ, как воркеры истечения; `grant_access` на bypass-only строке считает от now и сбрасывает `is_bypass_only` во всех ветках (§2.1b, e2e) |

**Почему legacy-баги с ГБ не чиним в legacy.** Их цель уже реализована в outbox (T8–T16) и доказана ячейками с флагом on. Точечная починка legacy продублировала бы выдачу ГБ во втором месте. Выход — выкатка флагов по runbook.

## 5. Покрытие алертов (сбои на денежных путях)

«forced» — `admin_alerts.send_alert(force=True)`, мимо кулдауна. У `purchase_flow` бюджет: 5 алертов за 5 минут, дальше кулдаун категории; все случаи всё равно пишутся в `payment_errors`.

| Ветка сбоя | Алерт | `payment_errors` | Тест |
|---|---|---|---|
| Вебхук: purchase не найден (`lookup`) | forced (WATA — свой forced) | ✅ | `test_payment_core_t6` |
| Вебхук: purchase чужого юзера или пропал к моменту confirm | **forced (новое)** | ✅ | `test_alert_confirmation_purchase_missing_is_forced` |
| Вебхук: провайдер не совпал | forced | ✅ | `test_provider_mismatch_is_rejected_with_forced_alert` |
| Вебхук: недоплата / несовпадение суммы | forced (`admin_notifications`) | — | `test_underpayment_is_rejected_with_forced_alert` |
| Вебхук: постоянная ошибка финализации | forced (PERMANENT) | — | `test_alert_webhook_permanent_error_is_forced` |
| Вебхук: транзиентная ошибка (БД, сеть) | алерт с кулдауном + 5xx, провайдер ретраит; осознанно не forced | — | `test_payment_core_t6` |
| Вебхук: ГБ не доставлены (legacy) | forced | — | `test_owner_example_…[platega-off]` |
| Вебхук: неожиданная ошибка доставки или уведомления после commit | **forced (новое)** | — | `test_alert_delivery_unexpected_error_after_commit_is_forced` |
| Продление: синхронизация premium упала (все legacy-пути) | **forced (новое)** + re-sync | ✅ `renewal_sync` | `test_owner_example_renewal_while_panel_down[*-off]` |
| Продление: re-sync сдался | **forced (новое)** | ✅ | `test_alert_legacy_resync_that_gives_up_is_forced_again` |
| Legacy-доливка ГБ (`renew_remnawave_user`) не прошла | **forced (новое)** | ✅ `bypass_topup` | `test_alert_legacy_bypass_topup_not_applied_is_forced` |
| Telegram: деньги списаны, purchase нет или payload невалиден | **forced (новое)** | ✅ | `test_alert_telegram_paid_but_purchase_missing_is_forced` |
| Telegram: сумма отклонена | **forced (новое)** | ✅ | `test_telegram_underpayment_is_rejected_with_forced_alert` |
| Telegram: финализация упала или неожиданная ошибка | **forced (новое)** | ✅ | `test_alert_telegram_finalization_failure_is_forced` |
| Telegram: ветка подарка или пакета трафика упала | **forced (новое)** | ✅ | покрыто кодом, как ветки выше |
| Баланс: неожиданная ошибка (кроме пользовательского `ValueError`) | **forced (новое)** | — | `test_alert_balance_unexpected_error_is_forced`, `test_no_alert_for_user_side_insufficient_balance` |
| Автопродление: ошибка по юзеру | **forced (было с кулдауном)** | — | `test_alert_autorenew_per_user_error_is_forced` |
| Автопродление: не удался возврат средств | forced | — | существующий код |
| Outbox: первый сбой, `dead`, конфликт | forced в пределах бюджета, иначе дайджест воркера | ✅ | `test_provisioning_alerts`, матрица (флаг on) |
| Триал после покупки упал | forced | — | `test_payment_core_t15` |
| WATA: сирота, возврат, отказ, 401 | forced / кулдаун | — | `test_wata_wave1` |
| Колбэк рекуррента Platega | forced | ✅ | `test_platega_wave1` |

## 6. Остаточные риски (флаг off)

- **R-TG-NEW.** Новая покупка через Telegram, когда панель лежит. Telegram деньги взял, выдачи нет, авто-повтора нет (Telegram не ретраит). Теперь уходит forced-алерт, дальше вручную. Чинит флаг `telegram`: `pending_activation` + задача.
- **R-WEBHOOK-NEW-NO-RETRY.** Новая выдача по вебхуку, когда панель лежит. `grant_access` бросает общее `Exception` → `status=error` → 200, провайдер не ретраит. Pending остаётся pending: деньги не потеряны, покупку можно повторить. Уходит forced PERMANENT-алерт. WATA дожимает reconciler, Platega и CryptoBot — вручную. Чинит флаг `webhook`.
- **M-PHASE1-DRIFT.** Legacy новая выдача. `expireAt` в панели ставится от `now` фазы 1, дата в БД — от `now` внутри `grant_access`. Панель раньше БД на время HTTP фазы 1, секунды. Чинит флаг: outbox ставит дату из БД.
- **R-RESYNC-RESTART.** Фоновый re-sync живёт в процессе. Рестарт между сбоем и повтором теряет его, но первый forced-алерт уже ушёл.
- **R-EXPIRY-RACE.** Продление ровно в момент истечения подписки. Предпроверка видит активную подписку, а внутри транзакции она уже истекла → INVARIANT RuntimeError → вебхук 5xx и ретрай. Для Telegram — forced-алерт.
- **`provisioning.py` не менялся** (его правит другой агент). Все ячейки с флагом on проходят без правок. Нужных изменений в `provisioning.py` матрица не выявила.

## 7. Коммиты

| Коммит | Что |
|---|---|
| `213e2620` | fix: legacy-продление внутри транзакции биллинга (`finalize_purchase`, автопродление) |
| `f0611cc5` | fix: сбой синхронизации premium → `payment_errors` + forced-алерт + фоновый re-sync; confirmation уведомляет и доставляет ГБ до 5xx; алерты confirmation |
| `5395293a` | fix: legacy-доливка ГБ не пишет «успех» на проваленный PATCH; алерт |
| `078e6596` | fix: forced-алерты на денежных сбоях Telegram и баланса |
| `af7e3f21` | test: матрица платежей (1201 ячейка) |
