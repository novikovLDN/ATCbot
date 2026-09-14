# Фаза 2. Платёжное ядро — план

- Дата: 2026-09-13
- База: `refactor/audit-2026-09`, код main + волна 1 (Platega, WATA, reconciler, hotfix Lava).
- Рамки: `docs/audit/SCOPE.md`. **Магазин не трогаем.**
- Статус: **ждёт «ок» владельца и ответов на §G**.

## Новые факты, найденные при планировании

1. **Рекуррент Platega не начисляет ГБ вообще.** `platega_service.py:1132` вызывает `grant_access` без `conn`, и продление сводится к `sync_renewal_to_remnawave`, который трогает только premium.
2. **Записать `combo_basic` в `pending_purchases.tariff` не получится.** Мешает CHECK-ограничение (`migrations/035` и inline-DDL `database/core.py:1036-1043`, который переприменяется на каждом старте). Поэтому явный ключ тарифа **вычисляется в коде** из пары `(tariff, is_combo)`. Схема `pending_purchases` не меняется.
3. **У `finalize_purchase` нет атомарности.** Если Phase 1 падает, `grant_access(conn=None)` выполняется на своём соединении, вне платёжной транзакции (`subscriptions.py:4957`).
4. **Бесплатные 10 ГБ при открытии экранов.** Профиль и трафик выдают их, если у пользователя нет bypass: `traffic.py:312`, `traffic.py:497` (trial 5 ГБ), `screens.py:596`, `admin/base.py:890`. Это не платёжный путь, в фазе 2 не трогаем.
5. **Текст для отложенной активации уже есть** (`payment.pending_activation`). Новые i18n-ключи не понадобятся.

## A. Архитектура

### Модули

**1. `app/services/tariffs.py` — каталог тарифов.** Только читает `config.TARIFFS`, `COMBO_TARIFFS`, `TRAFFIC_LIMITS`, `TRAFFIC_PACKS(_EXTENDED)`, `TRIAL_BYPASS_MB`. Новых цен и констант не добавляет.

```python
@dataclass(frozen=True)
class Entitlement:
    tariff_key: str          # basic|plus|combo_basic|combo_plus|biz_*|trial|pack|grant
    premium_days: int        # 0 = premium не трогаем
    premium_tier: str | None # что писать в subscriptions.subscription_type
    bypass_bytes: int        # ПРИБАВИТЬ один раз

def tariff_key(tariff: str, is_combo: bool) -> str
def for_purchase(key: str, period_days: int, *, is_new_issuance: bool) -> Entitlement
def for_pack(gb: int) -> Entitlement
def for_trial(days: int = 3) -> Entitlement
def for_grant(tariff: str, days: int) -> Entitlement
def for_bypass_gift(gb: int) -> Entitlement
```

Правила каталога:
- basic и plus: +10 ГБ (`TRAFFIC_LIMITS`).
- `combo_*`: `COMBO_TARIFFS[key][period]["gb"]`. Если периода нет в таблице, выбрасывается `TariffConfigError` без тихого отката на 10 ГБ.
- `biz_*`: как сейчас, то есть 10 ГБ только при первой выдаче (см. §G1).

**2. `database/provisioning_jobs.py` — outbox выдачи (asyncpg).**
- `insert_job(conn, *, key, telegram_id, source, ent, premium_until, context) -> int` — `ON CONFLICT (idempotency_key) DO NOTHING`, возвращает id существующей записи.
- `claim(job_id=None, *, lease_s=120)`
- `save_bypass_plan(job_id, base, target)` — срабатывает один раз: `WHERE bypass_target_bytes IS NULL`.
- `mark_done`, `mark_retry(err, next_at)`, `mark_dead(err)`, `get_by_key(key)`.

**3. `app/services/provisioning.py` — единственное место, где трогаются premium и bypass при выдаче.**
- `enqueue(conn, *, key, telegram_id, ent, premium_until, source, context=None) -> int`
- `run_now(job_id, *, timeout=8.0) -> bool` — быстрый путь после commit, никогда не бросает исключений.
- `apply(job)` — бросает `ProvisioningTransient` / `ProvisioningPermanent`.

Под капотом используются `remnawave_premium` (`create_premium_user_entity`, `renew_premium_user`), `remnawave_bypass.create_bypass_user_entity`, `remnawave_api.get_bypass_entity_safe` и `update_user(…, _trust_bypass=True)`. Собственного top-up кода вида read→+N→PATCH нет, вместо него CAS (§B).

