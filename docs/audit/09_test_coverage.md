# Фаза 9. Покрытие тестами: где отсутствие тестов угрожает корректной работе

- Дата: 2026-09-14
- Ветка: `worktree-agent-adbb977c9d6a42b08` поверх `refactor/audit-2026-09` (слит `d000cb99`: bypass-only покупка ГБ + подарок 3 дня).
- Запрос владельца: «всё покрыть тестами корректно для корректной работы, если это надо; если нет — то и не надо».
- Принцип: тесты только там, где непокрытый код может дать неверные деньги, доступ или данные либо тихий сбой. Геттеры, тексты и логирование не покрывались.

## 1. Как измерялось

- Покрытие строк и ветвей (`coverage` 7.16, `--cov-branch`) продового кода: герметичный набор + `tests/db` + `tests/e2e`, каждый на свежем PostgreSQL 16 (pgserver), затем `coverage combine`. Тесты, скрипты и миграции исключены.
- Скрипты: `scratchpad/cov09/run_cov.py` (прогон), `perfunc.py` (непокрытые строки по функциям), `compare.py` (таблица ниже).
- Непокрытое ранжировалось по риску, а не по размеру: деньги → доступ → целостность данных → воркеры → дашборд → запуск.

## 2. Покрытие до и после

«До» — `bc70d2a2` (все наборы), «после» — эта ветка со слитым `d000cb99`. Проценты — строки + ветви вместе. В «после» есть и новый код слитой ветки, поэтому у `confirmation.py` −1: там добавлен код bypass-only, а не пропали тесты.

| Модуль | Файл | До | После | Δ |
|---|---|---|---|---|
| Вебхук Telegram | `app/api/telegram_webhook.py` | 82 % | 93 % | +11 |
| Платёжные вебхуки | `app/api/payment_webhook.py` | 98 % | 98 % | 0 |
| Platega | `platega_service.py` | 77 % | 83 % | +6 |
| WATA | `wata_service.py` | 77 % | 80 % | +3 |
| CryptoBot | `cryptobot_service.py` | 80 % | 80 % | 0 |
| Финализация (confirmation) | `app/services/payments/confirmation.py` | 84 % | 83 % | −1 |
| Финализация/продление (БД) | `database/subscriptions.py` | 58 % | 59 % | +1 |
| Баланс, админ-выдачи, удаление (БД) | `database/admin.py` | 38 % | 46 % | +8 |
| Баланс, кэшбэк (БД) | `database/users.py` | 41 % | 42 % | 0 |
| Outbox выдачи | `app/services/provisioning.py` | 89 % | 89 % | 0 |
| Воркер outbox | `app/workers/provisioning_worker.py` | 85 % | 85 % | 0 |
| Legacy-выдача | `app/services/purchase_flow.py` | 80 % | 82 % | +2 |
| Контроль доставки | `app/services/payments/verify_delivery.py` | 72 % | 72 % | 0 |
| Клиент панели | `app/services/remnawave_api.py` | 58 % | 58 % | 0 |
| Автопродление | `auto_renewal.py` | 62 % | 76 % | +14 |
| Воркер активаций | `activation_worker.py` | 10 % | 58 % | +48 |
| Сервис активаций | `app/services/activation/service.py` | 28 % | 61 % | +33 |
| WATA reconciler | `app/workers/wata_reconciler.py` | 81 % | 86 % | +5 |
| Напоминания | `reminders.py` | 43 % | 58 % | +14 |
| Уведомления триала | `trial_notifications.py` | 48 % | 60 % | +12 |
| Миграции (раннер) | `migrations.py` | 72 % | 91 % | +18 |
| БД ядро (init_db, UTC) | `database/core.py` | 70 % | 70 % | 0 |
| Запуск/остановка | `main.py` | 64 % | 64 % | 0 |
| Дашборд: CSRF, блокировка | `app/api/dashboard/security.py` | 77 % | 92 % | +15 |
| Дашборд: Idempotency-Key | `app/api/dashboard/idempotency.py` | 76 % | 89 % | +13 |
| Дашборд: вход | `app/api/dashboard/auth.py` | 64 % | 64 % | 0 |
| Сессии админа | `app/services/admin_auth.py` | 53 % | 78 % | +25 |
| Дашборд: пользователи (запись) | `app/api/dashboard/routes/users.py` | 32 % | 34 % | +3 |

