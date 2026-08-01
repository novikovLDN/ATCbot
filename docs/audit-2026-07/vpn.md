# VPN-ядро и Remnawave

Находок: **24** — P0 1, P1 7, P2 14, P3 2. Опровергнуто при перепроверке: 1.

Перепроверялись три самые тяжёлые находки каждого домена плюс все P0. Остальные помечены «не перепроверено» — это гипотезы, требующие подтверждения перед правкой.

---

### 1. Удаление xray-веток вместе с XRAY_API_* переменными глушит весь провижининг

**P0** · ✅ подтверждено · уверенность автора находки: high · риск правки: высокий

`config.py:352`

**Дефект.** `VPN_ENABLED = bool(XRAY_API_URL and XRAY_API_KEY)`. Этот флаг после cut-over охраняет уже не xray, а Remnawave-путь: app/services/activation/service.py:385 (raise VPNActivationError), activation_worker.py:84 (worker выходит со skipped), database/subscriptions.py:1856 (grant_access уходит в pending activation вместо провижининга), database/subscriptions.py:2413, database/admin.py:2315/2352/2540/3012/3042/4481, app/services/subscriptions/service.py:179, main.py:543. То есть переменная от мёртвой подсистемы управляет живой. Для подпроекта B это главный капкан: если удалить XRAY_API_URL/XRAY_API_KEY из окружения раньше, чем переписать VPN_ENABLED, бот молча перестанет выдавать доступ.

**Когда ломается.** Инженер выпиливает xray-ветку и убирает XRAY_API_URL/XRAY_API_KEY из Railway/env. VPN_ENABLED становится False. Все новые покупки уходят в ACTIVATION_PENDING, activation_worker сразу возвращает skipped и никогда их не разбирает. Деньги приняты, ключи не выданы, ошибок в логах почти нет.

**Что сделать.** Перед удалением xray переопределить VPN_ENABLED через REMNAWAVE_ENABLED (или ввести PROVISIONING_ENABLED = REMNAWAVE_ENABLED) и заменить все перечисленные call-site'ы. Это должен быть ПЕРВЫЙ шаг подпроекта B, до удаления файлов.

**Скептик скорректировал severity** до P1.

**Уточнение проверки.** Находка верна по механике, но сценарий отказа описан неточно в трёх местах, и severity завышена.

а) «Все новые покупки уходят в ACTIVATION_PENDING» — неверно для основного пути покупки. app/services/subscriptions/service.py:179-185 при VPN_ENABLED=False бросает PurchaseCreationError («VPN service is temporarily unavailable») ДО оплаты, и через него идут все точки создания подписочной покупки (app/handlers/callbacks/payments_callbacks.py:898/1029/1154/1305/1430/1563, app/handlers/callbacks/navigation.py:1682). Главный видимый эффект — бот перестаёт продавать VPN с явной ошибкой, а не молча копит pending.

б) «Деньги приняты, ключи не выданы» реально возможно, но на более узком множестве: (1) покупки, созданные ДО снятия переменных и оплаченные после — они дойдут до app/services/payments/confirmation.py:110 → finalize_purchase → grant_access:1856 → pending; (2) прямые вызовы grant_access мимо service.py:179 — trial (app/handlers/user/start.py:882), админская выдача (app/handlers/admin/access.py:1337, app/handlers/admin/bonus.py:477), игровые награды (app/handlers/game.py:236/371). Автопродление с баланса при этом само себя защищает: auto_renewal.py:258-273 при `action != "renewal"` делает возврат на баланс.

в) «Ошибок в логах почти нет» — преувеличение. При старте пишутся config.py:358-361 (три warning'а), на каждую попытку покупки — `PURCHASE_BLOCKED_VPN_DISABLED` (service.py:180-182), на каждую выдачу — `grant_access: ACTIVATION_PENDING` (subscriptions.py:1858-1862, 1960-1964). Молчаливым отказ не будет, хотя ни один из этих логов не назовёт настоящую причину (флаг xray гасит Remnawave).

г) Дополнительный факт в пользу коварности, но не в тексте находки: ветка продления (subscriptions.py:2380-2406) проверки VPN_ENABLED не имеет и вызывает purchase_flow.sync_renewal_to_remnawave безусловно. То есть активные подписки продлеваются нормально, ломается только новая выдача — деградация частичная и потому дольше остаётся незамеченной.

д) Severity: P1, не P0. На текущем коде при выставленных XRAY_API_* поломки нет — это латентная связанность и капкан для подпроекта B, а не действующий продовый дефект. Отказ требует будущего действия инженера (снятия переменных). Кроме того, самый громкий исход (отказ продаж с явной ошибкой) не соответствует P0-профилю «тихая потеря денег». Фикс тривиален и обязателен до удаления xray-ветки: заменить config.py:352 на признак настроенности Remnawave (REMNAWAVE_API_URL/TOKEN, ср. app/services/purchase_flow.py:133), а заодно снять раннюю проверку на subscriptions.py:1856, которая обесценивает уже выполненный Phase 1 provisioning из finalize_purchase (subscriptions.py:4506) и admin.py:2413.

<details><summary>Как это проверялось</summary>

Проверил всё по коду — опровергнуть не удалось, находка подтверждается.

1) Место существует дословно. config.py:352 `VPN_ENABLED = bool(XRAY_API_URL and XRAY_API_KEY)`, источники — config.py:344-345 (`XRAY_API_URL = env("XRAY_API_URL")`, `XRAY_API_KEY = env("XRAY_API_KEY")`). Переменные необязательные: при их отсутствии config.py:357-361 только пишет warning'и, `sys.exit` нет (обязательны в prod лишь TG_PROVIDER_TOKEN config.py:337-338 и WEBHOOK_* config.py:412-417). То есть VPN_ENABLED=False — штатно достижимое состояние процесса.

