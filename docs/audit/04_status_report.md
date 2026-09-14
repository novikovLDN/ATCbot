# Фаза 4. Независимое ревью рефакторинга — статус-отчёт

- Дата: 2026-09-13
- База (прод): `cf8205fc` (= `origin/main` на момент создания ветки)
- Проверено: `refactor/audit-2026-09` @ `73a38011` — 105 коммитов команды: платёжное ядро, контроль доставки, матрица платежей, дашборд v3 + мобильная вёрстка
- Сверху — 3 коммита ревью с точечными фиксами: `a3d390a1`, `b7e0e682`, `ae1eca29`
- Контракт: `docs/audit/SCOPE.md`. Магазин, миграции и `.env*` ревью не трогало.
- Параллельно работают (здесь не оценивается и не дублируется): второй круг чистки мёртвого кода (удаляет доказанно мёртвое — ревью только перечисляет кандидатов, §6) и готовность к проду (путь обновления, diff env, runbook).
- Часовая сверка БД ↔ панель удалена по решению владельца (`98089fdc`), в отчёте не оценивается.

---

## 1. Коротко для владельца

**Что сделано хорошо**

- **Все дыры с деньгами из разведки закрыты.** Поддельный вебхук Lava невозможен: Lava удалена целиком, провайдер покупки проверяется. Подпись WATA проверяется строго: без ключа платёж не принимается, WATA повторит его позже. Пополнение баланса зачисляет ожидаемую сумму, а не присланную. Двойной вебхук больше не запускает двойную выдачу.
- **Новое платёжное ядро готово.** Все 8 точек выдачи (вебхук, Telegram, баланс, автопродление, админ, подарок, выдачи без покупки, триал) переведены на очередь выдачи. Правила ГБ собраны в одном каталоге тарифов. Повтор никогда не начисляет ГБ дважды, premium никогда не сокращается. После каждой выдачи бот перечитывает панель и шлёт алерт при расхождении.
- **Ваш пример «продлил месяц, а в панели старая дата» разобран на всех путях.** Матрица платежей (1 201 сценарий) нашла, что при сбое панели продление шло вне транзакции оплаты и могло засчитаться дважды или без записи о платеже. Это исправлено. Теперь каждый сбой синхронизации даёт принудительный алерт и автоматический повтор.
- **Тестов стало в 7,5 раза больше: 369 → 2 780.** До рефакторинга 46 тестов падали, сейчас не падает ни один. Линтер чистый. CI проверяет тесты, миграции на пустой базе, сборку фронта и Docker.
- **Выполнена первая чистка:** Lava, samopis, site_sync, рекуррент Platega, мёртвые модули и хэндлеры-сироты. Рекуррент оставлен только как страховка: алерт админу, ничего не выдаётся.

**Что важно понимать**

- **Новое ядро выключено** (`USE_NEW_PROVISIONING=off` по умолчанию). Пока флаг выключен, в проде работает старый путь, и его известные ошибки с ГБ остаются:
  - баланс basic даёт 20 ГБ вместо 10, combo 95/85 вместо 75, Telegram combo 150;
  - админ-выдача даёт ГБ, хотя по правилу должна давать 0;
  - автопродление combo идёт по цене basic.

  139 тестов прямо фиксируют эти ошибки (§5). Требования по ГБ и ценам **выполняются только после включения флага** по RUNBOOK.
- **Кода не стало меньше.** Удалено около 10,6 тыс. строк Python, добавлено около 11 тыс., итого +1 %. Новое ядро пока живёт рядом со старым путём, плюс метрики дашборда и CI. Реальное сокращение даст удаление старых путей после включения флага (T18) и второй круг чистки (уже идёт).

**Что нашло ревью**

- **1 критическая ошибка (P0).** Она старая, была и до рефакторинга, но рефакторинг её не закрыл. Если промокод закончился между выставлением счёта и оплатой, оплаченная покупка **молча теряется**: провайдеру уходит «уже обработано», алерта нет. Нужно решение, см. §4.
- **9 серьёзных (P1).**
  - **Свежая регрессия:** фикс матрицы `213e2620` в редком случае даёт автопродлению **списать баланс без продления и без возврата** (алерт приходит). Если подписка истекает прямо во время прогона воркера, продление падает, а списание всё равно коммитится. Это первое, что стоит починить.
  - Не закрыты:
    - при таймауте вебхука нет Telegram-алерта;
    - двойное списание с баланса при двойном тапе, если FSM хранится в Redis;
    - вход в дашборд обходит блокировку параллельными запросами;
    - отключённая в панели premium-сущность не включается повтором;
    - «починка трафика» в дашборде может переначислить ГБ.
  - Исправлены: premium мог продлиться примерно на 10 лет из-за даты-заглушки у bypass-only; проверка пароля в дашборде замораживала весь бот (оба — ревью); незавершённая синхронизация продления без повтора (командой).
- **Около 29 мелких (P2):** дубли алертов, соединение с БД во время HTTP, устойчивость очереди, мелочи дашборда. Плюс расхождения документации.

**Что делать дальше** (подробно в §10)

1. Починить регрессию автопродления, P0 с промокодом и алерты вебхуков.
2. Закрыть двойное списание с баланса и вход в дашборд.
3. Включать флаг по точкам по RUNBOOK.
4. Через 1–2 недели удалить старые пути (T18).

---

## 2. Требования владельца vs код

Легенда: ✅ сделано и доказано тестом · ⚠️ частично или только под флагом · ❌ не сделано.
«Флаг» = `USE_NEW_PROVISIONING=on` для соответствующей точки (`app/services/provisioning_flags.py`). По умолчанию `off`.
«Матрица» = `tests/services/test_payment_matrix.py` (1 201 ячейка: точка входа × тариф × состояние × флаг), описание — `docs/audit/03_payment_matrix.md`.