**Всего по продовому коду:** 50,7 % → 53,0 % (32 840 → 33 066 строк). Общий процент растёт мало нарочно: основной непокрытый объём — экраны, магазин и отчёты (§4), их покрывать не нужно.

**Прогоны** (после): герметичный — 3 320 passed, 127 xfailed, 398 skipped (e2e и `tests/db` без Postgres); `tests/db` — 37 passed; `tests/e2e` — 339 passed, 18 xfailed (17 прежних + REF-DANGLING); ruff — чисто.

## 3. Что добавлено и какой риск закрыто

| Тест | Слой | Риск, который закрыт |
|---|---|---|
| `tests/api/test_telegram_webhook_auth.py` (12) | герметичный | Поддельный апдейт Telegram (например, фальшивый `successful_payment`) без секрета, с неверным или «почти верным» секретом получает 403 и **не доходит до хэндлеров**. Секрет не задан → 503. Слишком большое или битое тело отклоняется. Раньше гонялся только happy path: ветки 403/503/413/400 не выполнялись ни разу. Ошибка там = бесплатная подписка по поддельной оплате |
| `tests/services/test_worker_loops.py` (8) | герметичный | Каждый воркер — одна задача на всю жизнь процесса. Если исключение выходит из цикла, задача тихо умирает, и её никто не перезапустит: автопродление перестаёт продлевать, напоминания и уведомления триала не уходят, reconciler WATA не спасает потерянные вебхуки. Циклы не запускал ни один тест (0–3 %). Проверено: после сбоя итерации цикл живёт дальше, шлёт алерт, делает паузу (нет «горячего» цикла), останавливается по cancel. Зависшую итерацию автопродления обрывает таймаут, и лок воркера освобождается (иначе все следующие итерации ждали бы вечно). БД недоступна → итерации пропускаются, без падения |
| `tests/e2e/test_18_activation_worker.py` (4) | Postgres | Воркер активаций (10 %) доводит отложенные выдачи (`activation_status='pending'`). Проверено: сущности в панели создаются один раз, дата panel = БД, +10 ГБ, одно сообщение; повторный тик ничего не делает; пользователь с открытой задачей outbox не трогается (иначе ГБ дважды); истёкшая pending-строка → `failed` без вызовов панели; панель лежит → попытка засчитана, следующий тик активирует |
| `tests/api/test_dashboard_redis_paths.py` (13) | герметичный | Прод-путь при `PROD_REDIS_URL`: блокировка входа, ответы `Idempotency-Key`, сессии админа — в Redis. Все прежние тесты принудительно отключали Redis, работал только запасной путь в памяти. Ошибка здесь «открывает дверь» молча: перебор паролей без блокировки, двойное зачисление баланса из дашборда, сессии, пережившие сброс пароля. Проверено и падение Redis: блокировка и повтор по ключу держатся через память, сессия только из Redis не принимается |
| `tests/services/test_platega_amount_parse.py` (24) | герметичный | Сумма колбэка Platega сверяется с суммой счёта. Platega шлёт `paymentDetails` объектом или строкой («100 RUB»); фейки e2e и матрицы шлют только объект, строковая ветка не выполнялась. Закреплено правило: неоднозначная сумма даёт меньшее значение или fallback (отказ + алерт), никогда не переплату |
| `tests/services/test_wata_public_key_fetch.py` (7) | герметичный | Без `WATA_PUBLIC_KEY_PEM` ключ берётся из `GET /public-key`. Этот запрос был замокан во всех тестах. Проверено: настоящий запрос (PEM в одну строку с литеральными `\n`) проверяет настоящую подпись RSA-SHA512 и кешируется; неудачный или битый ключ не кешируется → `TransientPaymentError` (500, WATA повторит, платёж не потерян); поддельные подписи отклоняются без шторма запросов ключа |
| `tests/test_migration_files.py` (4) | герметичный | `schema_migrations` хранит только номер. Новый файл с уже занятым номером применится на пустой БД (CI зелёный) и **молча пропустится на проде**. Номера уникальны, кроме исторической пары `006` (безвредна: таблицу создаёт inline DDL); все `*.sql` подхватываются раннером; порядок числовой |
| `tests/db/test_migrations_runner.py` (3) | Postgres | Путь сбоя раннера не выполнялся. Проверено: упавшая миграция откатывается целиком и не записывается, следующие поверх неё не идут, `run_migrations` сообщает о сбое (`init_db` не стартует). После исправления применяются только недостающие, уже применённые не перезапускаются; 9 раньше 10 |
| `tests/e2e/test_19_dashboard_writes.py` (10 + 1 xfail) | Postgres | Отзыв доступа, выдача минут и удаление пользователя из дашборда ни разу не выполнялись на Postgres (тест удаления мокал БД). Проверено: отзыв → в БД expired, в панели premium DISABLED, ГБ bypass сохранены, запись в `audit_log`, новая покупка возвращает premium (панель = БД); без Origin или с чужим Origin → 403 и ничего не меняется; выдача минут (флаг off/on) активному = конец + N минут, новому = now + N минут, панель = БД, 0 ГБ; удаление → строки и обе сущности в панели удалены, пригласивший остаётся, тот же аккаунт может заново сделать /start; удаление пригласившего не ломает оплату приглашённого. **Нашёл два бага** (§5) |