**4. `app/workers/provisioning_worker.py`.**
- Запускается через `create_task` в `main()` рядом с `wata_reconciler`.
- Цикл раз в 15 с: `claim` → `apply`.
- Бэкофф `min(30s·2^n, 1h)`. Через 24 часа задача переходит в `dead`.
- Работа по одному пользователю строго последовательна: in-process `asyncio.Lock` на пользователя плюс DB-lease.
- Работает **всегда**, независимо от флага, чтобы уже поставленные задачи не зависали.

**5. `database/subscriptions.py`.**
- `grant_access(…, defer_panel: bool = False)`. При `True` новая выдача идёт в существующую ветку `pending_activation` без HTTP. Продление остаётся только в БД.
- Новая функция `complete_activation(telegram_id, uuid, vpn_key, vpn_key_plus)`.

### Потоки данных

«tx» — одна DB-транзакция, внутри неё **ноль HTTP-вызовов**.

1. **Внешний вебхук** (Platega, WATA, CryptoBot, WATA-reconciler).
   - Проверка подписи → `lookup_pending_purchase(provider, id)` (добавляется проверка провайдера) → `process_confirmed_payment`. Ветка магазина остаётся без изменений.
   - `finalize_purchase`, одна tx: `SELECT … FOR UPDATE` (блокирующий, внутри tx) → `paid` → `payments` → `grant_access(defer_panel=True)` → промо и реферал → `record_traffic_purchase` (combo) → `enqueue("purchase:{id}")`.
   - После commit: `run_now` → `_send_confirmation` (только уведомление, кода ГБ там больше нет).
2. **Telegram-native и Stars.** Та же `finalize_purchase` с тем же ключом. Блоки `payments_messages.py:1212-1292` удаляются: `renew_bg`, `add_bypass_traffic`, FSM-фоллбэк combo.
3. **Баланс.** `finalize_balance_purchase(…, is_combo=False)`. В одной tx: advisory lock → списание → `grant_access(defer)` → `payments` → `enqueue("balance:{payment_id}")`. Блок `payments_callbacks.py:861-909` удаляется.
4. **Автопродление.** В существующей tx `enqueue("autorenew:{payment_id}")`. Удаляются `auto_renewal.py:386-404` (sync + `renew_bg`).
5. **Рекуррент Platega.** `claim_charge` → одна tx: `grant_access(conn, defer)` + `confirm_charge(conn)` + `enqueue("platega_charge:{charge_id}")` → commit → `run_now`.
6. **Админ-выдача.** `admin_grant_access_atomic` и `…_minutes_atomic` в своей tx вызывают `enqueue("admin:{uuid}")`. Удаление `renew_bg` заодно чинит `NameError days_int`.
7. **Подарок.** `activate_gift_subscription` в tx вызывает `enqueue("gift:{code}")`. `start.py:331-339` удаляется.
8. **Промо, игра, бонус, bypass-gift, ключ из рассылки.** `grant_access(defer)` + `enqueue` с ключами `promo:{link}:{tg}`, `game:{tg}:{date}:{kind}`, `bonus:{run}:{tg}`, `bgift:{code}:{tg}`, `btk:{bid}:{tg}`. Вызовы `remnawave_service.add_bypass_traffic` уходят.
9. **Trial.** Появляется `trials.service.activate_trial(telegram_id) -> bool` (вынесен из `callback_activate_trial`): `grant_access(source="trial", defer)` + `mark_trial_used` + `enqueue("trial:{tg}")` на 500 МБ. Им заменяются 3 вызова несуществующей функции.

## B. Идемпотентность

1. **`idempotency_key UNIQUE`, задача пишется в той же tx, что и биллинг** (outbox). Биллинга без задачи и задачи без биллинга быть не может.
2. **Premium.** Целевое значение абсолютное: `target = max(job.premium_until, subscriptions.expires_at)`. Если `expireAt` в панели уже `>= target`, PATCH не делается, срок никогда не сокращается. Если сущности нет — create с адопцией по username, затем `complete_activation`.
3. **Bypass — CAS с сохранённым планом.**
   1. Если `bypass_target_bytes IS NULL`: GET → `L` → `save_bypass_plan(base=L, target=L+N)`, commit **до** PATCH.
   2. Повторный GET → `C`:
      - сущности нет → create с `limit=target`;
      - `C == target` → уже применено, `done`;
      - `C == base` → PATCH абсолютным значением `target`;
      - иначе → `dead('conflict')` + принудительный алерт.
   3. `mark_done`.

   Если процесс упал между PATCH и `mark_done`, при повторе `C == target`, и второго +N не будет.
4. **Порядок по пользователю.** `claim` берёт задачу только при отсутствии более ранней открытой задачи того же `telegram_id`. Lease + `FOR UPDATE SKIP LOCKED` внутри короткой tx.
5. **Алерты.**
   - Первая неудача — `force=True`, промежуточные — с кулдауном, `dead` — `force=True`.
   - В алерте: `job_id`, ключ, пользователь, ГБ/дни, ошибка и SQL для ручного повтора.
   - Всё дублируется в `payment_errors`.
