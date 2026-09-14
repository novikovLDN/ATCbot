# Фаза 0. Разведка — ATCbot (Atlas Secure) на `origin/main`

- Дата: 2026-09-13
- База: `origin/main` @ `cf8205fc` (2026-09-09, прод). Ветка: `refactor/audit-2026-09`
- Режим: только чтение, прод-код не менялся. Решения владельца и границы работ — в `docs/audit/SCOPE.md`.

> Первая версия отчёта строилась по устаревшему снимку (`3ab7f84`, 11 июля; ветка отставала от main на 551 коммит). Она сохранена в `refactor/audit-2026-09-stale`. Всё ниже проверено на актуальном main.
>
> **[✔]** = перепроверено вручную по коду. Остальное — находки read-only агентов со ссылками file:line.

---

## 1. Стек

| Слой | Что используется |
|---|---|
| Процесс | **Один процесс**: бот (aiogram 3, webhook) + FastAPI (uvicorn) + ~13 воркеров `asyncio.create_task`. Advisory lock Postgres обеспечивает единственный инстанс |
| БД | PostgreSQL 16, **asyncpg, raw SQL, без ORM** |
| Миграции | самописный `migrations.py`, 74 файла `.sql`, последний `080`. **Downgrade нет.** Плюс **128 inline DDL** в `init_db` при каждом старте (`database/core.py:378-1180`). [✔] Дубль версии `006` |
| Провайдеры | Platega (СБП, карта, intl, рекуррентные подписки), **WATA** (карта, СБП, T-Pay), CryptoBot, Lava (не используется, но подключена), Telegram Payments и Stars, баланс |
| Remnawave | 3.4.3 (патч безопасности поверх 3.4.2). Две сущности на пользователя: premium + bypass; поверх них sub-aggregator `/a/{token}` |
| Админка | React 18 + Vite SPA (WebAuthn + пароль + magic-link JWT), бэкенд `app/api/dashboard/` |
| i18n | остались только `ru` и `en` (de, ar, kk, tj, uz удалены) |
| Деплой | Railway, Docker multi-stage (содержит Node-стадию для отключённой фичи incy) |

## 2. Метрики (факт)

| Проверка | Результат |
|---|---|
| Python | 270 файлов, около 104 тыс. строк (без тестов около 97 тыс.) |
| `ruff check .` | 5 ошибок, среди них **[✔] F821 `admin/audit_subs.py:667`**: неопределённая `uuid` роняет цикл admin-аудита после PATCH |
| ruff расширенный | 205 F401, 81 F841, **146 функций со сложностью > 10 (C901)** |
| `pytest` | **323 passed / 46 failed** |
| Файлы > 2000 строк | `database/admin.py` 5171, `database/subscriptions.py` 5162, `payments_callbacks.py` 2600, `admin/broadcast.py` 2584, `navigation.py` 2301, `admin/access.py` 2292, `database/users.py` 2054, `admin/stats.py` 2018 |
| Модулей, достижимых из `main.py` | 195 из 245 (AST-анализ импортов, включая ленивые) |

## 3. P0 — деньги, безопасность, доступ пользователей

### P0-A. [✔] Поддельный вебхук Lava: неограниченный баланс и бесплатные покупки

1. Роут `/webhooks/lava` зарегистрирован (`payment_webhook.py:310`). Подпись `_verify_webhook_signature` нигде не вызывается (`lava_service.py:178`). Проверка через API Lava только пишет warning.
2. `lookup_pending_purchase` **не проверяет провайдера** покупки: подходит любая pending-покупка, в том числе созданная через Platega или WATA.
3. Lava передаёт **сумму из вебхука**, если она отличается от ожидаемой.
4. `finalize_purchase` принимает любую переплату. `increase_balance(amount=amount_rubles)` зачисляет присланную сумму.

Итог: `POST /webhooks/lava {"order_id": "<свой purchase_id пополнения>", "status": "success", "amount": 1000000}` зачислит 1 000 000 ₽ на баланс.

Условие: в Railway заданы `PROD_LAVA_JWT_TOKEN` и `PROD_LAVA_SHOP_ID`.

**Решение:** владелец удаляет env Lava в Railway. Код Lava удаляем в фазе чистки. Отдельно исправляем в ядре: проверка провайдера покупки и зачисление **ожидаемой**, а не присланной суммы.

### P0-B. Подпись WATA проверяется fail-open

