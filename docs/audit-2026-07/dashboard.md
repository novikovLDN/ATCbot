# Админ-дашборд

Находок: **24** — P0 1, P1 10, P2 8, P3 5. Опровергнуто при перепроверке: 0.

Перепроверялись только три самые тяжёлые находки домена. Остальные помечены «не перепроверено» — это гипотезы, требующие подтверждения перед правкой.

---

### 1. Magic-link JWT остаётся полноценным админ-ключом на 30 дней вопреки документации

**P0** · ✅ подтверждено · уверенность автора находки: high · риск правки: средний

`app/api/dashboard/deps.py:30`

**Дефект.** deps.py:30-42 принимает Bearer-JWT как полноценную админ-авторизацию для ВСЕХ роутеров дашборда. Этот же JWT выдаётся ботом как magic-link (app/handlers/admin/base.py:44-45, URL вида /dashboard/?login=<jwt>) с TTL 30 дней (app/api/dashboard/auth.py:44). Docstring auth.py:21-24 и текст в боте (base.py:60-66) обещают: «после установки пароля ссылка перестанет впускать». Код это не выполняет — SPA сохраняет токен в localStorage (dashboard/src/lib/auth.ts:33-41) и шлёт его в заголовке Authorization на каждый запрос (dashboard/src/lib/api.ts:17-18). Пароль/passkey фактически не являются границей доступа для API.

**Когда ломается.** Ссылка из /admin остаётся в истории чата Telegram и в истории браузера. Любой, у кого есть эта ссылка (старое устройство, синхронизированный Telegram, скриншот, шаринг экрана) или любой XSS в SPA, в течение 30 дней делает curl -H 'Authorization: Bearer <jwt>' DELETE /dashboard/api/users/<id> (каскадное удаление юзера и его платежей), POST /users/<id>/balance, GET /export/users.csv — пароль и passkey не спрашиваются.

**Что сделать.** Разделить bootstrap-токен и доступ к API: magic-link JWT должен приниматься ТОЛЬКО в /auth/setup (и только пока credentials_exist()==False). В require_admin оставить единственный путь — сессионная кука. Уменьшить TTL bootstrap-токена до часов, добавить одноразовость (jti в Redis). Убрать хранение JWT в localStorage.

**Скептик скорректировал severity** до P1.

**Уточнение проверки.** Ядро находки подтверждено, но две детали требуют правки.

(1) Утверждение «SPA сохраняет токен в localStorage» верно лишь частично. dashboard/src/App.tsx:90 и :97 вызывают auth.clear() после успешного setup и login — то есть в нормальном сценарии первого входа bootstrap-JWT из localStorage вычищается. НО есть реальный путь, где он остаётся навсегда: App.tsx:56-58 — если authStatus вернул has_session=true (админ уже залогинен и снова нажал «Открыть дашборд» в боте), stage сразу становится "ready", auth.clear() не вызывается, и captureMagicLink() (auth.ts:33-41) оставляет 30-дневный JWT в localStorage, откуда api.ts:16-18 шлёт его Bearer'ом на каждый запрос. Так что XSS-вектор жив, но не всегда, как заявлено в находке.

(2) Поведение частично намеренное и документировано прямо на месте: deps.py:1-5 «Bearer JWT (legacy) — kept for curl / API testing», auth.py:39-43 объясняет длинный TTL. Это не забытый код, а сознательный бэкдор для тестирования. Проблема в том, что он противоречит соседнему обещанию auth.py:21-24 и тексту бота base.py:60-66 — то есть это дефект дизайна/контракта, а не случайный баг.

Дополнительный факт, усиливающий находку и не упомянутый в ней: JWT нельзя отозвать. clear_credentials() (app/services/admin_auth.py:120-140) удаляет креды, сессии и passkey, но не трогает JWT — jti/blocklist отсутствует, verify_token (auth.py:61-71) проверяет только подпись и exp. Скомпрометированную magic-ссылку можно погасить только ротацией JWT_SECRET, о чём нигде не сказано.

Также ws.py:47-49: для JWT-пути проверяется только payload["role"]=="admin", без admin_auth.is_admin(sub) — слабее, чем в deps.py:40. Отдельный мелкий дефект в той же плоскости.

Снижаю severity до P1: эксплуатация требует владения секретом (сам JWT), а не доступна анонимно. Админ ровно один (is_admin -> telegram_id == config.ADMIN_TELEGRAM_ID, admin_auth.py:241), внешнего пути получить валидный токен без JWT_SECRET нет. P0 стоит резервировать за bypass'ом, не требующим утечки секрета.

<details><summary>Как это проверялось</summary>

1. Место существует и написано ровно то, что заявлено. app/api/dashboard/deps.py:30-42: при отсутствии/невалидности cookie-сессии функция принимает Bearer-JWT, проверяет только verify_token, role=="admin" и is_admin(sub), и возвращает {"role": "admin", "auth": "bearer"} — полноценный админ-контекст. Это тот же самый require_admin, который навешан как router-level dependency на ВСЕ роутеры дашборда (проверено: users.py:24, export.py:14, payments.py:10, promo.py:11, pricing.py:25, broadcasts, settings, reconciliation и т.д. — везде APIRouter(dependencies=[Depends(require_admin)])).