Итого новых тестов: 68 герметичных, 3 в `tests/db`, 14 e2e (+1 strict xfail). `tests/db` уже подключён к CI (джоба Migration Integrity), `tests/e2e` — там же; новых директорий нет.

## 4. Что сознательно НЕ покрывалось и почему

| Что | Покрытие | Почему не покрывал |
|---|---|---|
| Тексты оплаты, одно сообщение об успехе, уведомления о кешбэке, лимиты устройств 10/14, −15 % на 72 ч (`payments_messages.py`, тексты `payments_callbacks.py`, сообщения `confirmation.py`, `fast_expiry_cleanup.py`, реферальное уведомление, лимит устройств в provisioning) | — | Меняет параллельный агент прямо сейчас: тесты устарели бы под ним. **Покрывает агент, который их меняет** |
| 🔒 Магазин (`steam_purchase.py` 14 %, `spotify_purchase.py` 23 %, `telegram_premium.py` 16 %, `telegram_stars_purchase.py` 13 %, `apple_id_delivery.py`) | низкое | Код заморожен (SCOPE). Граница с ядром (`mark_pending_purchase_paid` + `send_*_success`, `alert_shop_order_not_notified`) уже покрыта (`test_shop_order_not_lost`, e2e Apple ID/Spotify). Остальное — UI магазина |
| Экраны и навигация (`navigation.py` 22 %, `screens.py` 18 %, `broadcast_offers.py` 11 %, `traffic.py`, `devices.py`, `start.py`) | низкое | Экраны без денежной логики; слой экранов по плану пишется заново (SCOPE, «экраны по одному»). Денежные кнопки этих экранов проходят e2e (144 ячейки покупок, stale combo, скидки) |
| Аналитика и отчёты дашборда (`database/admin.py` 38 %: `get_*_stats`, `get_payments_breakdown`, `get_daily_summary`, экспорт; `database/reconciliation.py` 6 %; `routes/payments.py` 41 %) | низкое | Только чтение. Ошибка видна админу сразу и ничего не меняет. Денежные метрики уже сверены с покупками (`tests/db/test_dashboard_sql.py`, e2e `test_10`) |
| Клиент панели: `get_all_users`, hwid-устройства, статистика нод (`remnawave_api.py`) | 58 % | Используется аудитом/дашбордом на чтение. Пути выдачи (create/PATCH/resolve/adopt) покрыты e2e через HTTP-фейк панели |
| Игра и ферма (`game.py` 41 %, `farm.py` 64 %) | среднее | Денежные ходы фермы (двойной тап, шторм) уже в e2e `test_13`; остальное — игровой UI |
| `main.main()` (64 %), `init_db` (70 %) | среднее | Запуск, лок второго экземпляра, восстановление после простоя БД, drain при остановке — e2e `test_11`. Непокрыты ветки логирования и деградации при недоступном Redis/Node |
| Ветки ошибок внутри `grant_access`, `_finalize_purchase_locked` (≈60–80 строк) | 58 % файла | Большая часть — защитные `raise` на невозможных состояниях и логирование; денежные сбои (панель лежит, дубли, неверная сумма, провайдер, промокод) уже закрыты матрицей (1 200+ ячеек) и e2e |
| Реферальная статистика (`get_referral_*`, `referrals/service.py` 38 %) | низкое | Экраны; начисление кешбэка покрыто (`test_referral_cashback_rule`, e2e `test_06`, инварианты I3) |