Нет публичного ключа → вебхук принимается (`wata_service.py:285-287`). Нет библиотеки `cryptography` → тоже принимается (`:289-295`). Ключ кэшируется только в памяти процесса. В сочетании с пунктами 2–4 из P0-A это та же уязвимость, пока ключ не загружен. Нет суммы в вебхуке → подставляется ожидаемая, а при расхождении пишется только warning (`:471-480`).

### P0-C. Двойное и лишнее начисление bypass-ГБ

`provision_subscription` теперь начисляет ГБ только при **создании** bypass (`purchase_flow.py:239-265`). Путь внешних вебхуков починен (`confirmation.py:620-629`). Но 5 вызовов `renew_remnawave_user_bg` добавляют ещё +10 ГБ:

| Место | Итог |
|---|---|
| `admin/access.py:745` | новая выдача: 20 ГБ. В ветке «минуты» `days_int` не определена → NameError → 0 ГБ |
| `payments_callbacks.py:865` (баланс) | Basic: 20 ГБ. **Combo новый: 95 ГБ, продление: 85 ГБ.** Параллельный read-modify-write теряет обновления |
| `payments_messages.py:1249` (Telegram) | Basic: 20 ГБ. **Combo новый: 150 ГБ** |
| `user/start.py:337` (подарок) | 20 ГБ |
| `auto_renewal.py:402` | +10 ГБ корректно, **но combo получает 10 вместо ГБ combo** |

Combo по-прежнему хранится как «базовый тариф + `is_combo`», а должен быть отдельным тарифом (`SCOPE.md`).

### P0-D. Платёж прошёл, выдачи нет, провайдер не повторяет

- **[✔ на старом снимке, агент подтвердил на main]** `TransientPaymentError` внутри `process_confirmed_payment` ловится общим `except` и возвращает `{"status":"error"}`. Все роуты отдают `JSONResponse(result)` с кодом **200** (`payment_webhook.py:86,230,284,350`). Провайдер не повторяет. Пропадают ГБ продления и уведомление пользователю. «Purchase not found» тоже отвечает 200.
- **Platega-рекуррент:** `record_charge` коммитит списание **до** `grant_access` (`platega_service.py:733` → `:752`). Если выдача упала, ретрай Platega считается дублем, и пользователь заплатил без продления. Алерта нет. Суммы не сверяются, строки в `payments` не создаются.
- **Своей очереди повторной выдачи нет.** Ошибки `renew_bg`/`create_remnawave_user` глотаются в лог. Platega повторяет вебхук **не больше 3 раз с интервалом 5 минут** (`docs/providers/platega_api.md` §4).

### P0-E. Блокировка pending-покупки не работает

`SELECT … FOR UPDATE SKIP LOCKED` выполняется вне транзакции (`subscriptions.py:4463`), и блокировка снимается сразу. Два одновременных вебхука оба запускают провижининг. Второй падает на `UPDATE != 1`, возвращает 200 и шлёт ложный алерт «PERMANENT».

### P0-F. [✔] Остальное по деньгам

- `trial_service.activate_trial` не существует, но вызывается (`confirmation.py:881`, `payments_callbacks.py:907`, `payments_messages.py:1289`). Обещанный триал не выдаётся, ошибка проглатывается.
- Пополнение баланса зачисляет сумму вебхука, то есть включая комиссию провайдера (переплата принимается всегда).
- Combo теряет ГБ при автопродлении, рекурренте Platega и replay.
- Stars за bypass-пакет: `price_kopecks=price_stars` (`traffic.py:1382`), учёт в `payments` и `traffic_purchases` испорчен.
- `panel_traffic_audit` читает `usedTrafficBytes` не с того уровня ответа (`:249`, `:349`). «Исправление» выставляет лимит без учёта израсходованного, и пользователь недополучает ГБ.

## 3a. Соответствие протоколам провайдеров (эталон — `docs/providers/*.md`)