| # | Требование (SCOPE.md) | Статус | Где в коде | Чем доказано |
|---|---|---|---|---|
| 1 | Basic/Plus = premium на срок + 10 ГБ один раз | ⚠️ под флагом | `app/services/tariffs.py:100-107` (`for_purchase`) | матрица (все ячейки `on` ✅), `test_tariffs.py::test_basic_30`, `t8::test_webhook_basic_30d_new_adds_10gb_once_and_30d_premium`, `t10::test_balance_basic_30d_new_adds_10gb_once`. Без флага: новая покупка с баланса и через Telegram даёт 20 ГБ (xfail) |
| 2 | Combo Basic/Plus — отдельные тарифы, ГБ из таблицы combo (30 д → 75 ГБ), без базовых 10 | ⚠️ под флагом | `tariffs.py:67-82` (`tariff_key`), `:108-114` | матрица, `test_tariffs.py::test_combo_has_no_extra_10gb`, `t8::test_webhook_combo_basic_30d_new_adds_75gb_without_extra_10`, `t9::test_telegram_combo_basic_30d_new_adds_75gb_exactly_once`. Без флага: баланс 95/85, Telegram 150 (xfail) |
| 3 | ГБ прибавляются к остатку | ✅ под флагом | `app/services/provisioning.py:461-540` (CAS: план `base → target = base + N`, сохраняется до PATCH) | `test_provisioning_apply.py::test_basic_with_existing_bypass_3gb_becomes_13gb`, `::test_crash_after_patch_then_retry_adds_exactly_10gb`, `::test_bypass_plan_is_saved_before_the_patch`; матрица (`bypass = было + ожидаемое` в каждой ячейке) |
| 4 | Trial = 500 МБ | ✅ под флагом | `tariffs.py:136-140` (`for_trial`, `config.TRIAL_BYPASS_MB`). Остаток с 5 ГБ в `/white` исправлен ревью: `app/handlers/traffic.py:495`, `a3d390a1` | `t15::test_trial_entitlement_is_500mb_and_3_days`, `tests/handlers/test_traffic_white_trial_bypass.py`. Без флага: триал пользователю с уже существующей bypass-сущностью даёт 0 МБ (матрица, xfail M-TRIAL-NO-MB) |
| 5 | Выдача днями без покупки (админ, игра, промо, бонус) = 0 ГБ | ⚠️ под флагом | `tariffs.py:143-166` (`for_grant`, `for_grant_duration`), `app/services/grant_outbox.py:52` | `t13::test_admin_grant_7d_new_user_premium_7d_zero_gb`, `t16::test_game_bowling_strike_7_days_premium_only`, `::test_promo_days_premium_only_key_from_redemption`, `::test_bonus_days_premium_only_keyed_by_run`. Без флага: админ 20/10 ГБ, бонус новому юзеру 10 ГБ (xfail T0-ADMIN-GB, M-BONUS-DAYS-GB) |
| 6 | Автопродление по реальному тарифу: combo → цена и ГБ combo | ⚠️ под флагом; ⚠️ P1-9 без флага | `auto_renewal.py:62-100` (`_outbox_renewal_plan`), `tariffs.renewal_price_rub`; путь под флагом — отдельный savepoint на пользователя (`:122`) | `t12::test_on_combo_charges_combo_price_and_adds_combo_gb`, `::test_combo_basic_30d_is_329_in_the_current_config`, матрица §2.2. Без флага: combo по цене basic и +10 ГБ, plus превращается в basic (xfail); списание без продления при истечении во время прогона (P1-9) |
| 7 | Бизнес-тарифы не продаются; легаси `biz_*` продлевается как Plus за 199 ₽ | ✅ | `tariffs.py:115-116` (`for_purchase` бросает ошибку для biz), `:85-90` (`legacy_tariff_key` → plus). Цена = `config.TARIFFS["basic"][30]` = 199 ₽. Вход «Для бизнеса» удалён ещё в `cfbf8cfe`; каталог — замкнутая сирота (`docs/screens/candidates.md` A2) | `t12::test_on_legacy_biz_renews_as_plus_at_todays_price`, `test_tariffs.py::test_biz_not_purchasable`, матрица (`biz_team` off/on ✅) |
| 8 | Lava удалена полностью | ✅ (косметика ⚠️) | `4ea51867`: нет `lava_service.py`, нет роута `/webhooks/lava`, нет конфигов | grep по коду: 0 вызовов. Отдельного теста «404 на /webhooks/lava» нет. Остались ключи i18n `payment.lava*` (подпись WATA-кнопки «СБП резерв») и надпись «💳 Карта (Lava)» на WATA-кнопке магазина (`steam_purchase.py:191`, `telegram_stars_purchase.py:303`, магазин 🔒) |
| 9 | Рекуррент Platega удалён, осталась только страховка-алерт | ✅ | `platega_service.py:505-590` (`_handle_subscription_callback`; авторизация до разбора — `:516`), роуты `app/api/payment_webhook.py:209-233` | `test_platega_wave1.py::test_recurring_code_is_gone`, `::test_subscription_charge_callback_is_alert_only`, `::test_subscription_callback_bad_auth_is_rejected`, `::test_http_routes_answer_200_and_alert` |
| 10 | Возвраты и чарджбэки — только алерт, доступ не отзываем | ✅ | Platega `CHARGEBACKED`: `platega_service.py:434-460`. WATA `Refund`: `wata_service.py:542-570`, `:709-711` | `test_platega_wave1.py::test_chargeback_alerts_admin_and_does_not_revoke`, `test_wata_wave1.py::test_refund_forced_alert_no_confirmation` |
| 11 | Нет двойных начислений (идемпотентность по `purchase_id`) | ⚠️ | Под флагом: `migrations/082_provisioning_jobs.sql:17` (`idempotency_key UNIQUE`), `database/provisioning_jobs.py` (`insert_job … ON CONFLICT`). Без флага: повтор вебхука по-прежнему ничего не меняет (матрица, 36 ячеек), но повтор бонуса выдаёт второй раз (xfail M-BONUS-NO-KEY) | `t8::test_duplicate_webhook_creates_no_second_job_and_no_gb_change`, `test_provisioning_jobs.py::test_insert_job_conflict_returns_existing_id`, `t14::test_same_code_twice_grants_once`, матрица «повтор вебхука / successful_payment». **Дыра:** двойной тап оплаты с баланса при FSM в Redis (P1-2) |
| 12 | **Любая** ошибочная транзакция → алерт админу в Telegram | ⚠️ | Сильно расширено матрицей (`078e6596`, `e1394002`, `f0611cc5`, `5395293a`): forced-алерт на каждом денежном сбое Telegram, баланса, автопродления, синхронизации продления, доливки ГБ; очередь выдачи (первая ошибка, `dead`, `conflict`, сводки); `not_found`, `provider_mismatch`, недоплата; отказы, возвраты и 401 WATA; страховка рекуррента | таблица покрытия алертов `docs/audit/03_payment_matrix.md` §5, `test_provisioning_alerts.py`. **Нет алерта:** P0-1 (промокод), P1-1 (таймаут или необработанное исключение вебхука — только `payment_errors`), P2-12 (переполнение бюджета legacy-проверок). **Обратная сторона:** на один сбой синхронизации приходит 2–3 алерта (P2-25 … P2-27) |
| 13 | Premium никогда не сокращается | ✅ под флагом | `provisioning.py:346-425`: цель = `max(premium_until, expires_at)`, округление секунд вверх, PATCH только вперёд. Фикс ревью `b7e0e682`: дата-заглушка bypass-only не становится целью | `test_provisioning_apply.py::test_premium_never_shortened`, `::test_premium_target_uses_later_subscription_expiry`, `::test_bypass_only_placeholder_expiry_is_not_a_premium_target`; старый путь — `test_legacy_resync_never_lowers_a_later_db_date` (матрица). Остаточный риск T4-1 (адопция при промахе поиска) задокументирован |
| 14 | Панель — источник правды; после каждой выдачи проверка панели, при расхождении алерт и повтор | ⚠️ | Под флагом: `provisioning.py:560-600` (`_verify_delivery` → `DeliveryMismatch` → повтор задачи). Без флага: отложенная проверка каждой выдачи `app/services/payments/verify_delivery.py` (хуки в баланс, Telegram, автопродление, подарок, админ-выдачу, а с `98089fdc` и в первую покупку по вебхуку) + фоновый re-sync продления (`purchase_flow.sync_renewal_to_remnawave`, `f0611cc5`) | `test_provisioning_verify.py`, `test_verify_delivery_legacy.py`, `test_delivery_check_hooks.py`, матрица `test_owner_example_renewal_while_panel_down` (7 точек × off/on). Недочёты: P1-5 (статус DISABLED повтором не чинится), P2-11, P2-12 |
| 15 | Защита вебхуков для VPN: fail-closed подпись, провайдер, сумма; магазин — как есть | ✅ | WATA `wata_service.py:488-501`, `:683-694`, `:784-813`. Platega `platega_service.py:357-371`. Провайдер `confirmation.py:548-631`. Таблица HTTP-кодов `payment_webhook.py:52` (`_STATUS_HTTP`) | `test_wata_wave1.py` (подпись, сумма, валюта), `test_platega_wave1.py::test_vpn_purchase_*`, `t6::test_status_map_covers_every_known_status`, `::test_provider_check_skips_shop_purchases`; матрица «недоплата», «чужой провайдер» |
| 16 | Spotify, оплаченный картой через Telegram, завершается как Spotify | ✅ | `404cd66e`, ветка в `process_successful_payment` | `t9::test_spotify_via_telegram_card_never_enters_the_outbox` |
| 17 | Магазин не тронут | ⚠️ | Касания:<br>• удаление Lava-кнопок и Lava-сирот — разрешено;<br>• `ad462908`, `ff5e4e0f` добавили в `spotify_purchase.py` параметр `provider` и принудительный алерт, из логов убраны учётные данные покупателя;<br>• `e1394002`: алерт магазина подавляет общий алерт Telegram (один алерт на сбой; ревью проверило, что VPN-алерты не подавляются).<br>Логика, цены и тексты не менялись, но это правка файлов магазина сверх «только Lava» — для сведения владельца | `t9::test_shop_purchase_via_telegram_payments_is_unchanged`, `t8::test_shop_purchase_under_flag_on_keeps_shop_branch`, `test_shop_order_not_lost.py` |
| 18 | Чистка B1–B5 и site_sync | ✅ | §6 | ruff 0, компиляция, import-smoke 234 теста |
| 19 | Второй круг чистки (B6/B7/B9/B10) | ⏳ в работе | параллельный агент | кандидаты перечислены в §6.2 |
| 20 | Shadow-режим (T17) | ❌ | `provisioning_flags.py:8-9`: `shadow` работает как `off` | — |
| 21 | Удаление старых путей (T18) | ❌ | по плану — через 1–2 недели после включения флага | — |
| 22 | Уведомления N-01 … N-07 | ✅ | `2cba0b11` | проверено ревью. У N-05 остался хвост: соединение с БД удерживается во время отправки (P2-4) |

---

## 3. Цифры

### 3.1. Общие (`cf8205fc..73a38011`, работа команды)