**Кандидаты в мёртвый код** (доказательство — grep: нет вызовов, только реэкспорт в `database/__init__.py` или комментарии; не удалял):

| Функция | Где | Примечание |
|---|---|---|
| `approve_payment_atomic` (97 строк) | `database/subscriptions.py` | нет вызовов |
| `get_last_approved_payment` | `database/subscriptions.py` | заменён `get_last_subscription_payment` (P1-1) |
| `get_pending_payment_by_user` | `database/subscriptions.py` | нет вызовов |
| `expire_old_pending_purchases` | `database/admin.py` | нигде не вызывается — брошенные счета остаются `pending` навсегда (так и учтено в `revenue.py`) |
| `check_transaction_status` | `platega_service.py` | нет вызовов (сверки Platega нет) |
| ветка `vpn_api_permanently_disabled` / `should_mark_failed` | `activation_worker.py:229-301` | недостижима: при `VPN_ENABLED=False` воркер выходит раньше. Итог: pending-строка после `MAX_ACTIVATION_ATTEMPTS` не помечается `failed`, просто перестаёт выбираться; админ видит её в дашборде «Активации» |
| возврат при «UUID пересоздан» / `expires_at is None` | `auto_renewal.py:493-556` | по коду `grant_access` продления со своим conn эти состояния не возвращаются (истёкшая подписка даёт INVARIANT RuntimeError, R-EXPIRY-RACE) |

## 5. Найденные баги

| ID | Серьёзность | Суть | Статус |
|---|---|---|---|
| **DASH-DELETE-REFERRER** | высокая | Удаление пользователя из дашборда оставляло `users.referrer_id` приглашённых им людей указывающим на удалённую строку. Следующая оплата любого из них падала в `process_referral_reward` (`ValueError: Referrer … not found for reward`) → confirmation считал это постоянной ошибкой финализации → 200 `{"status":"error"}`, провайдер не повторяет: **деньги списаны, доступа нет**, только forced-алерт. Так же ломались покупка с баланса и автопродление этих пользователей | исправлено `e03ac73c`: удаление обнуляет `referrer_id` приглашённых в той же транзакции |
| **REF-DANGLING** | высокая (данные) | То же для ссылок, **уже** оставленных прошлыми удалениями (фикс их не чинит). Защита должна быть в `process_referral_reward`: реферер не найден → «нет реферера», а не исключение. Это зона параллельного агента (уведомления о кешбэке), поэтому только strict xfail. Проверить прод: `SELECT count(*) FROM users u WHERE u.referrer_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users r WHERE r.telegram_id = u.referrer_id);` | открыт, `test_19_dashboard_writes.py::test_invitee_of_an_already_deleted_referrer_can_still_pay` |
| **DASH-REVOKE-PANEL** | средняя | «Отозвать доступ» в дашборде меняла только БД. `admin_revoke_access_atomic` сохранял UUID «для удаления вне транзакции» и не использовал его: premium-сущность в панели оставалась ACTIVE со старым `expireAt`, **ключ продолжал работать** до оплаченной даты, а дашборд писал «Доступ отозван» | исправлено `5cc725b7`: после commit premium → DISABLED (по кешу UUID или по нашему username), ГБ bypass не трогаются; панель не подтвердила → forced-алерт. Новая покупка снова включает сущность — проверено e2e |