6. **`activation_worker`** не трогает пользователя, у которого есть открытая задача. Фильтр включается вместе с флагом.
7. **Replay вебхука.** Если задача не `done`, вызывается `run_now`, иначе ничего. Фантомный premium исключён.

## C. Миграция `migrations/082_provisioning_jobs.sql` (additive)

⚠️ Номер `081` уже занят в `atcnew`, дубль `006` существует. Берём **082** (§G6).

```sql
CREATE TABLE IF NOT EXISTS provisioning_jobs (
    id                  BIGSERIAL PRIMARY KEY,
    idempotency_key     TEXT        NOT NULL UNIQUE,
    telegram_id         BIGINT      NOT NULL,
    source              TEXT        NOT NULL,
    tariff_key          TEXT        NOT NULL,
    premium_until       TIMESTAMP   NULL,
    bypass_add_bytes    BIGINT      NOT NULL DEFAULT 0 CHECK (bypass_add_bytes >= 0),
    bypass_base_bytes   BIGINT      NULL,
    bypass_target_bytes BIGINT      NULL,
    status              TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','done','dead','shadow')),
    attempts            INTEGER     NOT NULL DEFAULT 0,
    next_attempt_at     TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    lease_until         TIMESTAMP   NULL,
    last_error          TEXT        NULL,
    context             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at          TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    done_at             TIMESTAMP   NULL
);
CREATE INDEX IF NOT EXISTS idx_prov_jobs_due  ON provisioning_jobs (next_attempt_at) WHERE status IN ('pending','running');
CREATE INDEX IF NOT EXISTS idx_prov_jobs_user ON provisioning_jobs (telegram_id, id);
ALTER TABLE platega_subscriptions ADD COLUMN IF NOT EXISTS is_combo BOOLEAN NOT NULL DEFAULT FALSE;
```

## D. Коды ответа вебхуков

**5xx** — только если биллинг не закоммичен и повтор может помочь:
- БД недоступна или tx упала до commit;
- WATA: нечем проверить подпись;
- таймаут до commit;
- `claim_charge` вернул `in_progress`.

**200** — во всех остальных случаях:
- `ok` — в том числе когда выдача упала **после** commit: это уже наша очередь плюс алерт;
- `already_processed`;
- `amount_mismatch` (+ алерт);
- `provider_mismatch` (+ алерт);
- `ignored` / `invalid`;
- `not_found` (+ принудительный алерт и `payment_errors`).

Изменения в коде:
- `except TransientPaymentError: raise` перед общим `except` в `process_confirmed_payment`.
- В `payment_webhook.py` таблица `_STATUS_HTTP` вместо `JSONResponse(result)`, который сейчас всегда отдаёт 200.

**Сразу, без флага (P0):**
- `FOR UPDATE` переносится внутрь `conn.transaction()` (становится блокирующим). Второй дубль получает `already_processed`, ложный алерт PERMANENT исчезает.
- `lookup_pending_purchase`: `payment_provider` задан и не совпадает → отказ + алерт. `NULL` → принять и записать в лог.
- Пополнение баланса зачисляет **ожидаемую** сумму (`price_kopecks/100`). Переплата уходит в лог и `context`.

## E. Выкатка

- `USE_NEW_PROVISIONING = off | shadow | on`, по умолчанию `off`.
- `NEW_PROVISIONING_ENTRYPOINTS = webhook,telegram,balance,autorenew,platega_recurring,admin,gift,grants,trial`.
- **shadow**: работает старый путь, а в той же tx пишется задача `status='shadow'` плюс лог `PROVISION_SHADOW_DIFF legacy_gb=… entitlement_gb=…`. Воркер такие задачи не исполняет.
- **Порядок включения:** webhook+telegram → balance → platega_recurring → autorenew → admin/gift → grants/trial.
- **Откат:** флаг в `off`. Воркер доделает уже открытые задачи.

## F. Задачи (TDD)

Общий фейк панели `tests/fakes/panel.py`:
- premium и bypass хранятся в памяти;
- режимы `down`, `crash_after_patch`, `conflict`.

Интеграционные тесты гоняются на `postgres:16` в CI.