2) Флаг действительно охраняет живой Remnawave-путь, а не мёртвый xray:
   - app/services/activation/service.py:385 `if not config.VPN_ENABLED: raise VPNActivationError`, а провижининг сразу ниже (service.py:399-405) идёт через `purchase_flow.provision_subscription` с комментарием «Task 2 cut-over ... instead of the legacy samopis xray master». Флаг xray убивает Remnawave-вызов.
   - activation_worker.py:84 `if not config.VPN_ENABLED: return (0, "skipped")` — воркер не разберёт pending никогда.
   - database/subscriptions.py:1856 — проверка внутри «STEP 3: новая выдача», ветка завершается `return {... "action": "pending_activation"}` на subscriptions.py:1986-1991.

_(обоснование сокращено, полностью — в findings.json)_

</details>

### 2. Вся компенсация при сбое провижининга — no-op: orphan-сущности остаются в панели

**P1** · ✅ подтверждено · уверенность автора находки: high · риск правки: высокий

`vpn_utils.py:640`

**Дефект.** `remove_vless_user` при PURCHASE_FLOW_REMNAWAVE=True возвращает None на строке 645, не делая ничего. Через него работает `safe_remove_vless_user_with_retry`, который вызывается как механизм отката ~15 раз: app/services/activation/service.py:440, 457, 484, 512; database/subscriptions.py:282, 2567, 2584, 5063, 5079; database/admin.py:2404, 2420, 2450, 2710, 2725, 3091, 3107, 3131, 3218, 4345. Все они пишут в лог CRITICAL «ORPHAN_PREVENTED», хотя ничего не удалено. Единственное место, где это честно признано, — database/subscriptions.py:2558 (PURCHASE_FLOW_ORPHAN_NOT_CLEANED).

**Когда ломается.** Phase 1 создала premium+bypass в Remnawave с expireAt=subscription_end, Phase 2 (DB-транзакция) упала. Код логирует ORPHAN_PREVENTED, но сущность в панели остаётся ACTIVE. Пользователь получает рабочий VPN, а в БД подписки нет (или она pending/failed). Обратно: логи говорят «предотвращено», аудит по логам даёт ложную картину.

**Что сделать.** Ввести единую функцию компенсации на Remnawave (delete/disable premium + bypass по telegram_id) и заменить ею все вызовы safe_remove_vless_user_with_retry. Учесть, что для adopt-or-create «сирота» — это сущность самого пользователя, поэтому удалять нельзя вслепую: нужно откатывать expireAt, а не удалять сущность при renewal.

**Скептик скорректировал severity** до P2.

**Уточнение проверки.** Уточнения к находке: (1) database/subscriptions.py:2567 закрыт флаг-гардом и не исполняется под PURCHASE_FLOW_REMNAWAVE — туда управление не доходит, работает честная ветка PURCHASE_FLOW_ORPHAN_NOT_CLEANED (2552-2563); (2) subscriptions.py:1298 компенсирует через remnawave_api.delete_user, а не через no-op; (3) subscriptions.py:282, admin.py:3218, admin.py:4345 — не откат, и для 282/4345 рядом есть настоящие Remnawave-вызовы (disable_remnawave_user_bg subscriptions.py:342, delete_remnawave_user_bg admin.py:4352); (4) 2584/5079/2450/2725/3131 — post-commit удаление старого uuid, не компенсация. Реально затронуто ~10 мест: activation/service.py:440,457,484,512; subscriptions.py:5063; admin.py:2404,2420,2710,3091,3107. Пропущено находкой и важно для фикса: uuid_to_cleanup_on_failure — это connection-uuid (purchase_flow.py:258 возвращает requested_uuid), а не panel_uuid, поэтому подмена вызова на remnawave_api.delete_user без прокидывания premium_panel_uuid работать не будет. Смягчающие факторы: adoption-идемпотентность провижининга, expireAt=subscription_end, отсутствие дублей. Отягчающий фактор: комментарий subscriptions.py:2556-2560 обещает видимость через админскую кнопку 🔬 Verify, но reconcile._scan_mismatches (reconcile.py:58-95) сканирует только БД→панель и панель-only сущности не находит; disable_premium_user (remnawave_premium.py:442) не вызывается нигде вне тестов.

<details><summary>Как это проверялось</summary>

Место существует и написано ровно то, что заявлено. vpn_utils.py:629 `async def remove_vless_user`, строки 639-645: при `getattr(config, "PURCHASE_FLOW_REMNAWAVE", True)` пишется лог VPN_UTILS_REMOVE_NOOP и `return None`. vpn_utils.py:802 `safe_remove_vless_user_with_retry` внутри цикла вызывает `await remove_vless_user(uuid_clean)` (строка 830) и сразу логирует ORPHAN_CLEANUP_SUCCESS — то есть под флагом никогда не падает и всегда «успешно» ничего не делает.

Проверил, отменяет ли что-то сценарий выше по коду — не отменяет для ключевых путей:
- app/services/activation/service.py:404 — Phase 2 провижинит РЕАЛЬНЫЕ сущности через `purchase_flow.provision_subscription` (premium + bypass в Remnawave, purchase_flow.py:166 create_premium_user_entity, :220 create_bypass_user_entity, expireAt=subscription_end). Компенсация на 440/457/484/512 — это no-op safe_remove..., после которого пишется CRITICAL ACTIVATION_ORPHAN_PREVENTED (442/459/486/514). Флаг-гарда здесь нет.
- То же самое без гарда: database/subscriptions.py:4518 provision → :5063 компенсация + ORPHAN_PREVENTED (:5066); database/admin.py:2318 provision → :2404/:2420 (+:2406/:2423); :2548 → :2710; :3015 → :3091/:3107.

