# Фаза 1. Мёртвый код — доказательная база

- Дата: 2026-09-13
- База: `refactor/audit-2026-09` @ `727fbca8` = `origin/main` `cf8205fc` + только docs-коммиты (прод-код идентичен `cf8205fc`)
- Режим: только чтение. Код не менялся. Единственный изменённый файл — этот.
- Обязательные рамки: `docs/audit/SCOPE.md` (магазин не трогаем; Lava удаляем полностью).

> Каждый пункт ниже — **кандидат** в удаление. Удаляем только после «ок» владельца, пакетами (1 пакет = 1 коммит), в порядке от безопасного к рискованному.

---

## 0. Как проверяли

### 0.1 Команды и сырой итог

| Команда | Итог |
|---|---|
| `vulture . --min-confidence 60 --exclude .venv,node_modules,dashboard,graphify-out` (vulture 2.16, поставлен в `.venv` через `uv pip`) | **729** строк: 631 unused function, 71 unused variable, 21 unused attribute, 4 unused import, 1 unused method, 1 unused property. По уверенности: 711 × 60 %, 4 × 90 %, 14 × 100 % |
| `ruff check --select F401,F811,F841 .` | **287** ошибок: **F401 = 205**, **F841 = 81**, **F811 = 1** (`database/subscriptions.py:1106` — повторный `import asyncio`). 220 автофиксятся |
| AST-граф импортов (свой скрипт, учитывает ленивые импорты внутри функций и `importlib.import_module("…")`) | 245 модулей, **195 достижимы из `main.py`**. Из 213 не-тестовых модулей недостижимы 18 (разбор ниже) |
| Скан символов (свой скрипт) | Для каждой top-level `def`/`class` в 209 не-тестовых модулях: число упоминаний по границе слова во всех `*.py` (отдельно `tests/`, отдельно реэкспорт в `database/__init__.py`), а также в `*.sql`, `*.yml`, `Dockerfile`, `.dockerignore`, `*.json/*.toml/*.ini/*.sh/*.service`, `dashboard/src/**` |
| Скан сирот-callback'ов (свой скрипт) | Каждый `@…callback_query(F.data == / .startswith / .in_(…))` сверен со всеми остальными строками репо (кроме строк с `F.data`). Найдено **22** фильтра, для которых нет ни одного производителя |
| Скан роутов дашборда | 140 роутов из `app/api/dashboard/**` сверены с `dashboard/src/**` |
| `pytest -p no:cacheprovider` по legacy-тестам (6 файлов) | **8 failed / 116 passed** (эти 8 входят в 46 известных падений из `00_recon.md`) |

**Как читать vulture.** Из 631 «unused function» 487 — хэндлеры aiogram и роуты FastAPI, которые регистрируются декоратором. Это ложные срабатывания. Ещё 7 — в `tests/`, 2 — вложенные функции. Остаются 135 недекорированных top-level функций, и у 58 из них grep тоже находит ноль ссылок. Поэтому vulture здесь только перекрёстная проверка. Доказательство — grep и скан символов.

**Консервативность.** Любое упоминание имени, даже в комментарии или строке, считается ссылкой. Поэтому скан может пропустить мёртвый код, но не объявит мёртвым живой.

**Ловушка.** В репо лежит неотслеживаемая копия `.claude/worktrees/agent-a702adcc68355bb41/` (git status: `?? .claude/`). Простой `grep -rn` находит в ней дубли всех ссылок. Во всех проверках она исключена.

### 0.2 Критерий «мёртвое» (должны выполняться все пункты)

1. vulture или ruff помечают элемент, **или** grep по всему репо даёт ноль использований (ленивые импорты, `import_module`, строки, `getattr`, SQL, CI, Dockerfile, `.dockerignore`, scripts, tests, dashboard/src).
2. Элемент не точка входа: не `create_task` в `main.py`, не роутер в `app/handlers/__init__.py` / `admin/__init__.py`, не FastAPI-роутер в `app/api/__init__.py`. Для хэндлера нет кнопки, которая производит его `callback_data`.
3. Нет регистрации через рефлексию. Проверено: `getattr(database, …)` встречается только в `app/handlers/user/devices.py:39`, где имена берутся из словаря `_TIERS` строками и поэтому попадают в grep. Отдельно проверены `app/events.py` и `automated_notifications/registry.py`.
4. Колонки и таблицы БД — только список кандидатов, без DROP.

### 0.3 Модули, недостижимые из `main.py` (AST), и вердикт

| Модуль | Импортёры | Вердикт |
|---|---|---|
| `app/core/i18n/__init__.py`, `types.py` | нет | **мёртв** → B2 |
| `app/handlers/admin/reconcile.py` | нет | **мёртв** → B2 |
| `app/handlers/callbacks/admin_callbacks.py` | нет | **мёртв** → B2 |
| `app/handlers/common/decorators.py` | нет | **мёртв** → B2 |
| `app/utils/audit.py` | нет | **мёртв** → B2 |
| `app/utils/message_guard.py` | нет | **мёртв** → B2 |
| `validate_language_content.py` | нет (только `pyproject.toml:34`) | **мёртв** → B2 |
| `app/services/broadcast_deleter.py` | ленивый импорт `app/api/dashboard/routes/broadcasts.py:581,622` | **жив** (AST-граф не видит `from app.services import X` внутри функции роутера, который монтируется условно) |
| `app/services/remnawave_backfill.py` | `app/api/dashboard/routes/remnawave.py:10` | **жив** (дашборд) |
| `app/utils/telegram_send.py` | ленивый импорт `routes/broadcasts.py:669` | **жив** (дашборд) |
| `app/services/panel_traffic_audit.py` | `routes/traffic_audit.py:20`, `scripts/audit_bypass_traffic_mismatch.py:20` | **жив** (дашборд) |
| `scripts/migrate_samopis_to_remnawave.py`, `scripts/verify_samopis_migration.py` | subprocess из `admin/migration.py:52-53`, тест | → B5 |
| `scripts/audit_bypass_traffic_mismatch.py`, `prep_remnawave_v3_migration.py`, `recover_stuck_trials.py` | нет (в образ не попадают, `scripts/` в `.dockerignore`) | → «Решение владельца» |

---

## B1. Документация → `docs/archive/` (риск: нулевой)

> **Статус (2026-09-13): выполнено.** Файлы из таблицы перенесены через `git mv`: корень → `docs/archive/root/`
> (включая `load_tests/results/` → `docs/archive/root/load_tests/results/`), `docs/X` → `docs/archive/X`.
> Пути в таблице ниже — исходные, до переноса. `scripts/*.md` остались на месте (пакет трогал только
> `docs/`, корневые `*.md` и `load_tests/`).

Код документацию не читает. Все `*.md`, кроме `README.md`, исключены из образа строкой `.dockerignore: *.md / !README.md`. Нужно поправить только ссылки в `CLAUDE.md` и `docs/AGENT_AUDIT_MAP.md`.

**Предложение по умолчанию: переносить в `docs/archive/`, а не удалять.** `CLAUDE.md` прямо называет аудит-файлы источником инвариантов, которые нельзя регрессировать. Удаление — отдельным решением.

| Файлы | Строк | Кто ссылается | Предложение |
|---|---|---|---|
| Корень: `AUDIT_REPORT.md`, `COMPREHENSIVE_CODE_AUDIT_2026_03.md`, `FULL_PRODUCTION_AUDIT.md`, `PRE_PRODUCTION_STABILITY_AUDIT.md`, `SECURITY_CODE_AUDIT_2026_03.md`, `WITHDRAWAL_BALANCE_AUDIT.md` | 3587 | `CLAUDE.md:113-114` (COMPREHENSIVE, FULL_PRODUCTION), `docs/AGENT_AUDIT_MAP.md` | archive; обновить пути в CLAUDE.md и AGENT_AUDIT_MAP |
| Корень: `I18N_KEY_MAPPING.md`, `I18N_STRING_AUDIT.md`, `LANGUAGE_REFACTOR_PLAN.md` | 1036 | `CLAUDE.md:47` (LANGUAGE_REFACTOR_PLAN §1.2), AGENT_AUDIT_MAP | archive (языки de/ar/kk/tj/uz уже удалены) |
| Корень: `BALANCE_REFERRAL_DOCS.md`, `VPN_API_LINK_NAMES_SERVER.md` | 319 | нет | archive (VPN_API_* — samopis) |
| `docs/audit/` старые (февраль 2026): `README.md`, `FINAL_RISK_SCORE.md`, `INFRA_DEPLOYMENT_RISK.md`, `PAYMENT_CALLBACKS_AUDIT.md`, `PAYMENT_VALIDATION.md`, `PRODUCTION_INCIDENT_AUDIT_REPORT.md`, `PRODUCTION_STABILITY_AUDIT.md`, `SAFE_DEPLOY_CHECKLIST.md`, `STABILITY_FIXES_IMPLEMENTED.md`, `SUBSCRIPTION_FLOW_DIAGRAM.md`, `SYSTEM_CRASH_AUDIT.md`, `XRAY_INTEGRATION_ANALYSIS.md` | 1724 | только друг на друга и AGENT_AUDIT_MAP | archive. В `docs/audit/` остаются `SCOPE.md`, `00_recon.md`, `01_dead_code.md` |
| `docs/` «enterprise-шаблон» (CLAUDE.md:86-89 называет их расходящимися с реальностью): `backward_compatibility`, `change_management`, `compliance_readiness`, `data_governance`, `data_locality`, `deployment_safety`, `endgame_readiness`, `failure_as_first_class`, `platform_vs_product`, `scaling_axes`, `incidents/*`, `postmortem/*`, `rfc/*`, `security/*`, `runbooks/region_failover.md` | 4938 | нет (кроме взаимных ссылок) | archive |
| `docs/` legacy samopis/Xray/cleanup: `BOT_XRAY_INTEGRATION`, `CLEANUP_WEBHOOK_ONLY`, `FINAL_CLEANUP_SUMMARY`, `HANDLERS_REFACTOR_PLAN`, `MIGRATION_STRATEGY_VPN_ENTITLEMENT`, `REFACTOR_PLAN_ARCHITECTURAL_SIMPLIFICATION`, `WEBHOOK_ONLY_MIGRATION`, `PLATEGA_API.md` (заменён `docs/providers/platega_api.md`), `runbooks/secure_vpn_api_binding.md`, `scripts/README_MIGRATION.md`, `scripts/VPN_AUDIT_README.md`, `scripts/XRAY_STABILIZE_README.md` | 1967 | `app/handlers/CLAUDE.md` → HANDLERS_REFACTOR_PLAN | archive. Внимание: `scripts/VPN_AUDIT_README.md` и `XRAY_STABILIZE_README.md` описывают `scripts/vpn_server_audit.sh` и `scripts/xray_config_stabilize.sh`, которых **нет в репо** |
| `load_tests/results/*.md` (9 отчётов от 12.02.2026, других файлов в `load_tests/` нет) | 689 | `.dockerignore:31` | удалить (сырые отчёты) или archive |