| Метрика | До (`cf8205fc`) | После (`73a38011`) |
|---|---|---|
| Коммитов в ветке | — | **105** (из них 2 merge) + 3 фикса ревью + этот отчёт |
| Изменено файлов | — | **410**: 161 добавлено, 138 изменено, 60 удалено, 51 перенесено |
| Строк | — | **+48 797 / −21 833** |
| Python-файлов (без тестов) | 238 | 238 (−20 удалено, +20 новых) |
| Строк Python (без тестов) | 97 069 | ≈ 98 084 (**+1 015, +1 %**); 98 099 с фиксами ревью |
| Строк тестов | 6 885 | 21 336 |
| Тестов собрано | 369 | **2 780** (2 784 с фиксами ревью) |
| Результат pytest | 323 passed / **46 failed** | **0 failed**. На HEAD ревью: 2 641 passed, 4 skipped, 139 xfailed (strict) |
| из них матрица платежей | — | 1 201 ячейка: 1 074 passed, 127 xfailed |
| `ruff check .` (набор проекта) | 5 ошибок (в т. ч. F821 `audit_subs.py:667`) | **0** |
| ruff F401/F841/F811 (кандидаты B10) | — | 275 (175 / 85 / 15) |
| vulture ≥ 60 % (без тестов и фронта) | 720 | 670 (замер на `9298fed6`) |
| Хэндлеры aiogram (декораторы `*router.message/callback_query/…`) | 504 | **464** (−40: сироты, Lava, samopis) |
| Роуты FastAPI | 154 | 163 (+9: metrics, panel, branding) |
| `grep -c "@router\."` (метрика из CLAUDE.md; смешивает оба типа) | 222 | 229 |
| Файлов миграций | 74 | 76 (+081, +082; правка 013 для пустой БД) |

### 3.2. По категориям

| Категория | Добавлено | Удалено | Нетто | Файлы (добавлено / изменено / удалено) |
|---|---:|---:|---:|---|
| Python-код приложения (без тестов и `scripts/ci`) | 10 951 | 10 563 | **+388** | 18 / 86 / 20 |
| Тесты | 16 734 | 2 423 | +14 311 | 48 / 13 / 6 |
| Документация | 6 636 | 755 | +5 881 | 41 / 5 / 16, плюс 51 перенос в `docs/archive/` |
| Фронт дашборда (без lock-файла) | 9 575 | 7 115 | +2 460 | 49 / 28 / 11 |
| Lock-файл `dashboard/package-lock.json` | 3 828 | 0 | +3 828 | 1 новый |
| Миграции | 104 | 5 | +99 | 2 / 1 / 0 |
| CI (`.github/` + `scripts/ci/`) | 926 | 131 | +795 | 2 / 2 / 0 |
| Прочее (json-переводы, `systemd/`, `pyproject.toml`, `.env.example`, `.dockerignore`) | 43 | 841 | −798 | 0 / 3 / 7 |
| **Итого** | **48 797** | **21 833** | **+26 964** | 161 / 138 / 60, 51 перенос |

### 3.3. Удалённые Python-модули (20 файлов, крупнейшие)

| Файл | Строк | Пакет |
|---|---:|---|
| `app/handlers/admin/migration.py` | 1 171 | B5 samopis |
| `handlers.py` (корень; `show_payment_method_selection` перенесён) | 1 104 | B2 |
| `scripts/migrate_samopis_to_remnawave.py` + `verify_samopis_migration.py` | 958 | B5 |
| `app/utils/audit.py` | 418 | B2 |
| `app/handlers/admin/reconcile.py` | 399 | B2 |
| `app/services/migration_broadcast.py` | 331 | B5 |
| `app/services/site_sync.py` + `workers/site_sync_worker.py` | 362 | site_sync |
| `lava_service.py` | 287 | B4 |
| `validate_language_content.py` | 215 | B2 |
| `vpn_utils.py`, `app/services/vpn/`, `app/api/subscription_proxy.py` | 351 | B5 |
| `app/utils/message_guard.py`, `app/core/i18n/`, `callbacks/admin_callbacks.py`, `common/decorators.py` | ≈ 250 | B2 |

Плюс удалены 16 отчётов `load_tests/results/`, 6 json-переводов, `systemd/vpn-api.service`, 6 тестов удалённых модулей.

### 3.4. Крупнейшие файлы (god-модули)

| Файл | Было | Стало |
|---|---:|---:|
| `database/subscriptions.py` | 5 162 | **5 653** (+491) |
| `database/admin.py` | 5 171 | **5 469** (+298) |
| `app/handlers/admin/broadcast.py` | 2 584 | 2 584 |
| `app/handlers/admin/access.py` | 2 292 | 2 285 |
| `app/handlers/callbacks/payments_callbacks.py` | 2 600 | 2 269 (−331) |
| `database/users.py` | 2 054 | 2 263 (+209) |
| `app/handlers/callbacks/navigation.py` (внутри магазин 🔒) | 2 301 | 2 239 |
| `app/handlers/admin/stats.py` | 2 018 | 1 870 |
| `app/services/payments/confirmation.py` | — | 1 292 |
| `app/services/provisioning.py` (новый) | — | 1 083 |

---

## 4. Найденные баги

Строки указаны по HEAD ревью. «Старый» — ошибка была и на `cf8205fc`, рефакторинг её не внёс. «Регрессия» — внесена работой в ветке.

### P0 — деньги или доступ теряются

**P0-1. Оплаченная VPN-покупка с промокодом молча теряется** (старый, не исправлен, нужно решение владельца)

- **Где:**
  - `app/services/payments/confirmation.py:382-499` — ветка `except ValueError`;
  - `database/subscriptions.py:3195-3237` — `_consume_promo_in_transaction`;
  - вызовы в `finalize_purchase`: `:4721`, `:5160`, `:5539`.
- **Сценарий:**
  1. Промокод записан в pending-покупку при выставлении счёта.
  2. Пока пользователь платит, промокод исчерпан или истёк.
  3. Finalize бросает `ValueError("PROMO_EXHAUSTED")`, транзакция откатывается, строка остаётся `pending`.
  4. `process_confirmed_payment` считает любой ValueError, кроме расхождения суммы, дублем и возвращает `already_processed`.
- **Итог:**
  - провайдер получает 200 и не повторяет;
  - нет ни алерта, ни `payment_errors`;
  - reconciler WATA, опрос и кнопка «Проверить» каждый раз упираются в то же самое.
- **Та же ловушка** у любого другого ValueError внутри finalize: неизвестный период, `_to_db_utc` с не-UTC датой, нет участка фермы.
- **Покрытие:** матрица платежей промокоды не варьирует, поэтому этот путь не покрыт.
- **Предложение:**
  - finalize бросает отдельное исключение `PurchaseAlreadyProcessed`, идемпотентным считается только оно;
  - любой другой ValueError → PERMANENT-алерт + `payment_errors`;
  - по промокоду владелец решает: выдать без скидки или резервировать промокод при выставлении счёта.

### P1 — реальные ошибки, вероятные в проде

