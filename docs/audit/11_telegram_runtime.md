# Фаза 11. Telegram runtime: что делает с ботом живой Telegram

Дата: 2026-09-14. Ветка агента от `refactor/audit-2026-09` (`f0e80321`, после фазы 10 — статических проверок).
Задача владельца: «теперь тестируй на ошибки, возможные теории — это же телеграм бот». Эта фаза —
**поведение в рантайме**: ошибки Bot API, повторы и гонки апдейтов, платежи Telegram, вебхук, типы
чатов, флуд, рестарты, алерты админу. Статику (длина callback_data, HTML-шаблоны, лимиты текста,
deep links) закрыла фаза 10 — здесь не дублируется.

## 1. Как тестировали

- **Сквозные тесты на настоящем PostgreSQL** — `tests/e2e/test_20_telegram_runtime.py` (31 тест). Реальный
  диспетчер aiogram с той же цепочкой middleware, что в `main.py`, получает `Update` через
  `/telegram/webhook`.
- **Фейковый Bot API** (`tests/fakes/telegram.py`) расширен:
  - `FakeTelegram.inject(method, factory, chat_id=, times=)` — заданный метод отвечает ошибкой: 429 `retry_after`, 403, «query is too old», «message to edit not found», «message can't be edited», сетевая ошибка;
  - `strict_html` — HTML с `parse_mode=HTML` проверяется так же, как в Telegram, и на ошибку приходит «can't parse entities»;
  - конструкторы апдейтов: `refunded_payment`, группы и супергруппы, `channel_post`, `edited_message`, `inline_query`, `chat_join_request`, `my_chat_member`, стикеры и фото.
- **Герметичные тесты**:
  - `tests/utils/test_telegram_runtime.py` — rate limit, request-middleware, `safe_send_message`, темп рассылки;
  - `tests/services/test_admin_alerts_digest.py` — повторы сводки алертов;
  - `tests/services/test_payment_core_t9.py` — повтор оплаты по charge id.
- Каждый баг сначала получал падающий тест (strict xfail с id), фикс — отдельным коммитом, который снимает xfail.

## 2. Итог

Найдено 10 багов, все исправлены, каждый отдельным коммитом: 2 × P1, 3 × P2, 5 × P3.
Ещё 10 пунктов — только отчёт (раздел 5). Магазин 🔒 не трогали.

| ID | Серьёзность | Что было | Коммит |
|---|---|---|---|
| TG-RT-1 | **P1** | `successful_payment` юзера под флуд-баном молча выбрасывался — деньги списаны, ничего не выдано, алерта нет | `aee90cb7` |
| TG-RT-9 | **P1** | `successful_payment` «съедали» экраны ввода суммы пополнения и промокода — деньги списаны, ничего не выдано, алерта нет | `7559e320` |
| TG-RT-2 | P2 | повторная доставка `successful_payment` уже оплаченной покупки: юзеру «сессия истекла», админу PERMANENT «деньги взяты, ничего не выдано» — провокация выдать второй раз | `d999fd70` |
| TG-RT-3 | P2 | возврат Stars (`refunded_payment`) не обрабатывался: ни лога, ни `payment_errors`, ни алерта | `54069e47` |
| TG-RT-6 | P2 | поздний ответ на колбэк («query is too old») обрывал весь хэндлер: юзер нажал кнопку и не получил экран | `35cbabed` |
| TG-RT-5 | P3 | блокировка/разблокировка бота (`my_chat_member`) не отслеживалась, апдейта не было даже в `allowed_updates` | `9530f258` |
| TG-RT-4 | P3 | `pre_checkout_query` ждал БД без ограничения; при зависшем пуле терялся 10-секундный бюджет Telegram | `351f779f` |
| TG-RT-7 | P3 | `safe_send_message` терял сообщение на любом 429 (включая «платёж получен») | `92028c35` |
| TG-RT-8 | P3 | недоступный админ-чат: сводка алертов повторялась каждые ~5 с до конца жизни процесса | `921f9efa` |
| TG-RT-10 | P3 | рассылка не держала ~30 сообщений/с: 15 параллельных слотов при быстром API давали сотни сообщений в секунду, 429-шторм | `aaac9b48` |