**Оставляем на месте:** `README.md`, `CLAUDE.md` и вложенные `CLAUDE.md`, `docs/providers/*`, `docs/audit/{SCOPE,00_recon,01_dead_code}.md`, `docs/data_ownership.md`, `docs/admin_dashboard_implementation_map.md`, `docs/AGENT_AUDIT_MAP.md`, `docs/WORKFLOWS.md`, `docs/security_model.md`, `docs/capacity_limits.md`, `docs/load_shedding.md`, `docs/ownership.md` (всё это упоминает CLAUDE.md). Также остаются актуальные ТЗ лета 2026: `docs/sub-aggregator-*.md`, `connection-flow-TZ.md`, `aggregator-UA-format-bug-TZ.md`, `REMNAWAVE_3_*.md` (на `REMNAWAVE_3_MIGRATION.md` ссылается `remnawave_api.py`), `MODERATION_VPN_BYPASS_CHANGESET.md` (на него ссылаются `language.py`, `navigation.py`, `screens.py`), `MIGRATION_TIMESTAMPTZ_ALIGNMENT.md` (ссылка из `migrations/025`), `SUPPORT_TEMPLATES.md`, `wata_setup.md`, `site_integration_audit.md`, `elma_remnawave_tz.md`, `lite_bot_context.md`, `BROKER_BOT_SPEC.md`, `tech_debt/registry.md`, прочие `runbooks/*`. Их судьба — вопрос владельцу, в этот пакет они не входят.

---

## B2. Точно мёртвые модули и файлы (риск: низкий)

| # | path[:symbol] | Доказательство | Риск | Что сделать на местах вызова |
|---|---|---|---|---|
| 2.1 | `app/core/i18n/__init__.py` (10), `app/core/i18n/types.py` (57) | AST: 0 импортёров. `grep -rn "core.i18n\|core/i18n"` по `*.py` → 0 (упоминается только в CLAUDE.md). vulture: 6 находок в `types.py` | низкий | убрать упоминания из `CLAUDE.md:44-45,79`, `app/handlers/CLAUDE.md` |
| 2.2 | `app/handlers/admin/reconcile.py` (399) | Роутер `admin_reconcile_router` (`:21`) **не подключён**: в `app/handlers/admin/__init__.py` 25 роутеров, этого среди них нет. `grep -rn "reconcile_router"` → только сам файл. Callback `admin:rmn_reconcile` не производит никто: скан сирот, производитель есть только у `admin:rmn_fix`, и он внутри самого файла (`:278`) | низкий | нет. Используемые им `remnawave_api.get_all_users` и `remnawave_premium.renew_premium_user` остаются живыми через `database/reconciliation.py` и `recovery_premium.py` |
| 2.3 | `app/handlers/callbacks/admin_callbacks.py` (3: `router = Router()`) | 0 импортёров. `callbacks/__init__.py` подключает 10 роутеров, этого нет | низкий | убрать из `app/handlers/CLAUDE.md` |
| 2.4 | `app/handlers/common/decorators.py` (7, только докстринг) | 0 импортёров. `grep handler_exception_boundary` → только докстринг самого файла | низкий | убрать из `app/handlers/CLAUDE.md` |
| 2.5 | `app/utils/audit.py` (418) | 0 импортёров. `grep "utils.audit\|utils/audit"` → 0. vulture: 7 находок | низкий | — |
| 2.6 | `app/utils/message_guard.py` (90) | 0 импортёров. `grep message_guard` → 0 | низкий | — |
| 2.7 | `validate_language_content.py` (215) | 0 импортёров. Упоминание только в `pyproject.toml:34` (per-file-ignores). Загружает `app.i18n.{code}` для удалённых языков | низкий | удалить строку `pyproject.toml:34` |
| 2.8 | `translation_patch_{ar,de,kk,tj,uz}.json` (778), `translation_tasks.json` (6) | grep → только `.dockerignore:35-36`. Скриптов `apply_*_patch.py` в репо нет | низкий | удалить `.dockerignore:34-37` |
| 2.9 | `systemd/vpn-api.service` (16) | grep `systemd` по коду, CI и Dockerfile → 0. CLAUDE.md:48 сам называет файл мёртвым samopis-артефактом | низкий | убрать правило «Не трогать systemd/…» из `CLAUDE.md:48-49` |
| 2.10 | `load_tests/` (только `results/*.md`) | см. B1. `.dockerignore:31` | низкий | удалить строку `.dockerignore` |
| 2.11 | `main.py:38-41` `XRAY_SYNC_AVAILABLE = False`, `xray_sync = None` | `grep -n "XRAY_SYNC_AVAILABLE\|xray_sync\b" main.py` → только эти строки и комментарий `:536-537` | низкий | удалить строки и комментарий |
| 2.12 | `main.py:110-112` `if hasattr(config, "XRAY_SERVER_IP")` | `grep -rnw XRAY_SERVER_IP` → только `main.py:111`. В `config.py` такого атрибута нет, условие всегда False | низкий | удалить блок |
| 2.13 | `config.py:346` `XRAY_API_TIMEOUT`, `:349` `VPN_SERVER_URL`, `:371` `XRAY_SYNC_ENABLED` | grep -w: вне `config.py` 0 использований (кроме `.env.example` для `VPN_SERVER_URL`) | низкий | удалить; из `.env.example:34-35` убрать `*_VPN_SERVER_URL` |
| 2.14 | `config.py:355,457` `VPN_PROVISIONING_ENABLED` | grep -w: вне `config.py` только `tests/test_vpn_utils_noop_cutover.py:31`, а тест уходит в B5. Не путать с `FEATURE_VPN_PROVISIONING_ENABLED` (`feature_flags.py:98`) — тот жив | низкий | удалить вместе с B5 |
| 2.15 | `app/services/incy_crypto.py:81-110` `selftest()` | `grep "selftest("` → 0 вызовов. В `main.py:328-334` только комментарий «used to be scheduled here» | низкий | удалить функцию и комментарий в main.py. **Остальной incy/Node-код жив**, см. «Решение владельца» §3 |
| 2.16 | `handlers.py` (корень, 1104 строки) — **всё, кроме** `show_payment_method_selection` (`:980-1095`) и `PAYMENT_METHOD_PHOTO_FILE_ID` (`:565`) | Импорт из корневого `handlers` есть только в 6 местах, и везде `from handlers import show_payment_method_selection`: `payments/callbacks.py:20`, `callbacks/navigation.py:1721`, `admin/broadcast.py:460,593,802,1139`. Обращений `handlers.<attr>` и патчей `"handlers.…"` в тестах нет. Остальные ~36 символов (`safe_edit_text`, `get_main_menu_keyboard`, `ensure_db_ready_*`, `get_reissue_lock`, …) — дубли живых копий в `app/handlers/common/{utils,keyboards,guards}.py`: grep находит именно их. `router = Router()` (`:560`) нигде не подключён (сам файл это признаёт на `:1098-1099`). vulture: 2 unused import (`:30`) | **средний** | Перенести `show_payment_method_selection` и константу в `app/handlers/payments/` (или `common/screens.py`) 1:1, поправить 6 импортов, удалить `handlers.py`. Функция содержит Lava-гейт (`:1007-1031`), поэтому переносить **после B4** или вместе с ним. Около 960 строк уходят |

Итог B2: около 3,7 тыс. строк (из них ~960 — `handlers.py`).

---

## B3. Хэндлеры-сироты: `callback_data` никто не производит (риск: низкий или средний)

Скан сирот, затем ручная проверка. Grep каждого префикса без `F.data` находит только объявление хэндлера и i18n-ключи. Динамических f-строк с таким префиксом нет.

**Общий риск:** кнопки из **старых сообщений** в чатах пользователей всё ещё нажимаются. После удаления хэндлера такой клик останется без ответа (часики на кнопке). Денег и доступа это не касается.

| # | Хэндлер | Фильтр | Доказательство | Строк | Риск / заметка |
|---|---|---|---|---|---|
| 3.1 | `app/handlers/admin/base.py:386-463` `callback_admin_reissue_key` | `startswith("admin:reissue_key:")` | Кнопка «Перевыпустить ключ» в `admin/keyboards.py:221` производит `admin:user_reissue:{id}`, а не `admin:reissue_key:`. Других производителей нет | 78 | низкий |
| 3.2 | `app/handlers/admin/stats.py:1038-1182` `callback_admin_referral_detail` | `startswith("admin:referral_detail:")` | Производителей нет. `database.get_admin_referral_detail` остаётся: его вызывает дашборд `routes/referrals.py:59` | 145 | низкий |
| 3.3 | `app/handlers/callbacks/navigation.py:381-395` `callback_connect_instead_of_copy` | `in_({"copy_key_menu","copy_key_plus"})` | Производителей нет. Живые кнопки шлют `copy_key` (`common/keyboards.py:466`) и `copy_vpn_key` (`:624`) | 15 | низкий. Похоже на осознанную заглушку для старых сообщений. Решение: удалить или оставить |
| 3.4 | `app/handlers/callbacks/subscription.py:384-457` `callback_renewal_pay` | `startswith("renewal_pay:")` | Производителей нет. Остались i18n-ключи `main.renewal_pay_button` и `main.renewal_payment_text` (проверить, что они больше нигде не нужны) | 74 | средний: путь продления, но UI его не вызывает |
| 3.5 | `app/handlers/traffic.py:1216-1280` `callback_bypass_pay_card` | `startswith("bypass_pay_card:")` | Экран пакета производит только `bypass_pay_wata:` (`:220`) и `bypass_pay_sbp:` (`:227`). Сам хэндлер лишь делегирует в `callback_bypass_pay_wata` (`:1224`) | 65 | низкий |
| 3.6 | `app/handlers/traffic.py:1355-1409` `callback_bypass_pay_stars` | `startswith("bypass_pay_stars:")` | Как в 3.5. **Здесь же находится баг P0-F** из recon (`price_kopecks=price_stars`, `:1382`). Он недостижим из UI, только через старые сообщения | 55 | низкий |
| 3.7 | `app/handlers/traffic.py:1413-1470` `callback_bypass_pay_crypto` | `startswith("bypass_pay_crypto:")` | Как в 3.5 | 58 | низкий |

Сироты, которые разобраны в других пакетах: `admin:mig_*` → B5, `*_pay_lava` → B4, `admin:rmn_reconcile` → B2, `stars_pay:balance` → «Защищено».

Итог B3: около 490 строк.

---

## B4. Lava — удалить полностью (решение владельца; риск: средний)

Факт из recon P0-A: `POST /webhooks/lava` зарегистрирован **безусловно**, а подпись не проверяется. Пока код жив, это поверхность атаки, даже если в Railway нет env Lava.

### 4.1 Сервис, конфиг, вебхук