| # | Статус | Где | Сценарий | Предложение |
|---|---|---|---|---|
| P1-9 | ❌ **регрессия `213e2620`** | `auto_renewal.py:213` (`now` один на весь прогон), `:380-403` (списание → `grant_access(conn, _caller_holds_transaction=True)`), `:521-532` (per-user `except`); `database/subscriptions.py:2097-2102` | Без флага. Воркер выбирает активные подписки по `now` начала прогона, а `grant_access` перепроверяет по свежему `now`. Если подписка истекла между выборкой и своим `grant_access`, та идёт в ветку новой выдачи и бросает `RuntimeError INVARIANT_VIOLATION`: при `_caller_holds_transaction` нужен `pre_provisioned_uuid`. Per-user `except` его глотает (forced-алерт уходит), savepoint на пользователя в legacy-пути нет, и батч коммитит **списание без продления, без строки `payments` и без возврата**. До коммита этот случай доходил до ветки возврата (`:409-432`). Триггер редкий: истечение во время прогона воркера | обернуть обработку каждого legacy-пользователя в `async with conn.transaction():` (savepoint), как уже сделано для outbox-пути (`:122`); `now` пересчитывать на батч |
| P1-1 | ❌ | `app/api/payment_webhook.py:141-154` (ветки timeout и unhandled), `:183-185` (`wait_for` 25 с) | Таймаут, необработанное исключение и `setup_missing` пишут только `payment_errors` и отвечают 500, **без Telegram-алерта**. `wait_for` отменяет обработку посреди работы; `CancelledError` не `Exception`, поэтому алерты внутри `confirmation` тоже не срабатывают. Если отмена пришла после commit, повтор провайдера упирается в `already_processed`, и доставку дожимает только фоновый re-sync продления (для новой выдачи его нет). Ветка ValueError → 200 (нечисловая сумма WATA/CryptoBot) тоже без алерта | forced-алерт из каждой не-200 ветки `_run_webhook`; пост-commit часть через `asyncio.shield` или с перехватом `CancelledError` |
| P1-2 | ❌ | `app/handlers/callbacks/payments_callbacks.py:527-540`; `database/admin.py:3066` | Защита от двойного тапа (`c107acb0`) — «прочитать FSM → записать FSM». С `RedisStorage` (`main.py:122`) это два сетевых запроса, и два параллельных апдейта оба видят `choose_payment_method`. Advisory lock в `finalize_balance_purchase` сериализует, но не дедуплицирует: ключ `balance:{payment_id}` новый на каждый вызов. Итог — баланс списан дважды | DB-guard внутри `finalize_balance_purchase` (дедуп по пользователю, тарифу и окну N секунд) или Redis `SET NX` |
| P1-3 | ✅ исправлено командой | `f0611cc5`, `213e2620` | Без флага сбой синхронизации premium после commit не повторялся (повтор вебхука упирался в `already_processed`). Теперь есть forced-алерт + фоновый re-sync (60 с / 5 мин / 15 мин), confirmation доставляет ГБ и уведомляет до 5xx, продление внутри транзакции оплаты, в самой транзакции HTTP нет | матрица `test_owner_example_renewal_while_panel_down`, `test_legacy_resync_never_lowers_a_later_db_date`. Остаток: re-sync живёт в памяти процесса и теряется при рестарте (R-RESYNC-RESTART, первый алерт уже ушёл) |
| P1-4 | ✅ исправлено ревью `b7e0e682` | `app/services/provisioning.py:351-358` | Цель premium = `max(job.premium_until, subscriptions.expires_at)`. У bypass-only строки `expires_at = now + 3650 д` — заглушка из `ensure_bypass_only_subscription` (`database/subscriptions.py:466`). Повтор premium-задачи после покупки пакета ГБ PATCH-ил бы premium примерно на 10 лет вперёд | заглушка bypass-only больше не цель; тест `test_bypass_only_placeholder_expiry_is_not_a_premium_target` |
| P1-5 | ❌ | `provisioning.py:398-425` (`_ensure_premium_expire` при `current >= target` — NOOP), `:579` (проверка требует `ACTIVE`) | Premium-сущность DISABLED, но с правильной датой. Задача ничего не PATCH-ит, проверка падает, повтор делает то же самое. Через 6 попыток задача `dead`, и приходит алерт «оплачено, доступа нет» | при `current >= target` и статусе не ACTIVE делать PATCH `status=ACTIVE`. **Нужно решение:** включать ли сущность, которую отключил админ |
| P1-6 | ❌ | `app/api/dashboard/auth.py:215-233`, `app/api/dashboard/security.py:156-200` | Блокировка входа обходится параллельными запросами. Проверка «не заблокирован» идёт до `await get_credentials()`, счётчик растёт после проверки пароля. Ревью воспроизвело: 200 параллельных неверных паролей дошли до проверки при лимите 5/10 | инкремент попытки до проверки пароля (increment-then-check) или лок на username |
| P1-7 | ✅ исправлено ревью `ae1eca29` | `auth.py:226` | `bcrypt.checkpw` (cost 12, ≈ 0,25 с) шёл синхронно в async-хэндлере открытого эндпоинта. Весь процесс (бот, вебхуки, воркеры) замирал на каждую попытку, а вместе с P1-6 — на десятки секунд | `asyncio.to_thread`; тест `tests/api/test_dashboard_login_nonblocking.py` |
| P1-8 | ❌ нужна семантика | `app/services/panel_traffic_audit.py:466` (`apply_fix`) | `new_limit = expected + used`. Лимит bypass пожизненный (`NO_RESET`), поэтому при `expected` как «лимит» переначисляется столько, сколько израсходовано (пример: вместо 50 ставится 68). Запускается только админом из дашборда | владелец определяет `expected`: «лимит» или «остаток». Для лимита — `max(current, expected)` |

### P2 — мелкие ошибки и устойчивость

| # | Статус | Где | Суть |
|---|---|---|---|
| P2-1 | ✅ `a3d390a1` | `app/handlers/traffic.py:495` | `/white` создавал trial-пользователю bypass на **5 ГБ** вместо 500 МБ, в 10 раз больше правила |
| P2-2 | ❌ | `database/subscriptions.py:4846-4876`, `:5016`, `:5627-5637` | Старый `finalize_purchase` держит соединение пула и session advisory lock во время HTTP в панель (Phase 1) и во время пост-commit синхронизации продления (теперь ещё с `log_payment_error` — второе соединение — и алертом). Дубль вебхука ждёт лок **со своим соединением**. Нарушено правило CLAUDE.md; пул 50. С флагом уходит; либо вернуть `sync_info` и синхронизировать после `async with` |
| P2-3 | ❌ | `auto_renewal.py:536-653` | Фаза B автопродления (`run_now` `:546`, `sync_renewal_to_remnawave` `:564`) делает HTTP в панель до освобождения batch-соединения (`cm.__aexit__` `:653`). Транзакции нет, но соединение занято на всё время синхронизации батча |
| P2-4 | ❌ | `trial_notifications.py:720`, `:837-838` | Воркер триала отправляет сообщение, удерживая соединение с БД (хвост N-05) |
| P2-5 | ❌ | `provisioning_flags.py:8-9`, `:73-75` | Shadow-режим (T17) не реализован: `shadow` = `off`, диффов нет. RUNBOOK это отмечает |
| P2-6 | ❌ | `provisioning.py:465-516` | Читатель сказал «сущности нет» → план `base = 0`. Create подхватил сущность с чужим лимитом → вечный `conflict` → `dead`, нужен ручной SQL |
| P2-7 | ❌ | `provisioning.py:622`, `:626` | 24 часа до `dead` считаются от `created_at`: задача, ждавшая за другой задачей пользователя, умирает на первой ошибке. Счётчик mismatch учитывает все попытки: после 5 попыток при лежащей панели первый mismatch сразу даёт `dead` |
| P2-8 | ❌ | `database/provisioning_jobs.py:178-218` | `mark_done/retry/dead` не привязаны к попытке (`attempts`). Безопасно при одном инстансе, но не при перекрытии деплоев |
| P2-9 | ❌ | `app/services/activation/service.py:207-209` | activation-воркер исключает только `pending/running` задачи. Пользователь с `dead`-задачей уходит в старый путь (+10 ГБ), ручной повтор добавит ещё |
| P2-10 | ❌ | `panel_traffic_audit.apply_fix`, `routes/traffic_audit.py` (`fix_all`) | Правка лимита без проверки открытой задачи выдачи → CAS `conflict` → `dead`. SQL «перепланировать» из алерта даст +N второй раз |
| P2-11 | ❌ | `database/admin.py:5104` | Продление подарком на старом пути проверяет bypass (`expect_bypass=True` для не-biz), хотя ГБ не выдаются: ложные алерты `DELIVERY_MISMATCH … LIMITED` у пользователей с израсходованными ГБ |
| P2-12 | ❌ | `app/services/payments/verify_delivery.py:451-456` | Сверх бюджета (5 за 5 минут) legacy-алерты идут без `force` и могут молча отброситься rate-limit, остаётся только `logger.critical`. После удаления часовой сверки подстраховки для этих случаев нет |
| P2-13 | ❌ | `provisioning.py:205` | Проверка доставки идёт внутри 8-секундного таймаута `run_now`. На медленной панели будет ложный алерт «первая ошибка» |
| P2-14 | ❌ | `app/api/dashboard/idempotency.py:133-151` | Гонка очистки лока (узкое окно на двойное выполнение). Кэш-ответ отдаётся до проверки сессии. `IdempotentRoute` нет у `pricing`, `remnawave` (`reset-premium-unlimited`, `backfill`), `automated_notifications/test-send`, `settings`, `incident` |
| P2-15 | ❌ | `auth.py:214`, `security.py:38-47` | Любой, кто знает username, может держать вход по паролю заблокированным (DoS; passkey работает). `client_ip` доверяет последнему `X-Forwarded-For` |
| P2-16 | ❌ | `app/api/dashboard/routes/payments.py`, `routes/stats.py` | В ответ API уходит текст исключения (только для админа) |
| P2-17 | ❌ | `app/services/panel_stats.py:204` | `date.today()` — локальная дата хоста, а не UTC или МСК |
| P2-18 | ❌ старый | `platega_service.py:311-400` | `id` колбэка не сверяется с `provider_invoice_id`. `check_transaction_status` не вызывается (recon §3a) |
| P2-19 | ❌ старый | `cryptobot_service.py:203-210` | Для VPN при отсутствии суммы подставляется ожидаемая, валюта не проверяется (тело подписано HMAC, риск низкий) |
| P2-20 | ❌ | `platega_service.py:579`, `:270`; `wata_service.py:254` | Полный payload колбэка в `payment_errors.raw_payload`, полный ответ провайдера в тексте исключения |
| P2-21 | ❌ низкая уверенность | `app/workers/wata_reconciler.py:137-154` | `ORDER BY created_at DESC LIMIT 20` учитывает и строки без провайдера (магазин). При нагрузке старые WATA-оплаты могут не проверяться |
| P2-22 | ❌ старый | `wata_service.py:619-631`, `confirmation.py` (тексты пакета трафика) | Русские тексты мимо `get_text` (Declined WATA, пакет трафика) |
| P2-23 | ❌ 🔒 магазин | `steam_purchase.py:191`, `telegram_stars_purchase.py:303` | Кнопка «💳 Карта (Lava)» ведёт в WATA; устаревшие комментарии «Код lava_service не удаляем». Менять только с разрешения владельца |
| P2-24 | ❌ | `traffic.py:310`, `common/screens.py:605`, `admin/base.py:795` | «Бесплатные 10 ГБ» при открытии экранов (известно, план факт 4). С флагом это legacy-писатель лимита → CAS `conflict` |
| P2-25 | ❌ | `purchase_flow.py` (forced-алерт синхронизации сразу, re-sync с 60 с) + `verify_delivery.py` (`LEGACY_CHECK_DELAY_S` = 20 с) | Один сбой синхронизации premium на старых путях (Telegram, автопродление, админ, баланс, подарок) даёт **два** forced-алерта: от синхронизации и от legacy-проверки через 20 с, у каждой свой бюджет. То же для bypass: «bypass create failed» (`remnawave_service.py:255-257`) + «entity absent» | не планировать legacy-проверку, пока для пользователя идёт re-sync, или ставить её позже последнего re-sync |
| P2-26 | ❌ | `confirmation.py` (ветка `remnawave_sync_failed` → `TransientPaymentError` → `alert_payment_failure`; `verify_premium_delivery` через 3 с) | Продление по вебхуку со сбоем синхронизации даёт до 3 алертов, плюс transient-алерт на каждый повтор провайдера, пока панель лежит | не запускать `verify_premium_delivery` при `sync_failed_err`; помечать итоговый transient как уже отправленный |
| P2-27 | ❌ | `confirmation.py:899-906` (legacy-проверка первой покупки, `98089fdc`) + `verify_premium_delivery` / `verify_bypass_delivery` | Для вернувшегося пользователя (bypass уже был) первую покупку проверяют два верификатора; одно расхождение premium даёт два алерта | legacy-проверку ставить только при `_skip_bypass` либо убрать `verify_premium_delivery` для первых покупок |
| P2-28 | ❌ | `purchase_flow._renewal_resync` (3 отложенные попытки) → `remnawave_premium.renew_premium_user` (свой `MAX_ATTEMPTS`, `:448`) → `remnawave_api` (свой `max_retries`, `:429`) | Три самописных слоя повторов друг в друге, ни один не через `retry_async`, — против правила CLAUDE.md «один retry-слой» | re-sync как одна попытка на задержку по не-ретраящему вызову, либо явно задокументировать как плановое восстановление |
| P2-29 | ❌ мелочь | `verify_delivery.py:16`; `remnawave_service.create_remnawave_user` | Докстринг ссылается на удалённую часовую сверку. `create_remnawave_user` объявлен `-> bool`, но при выключенном Remnawave делает голый `return` (сейчас безвредно) | — |