_(обоснование сокращено, полностью — в findings.json)_

</details>

### 3. ActivationNotAllowedError не перехватывается — одна плохая подписка обрывает всю итерацию воркера

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`activation_worker.py:236`

**Дефект.** В цикле обработки activation_worker ловятся только VPNActivationError (строка 236) и ActivationFailedError (строка 325). Но attempt_activation бросает ещё и ActivationNotAllowedError — из app/services/activation/service.py:382 и :464 (состояние подписки изменилось за время HTTP-окна). Это исключение не является подклассом двух перехваченных (см. app/services/activation/exceptions.py:13-25, общий предок только ActivationServiceError), поэтому улетает во внешний `except Exception` на activation_worker.py:364, который возвращает (items_processed, 'failed') и прекращает обработку всего оставшегося батча.

**Когда ломается.** В выборке из 50 pending-подписок вторая успела стать active/failed параллельным потоком. attempt_activation бросает ActivationNotAllowedError, воркер выходит с 'failed', остальные 48 оплаченных подписок не активируются в этой итерации. При устойчивом наличии такой строки в начале выборки (ORDER BY s.id ASC) батч не разгребается никогда.

**Что сделать.** Добавить в цикл `except ActivationNotAllowedError` с логированием и continue (импорт уже есть на строке 18). Отдельно рассмотреть перехват общего ActivationServiceError как страховочного.

### 4. Adopt легаси bypass-сущности отдаётся как conflict_unrelated_user и валит покупку

**P1** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: средний

`app/services/remnawave_bypass.py:65`

**Дефект.** `_is_our_entity` признаёт сущность своей только по полю telegramId или по маркеру в description ('bypass'/'samopis'/'via bot'). Bypass-сущности, созданные до Task-2 через remnawave_service.create_remnawave_user (app/services/remnawave_service.py:121), заводятся вызовом remnawave_api.create_user БЕЗ kwarg telegram_id и без description — значит ни telegramId, ни description у них нет. Имя при этом ровно `str(telegram_id)`, то есть совпадает с build_bypass_username. В результате при пустой колонке subscriptions.remnawave_uuid preflight находит сущность, не признаёт её своей и возвращает ok=False/error='conflict_unrelated_user' (remnawave_bypass.py:161-170), а purchase_flow.provision_subscription на строке 226 бросает RuntimeError.

**Когда ломается.** У пользователя есть старая bypass-сущность в панели, а колонка remnawave_uuid очищена (её чистит remnawave_service._get_user_with_recovery:81 при «легаси shortUuid вместо UUID»). Пользователь платит → provision_subscription падает → grant_access исчерпывает MAX_VPN_RETRIES=2 → подписка уходит в pending → activation_worker повторяет 5 раз и получает тот же конфликт → activation_status='failed'. Деньги приняты, доступ не выдан.

**Что сделать.** Расширить _is_our_entity: считать сущность своей, если username == build_bypass_username(telegram_id) (имя и так детерминировано по telegram_id и уже использовано для поиска). Либо ввести отдельный код ошибки и не валить покупку из-за bypass — premium важнее. Проверить ту же логику в remnawave_premium._is_our_entity (там имя tg_<id>_premium, риск ниже).

### 5. Adopt bypass-сущности не начисляет купленный трафик

**P1** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: средний

`app/services/remnawave_bypass.py:151`

**Дефект.** В create_bypass_user_entity при попадании в preflight-adopt (строка 151) возвращается `_result_from_existing(...)` с ok=True, но аргумент traffic_limit_bytes нигде не применяется — PATCH trafficLimitBytes не делается. purchase_flow.provision_subscription (app/services/purchase_flow.py:219-237) уходит в эту ветку всегда, когда database.get_remnawave_uuid(telegram_id) пуст, и трактует ok=True как успешный провижининг, просто записывая кэш.

**Когда ломается.** Пользователь без remnawave_uuid в БД, но с существующей панельной сущностью (тот же сценарий, что и в предыдущей находке, либо гонка, где set_remnawave_bypass_cache упал с warning на purchase_flow.py:238) покупает тариф с 50 ГБ bypass. Сущность усыновляется со старым лимитом, купленные 50 ГБ не начисляются, ошибок нет — бот рапортует успех.

**Что сделать.** В ветке adopt после _result_from_existing вызывать add_bypass_traffic / PATCH trafficLimitBytes = current + traffic_limit_bytes (по аналогии с _ensure_premium_entity_state в remnawave_premium.py:137). Обязательно сделать идемпотентно, иначе ретраи grant_access начислят трафик несколько раз.

### 6. Админский отзыв доступа не отключает premium в Remnawave

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`database/admin.py:3217`

**Дефект.** `admin_revoke_access_atomic` в PHASE 2 (строка 3217) делает `vpn_utils.safe_remove_vless_user_with_retry(uuid_to_remove)` — no-op. В функции нет ни одного вызова remnawave_premium.disable_premium_user / delete_user. В БД проставляется status='expired', uuid=NULL, vpn_key=NULL, но панельная сущность tg_<id>_premium остаётся ACTIVE со своим expireAt. Для сравнения: admin_delete_user (database/admin.py:4350) хотя бы вызывает delete_remnawave_user_bg — но и он гасит только bypass-сущность (remnawave_uuid), premium не трогает.

**Когда ломается.** Админ отзывает доступ у нарушителя или у пользователя, оформившего чарджбэк. Бот показывает 'подписка отозвана', а клиент продолжает пользоваться VPN до конца оплаченного периода (до 365 дней). После обнуления subscriptions.uuid у бота больше нет ссылки на панельную сущность в этой функции — ручная чистка усложняется.