| path[:symbol] | Доказательство | Заметка |
|---|---|---|
| `lava_service.py` (287) | импортируется только лениво из мест, перечисленных ниже | удалить |
| `app/api/payment_webhook.py:7` (докстринг), `:260-307` `_handle_lava_webhook`, `:310-312` `@router.post("/webhooks/lava")` `lava_webhook` | единственный роут Lava | удалить. Для уже отправленных Lava-вебхуков вернётся 404 (Lava не используется) |
| `config.py:392-398` `LAVA_WALLET_TO`, `LAVA_JWT_TOKEN`, `LAVA_SIGN_KEY`, `LAVA_SHOP_ID`, `LAVA_API_URL` | grep `LAVA_` вне `config.py` → только `lava_service.py` | удалить |
| i18n `payment.lava_waiting`, `payment.lava_unavailable` (`ru.py:668,670`, `en.py:527,529`) | используются только в Lava-хэндлерах ниже | удалить |
| i18n `traffic.pay_lava` (`ru.py:1146`, `en.py:845`) | grep `traffic.pay_lava` вне i18n → 0 | удалить |
| i18n `payment.lava` (`ru.py:667`, `en.py:526`), `payment.lava_pay_button` (`ru.py:669`, `en.py:528`) | **НЕ удалять**. `payment.lava` — подпись WATA-кнопок (`topup_fsm.py:117`, `payments_callbacks.py:219`, `gift.py:261`, `handlers.py:1020`). `payment.lava_pay_button` — подпись WATA-кнопки Apple ID в магазине (`navigation.py:1990`) | можно переименовать ключ, но не ради магазина (см. «Защищено») |
| `app/utils/button_defaults.py:93,120` (эмодзи-маппинг «Card (Lava)») | подпись кнопки → emoji-id | удалить строку `:93` после удаления кнопок |
| Метки провайдера `lava` для исторических платежей: `app/services/admin_notifier.py:105`, `dashboard/src/pages/{Dashboard.tsx:1971,1979, Payments.tsx:47, Statistics.tsx:521, Users.tsx:1338}`, `routes/payments.py:70` | нужны, чтобы корректно показывать **старые** строки `payments` с provider=`lava` | **оставить** |
| Комментарии: `database/farm.py:220`, `database/subscriptions.py:4505`, `wata_service.py:14,16,394`, `confirmation.py:738`, `common/screens.py:670`, `steam_purchase.py:14`, `proxy.py:11`, `tests/services/test_gift_confirmation.py:2` | только текст | поправить по пути |

### 4.2 Хэндлеры Lava (все недостижимы из UI, кроме двух)

| path:line | Хэндлер | Строк | Производитель callback | Заметка |
|---|---|---|---|---|
| `app/handlers/callbacks/payments_callbacks.py:1644-1782` | `callback_pay_lava` (`pay:lava`) | 138 | **нет** (кнопка уходит в `pay:wata`) | удалить |
| `app/handlers/callbacks/payments_callbacks.py:2371-2452` | `callback_topup_lava` (`topup_lava:`) | 81 | **нет** | удалить |
| `app/handlers/callbacks/gift.py:611-690` | `callback_gift_pay_lava` (`gift_pay:lava`) | 79 | **нет** | удалить. `_auto_delete_lava_msg` (`:40-46`) и `LAVA_INVOICE_TIMEOUT` (`:37`) **оставить**, их вызывают crypto/wata-ветки (`:765,822`); можно переименовать |
| `app/handlers/traffic.py:1020-1108` | `callback_traffic_pay_lava` (`traffic_pay_lava:`) | 88 | **нет** | удалить. `_auto_delete_lava_msg` (`:35-49`) и `LAVA_INVOICE_TIMEOUT` (`:32`) оставить: 8 вызовов из других веток |
| `app/handlers/traffic.py:1473-1537` | `callback_bypass_pay_lava` (`bypass_pay_lava:`) | 64 | **нет** | удалить |
| `app/handlers/proxy.py:221-274` | `callback_proxy_pay_lava` (`proxy_pay_lava`) | 53 | **ЕСТЬ, живая кнопка**: `proxy.py:83` «💳 Банковская карта», **без гейта** | ⚠️ нужно решение: убрать кнопку или перевести на WATA/Platega. Сейчас, если Lava не настроена, кнопка показывает ошибку |
| `app/handlers/game.py:1403-1459` | `callback_farm_shield_lava` (`farm_shield_lava:`) | 56 | **ЕСТЬ, живая кнопка**: `game.py:1392` «💳 Картой», **без гейта** | ⚠️ то же решение |
| 🔒 `app/handlers/payments/telegram_stars_purchase.py:419-464` | `callback_stars_pay_lava` (`stars_pay:lava`) | 43 | **нет** | файл магазина. См. «Защищено» |
| 🔒 `app/handlers/payments/steam_purchase.py:490-540` | `callback_steam_pay_lava` (`steam:pay:lava`) | 45 | **нет** | файл магазина |
| 🔒 `app/handlers/payments/spotify_purchase.py:659-708` | `cb_pay_lava` (`spotify_pay:lava:`) | 49 | **нет** | файл магазина |
| 🔒 `app/handlers/callbacks/navigation.py:2007-2066` | `callback_apple_pay_lava` (`apple_pay_lava:`) | 59 | **нет** | хэндлер магазина (`apple_*`) |

### 4.3 Гейты `lava_service.is_enabled()` на кнопках, которые на самом деле ведут в WATA

Это главная ловушка пакета. Во всех местах ниже кнопка с подписью Lava или «СБП резерв» уже переключена на WATA, но видимость по-прежнему зависит от `lava_service.is_enabled()`. **Если владелец уже удалил env Lava в Railway (фаза 0 из recon), эти WATA-кнопки уже скрыты в проде.**

| path:line | Экран | Кнопка → callback | Магазин? |
|---|---|---|---|
| `handlers.py:1007,1010,1017-1020,1030-1031` | выбор оплаты VPN (`/buy`) | `payment.lava` → `pay:wata` | нет |
| `app/handlers/payments/topup_fsm.py:112-120` | пополнение баланса | `payment.lava` → `topup_wata:{amount}` | нет |
| `app/handlers/callbacks/payments_callbacks.py:214-222` | пополнение баланса | `payment.lava` → `topup_wata:{amount}` | нет |
| `app/handlers/callbacks/gift.py:255-264` | подарок | `payment.lava` → `gift_pay:wata` | нет |
| 🔒 `app/handlers/payments/telegram_stars_purchase.py:299-303` | Stars | «💳 Карта (Lava)» → `stars_pay:wata` | **да** |
| 🔒 `app/handlers/payments/steam_purchase.py:184-195` | Steam | «💳 Карта (Lava)» → `steam:pay:wata` | **да** |
| (без гейта) 🔒 `spotify_purchase.py:548-553`, 🔒 `navigation.py:1985-1990` | Spotify, Apple ID | WATA-кнопки не зависят от Lava | да, не трогаем |

Для 4.3 нужно решение владельца («Решение владельца» §1): при удалении Lava либо **перевесить гейт на `wata_service.is_enabled()`** (`wata_service.py:53`), либо **убрать кнопку**. В магазине SCOPE разрешает только удалить кнопку «Карта (Lava)». Перевешивать гейт там — изменение UX магазина, это требует отдельного «ок».

Тестов, завязанных на Lava, нет. В `tests/test_webhook_signatures.py` классы `TestLava*` остались только в устаревшем `.pytest_cache/`, в самом файле их нет.

Итог B4: около 1150 строк (сервис 287, хэндлеры ~760, вебхук 50, гейты ~50, config/i18n ~15).

---

## B5. Samopis-cutover, `vpn_utils`/`vpn`-заглушки, subscription_proxy (риск: средний)

### 5.1 Админ-UI миграции samopis и рассылка — недостижимы

| path | Доказательство | Строк |
|---|---|---|
| `app/handlers/admin/migration.py` | Роутер подключён (`admin/__init__.py:16,44`), но **ни одна кнопка в репо не производит корневые callback'и**: `admin:mig_dryrun50`, `mig_dryrun_full`, `mig_apply10/100/500/1000`, `mig_apply1_input`, `mig_apply_all`, `mig_status`, `mig_verify`, `mig_clear_lock`, `mig_bcast`, `mig_bcast_test`, `migration_download`. Grep без `F.data` находит только докстринг `:6-13` и кнопки подтверждения внутри самого файла (`:596,599,923,924,1045,1046`). То есть в меню миграции войти нельзя. Внутри файла `asyncio.create_subprocess_exec` скриптов из `scripts/` | 1171 |
| `app/services/migration_broadcast.py` | единственные импортёры — `admin/migration.py:1056,1160` | 331 |
| `app/handlers/common/states.py:189-194` `AdminMigrationApply` | единственный пользователь — `migration.py` (скан второго порядка) | 6 |
| `scripts/migrate_samopis_to_remnawave.py` (581), `scripts/verify_samopis_migration.py` (377) | запускаются только из `migration.py:52-53`, плюс тест. `.dockerignore:40-42` держит для них исключения `!scripts/…` | 958 |
| `scripts/__init__.py` (1) | нужен только тесту `test_migrate_samopis_to_remnawave.py` (`import_module("scripts.migrate_…")`) и запуску `python -m scripts.*` | оставить, пока в `scripts/` есть модули |

После удаления поправить `.dockerignore:38-42`: оставшееся исключение касается только `incy_encode.mjs`.

### 5.2 `app/api/subscription_proxy.py` и `LEGACY_SAMOPIS_*`

| path | Доказательство | Заметка |
|---|---|---|
| `app/api/subscription_proxy.py` (148) | Монтируется только при `config.SUBSCRIPTION_PROXY_ENABLED` (`app/api/__init__.py:67-73`). Дефолт `False` (`config.py:591`), в `.env.example:124-125` тоже `false` | ⚠️ **Проверить в Railway**, что `PROD_SUBSCRIPTION_PROXY_ENABLED` не `true`. Это единственный обработчик `/api/sub/{token}`, а fallback-ссылки `vpn_utils.build_sub_url()` ведут именно туда (`{SUB_BASE_URL}/api/sub/{token}?id=`, `vpn_utils.py:148-151`). Если прокси выключен, эти fallback-ссылки уже отдают 404 |
| `config.py:586-591` `SUBSCRIPTION_PROXY_ENABLED`, `:627-632` `LEGACY_SAMOPIS_SUB_BASE_URL`; `.env.example:122-129` | используются только в `subscription_proxy.py:46` и `app/api/__init__.py:69` | удалить вместе с модулем |
| `app/api/__init__.py:12,65-73` | импорт и монтирование | удалить |

### 5.3 `vpn_utils.py` (no-op shim, 161) — все места вызова

Живые функции в модуле — только `generate_sub_token` и `build_sub_url` (`:133-151`). Их вызывают `user_subscription_links.py:96`, `admin/reissue.py:63`, `admin/access.py:144,149,400,1900,2151`. **Предложение:** перенести обе в `app/services/user_subscription_links.py`, поправить 7 импортов и удалить `vpn_utils.py`. Отдельно recon P1-5: admin reissue отдаёт legacy-ссылку. Это баг, а не мёртвый код, его чиним в фазе 2 или 3.

No-op вызовы, которые нужно вырезать. Контекст транзакции получен скриптом по отступам и проверен по коду:

| Место | Вызов | Контекст | Что переписать |
|---|---|---|---|
| `database/subscriptions.py:18` | `import vpn_utils` | — | удалить |
| `database/subscriptions.py:282` (`check_and_disable_expired_subscription`) | `safe_remove_vless_user_with_retry(uuid_to_remove)` | фаза 2, **вне** транзакции | После no-op пишется лог `EXPIRY_REMOVE_SUCCESS` и строка `vpn_lifecycle_audit(action="vpn_expire")`, то есть аудит фиксирует удаление, **которого не было**. Решить: убирать аудит-запись или заменить её реальным disable через `remnawave_service.disable_remnawave_user_bg` (уже используется в этом модуле). `removal_success=True` становится константой |
| `database/subscriptions.py:2572` (`approve_payment_atomic`) | cleanup-on-failure | **внутри** `except` транзакции | удалить ветку `if uuid_to_cleanup_on_failure` |
| `database/subscriptions.py:2589` | удаление `old_uuid` после commit | вне tx, внутри `pool.acquire()` | удалить блок `grant_result_for_removal` |
| `database/subscriptions.py:5113`, `:5129` (finalize-purchase, `pool.acquire()` с `:4463`) | cleanup / `old_uuid` | вне tx | как 2572 и 2589 |
| `database/admin.py:15` | `import vpn_utils` | — | удалить |
| `database/admin.py:2603`, `:2619` (`admin_grant_access_atomic`) | cleanup-on-failure | **внутри** `conn.transaction()` (`:2560`) | удалить ветку |
| `database/admin.py:2649` | `old_uuid` | после tx | удалить |
| `database/admin.py:2909`, `:2924` (grant, `pool.acquire()` с `:2772`) | cleanup / `old_uuid` | вне tx | удалить |
| `database/admin.py:3290`, `:3306` (`admin_grant_access_minutes_atomic`) | cleanup-on-failure | **внутри** `conn.transaction()` (`:3248`) | удалить ветку |
| `database/admin.py:3330` | `old_uuid` | после tx | удалить |
| `database/admin.py:3417` (`admin_revoke_access_atomic`) | `uuid_to_remove` | вне tx | удалить. Реальный disable уже идёт через `remnawave_service` в `access.py` |
| `database/admin.py:4558` (`admin_delete_user_complete`) | `uuid_to_remove` | вне tx | удалить. Реальное удаление — `remnawave_api.delete_user` |
| `app/services/activation/service.py:21` | `import vpn_utils` | — | удалить |
| `app/services/activation/service.py:442,459,486,514` | cleanup-on-failure (4 ветки) | вне tx, внутри `acquire_connection(pool, "activation_phase3_lock")` (`:429`) | удалить ветки |
| `trial_notifications.py:692-699` (`_process_single_trial_expiration`) | `import vpn_utils` + `remove_vless_user(uuid_val)` | вне tx | После удаления исчезает ветка `except → return` («не помечать expired, если удаление упало»). Она и так недостижима (no-op не бросает) |
| `database/core.py:14` | `import vpn_utils` | 0 обращений `vpn_utils.` в файле | удалить импорт |
| `app/handlers/admin/access.py:17` | `import vpn_utils` | 0 обращений `vpn_utils.` | удалить (`build_sub_url` импортируется лениво отдельно) |
| `app/handlers/admin/base.py:404` | `import vpn_utils` | 0 обращений | удалить |
| комментарии: `auto_renewal.py:384`, `subscriptions.py:966,1374,2066`, `payments_messages.py:4`, `purchase_flow.py:4,26,139`, `config.py:597` | текст | поправить |

Всего вырезается 19 вызовов `safe_remove_vless_user_with_retry`, 1 `remove_vless_user` и 6 лишних импортов. Прямых вызовов `add_vless_user`, `update_vless_user`, `reissue_vpn_access`, `ensure_user_in_xray`, `upgrade_vless_user`, `remove_plus_inbound`, `check_xray_health` и классов исключений `VPNAPIError` и др. в коде **нет**, есть только комментарии (grep по `vpn_utils.<attr>` и по именам).

### 5.4 `app/services/vpn/` (no-op shim, 42)

| Место | Вызов | Что переписать |
|---|---|---|
| `fast_expiry_cleanup.py:22` | `from app.services.vpn import service as vpn_service` | удалить |
| `fast_expiry_cleanup.py:232-246` | `remove_uuid_if_needed()` всегда `False` → `is_vpn_api_available()` всегда `False` → лог `VPN_API_DISABLED` | свести к очистке БД. Вне tx, внутри `_worker_lock` |
| `fast_expiry_cleanup.py:385` | `except vpn_service.VPNRemovalError` | ветка недостижима, удалить |
| `app/utils/logging_helpers.py:299-304,314` | `VPNServiceError` в `classify_error` | убрать из кортежа |

### 5.5 XRAY-конфиг и `VPN_ENABLED`

| Место | Факт | Предложение |
|---|---|---|
| `config.py:342-369` `XRAY_API_URL`, `XRAY_API_KEY`, первое определение `VPN_ENABLED`/`VPN_PROVISIONING_ENABLED` и логи | `VPN_ENABLED` и `VPN_PROVISIONING_ENABLED` переопределены на `REMNAWAVE_ENABLED` в `config.py:456-457` | удалить блок `:342-369` |
| `app/core/system_state.py:422-433`, `app/handlers/payments/payments_messages.py:199`, `app/handlers/callbacks/subscription.py:151` | Статус «VPN API» берётся из `config.XRAY_API_URL`. В `.env.example:27-30` стоит `PROD_XRAY_API_URL=https://api.mynewllcw.com` (samopis). Значит, в проде компонент может показываться healthy по мёртвому хосту | заменить на `config.REMNAWAVE_ENABLED` |
| `VPN_ENABLED` (17 использований вне config/tests) | алиас `REMNAWAVE_ENABLED` | → «Консолидировать» |
| `app/api/__init__.py:196-198` (`/health` → `vpn_api`) | читает `VPN_ENABLED` | переименовать ключ на `remnawave` (внешние потребители `/health` не найдены; проверить мониторинг) |
| `.env.example:27-30` | `*_XRAY_API_URL/KEY` | удалить |
| `config.py:594-599` `PURCHASE_FLOW_REMNAWAVE` | жив: `subscriptions.py:2563`, `payments_callbacks.py:805,811`, `purchase_flow.py:146` | не трогать в B5. Если флаг всегда `True`, ветки `else` — кандидаты на фазу 2 |

### 5.6 БД-функции только для samopis (второй порядок)

После удаления 5.1–5.2 у этих функций не остаётся внешних ссылок, кроме реэкспорта в `database/__init__.py`:

| Функция | Строк | Ссылки сейчас |
|---|---|---|
| `database/traffic.py:287-306` `get_subscription_by_premium_uuid` | 20 | `subscription_proxy.py`, тест |
| `database/traffic.py:309-323` `get_subscription_by_samopis_uuid` | 15 | `subscription_proxy.py`, тест |
| `database/traffic.py:326-343` `count_migration_broadcast_candidates` | 18 | `admin/migration.py` |
| `database/traffic.py:346-382` `list_migration_broadcast_candidates` | 37 | `migration_broadcast.py`, тест |
| `database/traffic.py:385-397` `mark_migration_notice_sent` | 13 | `migration_broadcast.py`, тест |
| `database/traffic.py:400-438` `count_premium_migration_progress` | 39 | `admin/migration.py`, тест |
| `database/traffic.py:441-492` `list_subscriptions_for_premium_migration` | ~52 | только `scripts/migrate_samopis_to_remnawave.py:449` (+ упоминание в докстринге `traffic.py:407`) |

Итого 7 функций, около 195 строк.

### 5.7 Тесты, которые уходят вместе с B5

| Тест | Строк | Статус сейчас |
|---|---|---|
| `tests/test_vpn_utils_noop_cutover.py` | 122 | 2 failed |
| `tests/integration/test_vpn_entitlement.py` | 149 | 1 failed; патчит `database.vpn_utils.add_vless_user`, которого код не вызывает |
| `tests/integration/test_subscription_proxy.py` | 161 | проходит |
| `tests/test_migrate_samopis_to_remnawave.py` | 468 | 3 failed |
| `tests/services/test_admin_migration.py` | 925 | 2 failed |
| `tests/services/test_migration_broadcast.py` | 367 | проходит |

Итого 2192 строки, **8 из 46** текущих падений уходят. `tests/services/test_purchase_flow.py` и `test_user_subscription_links.py` упоминают samopis, но проверяют живой код: **оставить**.

### 5.8 Колонки и таблицы БД — только кандидаты, без DROP

| Объект | Пишет | Читает | Заметка |
|---|---|---|---|
| `subscriptions.samopis_migrated_at` (миграция 045, `core.py:684`) | **живой** writer `database/traffic.py:154,194` (`set_remnawave_premium_uuid` проставляет `NOW()`) | только функции 5.6 и скрипты | writer живой, поэтому дроп — отдельным релизом после правки writer'а |
| `subscriptions.migration_notice_sent_at` (миграция 049) | `mark_migration_notice_sent` (5.6) | `count_/list_migration_broadcast_candidates` (5.6) | после B5 — ни читателей, ни писателей |
| `vpn_lifecycle_audit` | `subscriptions.py:289` (ложная запись после no-op, см. 5.3), `fast_expiry_cleanup.py` | дашборд? (не найдено) | решить после 5.3 |
| `subscriptions.uuid` (legacy samopis UUID) | много мест | много мест | **не кандидат** в этой фазе |
| `pending_purchases`/`payments` с provider=`lava` | исторические строки | дашборд | не трогать |

Итог B5: около 5,5 тыс. строк (код ~3,3 тыс., тесты ~2,2 тыс.) плюс `scripts/README_MIGRATION.md` (в B1).

---

## B6. Публичные функции `database/*` без единого вызова (риск: низкий)

Критерий: ноль упоминаний во всём репо (код, тесты, SQL, dashboard/src, scripts), кроме собственного `def` и реэкспорта в `database/__init__.py`. Динамический доступ `getattr(database, …)` проверен (см. 0.2).