Остаточные риски выключенного флага, найденные матрицей (R-TG-NEW, R-WEBHOOK-NEW-NO-RETRY, M-PHASE1-DRIFT, R-RESYNC-RESTART, R-EXPIRY-RACE), описаны в `03_payment_matrix.md` §6. Ревью их подтверждает; лечит включение флага.

### Расхождения документации (исправить текстом)

- `docs/RUNBOOK.md` §7 п. 8: написано, что N-01 … N-07 «в эту ветку не входят». Они влиты (`2cba0b11`).
- `docs/RUNBOOK.md` §7 п. 4: статус бага Stars за bypass-пакет (`price_kopecks=price_stars`) — «не проверен». Он удалён вместе с хэндлером-сиротой в `e4f038c3`.
- `docs/audit/03_payment_matrix.md`:
  - ссылается на хеши до ребейза (`e46c5b91`, `4d38bc81`, `8acb6951`, `82a582a9`, `a4ba4204`); в ветке это `213e2620`, `f0611cc5`, `5395293a`, `078e6596`, `af7e3f21`;
  - «ruff — 3 ошибки, как в базе» — сейчас 0;
  - «полный набор 2 291 passed» — сейчас 2 641.
- `docs/notifications/bugs-and-risks.md` ссылается на `547aa3c6` — это хеш до слияния, в истории ветки фикс называется `2cba0b11`.
- Корневой `CLAUDE.md`:
  - «полный прогон требует `postgres:16`» — CI гоняет тесты без Postgres (4 skipped);
  - «`add_bypass_traffic` не идемпотентен» — верно только для выключенного флага;
  - «~79 миграций» — их 76.
- «Флаг off = старый путь байт в байт» неверно, но сознательно: T6 (блокировка finalize, провайдер, сумма пополнения), `c107acb0` (двойной тап) и фиксы матрицы (продление в транзакции, алерты, re-sync) меняют старый путь без флага. Это фиксы P0/P1; один из них дал регрессию P1-9.

### Проверено и корректно

- **Вебхуки:**
  - все recon P0 закрыты (Lava, fail-open WATA, `SKIP LOCKED` вне tx, проглоченный `TransientPaymentError`, сумма пополнения, провайдер);
  - `_STATUS_HTTP` покрывает все возвращаемые статусы, неизвестный статус → 200 + warning;
  - reconciler WATA ограничен своим провайдером и не финализирует дважды.
- **Продление внутри транзакции оплаты (`213e2620`):**
  - внутри транзакции только БД, HTTP в панель нет: при `_caller_holds_transaction` каждая ветка продления возвращает `renewal_xray_sync_after_commit`;
  - откат после успешного PATCH невозможен;
  - повтор вебхука больше не продлевает дважды: replay зовёт `provision_subscription` с датой из БД;
  - ГБ доставляются до 5xx, повтор их не удваивает.
- **Фоновый re-sync:**
  - перечитывает дату из БД, пропускает неактивные и bypass-only строки, панель не ставится ни ниже, ни выше БД;
  - одна задача на пользователя по сильной ссылке, `CancelledError` не глотается.
- **Алерты магазина:** `_alert_if_shop_row_not_pending` подавляет общий алерт только для строк магазина того же пользователя; VPN-строки получают ровно один алерт.
- **Часовая сверка удалена чисто:** нет висящих импортов, задач, конфигов и тестов (кроме устаревшего докстринга, P2-29).
- **Очередь выдачи:**
  - `run_now` и воркер не берут одну задачу (`SKIP LOCKED` + статус);
  - истёкший lease переподхватывается, в `running` задача не застревает;
  - таймауты (45 + 30 с и 8 с) меньше lease 120 с;
  - CAS корректен при падении до и после PATCH;
  - UTC-контракт соблюдён (`_to_db_utc` / `_from_db_utc`);
  - HTTP внутри транзакции нет ни на одном outbox-пути;
  - цикл воркера пробрасывает только `CancelledError`.
- **Проверка доставки:**
  - соединение с БД не удерживается во время HTTP в legacy-проверках;
  - наивные и aware даты нормализованы, допуск 5 минут;
  - legacy-проверки только читают и повторно ГБ не начисляют;
  - задачи держатся по сильной ссылке;
  - хуки стоят только на ветках выключенного флага.
- **Дашборд:**
  - f-string SQL (S608 проигнорирован глобально) проверены: сортировка и единицы из белых списков, значения — плейсхолдеры, инъекций нет;
  - выручка: VPN отдельно от магазина, прокси и игры, без двойного счёта пополнений; Stars в рублях по `price_kopecks`; границы суток МСК корректны (все колонки TIMESTAMPTZ);
  - cookie `HttpOnly + Secure + SameSite=Lax`, CSRF на всех под-роутерах, WebSocket только по cookie + Origin;
  - magic-link только в bootstrap и только 15 минут.