| # | Что | Файлы | Ключевые тесты | Параллельность |
|---|---|---|---|---|
| T0 | Характеризующие тесты | `tests/services/test_payment_core_characterization.py` | ветка магазина; i18n-ключи; `payments.tariff`; реферал и промо; `pending_activation`. Баги как `xfail(strict)`: баланс basic 20→10, combo Telegram 150→75, продление combo с баланса 85→75, рекуррент Platega 0→10 | первой |
| T1 | Каталог тарифов | `app/services/tariffs.py` | basic 30d +10; plus 365d +10; combo_basic 30d +75; combo_plus 90d +200; combo 730d +1500; пакет 15 +15 (premium 0); пакет 5000 +5000; trial 500 МБ / 3 дня; biz new/renew 10/0; неизвестный период combo → `TariffConfigError` | ∥ T2, T3 |
| T2 | Outbox | `migrations/082_*.sql`, `database/provisioning_jobs.py` | дубль ключа → тот же id; `save_bypass_plan` один раз; FIFO по пользователю и lease | ∥ |
| T3 | Фейк панели | `tests/fakes/panel.py` | — | ∥ |
| T4 | `apply` / `run_now` / `enqueue` | `app/services/provisioning.py` | bypass 3 ГБ + basic → 13; combo без bypass → создан с 75; повторное продление → ещё +10 / +75; дубль → без изменений; `crash_after_patch` → ровно +10; `conflict` → `dead` + алерт; premium не сокращается | после T1–T3 |
| T5 | Воркер | `app/workers/provisioning_worker.py`, `main.py` | панель лежит → бэкофф, алерт один раз, после восстановления `done` без потерь; через 24 ч → `dead` + алерт | после T4 |
| T6 | P0 вебхуков (без флага) | `confirmation.py`, `payment_webhook.py`, `finalize_purchase` (блокировка и сумма пополнения) | 2 одновременных finalize → один `paid`, у второго `already_processed`, без PERMANENT; `TransientPaymentError` → 500; `not_found` → 200 + алерт; чужой провайдер → отказ; пополнение 199 ₽ при вебхуке 203.06 → 199 | ∥ T1–T5 |
| T7 | Отложенная выдача | `grant_access(defer_panel)`, `complete_activation`, фильтр в `activation/service.py` | новая выдача → `pending` и 0 HTTP; продление → только БД | после T4 |
| T8 | `finalize_purchase` под флагом | `subscriptions.py`, `confirmation.py` | вебхук basic 30d → +10; combo_basic 30d → +75; повтор → без изменений; панель лежит → 200, задача `pending`, алерт, воркер доводит | после T7 |
| T9 | Telegram-native | `payments_messages.py` | basic +10, combo +75 | ∥ T11, T12, T16 |
| T10 | Баланс | `database/admin.py`, `payments_callbacks.py:861-909` | basic +10, combo +75, продление combo +75 | последовательно: T10 → T13 → T14 |
| T11 | Рекуррент Platega | `platega_service.py`, `database/platega_subscriptions.py` | CONFIRMED → +30 дней и +10 ГБ; дубль `charge_id` → ничего; краш после tx → reclaim не продлевает второй раз | ∥ |
| T12 | Автопродление | `auto_renewal.py` | продление basic → +10 | ∥ |
| T13 | Админ-выдача | `admin/access.py`, `routes/users.py` | по ответу на §G4 | после T10 |
| T14 | Подарок | `database/admin.py`, `start.py:331-339` | подарок basic 90d → +10 | после T13 |
| T15 | Trial | `trials/service.py`, `callbacks/subscription.py`, 3 вызова | 500 МБ / 3 дня | после T9 и T10 |
| T16 | Промо, игра, бонус, ключ из рассылки | `game.py`, `start.py:1020-1125`, `bonus.py`, `broadcast_trial_key.py` | по §G4 | ∥ |
| T17 | Shadow-режим + лог расхождений | `provisioning.py` + точки из T8–T16 | — | после T8–T16 |
| T18 | Удаление старого (через 1–2 недели в `on`, отдельным PR) | `remnawave_service.renew_*`, `add_traffic`, `add_bypass_traffic` ×2, `_deliver_bypass_gb`, ГБ-часть `_handle_traffic_pack_confirmation`, `sync_renewal_to_remnawave` и bypass-часть `provision_subscription` | — | последней |

## T4 — обязательные нюансы реального кода Remnawave (найдены при T1–T3)

1. **Адопция premium может сократить срок.** `create_premium_user_entity` при подхвате существующей сущности ставит `expireAt` ровно в запрошенное значение, даже если оно меньше текущего. Проверку `max(current_expireAt, target)` T4 делает сама, до вызова.
2. **Адопция bypass не меняет лимит.** `create_bypass_user_entity` при подхвате не трогает `trafficLimitBytes`. После create или адопции CAS-шаг выставляет `target` сам.
3. **`None` от `get_bypass_entity_safe` неоднозначен.** Он означает и «сущности нет», и «панель лежит». Для CAS нужен читатель, который различает `ABSENT` и `UNAVAILABLE`, например через `_request_raw` со статусом 404 против 5xx/timeout. Иначе при недоступной панели выполнится create вместо retry.
4. **Вызывать через модуль:** `remnawave_api.update_user(...)`, без `from … import`. Фейк панели подменяет атрибуты модуля.
5. **Цены автопродления.** `tariffs.renewal_price_rub` берёт цены из config. Резолвер переопределений цен `app/services/pricing.get_effective_price` асинхронный, работает только для basic/plus. Для combo механизма переопределения нет. Решение принимаем в T12.