## 3. Исправлено: сценарий → было → фикс → тест

### TG-RT-1 (P1). Rate limit выбрасывал оплату
- **Было.** `GlobalRateLimitMiddleware` считает каждое `Message`. Если за минуту приходит 60 апдейтов, юзер получает бан на 5 минут, и его апдейты молча выбрасываются. `successful_payment`, пришедший во время бана, до хэндлера не доходил. Telegram этот апдейт не повторяет.
- **Фикс.** `app/core/rate_limit_middleware.py`: `successful_payment` и `refunded_payment` проходят мимо лимитера и не учитываются в счётчике.
- **Тесты.** e2e `test_flood_banned_user_still_gets_the_paid_purchase` (61 сообщение → бан → оплата картой → подписка выдана по правилам владельца). Герметичный `test_flood_banned_user_payment_messages_reach_the_handler`.

### TG-RT-9 (P1). Экран ввода «съедал» оплату
- **Было.** В роутере платежей `topup_fsm` и `promo_fsm` подключены раньше `payments_messages`, а их хэндлеры фильтруют только по FSM-состоянию. Пример: юзер открыл «введите сумму пополнения» или «введите промокод» и оплатил счёт, отправленный раньше. Тогда `successful_payment` забирал экран пополнения, который молча выходит на не-текстовом сообщении. Экран промокода отвечал «введите код текстом».
- **Фикс.** Оба хэндлера получили фильтр `~F.successful_payment, ~F.refunded_payment`. Текстовые состояния магазина 🔒 (Premium, Stars, Steam, Spotify) и чат админа подключены после `payments_messages` и не пострадали — это проверяет тот же тест.
- **Тест.** e2e `test_successful_payment_while_in_a_text_input_state_is_finalized` (6 состояний).

### TG-RT-2 (P2). Повторная доставка оплаты давала ложный алерт
- **Было.** Telegram доставляет апдейт повторно, если вебхук не ответил 2xx. Так бывает, когда процесс убит на полпути при редеплое; при этом `drop_pending_updates=False`. На повторе `get_pending_purchase` (со `status='pending'`) ничего не находил. Юзер, у которого ключ уже есть, видел «сессия истекла». Админ получал `[PERMANENT] … NEEDS MANUAL CHECK` и строку в `payment_errors`.
- **Фикс.** После финализации подписки, пакета ГБ или подарка Telegram charge id пишется в `payments.telegram_payment_charge_id`. Колонка уникальна с миграции 012; пополнения её уже использовали. Если такой charge id уже есть, апдейт пропускается с логом `TELEGRAM_PAYMENT_REDELIVERED`. **Другой** charge по уже оплаченной покупке — это реальные вторые деньги, и алерт остаётся.
- **Тесты.**
  - e2e `test_redelivered_successful_payment_is_silent` и `test_second_charge_for_a_paid_purchase_still_alerts`;
  - герметичный `test_telegram_redelivery_of_a_remembered_charge_is_skipped`;
  - replay-тесты T9 сбрасывают запомненный charge, чтобы и дальше покрывать ветку сервиса `already processed` (окно между commit и записью charge).

### TG-RT-3 (P2). Возвраты Telegram без алерта
- **Было.** Хэндлера `refunded_payment` не было. Сообщение уходило в молчаливый catch-all.
- **Фикс.** `process_refunded_payment` в `payments_messages.py` выполняет правило владельца:
  - лог `TELEGRAM_REFUND`;
  - `payment_errors` со stage `telegram_refund` и charge id;
  - forced-алерт админу;
  - доступ **не** отзывается.
- **Тест.** e2e `test_stars_refund_alerts_admin_and_keeps_access`.