- **CI:**
  - блокируют lint, pytest + import-smoke, миграции на пустом `postgres:16` (две загрузки + дифф схемы), сборка фронта, сборка и smoke Docker;
  - `continue-on-error` нет;
  - деплой гейтится CI.

---

## 5. Строгие xfail-тесты (баги старого пути при выключенном флаге)

Всего **139** `xfail(strict=True)`: 12 характеризующих (T0) и 127 ячеек матрицы. Каждый тест начинает проходить, только когда баг исчезает, а исчезают они при включении флага соответствующей точки.

**12 характеризующих** (`tests/services/test_payment_core_characterization.py`):

| # | Тест | Что сейчас (флаг off) | Цель | Лечит флаг |
|---|---|---|---|---|
| 1 | `test_balance_pending_activation_uses_pending_activation_text` | при лежащей панели покупка с баланса падает («vpn_key is missing»), ветка `pending_activation` недостижима | покупка проходит, выдача в очереди | `balance` |
| 2 | `test_balance_new_basic_30d_adds_10gb_once` | 20 ГБ (provision 10 + `renew_bg` 10) | 10 | `balance` |
| 3 | `test_balance_combo_basic_30d_new_adds_combo_gb_only` | 95 ГБ (10 + 10 + 75) | 75 | `balance` |
| 4 | `test_balance_combo_basic_30d_renewal_adds_combo_gb_only` | 85 ГБ | 75 | `balance` |
| 5 | `test_telegram_new_basic_30d_adds_10gb_once` | 20 ГБ | 10 | `telegram` |
| 6 | `test_telegram_combo_basic_30d_new_adds_combo_gb_only[fsm_kept]` | 150 ГБ (75 + 75) | 75 | `telegram` |
| 7 | `…[fsm_lost_fallback]` | 150 ГБ | 75 | `telegram` |
| 8 | `test_admin_grant_days_new_user_adds_no_gb` | 20 ГБ | 0 | `admin` |
| 9 | `test_admin_grant_days_renewal_adds_no_gb` | 10 ГБ | 0 | `admin` |
| 10 | `test_gift_activation_basic_new_recipient_adds_10gb_once` | 20 ГБ | 10 | `gift` |
| 11 | `test_auto_renewal_combo_charges_combo_price` | combo списывается по цене basic | цена combo (329 ₽ / 30 д) | `autorenew` |
| 12 | `test_auto_renewal_combo_adds_combo_gb` | combo получает 10 ГБ | ГБ combo (75) | `autorenew` |

**127 ячеек матрицы** — те же баги по всем тарифам и состояниям:

| ID | Что | Лечит флаг |
|---|---|---|
| T0-TG-NEW-20, T0-TG-COMBO-NEW | Telegram и Stars, новая покупка: basic 20 ГБ, combo 150 | `telegram` |
| T0-BAL-NEW-20, T0-BAL-COMBO-NEW, T0-BAL-COMBO-RENEW | баланс, те же лишние ГБ | `balance` |
| T0-AUTORENEW-PLUS, T0-AUTORENEW-COMBO | автопродление: plus → basic; combo по цене basic с 10 ГБ | `autorenew` |
| T0-ADMIN-GB, T0-GIFT-20 | ГБ за админ-выдачу; подарок 20 ГБ | `admin`, `gift` |
| M-BONUS-DAYS-GB, M-BONUS-NO-KEY | бонус днями даёт 10 ГБ; повтор прогона выдаёт второй раз | `grants` |
| M-TRIAL-NO-MB | триал пользователю с bypass-сущностью даёт 0 МБ | `trial` |

---

## 6. Мёртвый код

### 6.1. Удалено (первый круг, одобрен)

| Пакет | Коммит | Что |
|---|---|---|
| B1 | `8c9bf2f6` | около 51 устаревшего документа → `docs/archive/` |
| B2 | `e7cbf594` | мёртвые модули: `app/core/i18n/`, `utils/audit.py`, `utils/message_guard.py`, `admin/reconcile.py`, `callbacks/admin_callbacks.py`, `common/decorators.py`, `validate_language_content.py`, json-переводы, `systemd/`, `load_tests/`, корневой `handlers.py` (функция `/buy` перенесена) |
| B3 | `e4f038c3` | хэндлеры-сироты (−40 декораторов aiogram вместе с B4/B5) + общий fallback для старых кнопок |
| B4 | `4ea51867` | Lava целиком; WATA-кнопки завязаны на `wata_service.is_enabled()` |
| B5 | `5544d851` | samopis (админ-UI миграции, рассылка, 2 скрипта), `vpn_utils`, `app/services/vpn`, subscription_proxy |
| site_sync | `c5f2b1e7` | `site_sync.py` + воркер |
| рекуррент Platega | `9549e706` | создание подписки, `pay:sbp_sub`, обработка списаний и статусов, `check_subscription_status`, entrypoint `platega_recurring` (−479 строк в `platega_service.py`) |
| часовая сверка | `98089fdc` | `subscription_reconciler.py` (−404) и его тесты, по решению владельца |

### 6.2. Кандидаты второго круга

Второй круг сейчас выполняет параллельный агент; ревью только фиксирует список на `73a38011`. Проверено grep'ом по `*.py`, `*.ts(x)`, `*.sql`, `*.sh`, `*.yml`, `*.mjs` и Dockerfile (без `docs/`), кроме собственного `def` и реэкспорта в `database/__init__.py`.

| Пакет | Что | Сколько | Статус на `73a38011` |
|---|---|---|---|
| B6 | публичные функции `database/*` с 0 вызовов: `get_all_users_telegram_ids`, `check_user_still_eligible_for_no_sub_broadcast`, `get_user_ltv`, `get_average_ltv`, `get_gift_subscription`, `get_user_paid_subscription_history`, `has_applied`, `get_bypass_gift_link_by_code`, `safe_get`, `_get_pool_safe`, `get_stats_link`, `get_promo_link`, `_bulk_fetch_panel_expires_at`, `create_payment`, `update_payment_status`, `has_any_subscription`, `has_any_payment`, `has_active_special_offer`, `_log_vpn_lifecycle_audit_fire_and_forget`, `consume_promocode_atomic` ⚠️, `is_user_first_purchase`, `clear_remnawave_premium_uuid`, `calculate_referral_percent`, `update_username` | **24** (≈ 550 строк) | 0 вызовов. **`get_remnawave_premium_id` ожил** (его вызывает `provisioning.py`) — не удалять |
| B7 | Remnawave: `reset_user_traffic`, `enable_user`, `disable_user`, `revoke_user_subscription` (может понадобиться для recon P1-4), `extend_user_expiry`, `find_user_by_email`, `resolve_user_id`, `delete_bypass_user`, `get_premium_subscription_url`, `create_remnawave_user_bg`, `update_tariff` | **11** (≈ 150) | 0 вызовов |
| B9 | прочие: `get_last_pool_wait_spike_monotonic`, `create_default_system_state`, `broadcast._safe_send`, `recovery_premium._scan`, `is_activation_allowed`, `_update_subscription_activated`, `alert_vpn_api_failure`, `alert_security_event`, `_device_limit_for`, `subscription_count`, `sub_aggregator.get_url` (только тест), `log_operation`, `validate_message_text`, `require_ownership`, `config.tariff_for_vpn_api`, `config.get_biz_price_stars` | **16** (≈ 390) | 0 вызовов. `platega_service.check_transaction_status` оставить: нужен для P2-18 |
| B10 | ruff F401/F841/F811 | 275 правок (191 автофикс) | только отдельным коммитом с ревью `--diff` |
| Новое | biz-каталог `corporate_access_request` / `biz_country:` (`payments/callbacks.py:437-455`, `:892-925`) — замкнутая сирота | ≈ 125 | `docs/screens/candidates.md` A2 |
| Новое | biz-меню (`biz_profile`, `biz_ecosystem`, …) | ≈ 140 | нужен SQL владельца: сколько живых `biz_*` (candidates C1) |
| Новое | мёртвые i18n-ключи (`buy.corporate*` и др.) | см. `docs/screens/dead_i18n.md` | — |
| Новое | `database/platega_subscriptions.py` (осталось 2 read-only функции) + таблицы 074/082 | — | DROP отдельным релизом (SCOPE) |
| T18 | старые пути выдачи после 1–2 недель с флагом `on`: `remnawave_service.renew_remnawave_user_bg`, `add_bypass_traffic` (две разные функции с одним именем), `add_traffic`, `sync_renewal_to_remnawave` + фоновый re-sync, `_deliver_bypass_gb`, legacy-хуки `verify_delivery`, ГБ-блоки в `payments_messages`, `payments_callbacks`, `auto_renewal`, `start`, `game`, `bonus` | крупнейший резерв сокращения | по плану, после выкатки флага |