## Итоги T4 (`app/services/provisioning.py`, `dad10749`) — принятые отступления и остаточные риски

- Premium продлевается через `remnawave_api.update_user` с абсолютным `expireAt`, а не через `renew_premium_user`: у того свои 3 попытки, получился бы retry внутри retry. Цель округляется до секунды вверх.
- `enqueue` вне транзакции бросает `RuntimeError` — так outbox-правило соблюдается принудительно.
- Любой неоднозначный 4xx/5xx/401/429/timeout классифицируется как `unavailable` и ведёт к retry. Create при таком ответе не выполняется никогда.
- **Остаточный риск 1.** Если читатель premium не увидел существующую сущность, а её `expireAt` дальше и задачи, и `subscriptions.expires_at`, то create подхватит её и сократит срок. Постфактум это не обнаружить. Вероятность низкая: нужен промах поиска по кэшированному id и username одновременно.
- **Остаточный риск 2.** Legacy-писатели лимита bypass (экраны «бесплатные 10 ГБ», непереведённые `add_bypass_traffic`) между двумя чтениями CAS дают `conflict` → `dead` + алерт. Это ожидаемо и исчезает по мере T9–T16 и T18.
- Кэш premium-URL переписывается только при смене uuid/id. Если URL сменился при том же uuid, в кэше останется старый. Решить в T8, когда `vpn_key` станет писать `complete_activation`.

## Контроль доставки

Требование владельца: «если пользователь купил, а ему что-то не назначилось — например подписка была до 20 октября, он покупает месяц, а в панели всё так же до 20 октября — это должно ловиться». Три уровня, все шлют алерт админу; ничего не чинят молча.

**1. Outbox (флаг `on`) — проверка после `apply`** (`app/services/provisioning.py::_verify_delivery`).
- После успешных шагов premium/bypass задача перечитывает панель (`get_premium_state` / `get_bypass_state`):
  - premium: `expireAt >= ceil(premium_until)` (до секунды вверх) и `status == ACTIVE`;
  - bypass: `trafficLimitBytes == bypass_target_bytes` (ровно план CAS) и `status == ACTIVE`.
- Не сошлось → `DeliveryMismatch` (подкласс Transient): задача **не** `done`, уходит в retry с бэкоффом. Повтор безопасен: premium не сокращается, bypass идёт по CAS (`C == base` → PATCH, `C == target` → уже применено).
- **Первый** mismatch (предыдущая ошибка задачи была не mismatch) → forced-алерт `DELIVERY_MISMATCH` через общий бюджет/дайджест (новый вид `mismatch`: 5 per-job за 5 минут, остальное — одной сводкой). Следующие — по кулдауну категории.
- После `MISMATCH_DEAD_AFTER_ATTEMPTS = 6` попыток (≈30 минут) → `dead` + forced-алерт «DEAD (delivery mismatch persisted …)».
- Панель недоступна при перечитывании → обычный Transient (алерт «первая неудача»), без mismatch.
- Тесты: `tests/services/test_provisioning_verify.py`, фейк панели — режим `ignore_patch` (PATCH отвечает 200, ничего не меняет).

Пример (к тексту `send_alert` добавляет заголовок `PAYMENT ALERT`):
```
DELIVERY_MISMATCH: paid, but after a successful PATCH the panel does not show it — job RETRY at 14:05:00 UTC
job_id: 4812
key: purchase:9f3c2a
user: tg:123456789
tariff: basic_30
premium: +30d until 2026-11-20T10:00:00+00:00
bypass: +10 GB (plan 3 → 13 GB)
attempts: 1
error: DeliveryMismatch: DELIVERY_MISMATCH: premium: expected expireAt >= 2026-11-20T10:00:00Z ACTIVE, panel expireAt=2026-10-20T10:00:00Z status=ACTIVE

Manual retry:
UPDATE provisioning_jobs SET status='pending', next_attempt_at=now() AT TIME ZONE 'UTC' WHERE id=4812;
```