| Пункт | Статус | Суть |
|---|---|---|
| Platega: авторизация и секреты | ✅ | `X-MerchantId`/`X-Secret` через `compare_digest`, fail-closed при пустом конфиге |
| Platega: создание платежа | ⚠️ | `metadata.userId` (антифрод) не передаётся в 5 VPN-вызовах: `proxy.py:193`, `game.py:1494`, `traffic.py:972,1321`, `payments_callbacks.py:2329` |
| Platega: разбор трёх типов callback (§6.5) | ❌ | основной обработчик читает lowerCamel. **Коллбэки подписок на общем URL молча отбрасываются** («ignored», 200); разбираются только на отдельном `/webhooks/platega-subscription` |
| Platega: `CHARGEBACKED` | ❌ | не обрабатывается: доступ после возврата сохраняется, алерта нет |
| Platega: нет суммы в callback | ❌ | подставляется ожидаемая цена (`platega_service.py:294-299`), валюта не проверяется |
| Platega: `GET /transaction/{id}` перед выдачей | ❌ | `check_transaction_status` без вызовов. Единственная защита — статический секрет в заголовке; `id` callback не сверяется с `provider_invoice_id` |
| Platega: окно повторов (3×5 мин) | ❌ | нет своего reconciler и эскалации. Алерт о транзиентной ошибке — раз в 300 с |
| Platega-рекуррент | ❌ | списание записывается до выдачи (P0-D); ошибка БД в `record_charge` = «дубль» → 200; `intervalCount` не передаётся; тексты «повторим списание» противоречат доке (PastDue постоянный); отмены через API нет |
| WATA: подпись | ❌ | fail-open (P0-B). Неверная подпись → **200**: при ротации ключа настоящие платежи теряются без повтора |
| WATA: Refund | ❌ | игнорируется |
| WATA: reconciler | ❌ | сканирует pending **чужих** провайдеров (нет фильтра) и помечает их expired по 404. Ищет Paid в объекте ссылки, **поэтому, вероятно, никогда не находит оплату** — нужен `/v2/transactions?orderId=` |
| WATA: 401 (токен живёт 1–12 мес.) | ⚠️ | нет алерта |
| WATA: срок ссылки | ⚠️ | ссылка живёт 60 мин, pending — 30 мин (оплата после истечения всё равно засчитается) |
| Магазин в `confirmation.py:141-181` | ⚠️ | ветка `mark_pending_purchase_paid` **не сверяет сумму** (только warning при расхождении больше 1 ₽). Фиксируем как находку; решение по защите вебхука для магазина — за владельцем |

## 4. P1 — безопасность и корректность

1. **Дашборд.** Bearer-JWT magic-link живёт 30 дней и принимается как полный доступ к API после настройки пароля или passkey (`deps.py:29-43`). `?token=` в WebSocket. Нет rate-limit на `/auth/login` и `/setup`. Нет CSRF-токена.
2. **Секреты в коде.** MTProxy (`config.py:173,179`). `SUB_AGGREGATOR_INTERNAL_SECRET=""` оставляет `/a/_invalidate` без авторизации. `/health` публичный и раскрывает включённые провайдеры.
3. **Remnawave-клиент:**
   - новый `httpx.AsyncClient` на каждый запрос;
   - любой код ≥400 превращается в `None` (401 и 429 не различаются);
   - ретраев почти нет;
   - поиск premium по `telegramId` берёт первое совпадение и может попасть в bypass;
   - admin delete ходит на другой путь (`/api/users/delete/{id}`) в режиме quiet;
   - adopt по username без проверки владельца (`remnawave_service.py:126-168`, `:491-515`).
4. **Отозванный пользователь сохраняет доступ через sub-aggregator.** `revoke()` нигде не вызывается, устаревший ответ отдаётся до 24 часов.
5. **Admin reissue** отправляет legacy-ссылку `build_sub_url` (`reissue.py:63`, `access.py:144,2151`).
6. **Магазин.** Если `send_*_success` падает, оплаченный заказ не виден админу (только лог, `confirmation.py:178`). Сам магазин не трогаем, фиксим только уведомление в ядре.
7. **Пробелы в алертах админу:**
   - ГБ combo и bypass на Telegram-пути и через баланс;
   - `renew_bg` и `create_remnawave_user`;
   - premium-синк автопродления;
   - рекуррент Platega;
   - `not_found` у Platega и CryptoBot;
   - отказы WATA;
   - `activate_trial`;
   - rate-limit алертов (1 в 60 с) теряет часть сообщений.

## 5. Уведомления и рассылки

- **Флаги `reminder_7d_sent` и `reminder_1d_sent` не сбрасываются** при продлении (`subscriptions.py:1523` и др.). После первого цикла приходят только напоминания за 3 дня и за 3 часа.
- **Рассылки:**
  - нет глобального throttle;
  - при RetryAfter задача спит, удерживая семафор;
  - Forbidden не помечает `is_reachable`;
  - нет паузы, отмены, резюма;
  - у `broadcast_log` нет UNIQUE.

  Отложенные рассылки помечаются «выполнено» до отправки, а `SKIP LOCKED` у них вне транзакции.