Итого второй круг без T18: **≈ 1 350 строк + 275 ruff-правок**.

---

## 7. Что упростилось и что всё ещё сложно

### Упростилось

- **Одно место для правил ГБ и цен:** `app/services/tariffs.py` (188 строк, без I/O, неизвестное → ошибка, а не тихие 10 ГБ). Раньше «+10 ГБ» было захардкожено в 6 местах.
- **Одно место для выдачи:** `provisioning.py` + `provisioning_jobs` + воркер. 8 точек входа пишут одну задачу в своей транзакции вместо 5 разных копий «прочитать лимит → +N → PATCH».
- **Одна политика HTTP-кодов** для всех вебхуков: `_STATUS_HTTP` + `_run_webhook`, вместо 4 копий `JSONResponse(result)` (`payment_webhook.py` −252 / +143).
- **Провайдеры стали тоньше и строже:** `platega_service.py` −479 строк (рекуррент), единые проверки суммы и валюты для VPN.
- **Алерты на деньгах полные:** forced-алерт на каждом денежном сбое; агрегация очереди (бюджет + сводка).
- **Минус 20 Python-модулей и 40 хэндлеров,** а `payments_callbacks.py` −331 строка, `traffic.py` −358.
- **Удалена часовая сверка** (−404 строки): проверка идёт по каждой покупке.

### Всё ещё сложно (топ)

| Что | Размер | Почему сложно |
|---|---|---|
| **Два пути в каждой точке выдачи** (старый + outbox за флагом) | около 2,5 тыс. строк дублирования по оценке T18 | пока флаг не включён и T18 не сделан, каждая правка выдачи делается дважды. Матрица это показала: фиксы старого пути сами дали регрессию (P1-9) и дубли алертов (P2-25 … P2-27) |
| Слои доставки старого пути | `purchase_flow` + re-sync + `verify_delivery` + `verify_premium_delivery` / `verify_bypass_delivery` + `remnawave_service` | 4 механизма «проверить и повторить» на один платёж, 3 слоя повторов (P2-28); после включения флага всё это уходит (T18) |
| `database/subscriptions.py` | 5 653 (+491) | finalize, grant_access, промокоды и рефералы в одном файле; `finalize_purchase` одна из самых длинных функций |
| `database/admin.py` | 5 469 (+298) | баланс, подарки, админ-выдачи, аудит |
| `app/handlers/admin/broadcast.py` | 2 584 | рассылки (recon §5: нет throttle, паузы, резюма) |
| `app/handlers/admin/access.py` / `database/users.py` | 2 285 / 2 263 | — |
| `payments_callbacks.py` / `navigation.py` | 2 269 / 2 239 | в `navigation.py` магазин 🔒, дробить только переносом 1:1 |
| `app/services/provisioning.py` | 1 083 | ядро + алерт-агрегатор + проверка доставки в одном модуле; агрегатор (≈ 300 строк) стоит вынести |
| Три модуля уведомлений админа (К1) | 324 + 243 + 283 | не консолидированы |
| Слои Remnawave (К2) | `remnawave_service` + `premium` + `bypass` + `purchase_flow` + `provisioning` | две функции `add_bypass_traffic` с разной семантикой всё ещё существуют |
| `database/core.py` | 1 231 | около 128 inline-DDL при каждом старте, параллельно с миграциями |

---

## 8. Прогоны (HEAD ревью: `73a38011` + 3 фикса)

| Проверка | Результат |
|---|---|
| `pytest -q` | **2 641 passed, 4 skipped, 139 xfailed, 0 failed** (2 784 собрано, ≈ 19 с) |
| матрица `tests/services/test_payment_matrix.py` | 1 074 passed, 127 xfailed |
| `ruff check .` | All checks passed |
| `python -m compileall -q app database` | OK |
| import-smoke (`tests/test_import_smoke.py`) | 234 passed |
| База для сравнения: `cf8205fc` в отдельном worktree | 369 собрано: 323 passed / 46 failed; ruff 5 ошибок |

Тесты с Postgres (4 skipped) ревью не прогоняло.

---

## 9. Коммиты ревью (по одному на фикс, каждый с тестом, красным на старом коде)

| Коммит | Что |
|---|---|
| `a3d390a1` | `/white`: trial получает `TRIAL_BYPASS_MB` (500 МБ), а не 5 ГБ |
| `b7e0e682` | outbox: дата-заглушка bypass-only никогда не становится целью premium (не будет premium на 10 лет) |
| `ae1eca29` | дашборд: проверка bcrypt вынесена из event loop (`asyncio.to_thread`) |

P1-9 ревью сознательно не чинило: это изменение транзакционной логики денежного цикла, а владелец просил без лишних правок. Фикс маленький (savepoint на пользователя), нужен тест матрицы «подписка истекла во время прогона».

---

## 10. Рекомендуемые шаги (по приоритету)

1. **P1-9: регрессия автопродления** — savepoint на каждого legacy-пользователя (как у outbox-пути), `now` на батч, тест «истекла во время прогона». Деньги списываются без продления.
2. **P0-1: промокод.** Отдельное исключение для «уже обработано», любой другой ValueError в finalize → PERMANENT-алерт + `payment_errors`. Решение владельца: исчерпанный промокод после оплаты — выдавать без скидки (рекомендация: да, деньги уже списаны) или резервировать при выставлении счёта. Добавить промокоды в матрицу.
3. **P1-1: алерт на каждую не-200 ветку вебхука,** перехват `CancelledError` / `shield` для пост-commit части.
4. **P1-2: DB-дедуп двойного списания с баланса,** а не только FSM.
5. **P1-6: блокировка входа в дашборд** — increment-then-check. Обязательно до выкатки дашборда v3.
6. **P1-5: реактивация DISABLED premium** — нужно решение владельца по отключённым админом.
7. **Выкатка ядра по RUNBOOK:** миграция 082 → webhook → telegram → balance → autorenew → admin/gift → grants/trial. Это закрывает 139 xfail, остаточные риски матрицы, P1-9 и дубли алертов на этих точках. Заранее решить: реализовать shadow (T17) или убрать его из процедуры.
8. **Дубли алертов старого пути** (P2-25 … P2-27) — если выкатка флага затянется; иначе уйдут с T18.
9. **Правило «нет соединения во время HTTP»:** P2-2 (уйдёт с флагом), P2-3 (автопродление), P2-4 (триал).
10. **Остальные P2 очереди и проверки доставки:** P2-6 … P2-13, прежде всего P2-9, P2-10, P2-12.
11. **Второй круг чистки** (идёт) + biz-каталог.
12. **T18** через 1–2 недели в `on`: удалить старые пути выдачи, legacy-проверки и фоновый re-sync. Это основное реальное сокращение кода.
13. **Документация:** RUNBOOK §7 п. 4 и 8; хеши и цифры в `03_payment_matrix.md`; `CLAUDE.md` (Postgres в тестах, число миграций, идемпотентность `add_bypass_traffic`); докстринг `verify_delivery.py:16`.
14. **Разбиение god-модулей** (`database/subscriptions.py`, `database/admin.py`) — после T18, когда уйдут дубли.

---

## 11. Как проверяли

- Метрики — `git diff --numstat -M` и `--shortstat` по `cf8205fc..73a38011` с разбиением по путям. Тесты и ruff «до» — прогон на `cf8205fc` в отдельном worktree тем же venv.
- Требования — чтение кода и сопоставление с тестами (имена выше), точечные и полные прогоны pytest.
- Ревью диффа — 5 независимых read-only ревьюеров по областям:
  - ядро выдачи;
  - вебхуки и провайдеры;
  - дашборд + уведомления + CI;
  - контроль доставки;
  - новые коммиты матрицы и доставки.

  Каждая находка P0/P1 перепроверена вручную по коду: P0-1 (в т. ч. на `cf8205fc`), P1-2, P1-4, P1-7, P1-9 (включая diff `213e2620` и условие выборки воркера).
- Мёртвый код — скрипт поиска ссылок по всему репо (без `docs/`), vulture ≥ 60 % как вспомогательный сигнал.

---

## 12. Исправлено после ревью