### TG-RT-6 (P2). «Query is too old» обрывал экран
- **Было.** Бывает, что ответ на колбэк уходит позже ~15 с: нагрузка или медленная БД либо панель перед `callback.answer()`. Тогда `answerCallbackQuery` получает 400, исключение обрывает хэндлер, а error boundary его молча глотает. Воспроизведено на «Мой профиль»: `callback_profile` отвечает до отрисовки, `app/handlers/callbacks/subscription.py:336`.
- **Фикс.** `app/utils/telegram_request_middleware.py` — request-middleware сессии Bot API. Ровно эта ошибка и ровно для `AnswerCallbackQuery` превращается в debug-лог и `True`, всё остальное бросается как раньше. Ставится в `main.py`; e2e-`World` повторяет `main.py`.
- **Тесты.**
  - e2e `test_late_callback_answer_does_not_abort_the_screen` и `…_a_balance_purchase`;
  - герметичный `test_stale_callback_answer_is_ignored_other_errors_still_raise`;
  - проверка, что `main.py` ставит middleware.

### TG-RT-5 (P3). Блокировка бота
- **Было.** Хэндлера `my_chat_member` не было, поэтому апдейт не попадал в `allowed_updates` (`main.py` берёт их из `dp.resolve_used_update_types()`). Заблокировавший бота юзер оставался `is_reachable = TRUE` до следующей ошибки 403, и все напоминания и рассылки сначала пробовали его.
- **Фикс.** `app/handlers/user/reachability.py`: статусы `kicked` и `left` ставят `mark_user_unreachable`, статус `member` — `mark_user_reachable`. Хэндлер ничего не отправляет.
- **Тест.** e2e `test_blocking_and_unblocking_the_bot_updates_reachability`; заодно проверяется, что тип апдейта попал в `allowed_updates`.

### TG-RT-4 (P3). pre_checkout без таймаута
- **Было.** `get_pending_purchase` в `process_pre_checkout_query` не был ограничен по времени. Пул исчерпан → ответ позже 10 с → у юзера «бот не ответил», оплата не проходит (деньги не списаны).
- **Фикс.** `asyncio.wait_for(…, PRE_CHECKOUT_DB_TIMEOUT_S=5)`. Таймаут уходит в существующую ветку «ошибка БД → одобрить»; покупка всё равно полностью проверяется на `successful_payment`.
- **Тесты.** e2e `test_pre_checkout_is_answered_in_time_when_the_db_hangs` и `test_pre_checkout_rejects_foreign_expired_and_paid_purchases`. Второй тест проверяет отказ на чужую, просроченную и уже оплаченную покупку.

### TG-RT-7 (P3). 429 терял сообщение
- **Было.** `safe_send_message` глотал `TelegramRetryAfter` и возвращал `None`, а aiogram сам не повторяет. Под коротким флуд-вейтом терялись «платёж получен», ключ, напоминания.
- **Фикс.** Если `retry_after ≤ FLOOD_WAIT_INLINE_MAX_S` (5 с), бот ждёт и отправляет **один** повтор. Вторая ошибка — лог, без петли. Длинное ожидание — сообщение отбрасывается, как раньше. Рассылки (`raise_retry_after=True`) выдерживают паузы сами, так что на каждом месте вызова один слой ретраев.
- **Тесты.**
  - герметичные `test_short_flood_wait_is_slept_out_and_sent_once_more` и `test_flood_wait_retries_only_once_and_long_waits_are_dropped`;
  - e2e `test_flood_wait_on_the_payment_confirmation_does_not_lose_it`.

### TG-RT-8 (P3). Недоступный админ-чат зацикливал сводку
- **Было.** Админ заблокировал бота или указан неверный `ADMIN_TELEGRAM_ID`. Алерт откладывается в сводку (не теряется — это правильно). Но неудачная сводка снова откладывалась и через `_DIGEST_MIN_DELAY` отправлялась опять: каждые ~5 с на категорию, по 2 вызова API и 2 ERROR-лога, до конца жизни процесса.
- **Фикс.** В `app/services/admin_alerts.py` задержка повтора удваивается: 5 с, 10 с, 20 с и так далее, но не больше 1 ч. Первая успешная отправка админу сбрасывает счётчик. Число отложенных алертов сохраняется.
- **Тесты.**
  - `test_unreachable_admin_chat_is_retried_with_backoff_not_in_a_loop` — было ~80 попыток за 0,6 с, стало не больше 16;
  - `test_digest_backoff_resets_once_the_admin_chat_works_again`;
  - e2e `test_admin_chat_unreachable_does_not_break_payments`: оплата проходит, `ADMIN_ALERT_FAILED` в логе.