| path:line | Функция | Строк | `__init__` реэкспорт |
|---|---|---|---|
| `database/admin.py:1444` | `get_all_users_telegram_ids` | 6 | да |
| `database/admin.py:1494` | `check_user_still_eligible_for_no_sub_broadcast` | 28 | да |
| `database/admin.py:3741` | `get_user_ltv` | 23 | да |
| `database/admin.py:3766` | `get_average_ltv` | 24 | да |
| `database/admin.py:4664` | `get_gift_subscription` | 15 | да |
| `database/admin.py:4864` | `get_user_paid_subscription_history` | 21 | да |
| `database/beta_applications.py:125` | `has_applied` | 16 | нет |
| `database/bypass_gift_links.py:91` | `get_bypass_gift_link_by_code` | 12 | да |
| `database/core.py:141` | `safe_get` | 15 | да |
| `database/core.py:366` | `_get_pool_safe` | 10 | да |
| `database/marketing_links.py:95` | `get_stats_link` | 11 | да |
| `database/marketing_links.py:312` | `get_promo_link` | 11 | да |
| `database/reconciliation.py:114` | `_bulk_fetch_panel_expires_at` | 26 | нет |
| `database/subscriptions.py:90` | `create_payment` | 58 | да |
| `database/subscriptions.py:191` | `update_payment_status` | 29 | да |
| `database/subscriptions.py:590` | `has_any_subscription` | 13 | да |
| `database/subscriptions.py:605` | `has_any_payment` | 13 | да |
| `database/subscriptions.py:840` | `has_active_special_offer` | 4 | да |
| `database/subscriptions.py:1092` | `_log_vpn_lifecycle_audit_fire_and_forget` | 28 | да |
| `database/subscriptions.py:3168` | `consume_promocode_atomic` | 106 | да — ⚠️ финансовая функция; перед удалением убедиться, что промокоды списываются другой функцией (grep показывает 0 вызовов) |
| `database/subscriptions.py:3276` | `is_user_first_purchase` | 34 | да |
| `database/traffic.py:93` | `get_remnawave_premium_id` | 13 | да |
| `database/traffic.py:273` | `clear_remnawave_premium_uuid` | 12 | да |
| `database/users.py:1233` | `calculate_referral_percent` | 21 | да |
| `database/users.py:2045` | `update_username` | 8 | да |

**25 функций, около 560 строк**, плюс ~23 строки реэкспорта. Добавляются 7 функций из 5.6 после B5. Итого 32.

**Расхождение с recon (38).** Recon считал и функции, которые вызываются только внутри своего модуля. Таких 17, и они не мертвы: это кандидаты стать приватными (`_name`), а не на удаление. Раскладка recon по модулям (subscriptions 11, admin 19, …) воспроизводится только с ними. По строгому критерию получается 25 + 6.

---

## B7. Неиспользуемые функции Remnawave-клиентов (риск: низкий)

| path:line | Функция | Строк | Доказательство |
|---|---|---|---|
| `app/services/remnawave_api.py:545` | `reset_user_traffic` (`/actions/reset-traffic`) | 6 | 0 упоминаний вне `def` |
| `…:553` | `enable_user` (`/actions/enable`) | 6 | 0 |
| `…:561` | `disable_user` (`/actions/disable`) | 6 | 0 |
| `…:569` | `revoke_user_subscription` (`/actions/revoke`) | 6 | 0 |
| `…:577` | `extend_user_expiry` | 24 | единственное прочее упоминание — строка его же `ValueError` (`:596`) |
| `…:744` | `find_user_by_email` | 5 | 0 |
| `…:921` | `resolve_user_id` | 35 | единственное прочее упоминание — модульный докстринг (`:30`) |
| `app/services/remnawave_bypass.py:378` | `delete_bypass_user` | 18 | 0 (+ строка в `__all__`) |
| `app/services/remnawave_premium.py:577` | `get_premium_subscription_url` | 16 | 0 |
| `app/services/remnawave_service.py:203` | `create_remnawave_user_bg` | 2 | 0 |
| `app/services/remnawave_service.py:533` | `update_tariff` | 25 | 0 |

Около 150 строк. Замечание к фазе 3: `revoke_user_subscription` может понадобиться для P1-4 из recon (отозванный пользователь сохраняет доступ через sub-aggregator). Удалять только если фаза 3 не берёт её в работу.

---

## B8. Неиспользуемые роуты дашборда — **кандидатов нет**

Все 140 роутов из `app/api/dashboard/**` (21 роутер + auth + ws) вызываются из `dashboard/src`. Три роута не нашлись автоматическим сопоставлением, но проверены вручную: `POST /broadcasts/upload-photo` и `/upload-animation` (`dashboard/src/lib/api.ts:1130,1134`, через `_uploadMultipart`), `WEBSOCKET /ws` (`lib/ws.ts:51-55`).

---

## B9. Прочие мёртвые функции в живых модулях (риск: низкий)

| path:line | Символ | Строк | Заметка |
|---|---|---|---|
| `app/core/pool_monitor.py:19` | `get_last_pool_wait_spike_monotonic` | 3 | |
| `app/core/system_state.py:367` | `create_default_system_state` | 12 | |
| `app/handlers/admin/broadcast.py:75` | `_safe_send` | 31 | |
| `app/handlers/admin/recovery_premium.py:142` | `_scan` | 94 | модуль жив, функция нет |
| `app/services/activation/service.py:128` | `is_activation_allowed` | 38 | |
| `app/services/activation/service.py:534` | `_update_subscription_activated` | 25 | |
| `app/services/admin_alerts.py:198` | `alert_vpn_api_failure` | 32 | |
| `app/services/admin_alerts.py:232` | `alert_security_event` | 12 | |
| `app/services/purchase_flow.py:94` | `_device_limit_for` | 4 | |
| `app/services/push_notifications.py:225` | `subscription_count` | 13 | |
| `app/services/sub_aggregator.py:169` | `get_url` | 17 | см. «Решение владельца» §6 |
| `app/utils/logging_helpers.py:340` | `log_operation` | 51 | |
| `app/utils/security.py:77` | `validate_message_text` | 23 | |
| `app/utils/security.py:310` | `require_ownership` | 22 | |
| `config.py:192` | `tariff_for_vpn_api` | 7 | samopis |
| `config.py:249` | `get_biz_price_stars` | 7 | |

Около 390 строк. **Не включены намеренно:** `platega_service.check_transaction_status` (`:501`) и `check_subscription_status` (`:466`). Сейчас у них 0 вызовов, но фаза 2 должна вызывать `GET /transaction/{id}` перед выдачей (recon §3a). Функции `site_sync` вынесены в «Решение владельца» §7.

## B10. Механическая чистка ruff (риск: низкий, но только с ревью)

`ruff check --select F401,F841,F811 --fix`: 220 автофиксов, из них 205 F401, 81 F841 и 1 F811 (`database/subscriptions.py:1106`). Больше всего в `admin/access.py` (21), `admin/reissue.py` (16), `database/users.py` (15), `admin/stats.py` (14), `database/admin.py` (12), `activation_worker.py` (11). **Только отдельным коммитом и с ручным ревью.** Импорт ради побочного эффекта (`main.py:18 import app.utils.button_defaults`) защищён `noqa`; другие подобные импорты ruff может принять за неиспользуемые. Сначала прогнать `--diff`.

---

## Консолидировать (не мёртвое, а дубли)

### К1. Три модуля уведомлений админа

| Модуль | Роль | Кто использует |
|---|---|---|
| `admin_notifications.py` (корень, 324) | degraded/recovered-режим, pending activations, `send_admin_notification`, **`send_user_notification`** (это уведомление пользователю) | `main.py:33,190,206,216,405`, `activation_worker.py:14,121`, `confirmation.py:301` (`send_admin_notification`), `admin/access.py:1370,1441,1592,1799` (`send_user_notification`) |
| `app/services/admin_alerts.py` (243) | `send_alert` с rate-limit и типизированные `alert_payment_failure`, `alert_subscription_failure`, `alert_worker_failure` | 25 ленивых импортов: `auto_renewal`, `trial_notifications`, `reminders`, `activation_worker`, `fast_expiry_cleanup`, `farm_notifications`, `confirmation`, `wata_service`, `main`, `sub_aggregator_route`, `admin/broadcast`, `subscription_watchdog`. Функции `alert_vpn_api_failure` и `alert_security_event` мертвы (B9) |
| `app/services/admin_notifier.py` (283) | подписчик event-bus (`payment_error`, `broadcast_done`, `payment_approved`) → DM и push | только `main.py:297` (`run_admin_notifier`) |

**Предложение:** единая точка — `app/services/admin_alerts.py`. В неё переносим degraded/recovered/pending из `admin_notifications.py`. `send_user_notification` уходит в `app/services/notifications/`. `admin_notifier.py` остаётся подписчиком event-bus, но шлёт через `admin_alerts`. Прямо в этой задаче: recon P1-7 — «любая ошибочная транзакция → алерт» и rate-limit «1 в 60 с» теряет сообщения.

### К2. Слои Remnawave

- `remnawave_api.py` — HTTP-клиент (живые функции: `create_user`, `get_user` (34 модуля), `update_user`, `delete_user`, `find_user_by_*`, `get_bypass_entity_safe`, `get_user_traffic`, HWID). Мёртвые перечислены в B7.
- `remnawave_premium.py` — premium-сущность: `create_premium_user_entity` (5 вызовов), `renew_premium_user`, `disable_premium_user`, `reissue_premium_user_entity`.
- `remnawave_bypass.py` — bypass-сущность: `create_bypass_user_entity`, `add_bypass_traffic`.
- `remnawave_service.py` (legacy-обёртки) — `create_remnawave_user` (5), `renew_remnawave_user[_bg]` (5 — это те самые +10 ГБ из P0-C), `extend_remnawave_for_bypass[_bg]`, `disable_/delete_remnawave_user_bg`, `ensure_squad`, `add_traffic`, **`add_bypass_traffic`**.

**Главная опасность дубля:** две функции **с одинаковым именем** `add_bypass_traffic` и разной семантикой.
- `remnawave_service.add_bypass_traffic` (`:463`) — create-if-missing, через `add_traffic`. Её вызывают `payments_messages.py:1262`, `payments_callbacks.py:879`, `broadcast_trial_key.py:73`, `admin/bonus.py:466`, `user/start.py:193,1113`.
- `remnawave_bypass.add_bypass_traffic` (`:292`) — только top-up. Её вызывает `confirmation.py:71,77`.

Цель фазы 3: один `ProvisioningService` поверх `premium` и `bypass`, а `remnawave_service` растворить в нём.

### К3. Копии «добавить трафик» (read-modify-write `trafficLimitBytes`)

Файлы с ручной работой с `trafficLimitBytes` (разобрать в фазе 3): `remnawave_api.py` (12), `remnawave_service.py` (11), `panel_traffic_audit.py` (7), `routes/remnawave.py` (7), `remnawave_bypass.py` (4), `common/screens.py` (3), `admin/traffic_admin.py` (3), `confirmation.py`, `verify_delivery.py`, `traffic.py`, `broadcast_trial_key.py`, `traffic_monitor.py`, `purchase_flow.py`, `access.py`, `routes/users.py`, `routes/traffic_audit.py`. Цель — одна функция «прибавить N байт к bypass» с идемпотентностью по `purchase_id` (SCOPE).

### К4. Прочее

- `config.VPN_ENABLED` / `VPN_PROVISIONING_ENABLED` — алиасы `REMNAWAVE_ENABLED` (`config.py:456-457`). 17 использований → заменить на `REMNAWAVE_ENABLED`.
- `show_payment_method_selection` → перенос из корневого `handlers.py` (B2.16).
- `build_sub_url` / `generate_sub_token` → перенос из `vpn_utils.py` (B5.3).
- `_auto_delete_lava_msg` (`gift.py:40`, `traffic.py:35`) и `_auto_delete` (`proxy.py:277`) — три копии «удалить инвойс-сообщение по таймеру».