Ветка `worktree-agent-ae92b07ac171f7301` поверх `refactor/audit-2026-09` (`f6f798c1`, после второго круга чистки). Один баг — один коммит; каждый тест сначала падал на старом коде, потом фикс, потом зелёный полный прогон. Магазин, миграции, `.env*`, фронтенд дашборда, P2-2/P2-3/P2-28 и shadow-режим не трогались.

| # | Коммит | Что было → что теперь | Тест |
|---|---|---|---|
| P0-1 | `92222a9a` (+ `166327f9`: вернуть `import asyncio`, снятый авточисткой B10) | Промокод исчерпан/истёк между счётом и оплатой → `ValueError` → «already processed», покупка молча терялась. Теперь оплаченная покупка всегда финализируется по оплаченной (счётной) цене, использование засчитывается даже сверх лимита, админу — info-алерт после commit. Дубль — только новое `PurchaseAlreadyProcessed(ValueError)`; любой другой `ValueError` из finalize → PERMANENT forced-алерт + `payment_errors` (`finalize_rejected`), статус `error` (200). Покупка с баланса по-прежнему строгая (откат до списания) | матрица: `test_promo_purchase_is_honoured_at_the_paid_price` (valid / exhausted / expired × platega, wata, telegram × флаг off/on), `test_unexpected_finalize_value_error_is_a_permanent_alert_not_a_duplicate`, `test_duplicate_finalize_is_already_processed_without_alert` |
| P1-9 | `b9d6023e` | Подписка истекла между выборкой и `grant_access` → `INVARIANT_VIOLATION` проглатывался, батч коммитил списание без продления. Теперь у каждого legacy-пользователя свой savepoint (как в outbox): исключение откатывает только его списание, forced-алерт остаётся; `now` пересчитывается на батч | матрица `test_autorenew_expiry_mid_run_does_not_debit_without_renewal` (настоящий `grant_access`), `tests/services/test_autorenew_savepoint.py` (второй пользователь батча продлён и списан) |
| P1-1 | `8a9ca01e` | Таймаут, необработанное исключение, `setup_missing`, `service_missing`, transient без алерта → только `payment_errors`. Теперь каждая не-200 ветка `_run_webhook` (и ValueError-ветка с 200) шлёт forced-алерт через агрегатор выдачи (новый вид `webhook`: 5 за 5 мин сразу, остальное одной сводкой от воркера выдачи, который работает без флага). Transient, по которому confirmation уже отправил алерт, помечен `alerted` и не дублируется. `process_confirmed_payment` идёт shielded-задачей: таймаут вебхука отменяет только ожидание, пост-commit доставка доходит до конца | `tests/api/test_webhook_alerts.py` |
| P1-2 | `a6ae6b93` | Двойной тап с Redis FSM списывал баланс дважды. Теперь `finalize_balance_purchase` (legacy и outbox) под per-user advisory-локом отклоняет вторую идентичную покупку (тот же пользователь, `tariff_period`, сумма) в окне 60 с — `DuplicateBalancePurchase`, до списания. Хэндлер отвечает «Оплата уже обрабатывается», FSM не трогает, алерта нет. Повтор после окна — обычная покупка | `tests/services/test_balance_double_tap.py` (два параллельных вызова, флаг off/on), матрица `test_balance_double_tap_duplicate_is_answered_without_alert` |
| P1-5 | `094b71a0` | DISABLED premium с верной датой → 6 бесполезных повторов → dead с ложным «доступа нет». Теперь (без автовключения, решение владельца) ГБ задачи доставляются, затем `PremiumDisabled` (Permanent): dead с первой попытки и один forced-алерт «PREMIUM_DISABLED: оплачено, premium-сущность DISABLED в панели — включите вручную, если пользователь не забанен» с tg и ключом покупки. PATCH продления не менялся | `tests/services/test_provisioning_premium_disabled.py`; старый тест в `test_provisioning_verify.py` переписан под новый контракт |
| P1-6 | `352fe356` | Параллельные неверные пароли обходили блокировку (все доходили до bcrypt). Теперь increment-then-check: попытка считается атомарно до проверки пароля, до bcrypt доходят не больше `max_failures` за окно, остальные — 429; счётчик при блокировке не удаляется; успешный вход сбрасывает. bcrypt по-прежнему в `to_thread`, setup/passkey не менялись | `tests/api/test_dashboard_login_lockout_race.py` (50 параллельных → ≤ 5 проверок; последовательная семантика прежняя) |
| P1-8 | `2f288799` | `apply_fix` ставил `expected + used` (израсходованное начислялось второй раз). Теперь `max(current, expected)`: лимит пожизненный, `expected` — сумма всех выданных ГБ; не ниже текущего. Строка в `02_payment_core_plan.md` обновлена | `tests/services/test_panel_traffic_audit.py` (старые тесты формулы заменены) |
| P2-25 | `62a82876` | Сбой синхронизации premium (или «bypass create failed») + через 20 с legacy-проверка = два алерта. Теперь `verify_delivery` помнит уже отправленные части (premium / bypass, TTL 30 мин > всего re-sync), legacy-проверка их не повторяет, другие проблемы шлёт | `test_verify_delivery_legacy.py`: 3 новых теста |
| P2-26 | `ae00d707` | Продление по вебхуку со сбоем синхронизации давало до 3 алертов + transient на каждый повтор провайдера. Теперь transient помечен `alerted` (алерт уже отправил `purchase_flow`), `verify_premium_delivery` не запускается, повтор при уже отправленном алерте тоже `alerted`. Ответ по-прежнему 5xx | матрица `test_webhook_renewal_sync_failure_is_one_alert`; `test_payment_core_t6::test_remnawave_sync_failure_raises_transient` фиксирует один алерт |
| P2-27 | `860d8f8a` | Первая покупка вернувшегося пользователя: premium проверяли legacy-проверка и `verify_premium_delivery`. Теперь `verify_premium_delivery` только для продлений; ГБ по-прежнему проверяет `verify_bypass_delivery` | матрица `test_webhook_first_purchase_premium_has_one_verifier` |
| P2-12 + P2-29 | `a6e3a3bc` | Сверх бюджета legacy-алерты шли без `force` и могли молча отброситься. Теперь через агрегатор (вид `legacy_delivery`): в бюджете — forced, остальное (и без бота, и при сбое отправки) — одной forced-сводкой. Докстринг про удалённую часовую сверку исправлен | `test_verify_delivery_legacy::test_over_budget_alerts_go_into_one_digest_never_dropped` |
| P2-9 | `27b6eee8` | activation-воркер брал пользователя с `dead`-задачей → старый путь +10 ГБ. Теперь предикат открытой задачи включает `dead` | `test_grant_access_deferred` (предикат воркера) |
| P2-17 | `b934a87a` | `date.today()` хоста в окне трафика панели. Теперь дата МСК, как везде на дашборде | `test_dashboard_metrics_api::test_nodes_usage_window_ends_on_the_msk_date_not_the_host_date` |
| Документация | `6cc10b20` | RUNBOOK §7 п. 4 и 8; хеши и цифры в `03_payment_matrix.md`; хеш в `notifications/bugs-and-risks.md` (`547aa3c6` → `2cba0b11`); `CLAUDE.md` (Postgres в тестах, 76 миграций, идемпотентность `add_bypass_traffic` только при выключенном флаге), то же в `database/` и `payments/CLAUDE.md` | — |

**Не исправлено:**

- **P2-11 не воспроизводится.** Единственный вызов `activate_gift_subscription` — `/start gift_…` (`app/handlers/user/start.py:295-301`), и на старом пути он для любого basic/plus-получателя, в том числе при продлении, зовёт `renew_remnawave_user_bg`: тот доливает ГБ тарифа и возвращает bypass-сущности `ACTIVE`. Значит, `expect_bypass=True` для подарка корректен. Матрица это подтверждает: `test_gift_cell[…-active-off]` требует +ГБ и проходит без xfail. Код не менялся.
- P2-23 (магазин 🔒), P2-2/P2-3/P2-28 (уходят с флагом), shadow-режим — вне задачи.

**Прогоны после всех фиксов:** `pytest -q` — **2 687 passed, 4 skipped, 139 xfailed, 0 failed** (было 2 641 / 139); матрица — 1 098 passed, 127 xfailed (было 1 074 / 127, +24 ячейки: промокоды, finalize, автопродление, двойной тап, P2-26, P2-27); `ruff check .` — чисто; `compileall` и import-smoke — OK. Строгих xfail не снято: все 139 — GB-баги старого пути, их лечит включение флага, эти фиксы их не касаются.