### TG-RT-10 (P3). Темп рассылки
- **Было.** `BROADCAST_CONCURRENCY = 15` («safe under Telegram 30 msg/sec») ограничивает параллельность, а не скорость. В тесте 120 отправок ушли в одну секунду. Telegram отвечает 429-штормом, каждый слот ждёт свой `retry_after`, остальные продолжают упираться в лимит, и после 3 попыток сообщения теряются.
- **Фикс.** `_Pacer` на каждую рассылку: каждая отправка, включая повторы, занимает следующий слот длиной 1/25 с. Батчи и обработка флуд-вейта остались.
- **Тест.** `test_broadcast_is_paced_under_the_global_telegram_limit` — не больше 30 отправок в любом окне 1 с.

## 4. Сценарии задания → поведение (после фиксов)

1. **Ошибки Bot API.**
   - **403.** Юзер заблокировал бота — оплата через провайдера всё равно выдаёт доступ, `is_reachable = FALSE`. Тест `test_blocked_user_still_gets_access_for_a_provider_payment`.
   - **429.** Короткое ожидание выдерживается один раз (TG-RT-7). В рассылке спасает темп (TG-RT-10).
   - **«message is not modified».** Глотается error boundary — экран уже на месте.
   - **«message to edit not found» / «message can't be edited».** `safe_edit_text` отправляет новое сообщение. Тест `test_uneditable_old_message_still_gets_a_screen` (5 экранов × 2 ошибки).
   - **«query is too old».** Экран больше не теряется (TG-RT-6).
   - **Админ-чат недоступен.** Ошибка в логе, повторы с backoff (TG-RT-8).
2. **Повторы и гонки.**
   - Повтор `successful_payment` не даёт второй выдачи; ложных алертов больше нет (TG-RT-2).
   - Двойной тап `pay:balance` уже покрыт (`test_04_balance`, 60-секундная защита).
   - Кнопки старых сообщений после рестарта (пустой FSM): **108 из 108** точных кнопок без падений (`test_every_button_of_an_old_message_after_a_restart`).
   - Префиксные кнопки (83) с устаревшим или подделанным суффиксом падают только в `apple_*` 🔒 и `farm_*` — это отчёт R4 (`test_prefix_buttons_with_stale_or_forged_suffix` ловит новые).
   - **Дедупликации по `update_id` нет** — R1.
3. **Платежи Telegram.**
   - `pre_checkout`: отказ на чужую, просроченную и уже оплаченную покупку, ответ укладывается в срок (TG-RT-4).
   - Повторная доставка `successful_payment` — одна выдача (TG-RT-2).
   - `successful_payment` после `pre_checkout`, одобренного предыдущим процессом, обрабатывается: `pre_checkout` не хранит состояния.
   - Возвраты — алерт (TG-RT-3).
   - Флуд и текстовые экраны оплату больше не теряют (TG-RT-1, TG-RT-9).
4. **Вебхук.** Уже покрыто `tests/api/test_telegram_webhook_auth.py` и `…_payment_shield.py`:
   - неверный или пустой секрет → 403 и 503 без обработки;
   - больше 1 МБ → 413;
   - кривой JSON → 200 без обработки;
   - исключение хэндлера → 200 (Telegram не долбит повторами);
   - `successful_payment` не отменяется таймаутом 25 с.
5. **Типы чатов и источники.**
   - Группа, супергруппа, канал, `edited_message`, `inline_query`, `chat_join_request` → 200, ни одного исходящего сообщения.
   - Стикер, фото, фото с подписью, 5000 символов, zero-width, HTML в 7 текстовых состояниях — без падений.
   - Имя `<Admin> & Co </b>`, фамилия `<i>`, нет username, `language_code=None` и `pt-br`, RTL-имя — Telegram не отклонил ни одного сообщения (`strict_html`).
   - `my_chat_member` (TG-RT-5).
6. **Флуд.**
   - Флуд-бан больше не трогает оплату (TG-RT-1). `pre_checkout_query` лимитером не считается.
   - Рассылка в темпе (TG-RT-10).
   - Воркеры шлют через `safe_send_message` с паузой 0,05 с. На 429 напоминание не помечается отправленным и повторяется в следующем проходе.