**2. Legacy (флаг `off`) — отложенная проверка** (`app/services/payments/verify_delivery.py::schedule_legacy_check` → `check_legacy_delivery`).
- Хук в 1–3 строки после пост-commit работы с панелью:

  | Путь | Где | bypass проверяется |
  |---|---|---|
  | баланс | `database/admin.py::finalize_balance_purchase` (legacy-ветка) | да (кроме biz) |
  | Telegram | `payments_messages.py`, ветка без outbox | да (кроме trial/biz) |
  | автопродление | `auto_renewal.py`, фаза B при `provisioning_job_id is None` | basic/plus |
  | выдача админом (дни, минуты) | `database/admin.py::admin_grant_access_atomic` / `_minutes_atomic` (бот и дашборд) | нет (выдача днями — только premium) |
  | подарок | `database/admin.py::activate_gift_subscription` (legacy-ветка) | да (кроме biz) |
  | внешний вебхук, первичная покупка | `confirmation.py`, ветка без outbox и не продление | да (кроме biz) |
  | внешний вебхук, продление / пакет ГБ | свой `verify_premium/bypass_delivery` в `confirmation.py` | — |

- Через 20 с читается **закоммиченная** строка `subscriptions` и панель: premium `expireAt` раньше `expires_at` больше чем на 5 минут, premium нет или не `ACTIVE`, bypass нет или не `ACTIVE` → forced-алерт `DELIVERY_MISMATCH` (5 forced за 5 минут, остальное — по кулдауну категории `payment`). Панель позже БД — не ошибка.
- Повторов нет, алерт и есть задача: в нём готовый SQL, который ставит outbox-задачу с `premium_until = expires_at`. Воркер доводит панель до `max(premium_until, expires_at)`, срок не сокращает, отсутствующий premium создаёт. Нужна миграция 082.
- Панель недоступна → проверка пропускается (лог `DELIVERY_CHECK_SKIPPED`).
- Количество ГБ на legacy-путях не сверяется: базы «до» нет. Проверяются наличие и `ACTIVE`, ГБ — аудит трафика в дашборде.
- Тесты: `tests/services/test_verify_delivery_legacy.py`, `tests/services/test_delivery_check_hooks.py`.

Пример:
```
DELIVERY_MISMATCH (legacy path): paid/granted, but the panel does not show it
source: balance · ref: balance_purchase_5521
user: tg:123456789
- premium: DB expires_at 2026-11-20 10:00:00 UTC, panel expireAt 2026-10-20 10:00:00 UTC (short by 31d 0h), status ACTIVE

This path has no automatic retry — this alert is the action item.
Fix premium (provisioning worker; never shortens, creates if absent):
INSERT INTO provisioning_jobs (idempotency_key, telegram_id, source, tariff_key, premium_until) SELECT 'fix:202609131400:' || telegram_id, telegram_id, 'manual_fix', 'manual', expires_at FROM subscriptions WHERE telegram_id = ANY(ARRAY[123456789]) AND expires_at > (NOW() AT TIME ZONE 'UTC') ON CONFLICT (idempotency_key) DO NOTHING;
```

**Часовой сверки «БД ↔ панель» нет** (решение владельца 2026-09-13: «платежи должны быть корректными, тогда сверка не нужна»). Каждая выдача проверяется сразу: outbox — внутри задачи, legacy-пути (включая первичную покупку через вебхук) — отложенной проверкой. Лишний полный проход по панели раз в час при большой базе только нагружал бы лимиты API.

**Заодно исправлен `panel_traffic_audit`** (recon §3 P0-F). `used` теперь читается из `userTraffic.usedTrafficBytes`, как в `remnawave_api.get_user_traffic` (контракт 3.4.3); верхний уровень остался fallback. `apply_fix` перед PATCH заново читает панель и ставит `limit = max(current, expected)` (P1-8, ревью 04: лимит bypass пожизненный, `NO_RESET`, покупки прибавляются к лимиту, поэтому `expected` — сумма всех выданных ГБ — и есть целевой лимит; прежняя формула `expected + used` начисляла израсходованное второй раз). Лимит не понижается: если панель уже `>= expected`, фикс ничего не делает (`no_shortfall_now`). Нет свежего чтения — PATCH не делается. Остаток: `app/api/dashboard/routes/traffic_audit.py:167` (диагностическое поле `panel_used_bytes`) по-прежнему читает верхний уровень — в эту задачу не входил.

## Статус выкатки и оговорки (обновлено после T15 и слияния дашборда)

- **Все точки входа переведены на outbox за флагом:**
  - T8 — webhook;
  - T9 — telegram;
  - T10 — баланс;
  - T12 — автопродление;
  - T13 — выдача админом;
  - T14 — подарок;
  - T15 — триал;
  - T16 — выдачи без покупки.
