# Мёртвый код и дубли архитектуры

Находок: **24** — P0 0, P1 2, P2 13, P3 9. Опровергнуто при перепроверке: 2.

Перепроверялись три самые тяжёлые находки каждого домена плюс все P0. Остальные помечены «не перепроверено» — это гипотезы, требующие подтверждения перед правкой.

---

### 1. Две системы управления схемой БД: 68 SQL-миграций и 116 DDL-операторов в database/core.py на каждом старте

**P1** · ✅ подтверждено · уверенность автора находки: high · риск правки: высокий

`database/core.py:457`

**Дефект.** init_db сначала прогоняет migrations.run_migrations_safe (строка 432), потом в блоке `async with _pool.acquire() as conn:` (строка 458) выполняет ещё ~700 строк императивного DDL: 116 операторов CREATE TABLE IF NOT EXISTS / ALTER TABLE ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS (подсчёт grep -c). Комментарий на строках 459-473 сам признаёт: «Migrations 001–053 have already created every table and column below… idempotent legacy fallbacks… Each one still asks Postgres for ACCESS EXCLUSIVE LOCK». Схема живёт в двух источниках истины: migrations/*.sql и core.py.

**Когда ломается.** На проде каждый рестарт бота берёт ACCESS EXCLUSIVE на все ключевые таблицы (users, subscriptions, payments, …). При одной висящей idle-in-transaction сессии или работающем autovacuum ALTER встаёт в очередь, за ним встают все читающие запросы к этой таблице. Спасает только SET lock_timeout='5s' (строка 476), после чего init_db идёт дальше молча — то есть при таймауте новая колонка на девственной БД не создастся, а ошибка проглотится except Exception: pass.

**Что сделать.** Признать migrations/*.sql единственным источником схемы. Вынести DDL-блок database/core.py:481-1160 в отдельную миграцию-бутстрап (или скрипт для локальной разработки) и убрать его из init_db. Перед удалением сверить, что каждая таблица/колонка из core.py действительно есть в migrations — расхождения выписать отдельным списком.

**Скептик скорректировал severity** до P2.

**Уточнение проверки.** Находка верна как архитектурный дефект (дублирование источника истины схемы: 67 миграций + 119 DDL-операторов в database/core.py:481-1111), но неверна как P1-прод-инцидент. Три поправки: (а) сценарий «колонка не создастся на девственной БД из-за lock_timeout» самопротиворечив — lock_timeout требует конкурирующей сессии, которой на девственной БД нет, и миграции там отрабатывают первыми (core.py:432); (б) ACCESS EXCLUSIVE берут 78 ALTER-ов, а не все 119 — CREATE TABLE IF NOT EXISTS пропускается без AE-лока, CREATE INDEX IF NOT EXISTS берёт ShareLock и читателей не блокирует; (в) блокировка ограничена 5 секундами намеренно (core.py:476), с объяснением на :459-474, и исполняется один раз на процесс (guard core.py:392). Рекомендуемая формулировка дефекта: не «риск залипания прода», а «схема живёт в двух местах, 111 DDL-операторов молча глотают ошибки (`except Exception: pass`), определения в core.py разошлись с миграциями». При этом блок нельзя просто удалить: `gift_subscriptions` и `user_traffic_discounts` определены только здесь и используются в database/admin.py:4395, database/subscriptions.py:4713, database/traffic.py:574 — их сначала надо вынести в миграции.

<details><summary>Как это проверялось</summary>

Архитектурное ядро находки подтверждается, но сценарий отказа и цифры завышены.

ЧТО ПОДТВЕРДИЛОСЬ:
- database/core.py:378 `init_db`, :432 `migrations.run_migrations_safe(_pool)`, :458 `async with _pool.acquire() as conn:` — всё на месте.
- Комментарий на :459-474 дословно такой, как цитирует находка («Migrations 001–053 have already created every table and column below… idempotent legacy fallbacks… ACCESS EXCLUSIVE LOCK»), `SET lock_timeout = '5s'` на :476, `SET statement_timeout = '20s'` на :477.
- Блок DDL тянется с :481 до :1111 (~630 строк). Пересчёт: 119 строк с `IF NOT EXISTS`, а не 116 — из них 78 `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, 20 `CREATE TABLE IF NOT EXISTS`, 21 `CREATE (UNIQUE) INDEX IF NOT EXISTS`. Миграций 67 `.sql`, не 68 (68-й файл — migrations/README.md).
- Два источника истины по схеме — реальность, и 78 ALTER TABLE действительно берут ACCESS EXCLUSIVE до проверки `IF NOT EXISTS`.

ЧТО ОПРОВЕРГАЕТСЯ:

1. Сценарий «при таймауте новая колонка на девственной БД не создастся» логически невозможен. lock_timeout срабатывает ТОЛЬКО когда конфликтующую блокировку держит другая сессия. На девственной БД таких сессий нет по определению. Более того, на девственной БД миграции 001–070 отрабатывают раньше (core.py:432), то есть core.py-DDL там вообще не путь бутстрапа. Два условия сценария (девственная БД + конфликт блокировок) взаимоисключающи.


_(обоснование сокращено, полностью — в findings.json)_

</details>

### 2. Автопродление считает цену по своей формуле мимо calculate_final_price и админских override

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`auto_renewal.py:215`

**Дефект.** auto_renewal.py:211-224 самостоятельно берёт base_price = config.TARIFFS[tariff_type][period_days]["price"], затем вручную применяет VIP-скидку 30% (строка 218) и персональную скидку (строки 221-223). Это отдельная реализация лестницы скидок, дублирующая database.calculate_final_price (database/subscriptions.py:3970-4105) и обёртку app/services/subscriptions/service.calculate_price (строка 37). Обёртка сервиса дополнительно применяет админские price override и global discount из миграции 069 (service.py:91-105) — автопродление их не видит вообще. Также не видит спецпредложение -15% и промокоды.

**Когда ломается.** Админ снижает цену basic/30 в дашборде (app/api/dashboard/routes/pricing.py) со 199 до 149. Пользователь, покупающий вручную, платит 149. У пользователя с автопродлением с баланса списывается 199. Обратная ситуация — админ поднял цену, автопродление продолжает списывать старую и бот теряет деньги на каждом продлении.

**Что сделать.** Заменить блок auto_renewal.py:211-224 вызовом app.services.subscriptions.service.calculate_price с теми же tariff/period_days и списывать final_price_kopecks. Отдельно решить с владельцем, применяются ли к автопродлению промокоды и спецпредложение.

### 3. Пакет app/core/i18n сломан на импорте и никем не используется

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/core/i18n/__init__.py:7`

**Дефект.** app/core/i18n/__init__.py:7 делает `from .manager import I18nManager`, но файла app/core/i18n/manager.py в директории нет — там только __init__.py и types.py (ls директории). Любой `import app.core.i18n` упадёт с ModuleNotFoundError. Пакет никем не импортируется, I18nManager нигде не упоминается. Живая локализация — это app/i18n/ с get_text (app/i18n/__init__.py:32) и семью модулями языков.

**Когда ломается.** Попытка воспользоваться «enterprise I18N архитектурой», описанной в docstring, немедленно ломает импорт. При включении пакета в любой __init__ верхнего уровня падает старт бота.

**Что сделать.** Удалить директорию app/core/i18n целиком. Плюрализация из types.py (_ar_plural и константы PLURAL_*), если нужна, переносится в app/i18n/.

### 4. audit_subs.py и audit_db_dates.py — почти клоны (1359 строк на два варианта одного аудита)

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`app/handlers/admin/audit_subs.py:90`

**Дефект.** Оба модуля повторяют один и тот же каркас: _compute_real_end (audit_subs.py:90 ↔ audit_db_dates.py:79), _audit_worker (111 ↔ 95), _format_report (281 ↔ 196), _fix_worker (596 ↔ 539), _iso_z (614 ↔ recovery_premium.py:408), _start_audit (734 ↔ 564), _one (702 ↔ database/reconciliation.py:125). Отличаются только источником сравнения (панель Remnawave против дат в БД) и набором callback-имён. Аналогичная пара — app/handlers/admin/recovery_premium.py и app/handlers/admin/reconcile.py: _parse_rmn_dt (73 ↔ 42), _fix_one (411 ↔ 320), _run_all_fixes (531 ↔ 362).

**Когда ломается.** Правка формулы «реальной» даты окончания (_compute_real_end) делается в одном файле из трёх (audit_subs.py:90, audit_db_dates.py:79, recovery_premium.py:89) — два оставшихся аудита начинают показывать другие цифры и админ получает противоречивые отчёты по одним и тем же подпискам.

**Что сделать.** Вынести общий каркас в app/handlers/admin/_audit_base.py: _compute_real_end, _iso_z, _parse_rmn_dt, обёртки воркера с прогресс-сообщением и общий _format_report с параметром колонок. Оставить в каждом модуле только источник данных и набор callback_data. reconcile.py при этом кандидат на удаление целиком (см. отдельную находку).

### 5. Админские клавиатуры существуют в трёх копиях; используется только версия admin/keyboards.py

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/handlers/common/keyboards.py:589`

**Дефект.** get_admin_dashboard_keyboard, get_admin_back_keyboard, get_broadcast_test_type_keyboard, get_broadcast_type_keyboard, get_broadcast_segment_keyboard, get_broadcast_confirm_keyboard, get_ab_test_list_keyboard, get_admin_export_keyboard, get_admin_user_keyboard, get_admin_user_keyboard_processing определены трижды: handlers.py:793-964, app/handlers/admin/keyboards.py:11-291 и app/handlers/common/keyboards.py:589-746. Все реальные импорты идут из app.handlers.admin.keyboards (broadcast.py:20-26, access.py:23-31, reissue.py:17-22, export.py:18, stats.py:23, notifications.py:21, migration.py:40, audit.py:15, audit_subs.py:46, audit_db_dates.py:60, activations.py:15, promo_trial.py:53, recovery_premium.py:39, reconcile.py:18, base.py:18). Копии в common/keyboards.py только реэкспортируются из common/__init__.py и никем не вызываются.

**Когда ломается.** Разработчик добавляет кнопку в админ-панель, правит копию в common/keyboards.py (её легко найти по имени), кнопка не появляется. Или наоборот: правит admin/keyboards.py, а тест/скрипт импортирует common-копию и видит старый набор кнопок.

**Что сделать.** Удалить админские клавиатуры из app/handlers/common/keyboards.py (строки 589-746) и соответствующие реэкспорты из common/__init__.py. Оставить app/handlers/admin/keyboards.py единственным источником. Проверить построчным diff, нет ли в common-копии кнопок, которых нет в admin-копии.

### 6. app/handlers/traffic.py: два обработчика с мёртвым legacy-телом после return

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/handlers/traffic.py:231`

**Дефект.** На строке 228 стоит return после callback.answer с текстом «Оплата с баланса ... больше недоступна», а дальше на строках 231-259 идёт комментарий «unreachable: legacy body kept for reference only» и 29 недостижимых инструкций со старой логикой списания. Точно такая же конструкция на строках 817 (return) и 820-838 (19 инструкций). Это единственные два случая недостижимого кода в проекте — AST-скан по всему дереву больше ничего не нашёл.

**Когда ломается.** Не ломается в рантайме, но vulture/ruff шумят, а тело содержит устаревшую бизнес-логику списания баланса за трафик. При «восстановлении» функции кто-нибудь снимет return и включит логику, не прошедшую ревью.

**Что сделать.** Удалить блоки app/handlers/traffic.py:231-259 и 820-838. Если старая логика нужна как справка — она есть в git-истории.

### 7. Смешанное обращение к слою: часть кода зовёт database.finalize_purchase напрямую в обход сервиса

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`app/services/payments/confirmation.py:110`

**Дефект.** Есть сервисная обёртка app/services/subscriptions/service.py:274 finalize_purchase, которая при неуспехе поднимает PaymentFinalizationError (строка 316). Через неё идут app/handlers/callbacks/navigation.py:1701 и app/services/payments/service.py:555. Но app/services/payments/confirmation.py:110 и app/handlers/payments/payments_messages.py:574 и :724 зовут database.finalize_purchase напрямую и разбирают результат сами (confirmation.py:118-119 бросает голый Exception). То же расслоение у check_and_disable_expired_subscription: сервис app/services/subscriptions/service.py:477 — тонкая обёртка, но app/handlers/callbacks/subscription.py:347 идёт мимо неё в database.

**Когда ломается.** В сервисную обёртку добавляют обязательный шаг (метрику, идемпотентный лог, инвалидацию кеша) — три вызывающих в обход неё этот шаг не выполняют. Обработка ошибок расходится: одни ловят PaymentFinalizationError, другие — голый Exception, и вебхук-хендлер confirmation.py возвращает провайдеру другой статус, чем handler бота при той же ошибке.

**Что сделать.** Определить правило: database/* вызывается только из app/services/*, хендлеры ходят через сервисы. Перевести app/services/payments/confirmation.py:110, app/handlers/payments/payments_messages.py:574/724 на subscription_service.finalize_purchase, а app/handlers/callbacks/subscription.py:347 — на subscription_service.check_and_disable_expired_subscription. Изменение поведения при ошибке (исключение вместо dict) требует отдельной проверки каждого места.

### 8. app/services/vpn_client.py — 251 строк мёртвого фасада к декоммиссированному xray

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/services/vpn_client.py:1`

**Дефект.** Модуль не импортируется нигде (единственное вхождение строки vpn_client в репозитории — комментарий внутри самого файла на строке 5). Он описывает фасад Bot → vpn_client → vpn_utils → Xray API и содержит health_check (62), create_user (76), extend_user (132), disable_user (191), get_user (218) плюс пять классов исключений (34-56). Целевой слой vpn_utils при PURCHASE_FLOW_REMNAWAVE=True уже no-op (vpn_utils.py:198, 561, 640), то есть даже при подключении модуль ничего бы не делал.

**Когда ломается.** Не ломается ничего сейчас — но модуль вводит в заблуждение: имена create_user/get_user совпадают с app/services/remnawave_api.py:110/273, и при рефакторинге легко импортировать не тот фасад и получить тихие no-op вызовы вместо провижининга в Remnawave.

**Что сделать.** Удалить app/services/vpn_client.py вместе с веткой xray (решение владельца #1). Убедиться, что тесты не мокают этот модуль.

### 9. app/utils/audit.py — 418 строк мёртвой параллельной подсистемы аудита

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/utils/audit.py:179`

**Дефект.** Модуль определяет класс AuditEvent (28), словарь AUDIT_EVENT_TYPES (83), redact_metadata (133), log_audit_event_safe (179) и шесть тематических обёрток audit_auth_decision (254), audit_payment_event (283), audit_subscription_event (309), audit_vpn_event (335), audit_admin_action (361), audit_worker_side_effect (391). Ни одно из этих имён не встречается в репозитории вне самого файла (проверено grep по .py). Сам модуль тоже никем не импортируется. Реально работающий аудит — это database._log_audit_event_atomic (database/subscriptions.py:989), _log_audit_event_atomic_standalone (1146) и таблица audit_log, их зовут reminders.py:305, fast_expiry_cleanup.py:347, database/users.py:1933 и десятки мест в database/.

**Когда ломается.** Разработчик, которому поручили «добавить аудит платежа», находит по имени audit_payment_event, вызывает её и считает задачу закрытой. Событие никуда не пишется — функция ведёт в log_audit_event_safe, который в лучшем случае положит строку в лог-файл, а не в таблицу audit_log, по которой строится дашборд (app/api/dashboard/routes/audit.py).

**Что сделать.** Удалить app/utils/audit.py целиком. Если нужны редакция PII и типизированные события — перенести redact_metadata и AUDIT_EVENT_TYPES внутрь database/_log_audit_event_atomic, а не держать вторую подсистему.

### 10. Четыре реализации проверки «я админ» плюс 203 инлайновых сравнения с ADMIN_TELEGRAM_ID

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`app/utils/security.py:194`

**Дефект.** Проверка админа существует как: app/utils/security.py:194 is_admin (с валидацией telegram_id), app/services/admin_auth.py:241 is_admin (однострочник), app/utils/security.py:218 require_admin, app/utils/security.py:240 декоратор admin_only. Из них декоратор используется в двух местах (app/handlers/admin/migration.py:43, base.py:17), admin_auth.is_admin — только в дашборде (deps.py:26/40, auth.py:138/167/218/243, ws.py:42). Основная масса хендлеров пишет проверку руками: 203 вхождения `== config.ADMIN_TELEGRAM_ID` / `!= config.ADMIN_TELEGRAM_ID` в .py (например app/handlers/admin/audit_subs.py:365,416,429,473,538; promo_trial.py:303,356,391,436,450,470). Рядом лежит мёртвый app/utils/security.py:310 require_ownership и мёртвый app/utils/security.py:102 validate_callback_data (Tuple-версия), у которого есть живой одноимённый двойник с другим типом возврата — app/handlers/common/utils.py:296 (bool).

**Когда ломается.** Появляется второй администратор или роль «оператор» — правку нужно внести в 203 местах, и любое пропущенное место остаётся доступным только одному ID либо, наоборот, открывается лишним людям. Отдельная ловушка: если кто-то импортирует validate_callback_data из app.utils.security вместо app.handlers.common.utils, проверка `if not validate_callback_data(x)` всегда даст False (непустой кортеж истинен) и валидация молча отключится.

**Что сделать.** Оставить одну реализацию is_admin (app/services/admin_auth.py:241) и один декоратор admin_only, механически заменить 203 инлайновых сравнения на вызов декоратора/хелпера. Удалить app/utils/security.py:102 validate_callback_data и :310 require_ownership как мёртвые, либо переименовать первую, чтобы исключить путаницу с bool-версией.

### 11. Разбиение database/admin.py (4917 строк) по ответственностям

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`database/admin.py:1`

**Дефект.** Модуль смешивает аналитику, рассылки, финансовые операции с балансом, админские гранты, скидки/VIP, подарки, восстановление premium и работу с ошибками платежей. Отдельно выделяется get_users_by_segment (1431) длиной около 500 строк и finalize_balance_purchase (2470) + finalize_balance_topup (2745) — денежные транзакции, спрятанные посреди аналитических запросов.

**Когда ломается.** Денежные функции finalize_balance_purchase/finalize_balance_topup лежат в одном файле с сотней read-only отчётных запросов. Любой рефакторинг «отчётов» рискует задеть транзакционный код, а code review не масштабируется на 4917 строк.

**Что сделать.** Разрезать на пакет database/admin/ : (1) analytics.py — 222,510,564,594,653,690,825,881,1092,1147,1229,3505,3524,3542,3567,3593,3883,3961,4033,4061,4139,4212; (2) broadcasts.py — 323,398,408,426,499,1305,1313,1355,1385,1413,1431,1942,1961,1983,2065,2083,2095,2106,2191; (3) balance_purchases.py — finalize_balance_purchase 2470, finalize_balance_topup 2745 (денежное ядро, отдельные тесты); (4) access_grants.py — admin_grant_access_atomic 2258, admin_grant_access_minutes_atomic 2964, admin_revoke_access_atomic 3151, admin_delete_user_complete 4279; (5) discounts_vip.py — 3230,3260,3303,3327,3360,3403,3428,3461; (6) gifts.py — 4364,4374,4410,4427,4566; (7) premium_recovery.py — 3635,3804,4592,4610,4633,4667,4700,4729,4774,4808,4834,4863; (8) payment_errors.py — 930,997,1049; (9) incidents.py — 2123,2153; (10) exports.py — 29,59,71,91,120,289. Реэкспорт из database/admin/__init__.py, чтобы database/__init__.py не трогать.

### 12. Мёртвый legacy-API ручной модерации платежей в database/subscriptions.py (~350 строк, деньги)

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`database/subscriptions.py:2362`

**Дефект.** Функции старого потока «пользователь прислал чек → админ подтвердил» экспортируются из database/__init__.py, но вызывающих вне database/ нет: create_payment (subscriptions.py:90, экспорт __init__.py:110), get_pending_payment_by_user (72, экспорт 109 — зовётся только из create_payment), update_payment_status (191, экспорт 113), approve_payment_atomic (2362-2618, 257 строк, экспорт 138), get_subscriptions_needing_reminder (2629, экспорт 140), mark_reminder_sent (2660, экспорт 141). Живой поток — pending_purchases + finalize_purchase (4394). Из старого API реально используются только get_last_approved_payment (auto_renewal.py:187), get_pending_payments (app/api/dashboard/routes/payments.py:42) и get_payment (routes/payments.py:147).

**Когда ломается.** approve_payment_atomic содержит полноценную двухфазную выдачу доступа с обращением к vpn_utils.safe_remove_vless_user_with_retry (2567, 2584) и синком Remnawave (2601). Эти 257 строк не покрыты тестами и расходятся с finalize_purchase — при попытке «оживить» ручную модерацию она выдаст доступ по устаревшей логике. Пока код мёртв, он раздувает файл и путает поиск по finalize/approve.

**Что сделать.** Подтвердить у владельца, что ручная модерация платежей выведена из эксплуатации. Затем удалить create_payment, get_pending_payment_by_user, update_payment_status, approve_payment_atomic, get_subscriptions_needing_reminder, mark_reminder_sent и снять их экспорты из database/__init__.py. get_last_approved_payment, get_payment, get_pending_payments оставить.

### 13. Разбиение database/subscriptions.py (5112 строк) по ответственностям

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`database/subscriptions.py:1`

**Дефект.** Один модуль держит одиннадцать несвязанных ответственностей: legacy-платежи, жизненный цикл подписки, триалы, спецпредложения, перевыпуск ключей, запись аудита, напоминания, промокоды, реферальную отчётность, расчёт цены, pending-покупки и финализацию покупки. Итог — 5112 строк, из которых finalize_purchase занимает 718 (4394-5112). Любая правка требует чтения всего файла, а pyright по нему даёт основную массу reportOptionalMemberAccess.

**Когда ломается.** Две параллельные задачи (например, правка промокодов и правка триалов) неизбежно конфликтуют в одном файле. Обзор PR по такому файлу невозможен — diff теряется в контексте.

**Что сделать.** Разрезать на пакет database/subscriptions/ со следующими модулями, границы уже проходят по существующим блокам: (1) payments_legacy.py — 72,90,150,160,191,2362,2619 (кандидат на удаление, см. отдельную находку); (2) core.py — check_and_disable_expired_subscription 222, set_combo_flag 386, set_bypass_only_flag 403, ensure_bypass_only_subscription 420, get_subscription 494, get_subscription_any 525, admin_switch_tariff 546, has_any_subscription 579, get_active_subscription 835, update_subscription_uuid 859, get_all_active_subscriptions 886; (3) trials.py — 609,628,648,660,686,704; (4) special_offers.py — 756,779,829; (5) provisioning.py — reissue_subscription_key 905, reissue_vpn_key_atomic 1192, grant_access 1353; (6) audit_write.py — 989,1040,1087,1117,1146; (7) reminders.py — 2629,2660,_REMINDER_FLAG_UPDATE_QUERIES 2670,2711,2732,2751,3307; (8) promocodes.py — 2776-3270 (get_promo_code, get_active_promo_by_code, has_active_promo, check_promo_code_valid, log_promo_code_usage, get_promo_stats, generate_promo_code, create_promocode_atomic, reactivate/deactivate, _consume_promo_in_transaction, validate/consume_promocode_atomic); (9) referral_reports.py — 3350,3403,3604,3697,3839,3931; (10) pricing.py — calculate_final_price 3970; (11) pending_purchases.py — 4106,4134,4202,4245,4279,4298,4328,4353,4374; (12) finalization.py — finalize_purchase 4394. Сохранить обратную совместимость реэкспортом из database/subscriptions/__init__.py, чтобы database/__init__.py не менялся в том же коммите.

### 14. handlers.py — 950 из 1072 строк дублируют app/handlers/common/*, снаружи используется одна функция

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`handlers.py:39`

**Дефект.** Из handlers.py снаружи импортируется ровно одно имя — show_payment_method_selection (app/handlers/payments/callbacks.py:20, app/handlers/callbacks/navigation.py:1635, app/handlers/admin/broadcast.py:464/673/1010). Всё остальное — точные копии функций из app/handlers/common: safe_resolve_username (39 ↔ common/utils.py:303), safe_resolve_username_from_db (71 ↔ 336), safe_edit_text (248 ↔ 407), _markups_equal (332 ↔ 372), safe_edit_reply_markup (368 ↔ 584), get_promo_session/create_promo_session/clear_promo_session (400/434/474 ↔ 607/639/679), _get_promo_error_keyboard (479 ↔ common/keyboards.py:625), ensure_db_ready_message/callback (492/547 ↔ common/guards.py:16/59), get_language_keyboard (568 ↔ keyboards.py:57), format_text_with_incident (590 ↔ utils.py:684), get_main_menu_keyboard (610 ↔ keyboards.py:79), get_back_keyboard (671 ↔ 340), get_profile_keyboard (681 ↔ 350), get_about_keyboard (740 ↔ 533), get_service_status_keyboard (759 ↔ 551), get_instruction_keyboard (776 ↔ 568), get_reissue_notification_* (822/832 ↔ 616/utils.py:748), все broadcast/admin-клавиатуры (846-964), get_reissue_lock (973 ↔ utils.py:742). Роутер handlers.py:560 создан, но ни одного @router в файле нет (grep: единственное вхождение — комментарий на строке 1066) и в диспетчер он не включён (main.py:184 включает только app.handlers.router).

**Когда ломается.** Правку в get_main_menu_keyboard или safe_edit_text делают в одном из двух файлов. Второй остаётся старым. Поскольку show_payment_method_selection живёт в handlers.py и использует локальный safe_edit_text (строка 1056), часть экранов рендерится по старой копии, часть — по новой.

**Что сделать.** Перенести show_payment_method_selection в app/handlers/payments/ (по смыслу — рядом с payments/callbacks.py), переключить пять импортов на новое место, удалить handlers.py целиком. Перед удалением сравнить содержимое парных функций и зафиксировать расхождения — они могут скрывать невыпущенные фиксы.

### 15. Ветка xray (xray_sync.py, xray_api/, scripts/full_xray_resync.py) держится в дереве, но по решению владельца мертва

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`xray_sync.py:60`

**Дефект.** xray_sync.py импортируется только в main.py:39 (в try/except с флагом XRAY_SYNC_AVAILABLE) и запускается на main.py:547 через start_xray_sync_safe. Его full_sync (строка 60) зовёт vpn_utils.ensure_user_in_xray (xray_sync.py:93) и vpn_utils.check_xray_health (180). ensure_user_in_xray (vpn_utils.py:507) ведёт в add_vless_user/update_vless_user, которые при PURCHASE_FLOW_REMNAWAVE=True — no-op (vpn_utils.py:198, 561). Отдельный сервис xray_api/main.py (468 health_check, 675 update_user, 396 find_client_in_config) не импортируется из бота вообще. scripts/full_xray_resync.py:65 делает то же самое из CLI.

**Когда ломается.** Воркер стартует на каждом деплое, ходит в БД за всеми активными подписками (xray_sync.py:28) и крутит цикл вхолостую — тратит соединения пула и создаёт ложное впечатление, что синхронизация работает. Если кто-то выключит PURCHASE_FLOW_REMNAWAVE, воркер начнёт реально писать в декоммиссированный xray.

**Что сделать.** По решению владельца #1 удалить: xray_sync.py, директорию xray_api/, scripts/full_xray_resync.py, флаг XRAY_SYNC_ENABLED и блок main.py:38-44, 536-557. Из vpn_utils.py убрать xray-ветки после no-op guard (add_vless_user 213-406, update_vless_user 568-628, remove_vless_user 647-801) и функции ensure_user_in_xray, check_xray_health.

### 16. _schedule_invoice_deletion скопирована в пять модулей с расходящимися сигнатурами

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/handlers/callbacks/gift.py:48`

**Дефект.** Функция удаления инвойса по таймауту определена пять раз: app/handlers/payments/steam_purchase.py:85, app/handlers/payments/telegram_premium.py:89, app/handlers/payments/telegram_stars_purchase.py:78, app/handlers/callbacks/payments_callbacks.py:44 и app/handlers/callbacks/gift.py:48. Четыре версии принимают message_id: int, версия в gift.py принимает invoice_message: Message и берёт .message_id внутри. Рядом такая же ситуация с _auto_delete_lava_msg (app/handlers/traffic.py:34 и app/handlers/callbacks/gift.py:39) и _format_bytes (app/workers/traffic_monitor.py:24, app/handlers/traffic.py:339, app/handlers/admin/traffic_admin.py:29).

**Когда ломается.** Меняют INVOICE_TIMEOUT или добавляют логирование неудалённого инвойса — правка попадает в один-два файла из пяти. Инвойсы Steam остаются висеть, а Premium удаляются, и наоборот. Различие сигнатур в gift.py гарантирует TypeError при попытке унифицировать вызовы копипастой.

**Что сделать.** Вынести один _schedule_invoice_deletion(bot, chat_id, message_id, timeout) в app/utils/telegram_safe.py (модуль уже существует), заменить пять копий импортом, в gift.py передавать invoice_message.message_id на месте вызова. То же для _auto_delete_lava_msg и _format_bytes.

### 17. app/handlers/common/__init__.py — фасад реэкспортов, которым никто не пользуется

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/handlers/common/__init__.py:1`

**Дефект.** Файл на 85 строк реэкспортирует 38 имён из guards/utils/keyboards и объявляет __all__. Ни один модуль в репозитории не делает `from app.handlers.common import ...` — все импортируют напрямую из подмодулей (app.handlers.common.utils, app.handlers.common.keyboards, app.handlers.common.guards, app.handlers.common.states). Проверено grep по всему дереву. Именно этот фасад — единственная «ссылка» на мёртвые get_connect_button (keyboards.py:33), get_profile_keyboard_with_copy (455), get_profile_keyboard_old (460), get_vpn_key_keyboard (480), get_tariff_keyboard (508, помечена DEPRECATED в docstring на строке 511), get_broadcast_type_keyboard (646), detect_platform (utils.py:702), format_promo_stats_text (utils.py:707), из-за чего они выглядят живыми для статических анализаторов.

**Когда ломается.** Не ломается в рантайме. Но любой инструмент подсчёта мёртвого кода видит эти функции «использованными» и не сообщает о них — фасад маскирует ~150 строк мёртвых клавиатур.

**Что сделать.** Свести app/handlers/common/__init__.py к пустому docstring, затем прогнать анализ мёртвого кода заново и удалить проявившиеся функции: get_connect_button, get_profile_keyboard_with_copy, get_profile_keyboard_old, get_vpn_key_keyboard, get_tariff_keyboard, get_broadcast_type_keyboard, detect_platform, а также format_promo_stats_text (в app/handlers/admin/stats.py:29 есть своя рабочая версия).

### 18. get_broadcast_type_keyboard и её кнопки broadcast_type:* мертвы с обеих сторон

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/handlers/common/keyboards.py:646`

**Дефект.** Функция определена трижды (handlers.py:856, app/handlers/admin/keyboards.py, app/handlers/common/keyboards.py:646) и не вызывается ни разу — только реэкспорт в common/__init__.py:38 и 79. Кнопки, которые она строит (broadcast_type:info|maintenance|security|promo, common/keyboards.py:649-652 и handlers.py:859-862), по данным baseline входят в список 12 мёртвых кнопок — обработчиков для них нет. То есть мертвы и продюсер, и потребитель.

**Когда ломается.** Если кто-то подключит эту клавиатуру в поток создания рассылки, все четыре кнопки будут молча ничего не делать — админ выберет тип рассылки и застрянет на экране.

**Что сделать.** Удалить get_broadcast_type_keyboard из всех трёх файлов и связанные i18n-ключи broadcast_type_*. Если типизация рассылок нужна как фича — заводить заново вместе с обработчиками.

### 19. app/utils/message_guard.py — мёртвый модуль защиты от edit_text по фото-сообщениям

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/utils/message_guard.py:24`

**Дефект.** Модуль (90 строк) экспортирует is_photo_message (16) и safe_replace_screen (24). Ни имя модуля, ни имена функций не встречаются нигде вне самого файла (grep по всему дереву). При этом реальная проблема, которую он решает (edit_text на сообщении с фото), обрабатывается в другом месте — app/handlers/common/utils.py:407 safe_edit_text ловит TelegramBadRequest и делает fallback, и в app/handlers/common/screens.py:59 _send_screen_photo.

**Когда ломается.** Прямого сбоя нет. Модуль создаёт третий вариант навигации между экранами (safe_edit_text / _send_screen_photo / safe_replace_screen), из которых работают только два — при выборе «правильного» подхода разработчик может взять мёртвый.

**Что сделать.** Удалить app/utils/message_guard.py. Если нужна is_photo_message — перенести её в app/handlers/common/utils.py рядом с safe_edit_text.

### 20. Таблица vpn_keys — реликт пула ключей, ни одного обращения из кода

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`database/core.py:839`

**Дефект.** Таблица создаётся в database/core.py:839 (CREATE TABLE IF NOT EXISTS vpn_keys с колонками vpn_key, is_used, assigned_to, assigned_at) и в migrations/001_init.sql:41, а migrations/025 конвертирует её assigned_at в TIMESTAMPTZ. При этом ни одного SELECT/INSERT/UPDATE/DELETE по vpn_keys в коде нет — grep по шаблонам «FROM vpn_keys / INTO vpn_keys / UPDATE vpn_keys» находит только строку ALTER в миграции 025. Ключи сегодня хранятся в колонке subscriptions.vpn_key.

**Когда ломается.** Стартовый DDL берёт лишний ACCESS EXCLUSIVE на бесполезную таблицу (см. находку про core.py). Миграция 025 гоняет ALTER по ней же. При инвентаризации схемы таблица создаёт ложное впечатление, что есть пул предвыделенных ключей.

**Что сделать.** Удалить CREATE TABLE vpn_keys из database/core.py:837-846 и добавить миграцию DROP TABLE IF EXISTS vpn_keys после подтверждения у владельца, что данных там нет.

### 21. Точечно мёртвые функции: _bulk_fetch_panel_expires_at, get_biz_price_stars, site_sync.periodic_sync, admin_callbacks.py

**P3** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: низкий

`database/reconciliation.py:114`

**Дефект.** Функции без единого вызывающего, подтверждено AST-сканом всех Name/Attribute/ImportFrom по репозиторию: database/reconciliation.py:114 _bulk_fetch_panel_expires_at (приватная, вызовов нет — рядом живёт _parse_remnawave_dt:155, который зовут); config.py:249 get_biz_price_stars (соседняя get_biz_price:241 используется, эта — нет; к тому же ссылается на TARIFFS_STARS, объявленный ниже на строке 259); app/services/site_sync.py:119 check_balance, :193 get_user_status, :242 periodic_sync (остальные функции модуля живые — sync_balance зовут из app/handlers/admin/finance.py:1008, notify_subscription_extend из access.py:751); app/core/system_state.py:367 create_default_system_state; app/core/pool_monitor.py:19 get_last_pool_wait_spike_monotonic; app/services/remnawave_api.py:381 reset_user_traffic; app/services/activation/service.py:128 is_activation_allowed. Плюс файл-заглушка app/handlers/callbacks/admin_callbacks.py — три строки (Router() без единого обработчика), нигде не импортируется.

**Когда ломается.** Каждая из них выглядит частью рабочего API своего модуля. Например is_activation_allowed в сервисе активаций читается как обязательная проверка перед выдачей доступа — но она не вызывается, и реальный поток активации её не проходит. reset_user_traffic в remnawave_api выглядит как готовый способ сбросить трафик, а фактически не протестирован ни разу.

**Что сделать.** По каждой принять решение отдельно: _bulk_fetch_panel_expires_at, get_biz_price_stars, create_default_system_state, get_last_pool_wait_spike_monotonic, admin_callbacks.py — удалить. site_sync.check_balance/get_user_status/periodic_sync — уточнить у владельца, планируется ли интеграция с сайтом сверх текущего воркера (app/workers/site_sync_worker.py использует только sync_balance и sync_referrals). is_activation_allowed и reset_user_traffic — проверить у владельца, не потеряна ли проверка в потоке активации; это может быть не мёртвый код, а забытый вызов.

### 22. Мёртвая database.mark_reminder_sent и legacy-колонка subscriptions.reminder_sent

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`database/subscriptions.py:2660`

**Дефект.** database.mark_reminder_sent (строка 2660, docstring прямо говорит «старая функция, для совместимости») экспортируется из database/__init__.py:141 и не вызывается нигде. Одноимённая живая функция — app/services/notifications/service.py:335 с другой сигнатурой (telegram_id, reminder_type, conn), её зовёт reminders.py:213 и :292 через notification_service. Колонка reminder_sent (объявлена database/core.py:582 и migrations/001_init.sql:31) читается только в мёртвой get_subscriptions_needing_reminder (subscriptions.py:2653) и сбрасывается в FALSE при выдаче доступа (1698, 1911, 2226); в экспорте попадает в CSV (app/handlers/admin/export.py:62). Актуальные флаги — reminder_7d_sent/3d/1d/24h/6h/3h.

**Когда ломается.** Одинаковое имя двух функций с разными сигнатурами: вызов database.mark_reminder_sent(telegram_id, reminder_type) упадёт TypeError, а вызов database.mark_reminder_sent(telegram_id) молча выставит устаревший флаг, который никем не проверяется — напоминание будет отправлено повторно.

**Что сделать.** Удалить database.mark_reminder_sent (subscriptions.py:2660-2667) и снять экспорт из database/__init__.py:141. Отдельным шагом решить судьбу колонки reminder_sent: убрать её сбросы на 1698/1911/2226 и колонку из экспорта, затем DROP COLUMN миграцией.

### 23. Таблицы admin_notification_log и admin_notification_templates создаются и никогда не используются

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`migrations/036_notification_overhaul.sql:39`

**Дефект.** Миграция 036 создаёт admin_notification_templates (строка 39) и admin_notification_log (строка 51). Имена обеих таблиц не встречаются больше нигде: ни в .py, ни в других .sql, ни в дашборде (проверено grep по .py/.sql/.ts/.tsx). Соседние таблицы из той же миграции живут: cashback_promotions читается в app/handlers/admin/notifications.py:552/635/651/818 и database/users.py:1832, user_cashback_multipliers — в notifications.py:664/879 и users.py:1821.

**Когда ломается.** Прямого сбоя нет. Но админ, глядя на схему, считает, что история админских уведомлений пишется, и строит на этом отчёт — таблицы всегда пусты. Реальный лог уведомлений ведётся через automated_notification_sends.

**Что сделать.** Подтвердить у владельца, что шаблоны админских уведомлений не планируются, и добавить миграцию DROP TABLE для обеих. Если планируются — зафиксировать это в задаче, чтобы таблицы не считались мусором.

### 24. vpn_utils.upgrade_vless_user и remove_plus_inbound — мёртвые вызовы к декоммиссированному API, без no-op guard

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`vpn_utils.py:407`

**Дефект.** upgrade_vless_user (строка 407, POST /upgrade-to-plus/{uuid}) и remove_plus_inbound (строка 463, POST /remove-plus/{uuid}) не вызываются нигде в репозитории (проверено AST-сканом по всем именам). В отличие от add_vless_user/update_vless_user/remove_vless_user, у них нет guard'а на PURCHASE_FLOW_REMNAWAVE — они сразу идут в config.XRAY_API_URL (строки 413-420 и 469-476).

**Когда ломается.** Если кто-то восстановит апгрейд basic→plus и позовёт upgrade_vless_user, запрос уйдёт на выключенный xray-хост и упадёт по таймауту либо, что хуже, на чужой хост, если XRAY_API_URL переиспользован.

**Что сделать.** Удалить обе функции вместе с остальной xray-веткой (решение владельца #1). Апгрейд тарифа сегодня делается через app/services/remnawave_premium.

---

## Опровергнутые находки

Скептик показал, что эти находки неверны. Оставлены для истории.

- **Роутер admin/reconcile.py не подключён — 399 строк админ-функционала недостижимы** (`app/handlers/admin/reconcile.py:21`) — Механический факт верен, но интерпретация и сценарий отказа — нет.

ЧТО ПОДТВЕРДИЛОСЬ. app/handlers/admin/reconcile.py:21 действительно объявляет admin_reconcile_router = Router(), обработчики на строках 203 (admin:rmn_reconcile) и 290 (admin:rmn_fix). В app/handlers/admin/__init__.py его нет ни в импортах (строки 3-25), ни в include_router (29-51). Единственная сборка — app/handlers/__init__.py -> main.py:184 dp.include_router(root_router). Grep по всему дереву (кроме graphify-out/__pycache__) даёт ровно 6 вхождений «rmn_reconcile|rmn_fix|admin_reconcile_router» — все внутри самого reconcile.py. Динамической загрузки роутеров нет (importlib/pkgutil только в тестах и validate_language_content.py).

ПОЧЕМУ НАХОДКА ОПРОВЕРГНУТА.

1) Отключение НАМЕРЕННОЕ и задокументировано. git log -S "admin_reconcile_router" -- app/handlers/admin/__init__.py даёт коммит c074da9 «fix(recovery): ... + remo

- **reissue_subscription_key гарантированно падает: legacy-путь через no-op add_vless_user** (`database/subscriptions.py:956`) — Механизм описан верно, но сценарий отказа не воспроизводится: оба указанных хендлера недостижимы из UI — ни одна кнопка в репозитории не порождает их callback_data.

Что подтвердилось (код прочитан):
- database/subscriptions.py:956 действительно вызывает vpn_utils.reissue_vpn_access.
- vpn_utils.py:198-211 — add_vless_user при PURCHASE_FLOW_REMNAWAVE возвращает stub с "vless_url": "" (строка 207).
- config.py:585 — PURCHASE_FLOW_REMNAWAVE = _envbool("PURCHASE_FLOW_REMNAWAVE", True), дефолт True.
- vpn_utils.py:629-645 — remove_vless_user тоже no-op (return None), т.е. до add_vless_user поток доходит без исключения.
- vpn_utils.py:903-906 — `if not vless_url: raise VPNAPIError("VPN API did not return vless_link during reissue")`. Т.е. ЕСЛИ функцию вызвать, она гарантированно бросит исключение.

Что опровергнуто — достижимость:
1. app/handlers/admin/base.py:273 слушает F.data.startswith("a

---

## Что прочитано, а что нет

ПРОЧИТАНО ЦЕЛИКОМ ИЛИ ПОЧТИ ЦЕЛИКОМ: handlers.py (структура всех 36 определений + тело show_payment_method_selection 988-1064 + хвост файла), vpn_utils.py (все определения + no-op guard'ы 190-230, 545-580, 628-665, reissue_vpn_access 848-921, upgrade_vless_user/remove_plus_inbound 407-475), xray_sync.py, migrations.py, broadcast_service.py (списки определений), app/handlers/common/__init__.py, app/handlers/common/keyboards.py и utils.py (полные списки определений + ключевые тела), app/utils/message_guard.py, app/core/i18n/__init__.py и types.py, app/utils/audit.py (список определений), app/services/vpn_client.py (шапка + список), app/handlers/admin/__init__.py, app/handlers/callbacks/__init__.py, app/handlers/user/__init__.py, app/handlers/payments/__init__.py, app/handlers/__init__.py, app/handlers/admin/reconcile.py (шапка + точки входа), app/services/pricing/__init__.py (список), app/services/subscriptions/__init__.py и service.py:30-130, database/core.py:378-500 и 830-900 и 1160-1233, config.py:108-262.

ПРОСМОТРЕНО ВЫБОРОЧНО (по grep/AST, тела не читались полностью): database/subscriptions.py — прочитаны 905-990, 1192-1262, 2655-2700, 3970-4030 и полный список определений верхнего уровня; database/admin.py — только полный список определений (4917 строк тел не читались); app/handlers/admin/broadcast.py, access.py, base.py, stats.py, audit_subs.py, audit_db_dates.py, migration.py — импорты, декораторы и отдельные фрагменты; auto_renewal.py:205-245; app/handlers/traffic.py:225-240 и 812-830; app/handlers/callbacks/gift.py:185-215; app/utils/security.py (список + is_admin/validate_callback_data); app/services/site_sync.py (список); app/api/dashboard/** — только карта роутеров и точечные вызовы database.*.

НЕ ЧИТАЛОСЬ: тела большинства функций в database/admin.py, database/users.py, database/reconciliation.py, database/farm.py, database/marketing_links.py; app/handlers/game.py, app/handlers/admin/finance.py, notifications.py, bypass_gift.py, promo_trial.py, bonus.py, farm_storm.py, stage_users.py, apple_id_delivery.py, spotify_delivery.py целиком; app/api/dashboard/routes/* (кроме payments.py, reconciliation.py, pricing.py фрагментарно); React-дашборд в dashboard/ не анализировался вообще — возможные обращения фронта к API как «использование» БД-функций не учитывались, что могло дать ложные срабатывания по мёртвым функциям database/*; SQL-миграции читались только на предмет CREATE TABLE и двух конкретных файлов (001_init.sql, 036_notification_overhaul.sql, 025_full_timestamptz_alignment.sql); tests/ и load_tests/ не анализировались как источник использования (учитывались только при проверке мёртвости).

ОГРАНИЧЕНИЯ ДОКАЗАТЕЛЬСТВА: мёртвость проверялась AST-сканом всех Name/Attribute/ImportFrom-узлов по .py-файлам плюс grep по строковым литералам. Динамические обращения через getattr(database, name), importlib или строковые ключи не отслеживаются — для функций, экспортируемых из database/__init__.py, это основной остаточный риск. Продовых логов и БД нет, поэтому «редко используемый живой экран» и «мёртвый экран» статикой не различаются; там, где недостижимость доказана только отсутствием ссылок, но модуль подключён к роутеру, я не заявлял мёртвость. Одна находка помечена confidence=medium (точечно мёртвые функции) — по is_activation_allowed и reset_user_traffic нужно решение владельца: это может быть не мусор, а потерянный вызов.