---

## 🔒 Защищено — не трогаем (магазин, `SCOPE.md`)

Здесь перечислено то, что внутри магазина мертво или устроено странно. **Без отдельного решения владельца не удаляется.**

| Место | Факт |
|---|---|
| `telegram_stars_purchase.py:419-464` `callback_stars_pay_lava` | сирота (кнопка ведёт в `stars_pay:wata`), вызывает `lava_service` |
| `steam_purchase.py:490-540` `callback_steam_pay_lava` | сирота, вызывает `lava_service` |
| `spotify_purchase.py:659-708` `cb_pay_lava` | сирота, вызывает `lava_service` |
| `navigation.py:2007-2066` `callback_apple_pay_lava` | сирота, вызывает `lava_service` |
| `telegram_stars_purchase.py:299-303`, `steam_purchase.py:184-195` | кнопка «💳 Карта (Lava)» → WATA под гейтом `lava_service.is_enabled()` (см. B4.3) |
| `telegram_stars_purchase.py:364-376` `callback_stars_pay_balance` | кнопки нет «по политике», это серверный guard. Оставить |
| i18n `payment.lava_pay_button` | подпись WATA-кнопки Apple ID (`navigation.py:1990`) |
| vulture: 17 находок в `spotify_purchase.py`, 11 в `steam_purchase.py`, 9 в `telegram_stars_purchase.py`, 6 в `admin/apple_id_delivery.py` | это декорированные хэндлеры (ложные срабатывания) |
| ветка магазина в `confirmation.py` (`mark_pending_purchase_paid` + `send_*_success`) | не трогаем |

**Минимальное касание при B4** (SCOPE, «единственное допустимое»): в этих 4 файлах удалить только Lava-хэндлеры-сироты, у которых нет кнопок, и Lava-гейты с кнопками. Каждое место перечислить в PR. Удаление самих хэндлеров-сирот формально выходит за «удаление кнопки», но UX не меняет, потому что кнопок у них нет. Нужно явное «ок» владельца.

---

## Нужно решение владельца (только факты)

1. **WATA-кнопки за Lava-гейтом (B4.3).** В 6 местах (4 VPN и 2 магазина) WATA-кнопка видна только при `lava_service.is_enabled()`. Если env Lava в Railway уже удалены, эти кнопки **сейчас скрыты в проде**: оплата VPN, пополнение баланса ×2, подарок, Stars, Steam. Варианты: гейт на `wata_service.is_enabled()` или удалить кнопку.
2. **Живые «карточные» кнопки, которые ведут только в Lava:** прокси (`proxy.py:83` → `proxy_pay_lava`) и щит на ферме (`game.py:1392` → `farm_shield_lava:`). Без гейта. После удаления Lava — убрать кнопку или перевести на WATA.
3. **Node-toolchain / Incy — НЕ мёртв.** Комментарий в `main.py:328-334` («to_incy_link is now pure-Python») **противоречит коду**: `to_incy_link` (`incy_crypto.py:172`) первым делом вызывает `to_incy_link_crypt1` → `_spawn` → `node scripts/incy_encode.mjs`. Только при неудаче он отдаёт `incy://add/<url>`. Dockerfile ставит Node (`:19-28`) и делает `npm install` по `package.json` (`:37-39`), `.dockerignore:48` пропускает `incy_encode.mjs`. В проде, скорее всего, выдаются `incy://crypt1/…`. Вызовы идут из `navigation.py:1091,1148,1490`, `bypass_setup.py:145,229`, `traffic.py:393,575`, `deeplink_redirect.py:74`. `is_available()` всегда `True` (`:68-78`), поэтому утверждение CLAUDE.md «без Node кнопка молча скрывается» тоже неверно. **Удаление Node меняет формат ссылки:** crypt1 (адрес не виден) → plain URL в deeplink. Мёртв только `selftest` (B2.15). `package-lock.json` отсутствует, версия `@incy/link-encoder@^1.0.1` не закреплена.
4. **CryptoBot.** Включается через `CRYPTOBOT_API_TOKEN` (`config.py:389`, `cryptobot_service.py:28-30`). Подключения: вебхук `/webhooks/cryptobot` (`payment_webhook.py:205-240`), кнопки в оплате VPN (`handlers.py:1008-1011`), подарке (`gift.py:247-249,548`), оплате VPN (`payments_callbacks.py:1550`), Steam 🔒 (`steam_purchase.py:209,657`), пакете bypass (`traffic.py:1430` — это хэндлер-сирота `bypass_pay_crypto`, B3.7). Работает ли в проде — зависит от env в Railway; `/health` отдаёт `payment_providers.cryptobot`.
5. **Игра и ферма.** `game.py` 1559 + `database/farm.py` 553 + `workers/farm_notifications.py` 313 (`create_task` в `main.py:270`) + `admin/farm_storm.py` 174. Кнопка «Игры» есть в главном меню (`common/keyboards.py:236-237` → `games_menu`), роутер подключён. Щит на ферме оплачивается Lava (п. 2). Код живой, решение продуктовое.
6. **Sub-aggregator.** `SUB_AGGREGATOR_ENABLED = True` зашит в `config.py:659` (эндпоинт `/a/{token}` обслуживает уже выданные ссылки), `SUB_AGGREGATOR_ISSUE_ENABLED = False` (новые не выдаются). Код: `sub_aggregator.py` 316, `sub_aggregator_route.py` 783, `admin/sub_aggregator_cmd.py` 345, отдельный Node-сервис `sub-aggregator/` со своим Dockerfile. `sub_aggregator.get_url` мёртв (B9). Recon P1-2: `SUB_AGGREGATOR_INTERNAL_SECRET=""`.
7. **Site sync.** `site_sync.is_enabled()` жёстко возвращает `False` (`site_sync.py:42-51`). Воркер стартует только при `is_enabled()` (`main.py:510-513`), то есть никогда. Все вызовы `sync_*` — no-op. Модуль 287 + воркер 75 строк. Дополнительно мертвы `_is_temporarily_disabled`, `check_balance`, `get_user_status`, `periodic_sync`. Если сайт не вернётся, модуль можно удалить целиком вместе с вызовами.
8. **`SUBSCRIPTION_PROXY_ENABLED` в Railway.** Прежде чем удалять B5.2, подтвердить, что в проде `false`. Отдельно: fallback-ссылки `build_sub_url()` сейчас указывают на `/api/sub/{token}`, который обслуживает только этот прокси.
9. **One-shot скрипты вне образа:** `scripts/prep_remnawave_v3_migration.py` (207, миграция 078 применена?), `scripts/recover_stuck_trials.py` (182), `scripts/audit_bypass_traffic_mismatch.py` (138, CLI-аналог дашборда `/traffic-audit`). В Docker не попадают, запускаются локально.
10. **Хэндлеры-сироты для старых сообщений** (B3.3 `copy_key_*`, B3.4 `renewal_pay:`): удалить или оставить заглушкой, чтобы старые кнопки не зависали.

---

## Сводная таблица

| Пакет | Что | Файлов | ≈ строк | Риск |
|---|---|---|---|---|
| B1 | Документация → `docs/archive/` | ~63 md | ~14 300 (перенос) | нулевой |
| B2 | Точно мёртвые модули и файлы, заглушки в `main.py`/`config`, `selftest`, корневой `handlers.py` (с переносом одной функции) | 17 удалить + ~8 правок | ~3 700 | низкий (2.16 — средний) |
| B3 | Хэндлеры-сироты (7) | 5 | ~490 | низкий/средний |
| B4 | Lava целиком (сервис, вебхук, 11 хэндлеров, 6 гейтов, config, i18n) | 1 удалить + ~14 правок (4 🔒) | ~1 150 | средний; нужны решения §1–2 |
| B5 | Samopis: админ-UI миграции, рассылка, 2 скрипта, subscription_proxy, `vpn_utils`/`vpn` (26 мест вызова), XRAY-конфиг, 7 БД-функций, 6 тестов | 9 удалить + ~15 правок | ~5 500 (из них тесты ~2 200) | средний; нужно решение §8 |
| B6 | 25 публичных и приватных БД-функций без вызовов | 9 | ~580 | низкий |
| B7 | 11 функций Remnawave-клиентов | 4 | ~150 | низкий |
| B8 | Роуты дашборда | 0 | 0 | — |
| B9 | 16 мёртвых функций в живых модулях | 12 | ~390 | низкий |
| B10 | ruff F401/F841/F811 автофикс | ~60 | ~290 | низкий (ревью `--diff`) |
| **Итого код** (B2–B10) | | | **≈ 12 250** | |
| **+ документация** (B1) | | | **≈ 14 300** | |

**Не удаляем, решает владелец:** Node/Incy (~180 строк + Dockerfile), site_sync (~360), игра и ферма (~2 600), sub-aggregator, CryptoBot, one-shot скрипты (~530).

---

## Раунд 2 (2026-09-13): B6, B7, B9, B10 и группа A экранов — выполнено

- Решение владельца: «лишний мёртвый код убрать, всё проверить», «лишнее не надо делать». Удалялось только доказанно мёртвое, без рефакторинга.
- База: `refactor/audit-2026-09` @ `12bc5988` (ветка ребейзнута на свежий HEAD). Ветка `worktree-agent-a80e7cf661f6b58fb`, 7 коммитов, не запушено, в `main` не мержено.
- **Итог: 84 файла, +66 / −3287 строк** (`git diff --shortstat 12bc5988 HEAD`).
- **Тесты:** до — 2642 passed / 4 skipped / 139 xfailed, после — 2641 / 4 / 139. Минус один кейс — это `tests/test_import_smoke.py`, он параметризован по модулям (`pkgutil.walk_packages`), а модуля `withdraw_fsm.py` больше нет. После **каждого** коммита: полный `pytest`, `ruff check .`, `compileall`, `import tests.conftest, main` — чисто. Новый тест владельца `test_i18n_keys_exist.py` (все литеральные ключи `get_text` существуют) проходит после удаления ключей.

### Как доказывали

1. `git grep -nw <имя>` вне `docs/` и `*.md` по всему репо: код, тесты, `scripts/`, SQL, CI, Dockerfile, `dashboard/src`. Флаг `-w` ловит и обращения через модуль (`database.<имя>`, `remnawave_api.<имя>`). Мёртвым считалось имя, у которого остались только строка `def`, реэкспорт в `__init__.py` или своя строка в `__all__`.
2. Динамика: `getattr(database, …)` есть только в `user/devices.py`, имена там литеральные из `_TIERS`, ни одно не удалено. `import_module` — только провайдеры платежей.
3. Callback-хэндлеры: у `callback_data` нет производителя вне удаляемого кода.
4. vulture 60 % по `app/`, `database/` и корневым `*.py` плюс фильтр «не декорировано и нет ссылок» — перекрёстная проверка. Повторялась после каждой волны, чтобы поймать **второй порядок** (функции, которые умерли после удаления своих единственных вызывающих).
5. Импорты, осиротевшие после удаления, — `ruff F401/F841`: разница с `HEAD` проверялась отдельным скриптом.