- **T11 (рекуррент Platega) снят:** код рекуррента удалён по решению владельца (`9549e706`). Осталась страховка — алерт на каждый колбэк.
- **Ядро и защита:** T4 (сервис выдачи), T5 (воркер), T7 (отложенная выдача), агрегация алертов (`7b11ca83`), T6 (фиксы вебхуков).
- **`webhook,telegram` можно включать вместе** — T9 проверил это тестом.
- **Дашборд v3 слит в эту ветку** (`93a838e7`). Проверено: pytest, `tsc` и сборка фронта.
- **Дальше:**
  - T17 — shadow-режим и лог расхождений;
  - runbook выкатки: миграция 082, затем включение по точкам входа в порядке webhook → telegram → balance → autorenew → admin/gift → grants/trial;
  - T18 — удаление старых путей через 1–2 недели в режиме `on`.

### Ранее (после T8, T10, T11, T12)

- **Влито за флагом:** T8 (webhook + telegram через `finalize_purchase`), T10 (баланс), T11 (рекуррент Platega), T12 (автопродление).
- **⚠️ Не включать `telegram` до T9.** Устарело: T9 влит, двойной выдачи больше нет.
- **Двойное списание при двойном тапе оплаты с баланса** исправлено без флага (`c107acb0`).
- **T12, автопродление:**
  - combo — по цене combo (329 ₽ за 30 дней) + ГБ combo;
  - basic/plus — цена та же, что сейчас;
  - **легаси biz — Plus по ГБ, цена остаётся 199 ₽.** Решение владельца: поднимать ли до цены Plus.
- **Старые баги автопродления с выключенным флагом:**
  - plus-подписка каждый раз превращается в basic: `grant_access` вызывается без `tariff`;
  - в уведомлении стоит «0.00 ₽».

  Оба исправляются включением `autorenew`.
- **T11 — снято: рекуррент Platega удалён** (решение владельца «пока код рекуррентных платежей убрать»). Удалены создание подписки, `pay:sbp_sub`, обработка списаний/статусов (claim/confirm/grant/outbox), `check_subscription_status`, entrypoint `platega_recurring`, тесты T11 и T0-xfail «рекуррент 0→10». Вопрос G3 снят вместе с фичей. Миграции 074/082 (`is_combo`) не трогаем, DROP — отдельным релизом.
  - **Страховка:** callback по подписке на общем URL или `/webhooks/platega-subscription` → проверка `X-MerchantId/X-Secret` как раньше → лог + `payment_errors` + forced алерт «Рекуррентные подписки отключены. Отмените подписку в кабинете Platega (POST /subscription/{id}/cancel) и при необходимости сделайте возврат/выдачу вручную.» → 200. Ничего не выдаётся.
  - Живые подписки: `SELECT subscription_id, telegram_id, status, next_charge_at FROM platega_subscriptions WHERE status IN ('Active','PendingAgreement','PastDue');`
- **T16 (выдачи без покупки):** общий хелпер `app/services/grant_outbox.py`.
  - Ключи идемпотентности: `game:{tg}:{kind}:{ts}`, `promo:{link}:{tg}:{redemption_id}`, `bonus:{chat}-{msg}:{tg}`, `bgift:{code}:{tg}`, `btk:{bid}:{tg}`.
  - Выдача днями: 0 ГБ. ГБ-награды: ровно +N. Ключ из рассылки: +1 день и +1 ГБ (это не trial).
  - **TODO (P1): агрегировать алерты очереди.** Массовый бонус при лежащей панели сейчас шлёт форс-алерт на каждую задачу, а должен — одну сводку в N минут.
- **T13 (выдача из админки):**
  - Выдача днями и минутами даёт только premium, 0 ГБ. Для минут `premium_until` точный: `tariffs.for_grant_duration`.
  - Ключ `admin:{uuid4}` генерируется один раз на действие: у записей grant нет пригодного id.
  - `NameError days_int` в ветке минут и часов исправлен без флага, `days_int=1`. Раньше он молча пропускал legacy renew.
  - Двойной клик «Подтвердить» даёт одну выдачу, но только под флагом.
  - **TODO (P1): эндпоинты выдачи в дашборде не защищены от двойной отправки** — два POST дают две выдачи. Нужен idempotency-token от формы или дедуп в течение N секунд. Сделать в ветке `dashboard/v3` либо при слиянии.
- **T9 (Telegram-native):** при флаге `telegram` пост-commit блок ГБ в `payments_messages.py` пропускается, если есть `provisioning_job_id`. Повтор уже обработанной покупки ищет задачу по ключу, а не начисляет ГБ заново. **`webhook,telegram` можно включать вместе** — проверено тестом.
- **❓ Магазин, баг (нужно решение владельца):** Spotify, оплаченный картой через Telegram, обрабатывается в `process_successful_payment` как VPN-подписка с тарифом вида `spotify_individual_1`: отдельной ветки для Spotify нет. Покупатель не получает сообщение об успехе Spotify. Флагами не затронуто. Чинить только с разрешения, потому что это магазин.
- **T8:** replay вебхука по покупке, финализированной до включения флага, по-прежнему идёт старым resync. Промо и реферал на outbox-пути срабатывают и для pending-выдачи.