- `is_reachable` никогда не возвращается в TRUE. `traffic_monitor` ставит флаг даже при неудачной отправке и делает по GET в панель на каждого пользователя.
- `automated_notifications` не дедуплицирует и отдаёт тексты только на русском, в том числе EN-пользователям.

## 6. Мёртвый и лишний код (кандидаты; удаление — после подтверждения)

| Категория | Что |
|---|---|
| Точно мёртвое | `app/core/i18n/`, `admin/reconcile.py` (роутер не подключён), `callbacks/admin_callbacks.py`, `common/decorators.py`, `utils/audit.py`, `utils/message_guard.py`, `validate_language_content.py`, `translation_patch_*.json` + `translation_tasks.json`, `systemd/`, `load_tests/`, заглушки `xray_sync` в `main.py`, конфиги `XRAY_*`/`VPN_*` |
| Node-toolchain | `package.json`, `scripts/incy_encode.mjs`, часть `incy_crypto`, Node-стадия Dockerfile — прод работает на чистом Python |
| Samopis-cutover (завершён) | `admin/migration.py` (1171 строка, запускает subprocess), `migration_broadcast`, `scripts/*samopis*`, около 35 no-op вызовов `vpn_utils`, `subscription_proxy` (флаг выключен) |
| Lava | `lava_service.py` + около 18 ленивых импортов и кнопок (решение: удалить) |
| Дубли | три модуля уведомлений админа (`admin_notifications.py`, `admin_alerts.py`, `admin_notifier.py`), два Remnawave-слоя (`remnawave_service` legacy и `premium`/`bypass`), логика «добавить трафик» скопирована 5 раз |
| БД | **38 публичных функций без вызовов** (subscriptions 11, admin 19, users 5, core 1, traffic 2) |
| Remnawave API | `actions/{reset-traffic,enable,disable,revoke,extend}` без вызовов |
| Документация | 11 аудит-файлов `.md` в корне, 72 файла в `docs/`, большинство устарело |
| ⚠️ Не мёртвое | корневой `handlers.py`: `show_payment_method_selection` на пути `/buy` (вопреки `CLAUDE.md`) |

## 7. Тарифы (факт)

- Базовые тарифы, ГБ и сроки — в `config.py`: `TARIFFS`, `COMBO_TARIFFS`, `TRAFFIC_LIMITS`, `TRAFFIC_PACKS`.
- В БД (миграция 069) лежат только **переопределения цен** и глобальная скидка, и они применяются только к `TARIFFS`. На combo, пакеты и Stars не распространяются.
- Хардкод 10 ГБ: `purchase_flow.py:91`, `traffic.py:310,495`, `screens.py:594`, `user_subscription_links.py:271`, `admin/base.py:892`.
- Trial bypass: 500 МБ в большинстве мест, но **5 ГБ** в `traffic.py:495` и **1 ГБ** в `broadcast_trial_key.py:192`. По решению владельца должно быть 500 МБ везде.

## 8. План работ

| # | Фаза | Суть | Риск |
|---|---|---|---|
| 0 | **Сейчас, владелец** | удалить env Lava в Railway; проверить БД на злоупотребления (SQL в чате) | — |
| 1 | Тестовая сетка + мёртвый код | починить 46 упавших тестов; `01_dead_code.md` с доказательствами → «ок» → удаление пакетами (Lava, samopis, Node, мёртвые модули и функции, старые доки) | низкий |
| 2 | **Платёжное ядро** | `ProvisioningService.apply(entitlement, idempotency_key)` + `provisioning_jobs` + воркер + алерт на любой сбой; явные тарифы `combo_basic/combo_plus`; проверка провайдера и ожидаемой суммы; вебхуки: 5xx только на транзиентное, иначе своя очередь; fail-closed подписи; рекуррент Platega атомарно | **высокий**, флаг `USE_NEW_PROVISIONING` |
| 3 | Remnawave-клиент | один `AsyncClient`, типизированные ошибки (401/404/429/5xx), ретраи на безопасные операции, сверка с OpenAPI 3.4.3, reconcile dry-run | средний |
| 4 | Разбиение god-модулей | `database/{admin,subscriptions,users}.py`, крупные хэндлеры; магазин переносится 1:1 | средний |
| 5 | Дашборд | безопасность auth (bearer-fallback, rate-limit, CSRF); v2 из `atcnew` | средний |
| 6 | Рассылки и уведомления | `notification_log`, глобальный throttle, Forbidden → unreachable, резюм; сброс флагов 7d/1d | средний |
| 7 | Выкатка | RUNBOOK, health, миграции additive | — |