**Что сделать.** Добавить в admin_revoke_access_atomic (и в admin_delete_user) вызов remnawave_premium.disable_premium_user(telegram_id) после коммита, до обнуления remnawave_premium_uuid. Прочитать remnawave_premium_uuid ДО UPDATE, иначе он теряется.

### 7. Реконсиляция режет доступ до NOW+1 день, сравнивая пересчёт с потенциально устаревшей датой из БД

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: высокий

`database/reconciliation.py:721`

**Дефект.** В apply_reconciliation_fix ветка `elif old_expires_at and new_expires_at > old_expires_at:` присваивает new_expires_at = now + 1 день с пометкой 'would_extend'. При этом old_expires_at читается из subscriptions.expires_at (строка 666), а весь смысл модуля (докстринг, строки 55-66 и 638-642) в том, что именно это поле может быть протухшим, и источником истины считается панель. То есть корректно пересчитанная по платежам дата, оказавшаяся больше устаревшего значения в БД, трактуется как ошибка и заменяется на сутки.

**Когда ломается.** Пользователь оплатил basic_365, но subscriptions.expires_at подрезан прошлым инцидентом до +10 дней. Админ жмёт «Исправить» — симуляция даёт корректные ~360 дней, срабатывает clamp 'would_extend', в панель уходит expireAt = now+1 день. Оплаченный год превращается в сутки, через 24 часа штатный expiry-cleanup гасит пользователя.

**Что сделать.** Сравнивать пересчитанную дату не с subscriptions.expires_at, а с panel_expires_at (_fetch_panel_expires_at), либо вовсе не применять clamp 'would_extend', а возвращать в UI предупреждение и требовать явного подтверждения админом.

### 8. Админский перевыпуск ключа через reissue_subscription_key всегда падает

**P1** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`database/subscriptions.py:956`

**Дефект.** `reissue_subscription_key` вызывает `vpn_utils.reissue_vpn_access`. Внутри неё (vpn_utils.py:893) идёт `add_vless_user`, который при PURCHASE_FLOW_REMNAWAVE=True возвращает заглушку с vless_url="" (vpn_utils.py:206-211). Сразу за этим vpn_utils.py:904 проверяет `if not vless_url: raise VPNAPIError("VPN API did not return vless_link during reissue")`. То есть путь гарантированно бросает исключение при включённом флаге. Живые потребители есть: app/handlers/admin/base.py:311 (одиночный перевыпуск по кнопке) и app/handlers/admin/base.py:422 (массовый перевыпуск). Рабочий Remnawave-перевыпуск лежит рядом в reissue_vpn_key_atomic (subscriptions.py:1192), но эти два обработчика на него не переведены.

**Когда ломается.** Админ жмёт «Перевыпустить ключ» в карточке подписки → callback.answer("Ошибка при перевыпуске ключа: VPN API did not return vless_link during reissue"). Массовый перевыпуск даёт 100% failed_count по всем подпискам.

**Что сделать.** Перевести app/handlers/admin/base.py:311 и :422 на database.reissue_vpn_key_atomic(telegram_id, admin_id), затем удалить reissue_subscription_key и vpn_utils.reissue_vpn_access целиком. Оба уходят вместе с xray-веткой.