7. **Рестарты.** Пустой FSM и пустые in-memory-кэши (rate limit, сводка алертов) не ломают хэндлеры, см. пункт 2.
8. **Алерты админу.** Недоступный чат — лог и backoff (TG-RT-8). Флуд алертов → одна сводка (фаза P2, тесты `test_admin_alerts_digest.py`).

## 5. Открытые риски (только отчёт)

| # | Серьёзность | Где | Суть |
|---|---|---|---|
| R1 | P3 | `app/api/telegram_webhook.py` | Нет дедупликации по `update_id`. Повтор после рестарта исполняет апдейт ещё раз. Денежные пути идемпотентны (charge id, статус покупки, защита двойного тапа), остальные повторяют экран. In-memory-кэш после рестарта пуст и не помог бы; нужен Redis, если захотим |
| R2 | P4 | `app/core/telegram_error_middleware.py:91` | Текст ошибки захардкожен по-русски («⚠️ Произошла ошибка…»), в обход `get_text` (правило NEVER). Для апдейта-сообщения ответа нет вовсе — только лог |
| R3 | P4 | `app/handlers/payments/payments_messages.py:80` | `error_message` отказа `pre_checkout` захардкожен по-английски, в обход `get_text` |
| R4 | P4 | 🔒 `navigation.py:1783/1933` (`apple_*`), `game.py:891/982/562` (`farm_*`) | Подделанные или устаревшие `callback_data` → `IndexError` / `ValueError` → общий ответ об ошибке. Настоящая кнопка такой строки не несёт. Магазин — только отчёт |
| R5 | P3 | 🔒 магазин, `payments_messages.py` (`_alert_if_shop_row_not_pending`) | Повторная доставка `successful_payment` оплаченного заказа магазина даёт тот же ложный алерт «заказ не оформлен»: у магазина нет строки `payments`, и TG-RT-2 его не покрывает. Решение за владельцем |
| R6 | P3 | `app/workers/farm_notifications.py:68-96`, `:144`, `:243`; `app/workers/traffic_monitor.py:100` | Голый `bot.send_message` вместо `safe_send_message`: 403 не помечает недоступность. Ферма ставит флаг `notified_*` до отправки, поэтому при 429 или сетевой ошибке уведомление теряется |
| R7 | P4 | `app/utils/telegram_safe.py` | На «can't parse entities» нет запасного варианта — plain-текст через `telegram_html.visible_text`. Реального источника не нашли: имена экранируются, тест с HTML в имени зелёный. Шаблоны проверяет фаза 10 |
| R8 | P4 | `app/core/telegram_error_middleware.py` | 403 внутри хэндлера — только debug-лог, пометки недоступности нет. Сейчас частично закрыто `my_chat_member` (TG-RT-5) |
| R9 | P4 | `app/core/rate_limit_middleware.py` | 30 апдейтов в минуту на юзера: быстрый «листатель» кнопок может упереться, и колбэк молча отбрасывается — «часики» до таймаута Telegram. Порог — решение владельца |
| R10 | инфо | `app/api/telegram_webhook.py` | Не-платёжный апдейт дольше 25 с отменяется и отвечает 200; Telegram его не повторит. Так задумано: `successful_payment` защищён отдельно (P1-6) |

## 6. Прогоны

Ветка: 10 фикс-коммитов поверх `f0e80321`.

| Набор | До | После |
|---|---|---|
| Герметичный (`pytest tests --ignore=tests/e2e`) | 3467 passed (до ребейза на фазу 10) | **3508 passed**, 44 skipped, 86 xfailed |
| e2e (`tests/e2e`, PG16) | 343 passed, 12 xfailed, 2 xpassed | **374 passed**, 12 xfailed, 2 xpassed (xfail и xpass прежние, не мои) |
| `tests/db` + гейт миграций (`run_db_tests.py`) | — | **40 passed**, migrations rc=0 |
| `ruff check .` | clean | clean |

Новые тесты:
- 31 e2e в `test_20_telegram_runtime.py`;
- 10 герметичных: 7 в `tests/utils/test_telegram_runtime.py`, 2 в `test_admin_alerts_digest.py`, 1 в `test_payment_core_t9.py`.