### Что удалено

| Коммит | Пакет | Что удалено | Строк |
|---|---|---|---:|
| `1b54181e` | B6 | 30 функций БД + реэкспорты (список ниже) | −736 |
| `1ee4056c` | B7 | 10 функций Remnawave-клиентов | −169 |
| `c4f01468` | B9 | 20 хелперов в живых модулях + 3 константы | −532 |
| `d6015b48` | A1, A3, A4, A6, A7 | хэндлеры-сироты, `withdraw_fsm.py`, `WithdrawStates`, 4 функции БД второго порядка | −698 |
| `1a2cdf3d` | A8, A9 | 16 мёртвых клавиатур в `common/keyboards.py` | −272 |
| `ed0f7fec` | A10 | 367 ключей i18n из `ru.py` и `en.py` | −729 |
| `deada3c4` | B10 | safe-автофикс ruff F401/F841 в 58 файлах | −151 / +61 |

**B6 — функции `database/*`** (проверка: у каждой имени только `def` и строка реэкспорта):
- `admin.py`: `get_all_users_telegram_ids`, `check_user_still_eligible_for_no_sub_broadcast`, `get_user_ltv`, `get_average_ltv`, `get_gift_subscription`, `get_user_paid_subscription_history`. Новые с раунда 1: `get_payments_by_provider`, `get_daily_timeseries` — вызывающих нет после переработки дашборда.
- `beta_applications.py`: `has_applied`.
- `bypass_gift_links.py`: `get_bypass_gift_link_by_code`.
- `core.py`: `safe_get`, `_get_pool_safe`.
- `marketing_links.py`: `get_stats_link`, `get_promo_link`.
- `reconciliation.py`: `_bulk_fetch_panel_expires_at`, `_PANEL_FETCH_CONCURRENCY`, импорт `asyncio`. Осиротели, когда убрали часовую сверку «БД ↔ панель».
- `subscriptions.py`: `create_payment`, `update_payment_status`, `has_any_subscription`, `has_any_payment`, `has_active_special_offer`, `_log_vpn_lifecycle_audit_fire_and_forget`, `consume_promocode_atomic`, `is_user_first_purchase`.
  - `consume_promocode_atomic` — финансовая функция. Проверено, что промокоды списываются через `_consume_promo_in_transaction`: 5 мест вызова в `subscriptions.py` и `admin.py`.
- `traffic.py`: `clear_remnawave_premium_uuid`.
- `users.py`: `calculate_referral_percent`, `update_username`.
- Функции B5.6 (samopis) удалены ещё в раунде 1. `get_remnawave_premium_id` теперь жива (11 ссылок), её не трогали.

**B7 — Remnawave:**
- `remnawave_api.py`: `reset_user_traffic`, `enable_user`, `disable_user`, `extend_user_expiry`, `find_user_by_email`, `resolve_user_id` (плюс её пункт в докстринге модуля). Осиротевший импорт `urllib.parse.quote` тоже удалён.
- `remnawave_bypass.delete_bypass_user`, `remnawave_premium.get_premium_subscription_url` — каждая вместе со строкой в `__all__`.
- `remnawave_service.py`: `create_remnawave_user_bg`, `update_tariff`.

**B9 — прочее:**
- `pool_monitor.get_last_pool_wait_spike_monotonic` вместе с переменной, которую она возвращала. Без геттера переменная только записывалась.
- `system_state.create_default_system_state`.
- `admin/broadcast._safe_send`: живой путь — `_safe_send_with_buttons`.
- `recovery_premium._scan`, а за ним `database.get_premium_recovery_candidates` (второй порядок).
- `activation/service.py`: `is_activation_allowed`, `_update_subscription_activated`.
- `admin_alerts.py`: `alert_vpn_api_failure`, `alert_security_event`.
- `purchase_flow._device_limit_for`, `push_notifications.subscription_count`, `sub_aggregator.get_url`, `logging_helpers.log_operation`.
- `security.py`: `validate_message_text`, `require_ownership`, `log_security_error` (новая).
- `config.py`: `tariff_for_vpn_api` (samopis), `get_biz_price_stars`.
- Новые с раунда 1, `constants/loyalty.py`: `get_loyalty_status_names`, `get_loyalty_screen_attachment`. Второй порядок: `get_loyalty_photo_id`, `LOYALTY_IMAGES`, `LOYALTY_PHOTOS`, `_TIER_TO_IMAGE_KEY`.

**Группа A экранов** (`docs/screens/candidates.md`):
- **A1, мастер вывода средств.** Удалены:
  - `withdraw_start` — заглушка-алерт, кнопку убрали в `39f42c2a`;
  - `withdraw_confirm_amount`, `withdraw_final_confirm`, `withdraw_cancel` и `withdraw_back_to_*`;
  - весь `payments/withdraw_fsm.py` вместе с регистрацией роутера;
  - `WithdrawStates`.
  Доказательство: у `withdraw_start` нет производителя, а `WithdrawStates.withdraw_amount` нигде не выставлялся, поэтому шаги FSM были недостижимы.
- **A3** `setup_device` и **A4** `traffic_pay_card:`: нет производителей. Докстринг `traffic.py` поправлен.
- **A6** `admin:reissue_all_active` и `_go`: нет производителей. За ними ушли функции БД второго порядка: `get_all_active_subscriptions`, `reissue_subscription_key`, `get_active_subscription`, `update_subscription_uuid`.
- **A7** `noop_handler` в админке — затенён. `noop` раньше ловит `callbacks/navigation.py`, потому что роутер callbacks подключается перед admin.
- **A8, A9**: 7 мёртвых клавиатур и 9 копий админ-клавиатур в `common/keyboards.py`, плюс реэкспорты в `common/__init__.py`. Все админ-модули импортируют оригиналы из `admin/keyboards.py` (он не тронут). Мёртвый импорт `get_service_status_keyboard` в `navigation.py` удалён.
- **A10: 367 ключей i18n.**
  - Это 349 ключей из `dead_i18n.md` плюс ключи, которые использовал только удалённый выше код: тексты мастера `withdraw.*`, `traffic.pay_card` и т. п.
  - По неймспейсам: main 85, admin 62, referral 38, farm 31, profile 25, buy 17, setup 17, subscription 15, withdraw 13, broadcast 11, errors 10, traffic 9, instruction 8, connect 5, trial 4, common 3, get_key 3, lang 3, gift 2, reminder 2, biz 1, combo 1, share_discount 1, support 1.
  - Критерий «жив»: ключ встречается в кавычках (`'…'`, `"…"` или `` `…` ``) в любом отслеживаемом `.py/.ts/.tsx/.js/.mjs/.json/.sql/.html/.yml` вне `app/i18n/` и `docs/`, **или** начинается с префикса, который код собирает динамически. Такие префиксы: `buy.tariff_`, `help.faq_a`, `help.faq_q`, `lang.button_`, `payment.`, `provisioning_jobs.`, `public.`, `setup.combined_`, `setup.connect_`, `setup.download_`.
  - Ключи из переменных проверены вручную: `tpl["text_key"]`, `meta["desc_key"]`, `period_keys`, `error_keys`, `label_key`, `instruction_key`, `toast_key`, `notification_key`. Все берутся из литеральных таблиц в коде.
  - Расписание триала `get_notification_schedule()` возвращает `[]`. Финальное напоминание — литерал `trial.notification_71h`, он оставлен.
  - `LANGUAGES` нигде не перечисляется: `automated_notifications/helper.py` только проверяет вхождение.
  - После удаления скрипт находит 0 мёртвых ключей из 784.
- **B10.** `ruff check --select F401,F841 --fix`, только safe-фиксы:
  - неиспользуемые импорты;
  - `except … as e:` → `except …:`, если `e` не используется.
  - В каждом удалённом импорте `N` из модуля `M` скриптом искались `from M import … N`, `M.N` и строки патчей `"M.N"` в тестах. Единственное попадание — реэкспорт `SubscriptionServiceError` через `app/services/subscriptions/__init__.py`, поэтому этот файл исключён.

### Что оставлено и почему

| Что | Почему |
|---|---|
| `withdraw_approve:` / `withdraw_reject:` и функции БД `get/approve/reject_withdrawal_request` | единственный способ закрыть висящую заявку на вывод из старого сообщения админу. Дашборд только считает pending (`metricsApi.withdrawals_pending`), обработать заявку не может. Деньги |
| `database.create_withdrawal_request` | после удаления мастера вызовов **нет**. Но `candidates.md` A1 прямо требует таблицу и функции вывода не трогать (Compatibility > Cleanliness) → решение владельца |
| `remnawave_api.revoke_user_subscription` | 0 вызовов, но оставлена под фазу 3 (recon P1-4: отозванный юзер сохраняет доступ через sub-aggregator) |
| `platega_service.check_transaction_status` / `check_subscription_status` | 0 вызовов, оставлены под фазу 2 (`GET /transaction/{id}` перед выдачей) |
| `payments/verify_delivery.reset_legacy_alert_state` | вызывается только из тестов, но это хук изоляции состояния модуля для тестов |
| A2: biz-каталог `corporate_access_request`, `biz_country:` | biz-ветка сидит внутри живого хэндлера `tariff:`. Если убрать её, старые кнопки `tariff:biz_*` пойдут в обычный экран периодов, то есть поведение изменится. Нужен guard, это не чистое удаление → владелец (вместе с §C1 biz-меню) |
| A5: `/platega_sub_status` | нужна по `SCOPE.md` (живые подписки Platega). `pay:sbp_sub` уже удалён раньше |
| A11 и ключи `shop.*`, `premium.*` | 🔒 магазин |
| B10: `tests/`, все `__init__.py`, `main.py`, файлы магазина, `navigation.py`, `confirmation.py`, `subscriptions/service.py` | реэкспорты, side-effect импорт `button_defaults`, pytest-фикстуры, импортированные по имени, 🔒 магазин |
| B10: 67 unsafe F841 и 14 F811 | unsafe — это в основном `user = await database.get_user(...)` без использования результата. Удаление убирает вызов, это уже правка поведения, а не мёртвый код |

### Нужно решение владельца

1. **Старая админка в боте** (`candidates.md` §D) — **не удалена**, как и требовалось. `app/handlers/admin/`: **26 файлов, 16 471 строка, 246 хэндлеров** (`@…callback_query` / `@…message`). Дублирует дашборд. Войти в неё можно только через «чёрный ход» (`/promo_stats` → «Назад», чат `admin:chat`).
2. **`create_withdrawal_request`** — мёртв. Удалять вместе с решением по таблице заявок на вывод. Там же вопрос, как закрыть оставшиеся pending-заявки, если кнопок подтверждения в чате у админа уже нет.
3. **A2 biz-каталог и §C1 biz-меню**: сколько живых `biz_*` подписок? Если 0 — удалить одним пакетом вместе с guard'ом для старых кнопок `tariff:biz_*`.
4. **Фронт дашборда** (вне раунда, данные координатора): 24 неиспользуемые функции в `dashboard/src/lib/api.ts` и CSV-экспорт без кнопки в UI. `dashboard/mock-api.ts` — используемый dev-плагин Vite, оставить.
5. `revoke_user_subscription` и `platega_service.check_*` — удалить, если фазы 2 и 3 их не возьмут.