## Находки T0 (характеризующие тесты, `tests/services/test_payment_core_characterization.py`)

Замеры совпали с аудитом:

| Сценарий | Сейчас | Цель |
|---|---|---|
| баланс, basic, новая покупка | 20 | 10 |
| баланс, combo, новая покупка | 95 | 75 |
| баланс, combo, продление | 85 | 75 |
| Telegram, basic, новая покупка | 20 | 10 |
| Telegram, combo, новая покупка | 150 | 75 |
| админ-выдача 7 дней | 20 / 10 | 0 |
| подарок | 20 | 10 |
| рекуррент Platega | 0 | 10 |
| автопродление combo | 10 ГБ за 199 ₽ | 75 ГБ за 329 ₽ |

Все эти сценарии помечены `xfail(strict)` и должны начать проходить после T8–T16. Новые находки:

1. **Баланс при недоступной панели (T10).** `finalize_balance_purchase` падает на «vpn_key is missing» (`database/admin.py:2808`), транзакция откатывается. Пользователь видит `errors.payment_processing` и деньги не теряет, но ветка `payment.pending_activation` (`payments_callbacks.py:645`) недостижима. С outbox покупка должна проходить, а выдача — встать в очередь.
2. **Подарок активному пользователю (T14).** `activate_gift_subscription`, по чтению кода, не синхронизирует продление с панелью, поэтому premium, вероятно, не продлевается. Тестом не закреплено: закрыть в T14.
3. **Фейк `grant_access` в `tests/services/payment_core_harness.py`** воспроизводит его текущие вызовы панели. При T7 (`defer_panel`) харнес нужно обновить синхронно.

## G0. Решения владельца (2026-09-13)

- План одобрен («ок, начинай»).
- **Выдача днями без покупки** (админ, игра, промо, бонус) даёт **0 ГБ, только premium**. Если нужны ГБ, это отдельная ГБ-награда.
- **Автопродление** идёт по фактическому тарифу подписки:
  - basic: цена basic и +10 ГБ;
  - combo_basic / combo_plus: **цена combo из `COMBO_TARIFFS`** и **ГБ combo** (например, +75 за 30 дней).

  Это меняет сумму списания у combo-пользователей: сейчас у них списывается цена basic, так что это осознанное изменение.
- **Бизнес-тарифы больше не продаются.** Их нет в каталоге для новых покупок. Легаси-подписки `biz_*` при автопродлении и продлении считаются как Plus (+10 ГБ). Это допущение, владелец может его поправить.
- Миграция: **082** (в `atcnew` занят только 081).

## G. Вопросы владельцу (исходные)

1. **`biz_*`:** 10 ГБ только при первой выдаче, при продлении 0 (как сейчас)? Рекомендация: оставить так.
2. **Автопродление combo с баланса** списывает цену basic (`auto_renewal.py:215`). После фикса такой пользователь получит +10 ГБ. Рекомендация: +10 по оплаченной цене, цены не трогаем.
3. **Старые рекуррентные подписки Platega на combo:** как их отличить от обычных? Для новых подписок пишем `is_combo`.
4. **Выдача днями** (админ, игра, промо, бонус): только premium (0 ГБ) или +10 ГБ? Сейчас фактически 10–20 ГБ. Рекомендация: 0 ГБ, а ГБ выдавать отдельной наградой.
5. **Пользователи, уже получившие лишние ГБ,** — оставить как есть. Рекомендация.
6. **Номер миграции 082** не должен быть занят в `atcnew`.

## H. Что не трогаем

- **Магазин.** Ветка `confirmation.py:141-181` (`mark_pending_purchase_paid` + `send_*_success`) остаётся на прежнем пути, без новых проверок. Файлы магазина и ключи `shop.*` не меняются.
- **i18n-тексты.** Все тексты и ключи переиспользуются как есть.
- **Цены и тарифы.** Цены, конфиги тарифов, pricing (миграция 069) и скидки не трогаем.
- **Провайдеры (волна 1).** Подписи провайдеров, WATA-reconciler, chargeback/refund (только алерт), отмена рекуррента через поддержку — без изменений.
- **Схемы БД.** `pending_purchases`, `subscriptions`, `payments` не меняются, кроме additive-миграции 082.
- **Параллельная чистка.** Модули, которые она удаляет, ядро не использует.