### 9. Эндпоинт /api/sub/{token} в subscription_proxy не может отрезолвиться в принципе

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/api/subscription_proxy.py:143`

**Дефект.** bot_sub передаёт token в _resolve(uuid), который ищет его в subscriptions.remnawave_premium_uuid (строка 103) и в subscriptions.uuid (строка 114). Но token — это HMAC-SHA256(bot_token, telegram_id), обрезанный до 32 base64url-символов (vpn_utils.py:927-942), и он не хранится ни в одной колонке. Следовательно оба поиска всегда промахиваются и функция уходит в _legacy_fallback_url(token) (строка 130), формируя {LEGACY_SAMOPIS_SUB_BASE_URL}/sub/{hmac_token} — путь, которого на самописном сервере тоже нет. При пустом LEGACY_SAMOPIS_SUB_BASE_URL (дефолт, config.py:615) — 404.

**Когда ломается.** SUBSCRIPTION_PROXY_ENABLED включают, чтобы старые ссылки заработали. Ссылки формата /sub/{uuid} действительно чинятся, а все ссылки, которые бот раздавал сам (build_sub_url → /api/sub/{token}), получают 404 или редирект в никуда.

**Что сделать.** Либо резолвить token обратно в telegram_id (перебор невозможен — нужно хранить token или принимать ?id= и проверять HMAC; параметр id в запросе есть, но игнорируется, см. докстринг строка 18), либо удалить маршрут /api/sub/{token} как нерабочий. Тесты tests/integration/test_subscription_proxy.py этот случай не покрывают.

### 10. Провал _ensure_premium_entity_state игнорируется — покупка засчитывается со старым expireAt

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`app/services/purchase_flow.py:165`

**Дефект.** Ветка adopt в remnawave_premium.create_premium_user_entity (строки 264-272 и 326-333) после усыновления вызывает _ensure_premium_entity_state, который патчит expireAt/status. При провале PATCH функция логирует CRITICAL и возвращает False (remnawave_premium.py:179-192), но результат нигде не проверяется — usynовление всё равно отдаётся как PremiumCreateResult(ok=True, recovered=True). purchase_flow.provision_subscription (строка 172) смотрит только на result.ok и продолжает как при успешном провижининге.

**Когда ломается.** Пользователь платит, remnawave_uuid в БД пуст, панель отдаёт существующую сущность, PATCH expireAt падает (5xx/таймаут). provision_subscription возвращает успех, grant_access коммитит новую дату в БД, бот шлёт ключ. В панели expireAt остался старым — доступ умирает на старой дате. Ровно тот сценарий, который описан в докстринге _ensure_premium_entity_state, но не обработан.

**Что сделать.** Возвращать признак patched в PremiumCreateResult и в purchase_flow при patched=False бросать RuntimeError, чтобы сработал существующий retry-цикл MAX_VPN_RETRIES в grant_access.

### 11. provision_subscription возвращает requested_uuid независимо от того, принял ли его панель

**P2** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: средний

`app/services/purchase_flow.py:257`

**Дефект.** Возвращаемый dict всегда содержит `"uuid": requested_uuid` (строка 257), хотя create_premium_user_entity явно сообщает, приняла ли панель форсированный vlessUuid (PremiumCreateResult.forced_uuid_accepted, remnawave_premium.py:296) — этот флаг не читается. В adopt-ветке (recovered=True) forced_uuid_accepted всегда False, и requested_uuid к панельной сущности вообще отношения не имеет. Именно этот uuid уезжает в subscriptions.uuid через grant_access.

**Когда ломается.** Панель отклонила vlessUuid (400/422) или сущность была усыновлена. В subscriptions.uuid лежит выдуманный UUID, которого нет ни в одном инбаунде. Пострадают: app/api/subscription_proxy.py:114 (поиск по samopis-uuid), database/traffic.py:249 (lookup по легаси-uuid), а также любые сверки БД↔панель по этому полю. Комментарий на purchase_flow.py:255-256 проблему признаёт, но кода нет.

**Что сделать.** Либо писать в subscriptions.uuid фактический vlessUuid из ответа панели, либо явно занулять поле, когда форс не принят, и не делать вид, что связь есть. Решение принимать вместе с решением по subscription_proxy (см. отдельные находки).

### 12. Последний фолбэк ссылки на подписку ведёт на выводимую из эксплуатации samopis-инфраструктуру

**P2** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: средний

`app/services/user_subscription_links.py:351`

**Дефект.** get_user_primary_subscription_url при неудаче premium и lazy-provision возвращает _legacy_sub_url → vpn_utils.build_sub_url (vpn_utils.py:945), то есть https://{SUB_BASE_URL}/api/sub/{hmac_token}?id={tg}. Обслуживать этот URL должен либо внешний мини-апп поверх samopis, либо app/api/subscription_proxy.py, но SUBSCRIPTION_PROXY_ENABLED по умолчанию False (config.py:577) и в .env.example выключён и для stage, и для prod (строки 124-125). Тот же build_sub_url напрямую подставляется в админские уведомления: app/handlers/admin/reissue.py:64, app/handlers/admin/access.py:145/150/401/1892/2143.

**Когда ломается.** У пользователя не получилось создать premium-сущность (панель недоступна, conflict_unrelated_user и т.п.). Бот вместо ошибки показывает кнопку «Подключиться» с samopis-ссылкой. После выключения xray-инфраструктуры ссылка отдаёт ошибку — пользователь считает, что купил нерабочий ключ.

**Что сделать.** Решить продуктово: либо возвращать None и показывать честное «ключ выдаётся, попробуйте позже», либо включить subscription_proxy. В подпроекте B build_sub_url/generate_sub_token — это единственная часть vpn_utils, у которой есть живые потребители вне xray-пути (6 мест в admin-хендлерах + user_subscription_links), поэтому их надо переносить, а не удалять.

### 13. app/services/vpn/service.py: remove_uuid_if_needed возвращает True, ничего не удалив

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`app/services/vpn/service.py:145`

**Дефект.** remove_uuid делегирует в vpn_utils.remove_vless_user (строка 145), который при флаге — no-op. Единственный потребитель — fast_expiry_cleanup.py:232, который по True печатает 'cleanup: VPN_API_REMOVED' (fast_expiry_cleanup.py:238) и продолжает обнулять uuid/vpn_key в БД. То есть в момент истечения подписки premium-сущность в Remnawave не отключается вообще; она гаснет только естественным путём по своему expireAt.

**Когда ломается.** Подписка истекла раньше, чем expireAt в панели (например, после админского подреза expires_at или после «bypass-only»-перехода на fast_expiry_cleanup.py:299, где expires_at ставится now+10 лет). Строка в БД обнулена, ссылки в панели живы — пользователь продолжает пользоваться premium-VPN, лог утверждает обратное.

**Что сделать.** Заменить vpn_service.remove_uuid_if_needed в fast_expiry_cleanup на вызов remnawave_premium.disable_premium_user (bypass там уже обрабатывается отдельно, строки 288-316), после чего удалить app/services/vpn/service.py целиком. Помимо fast_expiry_cleanup модуль упоминается только в app/utils/logging_helpers.py:300 (импорт класса исключения) — это тоже надо снять.

### 14. app/services/vpn_client.py — модуль без единого потребителя

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`app/services/vpn_client.py:1`

**Дефект.** 251 строка фасада (health_check, create_user, extend_user, disable_user, get_user, пять классов исключений). Поиск по всему репозиторию даёт только самоупоминание в докстринге (строка 5) — ни одного import vpn_client / from app.services import vpn_client. Функционально он к тому же дублирует grant_access и вызывает no-op'нутые update_vless_user (строка 181) и remove_vless_user (строка 209).

**Когда ломается.** Мёртвого рантайм-сценария нет. Вред в другом: файл выглядит как действующий API-фасад, его докстринг описывает архитектуру 'Telegram Bot → vpn_client → vpn_utils → Xray API', которой нет, и он тянет за собой vpn_utils в графе импортов при попытке удалить xray.

**Что сделать.** Удалить файл целиком в подпроекте B. Потребителей вне xray-пути нет.

### 15. Лог реконсиляции коммитится до патча панели и фиксирует несделанное исправление

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`database/reconciliation.py:752`

**Дефект.** INSERT в subscription_reconciliation_log выполняется внутри транзакции (строка 752-772), которая коммитится на выходе из `async with conn.transaction()`. Патч Remnawave делается только после этого, на строке 782, и его результат влияет лишь на возвращаемое success. Комментарий на строках 774-778 утверждает, что «bot-DB подрезан», но по коду (строки 730-733) bot-DB намеренно НЕ трогается — то есть при падении панели в БД остаётся только запись аудита о фикса, которого не было.

**Когда ломается.** Панель недоступна. Админ жмёт «Исправить», получает ошибку в UI, но в subscription_reconciliation_log уже лежит строка old_expires_at→new_expires_at с proof_payment_ids. Повторные попытки плодят дубли. Последующий аудит по логу считает пользователей исправленными.

**Что сделать.** Либо писать лог после успешного патча панели, либо добавить в таблицу колонку panel_updated/status и заполнять её вторым UPDATE. Заодно поправить устаревший комментарий на строках 774-778.

### 16. Экран сверки на каждый запрос выкачивает весь список пользователей панели

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`database/reconciliation.py:204`

**Дефект.** find_over_issuance_candidates вызывает remnawave_api.get_all_users() без кэша и без ограничения. get_all_users (app/services/remnawave_api.py:278-341) листает панель страницами по 1000 с ретраями; в комментарии на строке 337 упоминается продовая база «~358k». Вызывающий — HTTP-роут дашборда app/api/dashboard/routes/reconciliation.py:31, то есть скан запускается на каждое открытие экрана. Параметр limit=200 применяется уже ПОСЛЕ полного скана (строка 253).

**Когда ломается.** Админ обновляет экран «Сверка» несколько раз подряд: каждый раз сотни запросов к панели, десятки секунд ответа, риск rate-limit со стороны Remnawave и залипание воркера FastAPI.

**Что сделать.** Кэшировать результат скана (TTL порядка 5-15 минут) или вынести его в фоновую задачу с записью кандидатов в таблицу, а роут пусть читает таблицу. Аналогичный полный скан есть в app/handlers/admin/reconcile.py:105 — там хотя бы есть progress_cb.

### 17. reissue_vpn_key_atomic держит соединение пула и session-lock во время HTTP к панели

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: средний

`database/subscriptions.py:1233`

**Дефект.** Функция берёт conn из пула (строка 1205), ставит session-level pg_advisory_lock (строка 1207) и внутри этого блока делает сетевые вызовы remnawave_premium.reissue_premium_user_entity (строка 1233) — это DELETE + preflight + POST к панели, до нескольких секунд. Весь остальной код домена явно выстроен по правилу «HTTP вне удерживаемого соединения» (см. app/services/activation/service.py:327-333 и комментарии POOL_STABILITY в fast_expiry_cleanup.py:232).

**Когда ломается.** Массовый перевыпуск или несколько админов одновременно: каждый перевыпуск удерживает соединение пула на время 2-3 запросов к Remnawave. При медленной панели пул (см. расчёт в database/core.py:239) выедается, и деградируют пользовательские хендлеры.

**Что сделать.** Разнести на фазы как в activation-сервисе: короткое соединение на чтение строки, освободить, сделать HTTP, затем взять соединение под транзакцию с pg_advisory_xact_lock (он там уже есть на строке 1262 — session-lock снаружи избыточен).

### 18. Инвентарь удаления: тесты и миграции, затрагиваемые выпиливанием xray

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`tests/test_vpn_utils_noop_cutover.py:1`

**Дефект.** Тесты, которые уносит или ломает удаление: tests/test_vpn_utils_noop_cutover.py — весь файл проверяет именно no-op поведение под флагом (строки 40-115, включая проверку `cfg.PURCHASE_FLOW_REMNAWAVE is True` на строке 115) и должен быть удалён целиком; tests/integration/test_vpn_entitlement.py — TestOrphanPreventionOnDBFailure патчит database.vpn_utils.add_vless_user / remove_vless_user (строки 28-29) и утверждает вызов remove_vless_user (строка 72) — этот тест защищает механику, которая по факту уже no-op, его надо переписать на Remnawave-компенсацию; TestExpiredSubscriptionRemoved (строка 104) заканчивается `assert True` на строке 146 и не проверяет ничего. Миграции при этом трогать не нужно: колонки uuid/vpn_key/vpn_key_plus (001_init.sql:29, 033_add_vpn_key_plus.sql) продолжают использоваться Remnawave-путём (grant_access пишет туда premium/bypass URL), а 045-048 — это уже Remnawave. Единственное упоминание флага в SQL — комментарий migrations/048_add_remnawave_bypass_cache.sql:11.

**Когда ломается.** Если удалить xray-ветку, не тронув тесты, к 39 уже падающим тестам из baseline добавятся падения из test_vpn_utils_noop_cutover (ImportError на vpn_utils.add_vless_user) — регресс будет неотличим от фонового шума.

**Что сделать.** Удалить tests/test_vpn_utils_noop_cutover.py вместе с флагом. Переписать TestOrphanPreventionOnDBFailure под новую компенсацию на Remnawave — это единственный тест, который вообще проверяет откат при сбое, терять его нельзя. Удалить или дописать пустой TestExpiredSubscriptionRemoved. Схему БД не менять.

### 19. Истечение триала не отключает premium-сущность

**P2** · ⚠️ не перепроверено · уверенность автора находки: medium · риск правки: средний

`trial_notifications.py:630`

**Дефект.** В обработчике истечения триала единственный акт «отзыва VPN» — `await vpn_utils.remove_vless_user(uuid_val)` (строка 630), то есть no-op. Более того, на строке 632 при исключении функция делает return и НЕ помечает подписку истёкшей — но исключения быть не может, потому что no-op всегда успешен. Далее (строки 637-660) при наличии remnawave_uuid строка переводится в bypass-only с expires_at=now+10 лет и продлевается bypass. Premium-сущность триальщика (её создаёт purchase_flow при is_trial или лениво user_subscription_links.py:174) нигде не отключается.

**Когда ломается.** Триал на 3 дня заканчивается. Premium-сущность в панели была создана с expireAt=subscription_end, так что обычно гаснет сама. Но если триал продлевали, или lazy-provision выставил expire_at по ветке user_subscription_links.py:164 (now+3 дня при пустом expires_at), или админ трогал даты — сущность остаётся ACTIVE, и триальщик пользуется premium бесконечно.

**Что сделать.** В ветке истечения триала вызывать remnawave_premium.disable_premium_user(telegram_id) и удалить вызов vpn_utils.remove_vless_user вместе с бессмысленным early-return на строке 632.

### 20. upgrade_vless_user и remove_plus_inbound: мёртвые и при этом НЕ закрытые флагом

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`vpn_utils.py:407`

**Дефект.** Обе функции (vpn_utils.py:407 и :463) не имеют ни одного вызывающего — остались только комментарии на database/subscriptions.py:1500 и app/handlers/admin/access.py:880, объясняющие, что путь выпилен из-за 404 после cut-over. В отличие от add/update/remove они не защищены проверкой PURCHASE_FLOW_REMNAWAVE и при вызове честно пойдут по HTTP на XRAY_API_URL.

**Когда ломается.** Рантайм-сценария сейчас нет (недостижимы). Опасность — регресс: любой будущий вызов уйдёт на выведенный из эксплуатации хост и упадёт по таймауту 10 секунд внутри пользовательского хендлера.

**Что сделать.** Удалить обе функции в подпроекте B вместе с остальным содержимым vpn_utils. Потребителей вне xray-пути нет.

### 21. Инвентарь удаления: что уносит xray-ветка (файлы и конфиг)

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: высокий

`xray_api/main.py:1`

**Дефект.** Полностью удаляемые артефакты: xray_api/ (main.py 816 строк — эндпоинты /health, /self-test, /add-user, /remove-user/{uuid}, /update-user, /list-users; .env.example; README.md; requirements.txt); systemd/vpn-api.service (ссылается на xray.service и /usr/local/lib/xray_api); scripts/full_xray_resync.py (единственный потребитель ensure_user_in_xray вне xray_sync); xray_sync.py; app/services/vpn_client.py; app/services/vpn/ (после переезда fast_expiry_cleanup); из vpn_utils.py — всё, кроме generate_sub_token/build_sub_url. Конфиг: config.py:343-373 (XRAY_API_URL, XRAY_API_KEY, VPN_ENABLED, VPN_PROVISIONING_ENABLED, XRAY_SYNC_ENABLED, VPN_SERVER_URL), config.py:192 tariff_for_vpn_api (единственный потребитель — vpn_utils.py:270), config.py:585 PURCHASE_FLOW_REMNAWAVE; .env.example строки 27-35. Важно: xray_api/main.py:26 импортирует app.core.logging_config — то есть отдельный сервис связан с пакетом бота, при удалении эту зависимость проверять не нужно, она уходит вместе с ним.

**Когда ломается.** Не дефект исполнения, а вход для подпроекта B. Риск в том, что VPN_ENABLED и VPN_PROVISIONING_ENABLED имеют потребителей ВНЕ xray-пути (см. отдельную находку P0 по config.py:352), а generate_sub_token/build_sub_url — 8 живых call-site'ов в admin-хендлерах и user_subscription_links. Их нельзя удалять, только переносить.

**Что сделать.** Порядок работ: (1) переопределить VPN_ENABLED через REMNAWAVE_ENABLED; (2) перевести fast_expiry_cleanup, trial_notifications, auto_renewal, admin_revoke, admin base.py reissue на Remnawave-эквиваленты; (3) вынести generate_sub_token/build_sub_url в отдельный модуль (например app/services/legacy_sub_links.py); (4) только потом удалять файлы и env-переменные.

### 22. xray_sync.py — воркер, который вхолостую обходит всех активных пользователей

**P2** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`xray_sync.py:93`

**Дефект.** full_sync выбирает все активные подписки с uuid (строки 36-42) и для каждой зовёт vpn_utils.ensure_user_in_xray, который при флаге сводится к update_vless_user → return None (vpn_utils.py:561). Триггерится это из xray_sync_worker_task каждые 5 минут при провале check_xray_health (строка 183-185), а health-check против выведенного из эксплуатации хоста будет проваливаться всегда. Модуль стартует только при config.XRAY_SYNC_ENABLED (config.py:371, дефолт false) — то есть в проде, скорее всего, не работает вовсе; проверить env я не могу.

**Когда ломается.** Если XRAY_SYNC_ENABLED где-то выставлен в true: каждые 5 минут полный SELECT по активным подпискам плюс N бесполезных вызовов, N раз в лог. Полезного эффекта ноль.

**Что сделать.** Удалить xray_sync.py, XRAY_SYNC_ENABLED (config.py:371), блок импорта main.py:39-44, ветку авто-восстановления main.py:440-444, start_xray_sync_safe main.py:536-557 и ключ 'xray_sync' в recovered_tasks (main.py:359). Потребителей вне xray-пути нет.

### 23. Несогласованный дефолт PURCHASE_FLOW_REMNAWAVE в getattr

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`database/subscriptions.py:2558`

**Дефект.** Здесь `getattr(config, "PURCHASE_FLOW_REMNAWAVE", False)`, тогда как во всех остальных местах дефолт True: vpn_utils.py:198, 561, 640, app/handlers/callbacks/payments_callbacks.py:724 (там тоже False). Сам config.py:585 объявляет флаг всегда, так что расхождение проявляется только при подменённом config-объекте.

**Когда ломается.** В тестах или при частичном мок-конфиге (как в tests/integration/test_vpn_entitlement.py:58, где config подменяется MagicMock без этого атрибута) одна ветка думает, что cut-over включён, другая — что выключен: rollback пойдёт дёргать samopis, хотя провижининг шёл через Remnawave.

**Что сделать.** При удалении флага в подпроекте B все три вхождения getattr снимаются вместе с ветками. До этого — привести дефолт к True.

### 24. Мёртвые символы VPN-домена: исключения, алерт, хелперы, импорт

**P3** · ⚠️ не перепроверено · уверенность автора находки: high · риск правки: низкий

`vpn_utils.py:84`

**Дефект.** Ни разу не используются: vpn_utils.CriticalUUIDMismatchError (объявлен на строке 84, не бросается нигде); vpn_utils.VPNTimeoutError (строка 69, только ловится в app/services/vpn/service.py:156, но не бросается); app/core/exceptions.XraySyncError и RenewalSyncError (только реэкспорт в app/core/__init__.py:2); app/services/admin_alerts.alert_vpn_api_failure (строка 198, вызовов нет); app/services/activation/service._update_subscription_activated (строка 532, сама себя объявляет deprecated); неиспользуемый import weakref в vpn_utils.py:27 (совпадает с находкой vulture из baseline); activation_worker.py:367 присваивает error_type = classify_error(e) и тут же выходит по return, не используя значение.

**Когда ломается.** Рантайм-эффекта нет. Эффект в аудите: ловля VPNTimeoutError создаёт ложное впечатление обработки таймаутов, а alert_vpn_api_failure — впечатление, что по VPN-сбоям приходят алерты админам (их не приходит).

**Что сделать.** Удалить перечисленные символы вместе с xray-веткой. Единственное, что требует внимания за пределами домена, — app/core/__init__.py:2-4, его реэкспорт надо снять.

---

## Опровергнутые находки

Скептик показал, что эти находки неверны. Оставлены для истории.

- **Автопродление с баланса не продлевает premium-сущность в Remnawave** (`auto_renewal.py:384`) — Находка опирается на предпосылку, что auto_renewal получает от grant_access payload `renewal_xray_sync_after_commit` и «сливает» его в no-op. Эта предпосылка не выполняется.

1. Payload формируется ТОЛЬКО под условием `if _caller_holds_transaction:` — database/subscriptions.py:1817 (обычное продление), :1553 (basic→plus), :1622 (plus→basic). Иначе выполняется ветка «Standalone: no transaction held — safe to sync inline» и грант сам делает `await purchase_flow.sync_renewal_to_remnawave({...})` — database/subscriptions.py:1829-1836, после чего возвращает result_dict БЕЗ ключа `renewal_xray_sync_after_commit`.

2. `_caller_holds_transaction` — отдельный параметр со значением по умолчанию False (database/subscriptions.py:1361). Внутри grant_access он ниоткуда не выводится из `conn` (см. database/subscriptions.py:1430-1437 — из conn выводится только should_release_conn). Явно True он передаёт

---

## Что прочитано, а что нет

Прочитано целиком: vpn_utils.py (951), app/services/vpn/service.py (200), app/services/vpn_client.py (251), app/services/activation/service.py (661), app/services/activation/exceptions.py, app/services/activation/__init__.py, activation_worker.py (500), database/reconciliation.py (949), app/api/subscription_proxy.py (148), xray_sync.py (204), app/services/purchase_flow.py (288), app/services/remnawave_api.py (492), app/services/remnawave_service.py (427), app/services/remnawave_premium.py (555), app/services/user_subscription_links.py (358), app/core/feature_flags.py, tests/integration/test_vpn_entitlement.py, app/services/remnawave_bypass.py (строки 1-265 из ~300). Прочитано выборочно (по grep + чтение конкретных блоков): database/subscriptions.py — блоки reissue_subscription_key (890-1000), reissue_vpn_key_atomic (1192-1330), grant_access renewal/upgrade/downgrade (1490-1870), new issuance (2000-2100), approve_payment_atomic rollback (2540-2600); database/admin.py — admin_revoke_access_atomic (3140-3230), admin_delete_user (4320-4360), gift activation (4465-4500); auto_renewal.py (320-410); fast_expiry_cleanup.py (220-340); trial_notifications.py (600-660); main.py (520-570 + grep по xray); config.py (по grep VPN/XRAY/REMNAWAVE, строки 343-373, 429, 434-620); app/handlers/admin/base.py (290-330, 400-430). Просмотрено только по grep, без полного чтения: xray_api/main.py (816 строк — прочитаны шапка, список эндпоинтов и .env.example; внутренняя логика работы с config.json не анализировалась, так как сервис удаляется целиком), scripts/migrate_samopis_to_remnawave.py, scripts/verify_samopis_migration.py, scripts/full_xray_resync.py, app/handlers/admin/migration.py, app/handlers/admin/reconcile.py, app/api/dashboard/routes/reconciliation.py, database/traffic.py, app/core/system_state.py, tests/services/*. НЕ читалось: dashboard/ (React), migrations/ кроме 001/045/048, app/services/trials/, app/services/subscriptions/service.py, app/handlers/callbacks/* кроме точечных строк. Проверить не удалось (нет доступа): фактические значения env в проде — поэтому выводы про XRAY_SYNC_ENABLED=false, SUBSCRIPTION_PROXY_ENABLED=false и про отсутствие telegramId у легаси bypass-сущностей опираются на дефолты в config.py/.env.example и помечены confidence=medium.