2. Токен действительно тот же. app/handlers/admin/base.py:44-45 строит URL f"{DASHBOARD_BASE_URL}/dashboard/?login={token}" из issue_login_token(), а issue_login_token (app/api/dashboard/auth.py:47-58) подписывает payload {"sub", "role": "admin", exp = now + 30 дней} тем же JWT_SECRET и алгоритмом HS256, который проверяет verify_token (auth.py:61-71), вызываемый из deps.py:31. Никакого различения типов токена (нет claim'а typ/scope="bootstrap") нет — по сигнатуре bootstrap-токен неотличим от «полного» админ-токена. auth.py:44 _MAGIC_TTL_DAYS = 30 — подтверждено.

3. Ничто выше по коду сценарий не исключает. Проверено:

_(обоснование сокращено, полностью — в findings.json)_

</details>

### 2. Нет никакой защиты от перебора пароля на /auth/login

**P1** · ✅ подтверждено · уверенность автора находки: high · риск правки: низкий

`app/api/dashboard/auth.py:183`

**Дефект.** Эндпоинт /dashboard/api/auth/login (auth.py:183-197) не имеет ни лимита попыток, ни блокировки, ни задержки, ни логирования неудач. Глобальный rate-limit (app/core/rate_limit_middleware.py:36) — это aiogram-middleware, подключённая только к dp.message/dp.callback_query (main.py:177-178), к FastAPI-приложению она не применяется. Пароль допускается от 8 символов (auth.py:79), учётка ровно одна.

**Когда ломается.** Атакующий, знающий домен дашборда, гонит словарь по POST /dashboard/api/auth/login. Единственный тормоз — bcrypt rounds=12 (~0.25 с на попытку), который распараллеливается по числу соединений. Успех = полный админ-доступ к деньгам и данным. В логах не остаётся ни одной записи о неудачных попытках.

**Что сделать.** Добавить счётчик неудач по IP и по логину в Redis с экспоненциальной задержкой и временной блокировкой, логировать каждую неудачу (WARN), поднять минимальную длину пароля, рассмотреть общий rate-limit ASGI-middleware для /dashboard/api/auth/*.

**Уточнение проверки.** Одна деталь сценария неверна: «bcrypt ~0.25 с на попытку, который распараллеливается по числу соединений». Распараллеливания нет. admin_auth.verify_password (app/services/admin_auth.py:53-57) — блокирующий синхронный bcrypt.checkpw, вызванный прямо из async def auth_login (app/api/dashboard/auth.py:191) без run_in_threadpool/asyncio.to_thread, а uvicorn запущен единственным процессом без workers (main.py:664-670). Все попытки сериализуются на одном event loop: замер в проектном .venv даёт 0.16 с на checkpw при rounds=12, то есть потолок ~6 попыток/с суммарно независимо от числа соединений (~0.5 млн/сутки), а не N×. Практически это переводит атаку из «мгновенного словаря» в «долгий подбор слабого пароля при известном username». Побочный эффект той же блокировки — обратная сторона: поток запросов на /auth/login стопорит event loop и вместе с ним обработку Telegram-webhook, что находка не отмечает. Остальные утверждения (нет лимита, нет блокировки, нет логов неудач, aiogram-middleware к FastAPI не применяется, min_length=8, одна учётка) подтверждены полностью.

<details><summary>Как это проверялось</summary>

Проверил каждое утверждение по коду — все ключевые факты подтвердились.

1. Место существует и написано ровно то, что утверждается. app/api/dashboard/auth.py:183-197 — вся функция auth_login: получение единственной строки кредов (:185), сравнение имени (:190), bcrypt-проверка (:191), `raise HTTPException(401, "invalid_credentials")` (:193). Ни счётчика попыток, ни блокировки, ни задержки, ни единого вызова logger в ветке неудачи. Для контраста, в этом же файле verify_token (:66-71) неудачу JWT логирует — то есть отсутствие лога в login это именно пропуск, а не общий стиль модуля.

2. Выше по коду ничего сценарий не исключает:
   - Роутер подключается без каких-либо router-level dependencies: app/api/dashboard/__init__.py:40 (`include_router(_auth.router, prefix="/auth")`), выше — app/api/__init__.py:84 (`prefix="/dashboard/api"`). Итоговый путь POST /dashboard/api/auth/login, аутентификации перед ним нет по определению.
   - Единственный HTTP-middleware на FastAPI-приложении — RequestSizeLimitMiddleware (app/api/__init__.py:20-59), он ограничивает только Content-Length. Больше ни одного app.add_middleware в проекте нет (grep по add_middleware даёт только эту строку).

_(обоснование сокращено, полностью — в findings.json)_

</details>

### 3. Событие payment:approved никогда не публикуется: live-лента мертва, milestone-push не приходит

**P1** · ✅ подтверждено · уверенность автора находки: high · риск правки: средний

`database/subscriptions.py:2607`

**Дефект.** bus.publish({'type':'payment:approved'}) стоит единственный раз — внутри approve_payment_atomic (database/subscriptions.py:2362, publish на 2606-2614). У approve_payment_atomic нет ни одного вызывающего: grep по репозиторию даёт только определение и реэкспорт в database/__init__.py:138. Реальный поток оплат идёт через finalize_purchase (database/subscriptions.py:~4470+), который в шину ничего не пишет. Потребители этого события: LivePaymentTicker (dashboard/src/components/LivePaymentTicker.tsx:31) и admin_notifier._on_payment_approved (app/services/admin_notifier.py:224), который и рассылает web-push о дневных milestone.

**Когда ломается.** Пользователь платит через Platega/CryptoBot/Stars → finalize_purchase проводит оплату → в шину ничего не уходит → лента LIVE навсегда показывает «Ожидаю платежи в реальном времени», а web-push «💸 N ₽ за день» не приходит никогда, сколько бы ни было выручки. Админ считает, что push сломан, хотя сломан источник события.

**Что сделать.** Публиковать payment:approved (с telegram_id, amount_rubles, tariff, is_renewal) из finalize_purchase после коммита транзакции. Отдельно решить судьбу approve_payment_atomic — он недостижим по коду.

**Скептик скорректировал severity** до P2.

**Уточнение проверки.** Находка верна по сути, но формулировка «live-лента мертва» шире реальности. Мёртв именно виджет LivePaymentTicker (dashboard/src/components/LivePaymentTicker.tsx:31 фильтрует строго e.type !== "payment:approved" и не имеет seed-запроса к API — useEffect на строках 48-52 ничего не делает), он навсегда останется в состоянии «Ожидаю платежи в реальном времени» (строки 55-64). Общая лента событий на Dashboard.tsx (useEventStream, строки 240+) при этом живая: она получает user:registered (database/users.py:742), admin:grant/admin:revoke и прочие события из app/api/dashboard/routes/users.py — просто в ней никогда не будет строк «Новая подписка / Продление подписки» (Dashboard.tsx:251-260) и зелёного маркера payment:approved (Dashboard.tsx:721).

Вторая часть — web-push о дневных milestone — подтверждается полностью и без оговорок: _on_payment_approved (app/services/admin_notifier.py:222) вызывается только из диспетчера run_admin_notifier по ветке etype == "payment:approved" (app/services/admin_notifier.py:277), других вызовов пересчёта milestone в коде нет (grep milestones_to_fire даёт только определение на admin_notifier.py:141 и этот единственный вызов на 246). Сам нотификатор запускается штатно (main.py:300-301), настройка revenue_milestone включена по умолчанию (app/services/admin_settings.py:23 — "revenue_milestone": True). То есть подписка на событие рабочая, отсутствует только источник.

<details><summary>Как это проверялось</summary>

Опровергнуть не удалось — находка воспроизводится по коду.

1. Место существует и содержит заявленное. database/subscriptions.py:2605-2614: внутри approve_payment_atomic, после `if ret_val and ret_val[0] is not None:`, стоит `bus.publish({"type": "payment:approved", "payment_id": ..., "telegram_id": ..., "is_renewal": ..., "expires_at": ...})` в try/except-pass. Это единственное вхождение строки "payment:approved" в Python-коде всего репозитория (остальные — только потребители: app/services/admin_notifier.py:277, dashboard/src/components/LivePaymentTicker.tsx:31, dashboard/src/pages/Dashboard.tsx:251 и :721 — и документация docs/admin_dashboard_implementation_map.md:290,462).

2. Функция-носитель действительно недостижима. grep "approve_payment_atomic" по всему репозиторию (без node_modules/.git) даёт: определение database/subscriptions.py:2362, реэкспорт database/__init__.py:138, собственные лог-строки внутри неё (2433, 2438, 2570, 2597, 2601), комментарий app/api/dashboard/routes/users.py:10 и три упоминания в docs/. Ни одного вызова. Дополнительно проверено, что это не косвенный вызов: grep "approve_payment"/"approve_pay" без суффикса _atomic не даёт в .py вообще ничего (нет обёртки-алиаса), динамического диспатча getattr(database, ...) с этим именем тоже нет (единственный getattr(database, ...) — app/handlers/user/devices.py:38 по таблице _TIERS, не связан).


_(обоснование сокращено, полностью — в findings.json)_

</details>

### 4. Перезагрузка страницы или прямая ссылка на любой раздел дашборда даёт 404

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/api/__init__.py:92`

**Дефект.** SPA монтируется как app.mount('/dashboard', StaticFiles(directory=_dist, html=True)) (app/api/__init__.py:92). StaticFiles с html=True отдаёт index.html только для каталогов, а при отсутствии файла ищет 404.html и, не найдя, поднимает HTTPException(404). Файла 404.html в dashboard/public нет. Роутер — BrowserRouter с basename='/dashboard' и реальными путями users/analytics/payments/settings и т.д. (dashboard/src/App.tsx:83, 105-120).

**Когда ломается.** Админ стоит на /dashboard/users, нажимает F5 (или открывает сохранённую ссылку /dashboard/settings, или PWA восстанавливает последний URL) — вместо приложения приходит голый 404 от FastAPI. Внутри сессии навигация работает, поэтому дефект выглядит случайным.

**Что сделать.** Добавить catch-all маршрут для /dashboard/{path:path}, отдающий index.html для всех не-API и не-статических путей (или положить 404.html = копию index.html как быстрый обходной вариант).

### 5. /stats/overview не отдаёт business_metrics — шесть KPI на дашборде всегда «—»

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/api/dashboard/routes/stats.py:36`

**Дефект.** stats_overview (routes/stats.py:35-54) возвращает database.get_extended_bot_stats(), чей набор ключей зафиксирован в database/admin.py:1288-1302: total_users, active_subs, expired_subs, total_trial, trial_rate, users_with_sub, conversion_rate, churn_rate, total_revenue, mrr, new_today, total_broadcasts, avg_subs_per_user. Ключа business_metrics там нет вообще. Фронт читает overview.data?.business_metrics?.approval_rate_percent / avg_subscription_lifetime_days / avg_renewals_per_user / avg_payment_approval_time_seconds в шести местах (Dashboard.tsx:431, 435, 439, 443, 493-494, 532, 537). asNum(undefined)→undefined, fmtNum→«—». Docstring stats.py:38-42 утверждает, что overview содержит business_metrics — это неправда. Готовый эндпоинт /stats/business есть, но дашборд его не вызывает.

**Когда ломается.** Карточка «Бизнес-метрики · KPI», блок «Финансы» и блок «Подписки · health» показывают «—%», «— дн», «—» при любых данных в БД. Метрики выглядят как «нет данных», хотя get_business_metrics() их считает.

**Что сделать.** Либо добавить data['business_metrics'] = await database.get_business_metrics() в stats_overview, либо добавить на фронте отдельный useQuery на endpoints.statsBusiness и брать значения оттуда. Исправить docstring stats.py:37-43.

### 6. Дашборд открывается из бота обычной url-кнопкой — на iOS это встроенный браузер Telegram, где push и установка невозможны

**P1** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: низкий

`app/handlers/admin/base.py:45`

**Дефект.** Кнопка «🛡 Открыть дашборд» строится как InlineKeyboardButton(text=..., url=...) (app/handlers/admin/base.py:44-46). На iOS Telegram открывает такие ссылки во встроенном браузере (WKWebView/SFSafariViewController), где не регистрируется service worker (main.tsx:15-22 молча падает в catch), недоступны PushManager/Notification и отсутствует пункт «На экран Домой». Web Push на iOS работает по требованиям Apple только в web-app, добавленном на домашний экран из полноценного Safari (iOS 16.4+), с display:standalone в манифесте и с запросом разрешения по явному жесту пользователя.

**Когда ломается.** Админ всегда открывает дашборд тапом по кнопке в боте, поэтому всегда оказывается во встроенном браузере: push подключить невозможно, установка на домашний экран недоступна, а после ухода из Telegram сессия/токен в этот контекст не переносятся. Итог — «в мини-приложении push не приходит».

**Что сделать.** Явно вести админа на iOS по маршруту: открыть ссылку в Safari (кнопка «…» → «Открыть в Safari»), затем «Поделиться → На экран Домой», затем запускать иконку и уже там нажимать «Подключить push». Продублировать этот маршрут текстом в сообщении бота и на экране Настроек. Технически ничего, кроме подсказки, тут исправить нельзя — это ограничение Apple.

### 7. Переключатели «Telegram DM» на самом деле управляют web-push, а DM не отправляются

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/services/admin_notifier.py:167`

**Дефект.** Секция настроек подписана «Telegram DM · Что присылать в личку» (dashboard/src/pages/Settings.tsx:109-114), а описания всех трёх флагов начинаются со слова «DM» (Settings.tsx:35-57). Но _send() в app/services/admin_notifier.py:167-178 отправляет исключительно push_notifications.send_to_all() и явно комментирует «Telegram DM is intentionally NOT used here». То есть флаги payment_error / broadcast_done / revenue_milestone управляют браузерным push, а не личкой.

**Когда ломается.** Админ включает «Ошибки платежей», не подключив web-push (или подключив его на устройстве, где push не работает — iPhone). Ломается платёжный webhook — уведомления не приходят вообще ни по одному каналу, при этом UI показывает тумблер включённым и обещает DM. Сбой платежей остаётся незамеченным.

**Что сделать.** Либо вернуть Telegram DM как гарантированный fallback в _send (push + DM, или DM когда подписок 0), либо переименовать секцию и описания флагов в «Браузерные push». Второй вариант дешевле, первый — надёжнее.

### 8. На iPhone в Safari секция push показывает «Не поддерживается» вместо инструкции «На экран Домой»

**P1** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: низкий

`dashboard/src/pages/Settings.tsx:309`

**Дефект.** PushSection считает supported = isPushSupported() (Settings.tsx:193), где isPushSupported требует наличия 'PushManager' in window и 'Notification' in window (dashboard/src/lib/push.ts:14-21). На iOS эти API выставляются только в установленном на домашний экран web-app, во вкладке Safari их нет. Тогда срабатывает ранний return на Settings.tsx:309-329 с текстом «Этот браузер не умеет push. Открой в Safari (iOS / macOS) или Chrome» — то есть пользователю в Safari советуют открыть в Safari. Предупреждение iosBlocker с правильной инструкцией (Settings.tsx:351-363) и функция iosNeedsHomeScreen (push.ts:52-54) расположены ПОСЛЕ раннего return и на iPhone недостижимы.

**Когда ломается.** Админ на iPhone заходит в Настройки → Push, читает «Не поддерживается, открой в Safari», делает это ещё раз, получает то же самое и делает вывод, что push на iPhone не работает. Реально требуется «Поделиться → На экран Домой» и включение push уже из установленной иконки.

**Что сделать.** Проверять iOS-специфический случай ДО ветки «не поддерживается»: если isIOS() && !isStandalonePWA() — показывать блок с инструкцией по установке на домашний экран, а «Не поддерживается» оставить только для реально неподдерживающих браузеров.

### 9. Двойной учёт денег в таблице payments: пополнение баланса и покупка с баланса пишутся как две выручки

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`database/admin.py:3505`

**Дефект.** get_total_revenue (database/admin.py:3505-3521), get_arpu (3593-3623), get_ltv (4033-4058) и дневной график get_daily_timeseries (3906-3913) суммируют payments.amount WHERE status='approved'. В payments пишется и пополнение баланса (database/subscriptions.py:4611, tariff='balance_topup'), и последующая покупка подписки с этого же баланса (database/admin.py:2651 в finalize_balance_purchase), и автопродление с баланса (auto_renewal.py:325). Одни и те же рубли учитываются 2-3 раза. Реферальный кешбэк, начисленный на баланс, при трате тоже превращается в «выручку».

**Когда ломается.** Юзер пополняет баланс на 1000 ₽ и покупает подписку за 1000 ₽. «Доход всего», ARPU, LTV и точка дневного графика показывают 2000 ₽ вместо 1000 ₽. Чем активнее используется баланс, тем сильнее завышение — метрика непригодна для решений о ценах и рекламе.

**Что сделать.** Определить единый источник истины по выручке (внешние поступления) и исключить из него внутренние движения: либо фильтровать tariff='balance_topup' и покупки с payment_provider='balance', либо считать выручку только по пополнениям + прямым покупкам. Задокументировать выбранное определение рядом с функцией.

### 10. Тот же двойной учёт в pending_purchases завышает «Доход сегодня» и порог milestone-пуша

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`database/admin.py:594`

**Дефект.** get_revenue_for_period (database/admin.py:594-650) суммирует price_kopecks по всем pending_purchases со status='paid' без фильтра по типу. При этом пополнение баланса создаёт строку purchase_type='balance_topup', а покупка, оплаченная с баланса, создаёт ещё одну строку с payment_provider='balance' (app/handlers/callbacks/navigation.py:1696-1705: сначала decrease_balance, затем finalize_purchase с payment_provider='balance'). Тот же расчёт используется в admin_notifier._on_payment_approved (app/services/admin_notifier.py:238-241) для порогов 5k/10k/…

**Когда ломается.** Тайл «Доход сегодня», «Средний чек» на главной и разбивки в /payments/breakdown считают одни деньги дважды. Milestone-уведомление «25 000 ₽ за день» срабатывает при реальной выручке существенно ниже порога.

**Что сделать.** Исключить из выручки строки purchase_type='balance_topup' ИЛИ строки с payment_provider='balance' (одно из двух, не оба), одинаково во всех местах: get_revenue_for_period, get_payments_breakdown, get_payments_by_provider. Зафиксировать правило в docstring.

### 11. Удаление пользователя из дашборда физически стирает историю платежей — выручка меняется задним числом

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: высокий

`database/admin.py:4328`

**Дефект.** DELETE /dashboard/api/users/{telegram_id} (app/api/dashboard/routes/users.py:562-583) вызывает admin_delete_user_complete, который в одной транзакции делает DELETE FROM payments (database/admin.py:4328) и DELETE FROM pending_purchases (4327), а также balance_transactions (4324) и subscription_history (4326). Финансовые записи удаляются физически, без архива и без soft-delete.

**Когда ломается.** Админ удаляет одного «мусорного» юзера — «Доход всего», ARPU, LTV, дневной график за прошлые месяцы и все разбивки уменьшаются задним числом, сверка с выписками провайдера перестаёт сходиться. Восстановить нечем: audit_log хранит только строку «Complete user deletion from DB».

**Что сделать.** Не удалять финансовые таблицы: анонимизировать users/subscriptions, а payments/pending_purchases/balance_transactions сохранять (или переносить в архивную таблицу). Как минимум — писать в audit_log суммы и количество удаляемых платёжных строк.

### 12. Лимит размера запроса обходится chunked-передачей, upload-photo читает файл целиком в память

**P2** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: низкий

`app/api/__init__.py:48`

**Дефект.** RequestSizeLimitMiddleware проверяет только заголовок Content-Length (app/api/__init__.py:47-56); если клиент шлёт Transfer-Encoding: chunked без Content-Length, проверка молча пропускается (ветка if content_length ложна). Обработчик upload_photo делает content = await file.read() целиком в память и проверяет размер уже после чтения (app/api/dashboard/routes/broadcasts.py:339-342).

**Когда ломается.** Аутентифицированный (или обладатель утёкшего magic-link) клиент шлёт chunked-запрос на /dashboard/api/broadcasts/upload-photo на сотни мегабайт — процесс бота съедает память и может быть убит OOM. Бот и вебхуки Telegram лежат вместе с дашбордом в одном процессе.

**Что сделать.** Считать байты потоково в middleware (или в самом обработчике читать чанками с накопительным лимитом и прерывать при превышении), не полагаться на Content-Length.

### 13. После установки на домашний экран iOS сессия теряется, а bootstrap-setup одноразовый

**P2** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: низкий

`app/api/dashboard/auth.py:170`

**Дефект.** Установленный на домашний экран web-app на iOS получает изолированное хранилище: кука atlas_admin_session (auth.py:99-112) и токен в localStorage (lib/auth.ts:14-20) из Safari туда не переносятся. Приложение стартует с start_url '/dashboard/' (manifest.webmanifest:5) и, не найдя сессии, показывает форму логина (App.tsx:53-74). Пройти setup повторно нельзя — auth_setup отвечает 409 already_setup (auth.py:170-172), а без пароля/passkey войти нечем: единственный путь восстановления — кнопка «Сбросить пароль» в боте, которая заодно удаляет все passkey (admin_auth.clear_credentials → admin_passkeys.purge_all_passkeys, admin_auth.py:120-140).

**Когда ломается.** Админ, который прошёл первичную настройку во встроенном браузере Telegram и не запомнил пароль, устанавливает иконку на домашний экран (единственный способ получить push на iOS) и упирается в форму логина. Восстановление сбрасывает и пароль, и все зарегистрированные passkey.

**Что сделать.** Явно предупреждать в UI, что после установки на домашний экран потребуется вход логином/паролем, и предлагать зарегистрировать passkey сразу после setup. Рассмотреть непересечение сброса пароля и удаления passkey.

### 14. Ключ active_subscriptions не существует — карточка «Активных с триалами» всегда пустая, а fallback превращается в None

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/api/dashboard/routes/stats.py:51`

**Дефект.** get_extended_bot_stats возвращает поле active_subs (database/admin.py:1290), а не active_subscriptions. При этом stats.py:47-51 в except-ветке делает data['active_paid_subscriptions'] = data.get('active_subscriptions') → None. Фронт трижды обращается к overview.data?.active_subscriptions: Dashboard.tsx:307, 369-379 (подсказка «с триалами …») и 522 (карточка «Активных с триалами»).

**Когда ломается.** Карточка «Активных с триалами» всегда показывает «—», подсказка под «Active subs» никогда не отображается. Если get_active_paid_subscriptions_count упадёт, «Active subs» тоже станет пустым вместо деградации к общему числу.

**Что сделать.** Привести имена к одному виду: либо переименовать ключ в active_subscriptions в get_extended_bot_stats, либо читать active_subs на фронте и в fallback stats.py:51.

### 15. WebSocket принимает JWT без проверки, что subject — действующий админ

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/api/dashboard/ws.py:46`

**Дефект.** В ws.py:46-49 при авторизации по ?token=<jwt> проверяется только валидность подписи и payload['role']=='admin'. В отличие от deps.py:36-41, здесь нет ни разбора sub, ни admin_auth.is_admin(sub). Также покрытие: покидающий сессию logout (auth.py:200-208) отзывает только куку, а токен в query продолжает работать до истечения 30 дней.

**Когда ломается.** Токен, выданный для telegram_id, который позже перестал быть ADMIN_TELEGRAM_ID (смена админа в конфиге), продолжает открывать поток событий шины со всеми telegram_id, суммами и админ-действиями. Отзыв сессий (purge_all_sessions при сбросе пароля) на этот путь не влияет.

**Что сделать.** В ws.py повторить проверку из deps.py: разобрать sub в int и вызвать admin_auth.is_admin(). Лучше — вообще убрать query-token из WS и оставить только куку, раз SPA всё равно работает по сессии.

### 16. Все PNG-иконки PWA отсутствуют в репозитории

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`dashboard/public/manifest.webmanifest:20`

**Дефект.** Манифест ссылается на /dashboard/icon-192.png (строка 20), /dashboard/icon-512.png (26) и /dashboard/icon-mask-512.png (32); index.html:22 — на /dashboard/apple-touch-icon.png; sw.js:39-40 и push_notifications.py:286-287 используют /dashboard/icon-192.png как icon и badge уведомления. В dashboard/public лежат только icon.svg и icon-mono.svg — ни одного PNG. Каталога dist в репозитории нет, генерации PNG в vite.config.ts и package.json тоже нет.

**Когда ломается.** Все четыре ссылки на PNG отдают 404. На iOS иконка на домашнем экране получается из скриншота страницы вместо логотипа, у push-уведомления нет иконки/бейджа, Chrome ругается на невалидные записи манифеста.

**Что сделать.** Сгенерировать и положить в dashboard/public icon-192.png, icon-512.png, icon-mask-512.png и apple-touch-icon.png (180×180), либо привести манифест/index.html/sw.js/push_notifications.py к реально существующему icon.svg.

### 17. ARPU и LTV на дашборде — математически одно и то же число

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`database/admin.py:3593`

**Дефект.** get_arpu (database/admin.py:3593-3623) = SUM(amount) / COUNT(DISTINCT telegram_id) по approved-платежам. get_ltv (database/admin.py:4033-4058) = AVG(SUM(amount) GROUP BY telegram_id), что алгебраически равно тому же отношению. Обе величины попадают в /stats/revenue (routes/stats.py:71-80) и рисуются как две разные карточки: «ARPU · на юзера» (Dashboard.tsx:467-472) и «LTV · средний» (Dashboard.tsx:473-478). Плюс подпись «на юзера» неверна: делится на платящих, то есть это ARPPU, а не ARPU.

**Когда ломается.** Две соседние карточки всегда показывают одинаковую сумму, админ считает это багом рендера. Настоящий ARPU (выручка / всех юзеров) на дашборде не показывается нигде.

**Что сделать.** Оставить одну карточку ARPPU/LTV, а вторую пересчитать по нужному определению (ARPU = выручка / total_users, либо LTV = ARPPU × среднее число продлений). Исправить подпись «на юзера».

### 18. «Доход сегодня» и дневной график выручки считаются по разным таблицам и не сходятся

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`database/admin.py:623`

**Дефект.** Тайл «Доход сегодня» и «Средний чек» берутся из /payments/revenue → get_revenue_for_period, который суммирует pending_purchases.price_kopecks по created_at (database/admin.py:620-624). Hero-график и «Доход всего» берутся из payments.amount по payments.created_at (get_daily_timeseries, database/admin.py:3906-3913; get_total_revenue, 3515-3519). Это два разных набора строк с разными правилами. Дополнительно у pending_purchases нет колонки времени оплаты (миграция 004_add_pending_purchases.sql:16-27 и последующие ALTER её не добавляют), поэтому используется created_at — момент старта чекаута, а не оплаты; сам код это признаёт в docstring get_purchase_breakdown (database/admin.py:1157-1159).

**Когда ломается.** Сумма последней точки дневного графика и тайла «Доход сегодня» на одном экране различаются. Покупка, начатая в 23:57 МСК и оплаченная в 00:03, попадает во вчерашние сутки.

**Что сделать.** Добавить в pending_purchases колонку paid_at, заполнять её в finalize_purchase и считать все оконные метрики по paid_at. Свести «Доход всего» и «Доход сегодня» к одной таблице-источнику.

### 19. avg_payment_approval_time_seconds строится парсингом текста audit_log и опирается на мёртвый путь approve

**P2** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: низкий

`database/admin.py:237`

**Дефект.** get_business_metrics (database/admin.py:237-250) вытаскивает payment_id регулярным SUBSTRING(al.details FROM 'Payment ID: ([0-9]+)') с CAST в INTEGER по строкам audit_log с action IN ('payment_approved','subscription_renewed'). Действие 'payment_approved' пишется в ручном пути подтверждения (approve_payment_atomic), у которого, как показано выше, нет вызывающих. Формат details — свободный текст, контракта на него нет.

**Когда ломается.** Метрика «Время апрува» либо всегда NULL (нет строк payment_approved), либо роняет запрос ошибкой приведения типа, если кто-то изменит формат details и в скобках окажется не число — тогда весь /stats/business отвечает 500. Так как business_metrics вообще не доходит до фронта (см. отдельную находку), сейчас это просто мёртвый расчёт.

**Что сделать.** Считать время апрува по payments.paid_at - payments.created_at (колонка paid_at существует, миграция 024) вместо парсинга текста audit_log. Убрать зависимость от action 'payment_approved'.

### 20. Одноразовый setup можно потерять: bootstrap-токен исчезает из localStorage до подтверждения успеха

**P3** · ⚠️ не перепроверено · уверенность автора находки: low · риск правки: низкий

`dashboard/src/App.tsx:88`

**Дефект.** После SetupPassword onDone вызывает auth.clear() (App.tsx:88-92) — токен удаляется из localStorage. Ветка setup доступна только пока auth.get() возвращает токен (App.tsx:60-68). Сам /auth/setup одноразовый: повторный вызов даёт 409 already_setup (auth.py:170-172).

**Когда ломается.** Если set_credentials прошёл, а create_session/установка куки не долетели (обрыв сети на ответе), фронт уже очистил токен: setup недоступен (409), пароль пользователь может не запомнить/опечататься при вводе — остаётся только «Сбросить пароль» в боте. Сценарий узкий, но выход только через бота.

**Что сделать.** Очищать токен только после подтверждённого has_session от /auth/status; либо оставлять токен до первого успешного входа.

### 21. На каждую страницу дашборда открывается два независимых WebSocket-соединения

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`dashboard/src/lib/ws.ts:31`

**Дефект.** useEventStream создаёт собственный WebSocket в каждом вызывающем компоненте (dashboard/src/lib/ws.ts:31-92). На главной он вызывается дважды: в Dashboard (pages/Dashboard.tsx:240) и в LivePaymentTicker (components/LivePaymentTicker.tsx:30). Каждое соединение получает отдельную очередь в шине (app/events.py:32-35) и свой цикл переподключения. Плюс обработчик в Dashboard.tsx:289 дергает qc.invalidateQueries({queryKey:['stats']}) на каждое событие любого типа.

**Когда ломается.** Два соединения на вкладку, два ping-таймера на сервере, двойной трафик событий; при всплеске событий (массовые регистрации) инвалидация ['stats'] запускает повторные запросы к тяжёлым агрегатам чаще, чем refetchInterval.

**Что сделать.** Вынести соединение в один провайдер/синглтон с мультиплексированием подписчиков; дебаунсить инвалидацию ['stats'] (например, не чаще раза в 5 секунд).

### 22. Подпись дельты выручки жёстко зашита как «vs prev 30d» при любом выбранном окне

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`dashboard/src/pages/Dashboard.tsx:348`

**Дефект.** revenueDelta (Dashboard.tsx:294-302) сравнивает вторую половину выбранного окна с первой, окно переключается кнопками 7/30/90/180 (Dashboard.tsx:110, 171). Текст подписи в Dashboard.tsx:348 всегда «vs prev 30d». Кроме того, при days=30 сравниваются две половины по 15 дней, а не 30 против предыдущих 30 — надпись неверна даже в дефолтном режиме.

**Когда ломается.** Админ выбирает окно 7 дней, видит «+42.0% vs prev 30d» и считает, что это месячная динамика.

**Что сделать.** Формировать текст из выбранного days и фактического способа расчёта, например «за последние N/2 дн против предыдущих N/2 дн».

### 23. Фильтр LIKE 'apple_id_%' без ESCAPE трактует подчёркивание как wildcard

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`database/admin.py:798`

**Дефект.** В get_payments_breakdown разбивка по apple-номиналам отбирает строки условием tariff LIKE 'apple_id_%' (database/admin.py:798). В SQL символ _ — одиночный wildcard, поэтому шаблон совпадает и с 'appleXidY…'. Далее tariff разбирается по '_' с позиционным доступом parts[2]/parts[3] (804-812) без проверки, что это действительно apple-строка.

**Когда ломается.** Появление любого тарифа вида 'appleZidZ_...' попадёт в разбивку Apple с мусорным region/nominal. Сейчас таких тарифов нет, поэтому дефект латентный.

**Что сделать.** Использовать LIKE 'apple\_id\_%' ESCAPE '\' или tariff ~ '^apple_id_' с явной проверкой числа сегментов перед разбором.

### 24. Неиспользуемая часть payload /stats/overview содержит метрики с ошибочной семантикой

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`database/admin.py:1271`

**Дефект.** get_extended_bot_stats возвращает поля, которые фронт не читает вовсе (проверено grep по dashboard/src): new_today считается от полуночи UTC (database/admin.py:1271), хотя весь дашборд оперирует сутками МСК; churn_rate = expired/(active+expired) по СТРОКАМ подписок, а не по пользователям (1286) и потому не является оттоком; avg_subs_per_user подписан «per paying user», но группирует все строки subscriptions, включая триалы и bypass (1280-1281); mrr — это просто сумма approved-платежей за 30 дней (1265-1268), а не MRR.

**Когда ломается.** Если кто-то выведет эти поля на экран или в отчёт (имена выглядят готовыми к употреблению), цифры будут систематически неверны: «сегодня» сдвинуто на 3 часа, «churn» завышен историческими строками, «MRR» смешивает разовые покупки.

**Что сделать.** Либо удалить неиспользуемые поля из payload, либо переименовать их в честные (new_today_utc, expired_subscription_rows_share, revenue_last_30d) и привести new_today к границе суток МСК, как в остальных тайлах.

---

## Что прочитано, а что нет

Прочитано целиком: app/api/dashboard/deps.py, auth.py, ws.py, __init__.py; routes/stats.py, users.py, payments.py, referrals.py, activations.py, pricing.py, settings.py, export.py, incident.py, audit.py, reconciliation.py; app/services/admin_auth.py, push_notifications.py; dashboard/src/lib/{api.ts (первые 120 строк + бинды по grep), auth.ts, ws.ts, push.ts, passkey.ts, format.ts}; dashboard/src/{App.tsx, main.tsx}; dashboard/src/pages/Settings.tsx; dashboard/src/components/{InstallHint.tsx, LivePaymentTicker.tsx}; dashboard/index.html, vite.config.ts, public/sw.js, public/manifest.webmanifest. Из database/admin.py прочитаны все функции, питающие дашборд: get_business_metrics, get_analytics_by_period, get_active_paid_subscriptions_count, get_revenue_for_period, get_payments_by_provider, get_payments_breakdown, get_purchase_breakdown, get_extended_bot_stats, get_total_revenue/paying_users/user_ltv/average_ltv/arpu/ltv, get_daily_timeseries, get_hourly_timeseries, admin_delete_user_complete. Прочитаны app/api/__init__.py, app/events.py, app/services/admin_notifier.py, app/handlers/admin/base.py (первые 90 строк), migrations 001/004/024/025 в части типов времени, database/core.py:_to_db_utc.

Просмотрено выборочно (grep + фрагменты, полного чтения не было): dashboard/src/pages/Dashboard.tsx прочитан до строки ~920 плюс хвост с asNum/fmtSeconds — средняя часть (компоненты графиков, ReferralBlock, TariffsBlock, ProvidersBlock, SegmentsCard, PaymentsBreakdownCard, строки ~920-2216) не читалась; pages/Statistics.tsx — только первые 120 строк; app/api/dashboard/routes/{broadcasts.py, links.py, bypass_audit.py, bgift.py, promo.py, automated_notifications.py} — только заголовки, объявления роутера и upload_photo; app/services/admin_passkeys.py — фрагменты (rp_id/origin, challenge storage, make_registration_options).

Не читалось вовсе: dashboard/src/pages/{Users.tsx, Broadcasts.tsx, BroadcastCreate.tsx, AutomatedNotifications.tsx, BypassAudit.tsx, BypassGifts.tsx, MarketingLinks.tsx, PromoCodes.tsx, Payments.tsx, Pricing.tsx, Referrals.tsx, Service.tsx, Analytics.tsx, Audit.tsx, Login.tsx, SetupPassword.tsx, ComingSoon.tsx}; components/{ReconciliationSection.tsx, Sidebar.tsx, MobileNav.tsx, Layout.tsx, Collapsible.tsx, MetricTooltip.tsx, StatCard.tsx, Toaster.tsx, RouteTransition.tsx}; app/services/{admin_settings.py, pricing/*, broadcast_sender.py} целиком; database/subscriptions.py прочитан лишь фрагментами (finalize_purchase 4590-4880, approve_payment_atomic 2560-2620), get_promo_stats и функции рефералок (get_referral_overall_stats, get_admin_referral_stats, get_referral_analytics) не проверялись — метрики раздела «Рефералы» и «Промокоды» в этом аудите НЕ проверены. Утверждения о поведении iOS (доступность PushManager во вкладке Safari, изолированное хранилище home-screen web-app, поведение встроенного браузера Telegram) основаны на требованиях Apple, а не на измерении на устройстве — отмечены confidence=medium. Прод-логов и БД нет, поэтому все выводы о «мёртвости» (approve_payment_atomic, payment:approved) сделаны только по достижимости в коде через grep по всему репозиторию.