---

## Раунд 3 (2026-09-14): удаление неиспользуемых фич по решению владельца — выполнено

- **Решение владельца (2026-09-14, дословно):** «Бизнес удаляем, тарифы и тп не используются. Вывод также не используется, удаляем. В старой админке в боте оставить функцию «написать пользователю» и ссылку на дашборд, сброс пароля.» Этим закрыты пункты 1–3 из «Нужно решение владельца» раунда 2.
- База: `refactor/audit-2026-09` @ `1fef1c69` (включая исправления Remnawave 3.4.3). Не запушено, в `main` не мержено. Миграций нет, данные и таблицы не трогали.
- **Код и тесты (6 коммитов):** 84 файла, +1624 / −18764 (`git diff --shortstat 1fef1c69 e9177b13`).
- **Хэндлеры:** `@router.` 233 → 225. Все декораторы `@*router.callback_query/message/pre_checkout_query`: 451 → 220. `app/handlers/admin/`: 246 → 16.
- **Тесты:** база — 2765 passed / 20 skipped / 139 xfailed (до ветки, @ `8193790c`: 2738 / 20 / 139), после — 2736 passed / 20 skipped / 127 xfailed. Ушли тесты удалённого кода (список ниже). Добавлены тесты нормализации biz, легаси-ячейка матрицы и удаление юзера в панели. Strict-xfail стало на 12 меньше: 9 ячеек теперь проходят (см. «Тесты»), 3 ячейки bonus удалены вместе с фичей.

| Коммит | Что |
|---|---|
| `0f298b2e` | бизнес-тарифы; одна нормализация legacy `biz_*` → Plus |
| `7f47d914` | вывод средств |
| `5c4e24e3` | старая админка 1/3: кнопки рассылок → `app/handlers/payments/broadcast_offers.py`, хелперы рассыльщика → `app/services/broadcast_sender.py`; удалены мастер рассылок и кампания «Trial → −30%» |
| `8c0c8eaf` | старая админка 2/3: 20 модулей, `base.py` урезан до оставленного |
| `3e174282` | старая админка 3/3: мёртвое второго порядка (24 функции БД, 3 функции вне БД, `broadcast_service.py`, 16 FSM-состояний, 202 ключа i18n, паттерн `^admin_.*$`) |
| `e9177b13` | исправление: полное удаление юзера (дашборд, `DELETE /users/{id}`) удаляло сущности панели по несуществующему в 3.x пути `DELETE /api/users/delete/{id}` → `remnawave_api.delete_user` + тест |

### Как доказывали

1. Как в раунде 2: `git grep -nw` по каждому имени, `callback_data`, FSM-состоянию и ключу i18n (включая f-строковые префиксы), регистрации в `app/handlers/__init__.py`, `scripts/`, Dockerfile, CI, дашборду.
2. **Карта производителей** для каждого хэндлера `app/handlers/admin/`: кто шлёт кнопку или ставит состояние (дашборд, воркеры, алерты, провижининг, магазин, сама админка).
   - Алерты админу (`admin_alerts`, `admin_notifications`) идут без кнопок.
   - Кнопки на алертах есть только у магазина: `admin:chat` (оставлен), `apple_*` и `spotify_done:` (🔒, не тронуты).
   - Кнопки рассылок из дашборда обслуживались хэндлерами в `admin/broadcast.py`. Их перенесли без изменений.
3. **Скрипты сирот** (прогонялись до пустого результата):
   - что использовали удалённые модули (прочитаны из базы) и больше никто: реэкспорты `database`, ключи i18n, классы состояний;
   - имена верхнего уровня без ссылок сейчас, но со ссылками на базе;
   - реэкспорты `database/__init__.py`.

### Бизнес-тарифы

- **Удалено.**
  - `config`: biz в `TARIFFS`/`TARIFFS_STARS`, `BIZ_TARIFFS`, `is_biz_tariff`, `BIZ_COUNTRIES`, `BIZ_TIER_SPECS`, `get_biz_price`.
  - Каталог и покупка: `corporate_access_request`, `biz_country:`, выбор страны и множитель страны в цене, FSM `choose_biz_tier`/`choose_country`.
  - Меню biz: `biz_profile`, `biz_ecosystem`, `biz_control_panel`, `biz_copy_login`/`_password` и экраны, куда можно было попасть только из него: `menu_settings`, `menu_ecosystem`, `menu_about`. `/info` и `about_privacy` остались.
  - Ветки «Business» в текстах оплаты, профиля и активации; метки biz в дашборде; 43 ключа i18n.
- **Совместимость.** Одно правило `app/services/tariffs.normalize_tier()`: любой сохранённый `biz_*` читается как `plus`. Для `payments.tariff` вида `biz_team_30` работает `normalize_payment_tariff()` → `plus_30`. Правило применяется:
  - в `database.core._normalize_subscription_row`, то есть в каждом `get_subscription*`;
  - в `tariffs.tariff_key` / `for_grant`;
  - на входе `grant_access`;
  - в сырых чтениях `subscription_type`: перевыпуск, `finalize_purchase`, активация, `activation_worker`, сверка, `panel_traffic_audit`, список юзеров в дашборде;
  - в последнем платеже для автопродления;
  - в группировке метрик и выручки.

  Автопродление легаси biz — обычный Plus по **текущей цене Plus**. Проверено ячейкой матрицы `test_legacy_biz_row_renews_as_plus`: +30 дн., +10 ГБ, один платёж, цена Plus. С флагом off `subscription_type` пишется как basic — это известный T0-AUTORENEW-PLUS, он зафиксирован в тесте.
- **Оставлено.** SQL `IN (...)`-списки и DDL с перечнем `biz_*`: они распознают старые строки, а миграции неизменны.

### Вывод средств

- **Удалено:**
  - `withdraw_approve:` / `withdraw_reject:` (производитель ушёл в раунде 2) и `MIN_WITHDRAW_RUBLES`;
  - 4 функции БД;
  - счётчик заявок в плитке «Обязательства» (`revenue.liabilities`, `metricsApi`, `Money.tsx`);
  - ключи `withdraw.*`;
  - в RU-текстах реферальной программы — только фразы и блоки про вывод на карту или СБП. В EN таких фраз не было.
- **Оставлено:** таблица `withdrawal_requests`, история в `balance_transactions`, пополнение, оплата с баланса, кэшбэк.
- **Если в таблице есть заявки в статусе `pending`**, их больше нигде не видно и нельзя закрыть кнопкой. Проверить SQL-запросом в проде и закрыть вручную.

### Старая админка: что осталось в боте

- `/admin`: «🛡 Открыть дашборд» (magic-link, `DASHBOARD_BASE_URL`), «💬 Написать пользователю», «🔄 Сбросить пароль» / «🆕 Установить пароль».
- `admin:reset_password` / `_cancel` / `_confirm`: без изменений, тот же `@admin_only` и `admin_auth.clear_credentials`.
- `admin:chat` → `AdminChat.waiting_for_user_id` → `AdminChat.chatting`: без изменений. Кнопка есть в `/admin` и на алертах заказов Steam, Apple и Spotify.
- `admin:main` показывает меню `/admin` и выходит из чата (раньше это был главный экран старой админки).
- `/platega_sub_status` оставлен: в дашборде есть только счётчики по статусам, списка живых подписок нет (SCOPE.md).
- 🔒 `apple_id_delivery.py`, `spotify_delivery.py` не тронуты.

### Эквиваленты в дашборде

| Функция старой админки | Дашборд |
|---|---|
| Главный экран, система, статус компонентов | Overview, Health (`/metrics/overview`, `/metrics/health`) |
| Поиск юзера, карточка, история | Users (`/users/search`, `/users/{id}`, `/history`) |
| Выдать дни / минуты, сменить тариф, отозвать | UserActions (`/grant`, `/grant-minutes`, `/switch-tariff`, `/revoke`); нет уведомления юзеру и единиц «часы/месяцы» |
| VIP, скидка на подписку, скидка на ГБ, баланс ±, удаление юзера | UserActions, DiscountCard, UserBalanceCard, UserDangerZone |
| Инцидент-режим | Service (`/incident`) |
| Промокоды (создание, статистика, деактивация) | PromoCodes (`/promo`) |
| Статистика, метрики, аналитика, покупки по тарифам, рефералы | Money, Statistics, Subscribers, Referrals |
| Рассылки: мастер, сегменты, кнопки, «без подписки», удаление у юзеров | Broadcasts (`POST /broadcasts`, `/segments`, `/delete-from-users`) |
| Гифт-ссылки на ГБ | BypassGifts (`/bgift/*`) |
| Экспорт CSV | Users, Subscribers (`/export/*.csv`) |
| `/pending_activations`, аудит | Service (`/activations`), Audit (`/audit/recent`) |
| Центр уведомлений (подписки) | AutomatedNotifications |
| Stage: список юзеров | Users (без stage-фильтра) |

### Что потеряно (в дашборде нет)

- Перевыпуск VPN-ключа юзеру (карточка, `/reissue_key`) и массовый перевыпуск. В дашборде «перевыпуск» меняет только ссылку агрегатора.
- Массовый бонус (дни или ГБ на сегмент).
- Выдача и списание ГБ обхода конкретному юзеру (в дашборде только просмотр).
- A/B-рассылки и их статистика; готовая рассылка «Тех. работы»; шаблоны промо и удержания; акция «x2 кэшбэк» (`cashback_promotions`); кампания «Trial → −30%».
- Консоль шторма фермы; «Добавить всех в Remnawave» (у backfill в дашборде другая логика); откат premium ×10y; аудит активных подписок и аудит дат в БД (в SPA не выведены).
- Список QoDev (`site_linked`); тестовое меню уведомлений.
- `/id` (эхо file_id); `/aggregator`, `/aggstats`, `/aggflush`, `/aggcheck`; `/wata_status`. В Health виден только `is_enabled`.
- Уведомление юзеру при выдаче, смене тарифа и отзыве доступа.

### Тесты

- Админские выдачи дней теперь идут через эндпоинт дашборда `routes/users.user_grant` / `user_grant_minutes` (он вызывает тот же `admin_grant_access_atomic`). Переписаны `payment_core_harness`, `test_payment_matrix`, `test_payment_core_t13`, характеризация. У пути дашборда нет лишнего `renew_remnawave_user_bg`, который делал бот. Поэтому T0-ADMIN-GB с флагом off теперь падает только для нового юзера, а характеризация «продление без ГБ» проходит. Маркеры обновлены.
- Удалены тесты удалённого кода:
  - t13: двойной клик (FSM-claim бота), быстрые минуты, NameError с флагом off, no-claim guard;
  - t16: раздел admin bonus;
  - матрица: вход «grants» (массовый бонус) и его replay;
  - share-тексты: `_admin_username_line`;
  - biz: ячейки автопродления заменены одной легаси-ячейкой.