## 6. Качество тестов: заглушки, которые прячут поведение

| Где | Что подменено | Вывод |
|---|---|---|
| `test_admin_delete_user_remnawave.py` | вся БД (`admin_delete_user_complete` 42/43 строк не выполнялись на Postgres) | Именно тут прятался DASH-DELETE-REFERRER. **Заменено** реальным e2e (`test_19`) |
| `test_dashboard_idempotency.py`, `test_dashboard_auth.py`, `test_dashboard_login_lockout_race.py` | `_redis()` → `None` во всех тестах | Прод-путь с Redis не проверялся. **Дополнено** `test_dashboard_redis_paths.py`; старые тесты оставлены — они проверяют запасной путь |
| `test_wata_wave1.py` | `_fetch_public_key_pem` → mock | Реальный запрос ключа не проверялся. **Дополнено** `test_wata_public_key_fetch.py` |
| `test_payment_core_t12.py`, `payment_core_harness.run_auto_renewal` | `get_last_subscription_payment` → mock (так был пропущен P1-1) | SQL теперь проверен на Postgres (`tests/db/test_subscription_payment_sql.py`), e2e `test_05` берёт тариф из настоящей БД. Замена mock в матрице не нужна |
| `test_payment_matrix.py` | `_deliver_bypass_gb`, `verify_*_delivery`, `finalize_purchase` | Подменены только в тестах веток сбоя и алертов (стр. 1771, 1888–1916), не в ячейках happy path. Нормально |
| `test_payment_matrix.py`, legacy `payment_core_harness.FakePanel` | панель на уровне функций, а не HTTP | Даёт артефакты (T0-TG-NEW-20 и T0-BAL-NEW-20 не воспроизводятся на реальном стеке, 07 §4). Итог по ГБ и датам сверяется e2e с HTTP-фейком панели — оставить как есть |
| `test_referral_cashback_rule.py`, `test_balance_double_tap.py`, `test_stars_balance_topup.py` | `process_referral_reward` → mock | Проверяют правило «кто вызывает начисление». Само начисление на Postgres — e2e `test_06` и инварианты I3 (реферер там всегда существует — поэтому REF-DANGLING не ловился) |

**Нестабильный strict xfail.** `test_02_purchases.py::test_purchase[off-balance-combo_basic30-active]` (T0-BAL-COMBO-RENEW) под замедлением `coverage` один раз прошёл (XPASS strict → FAILED), без `coverage` 3/3 xfail. Ячейка флага off зависит от порядка фоновых задач. На CI не влияет (там нет `coverage`), но если xfail снимут или флаг включат — пересмотреть.

## 7. Коммиты

| Коммит | Что |
|---|---|
| `5cc725b7` | fix(dashboard): admin revoke cuts VPN access in the panel (MEDIUM) |
| `e03ac73c` | fix(dashboard): deleting a referrer no longer breaks his invitees' payments (HIGH) |
| `9e57eb1b` | test(security): Telegram webhook secret and dashboard Redis paths |
| `c2aa1d1c` | test(workers): loops survive a failed iteration; activation worker on Postgres |
| `5d16ec2b` | test(payments): Platega amount parsing, WATA key fetched from the API |
| `146a2365` | test(migrations): unique version numbers; runner failure path on Postgres |
| `a96b0186`, `82351fc8` | test(e2e): dashboard write endpoints with a real session on Postgres (переименован в `test_19`) |
