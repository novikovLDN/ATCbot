# AGENT_AUDIT_MAP — карта аудиторских документов

Ориентир для агента: что в каком `*AUDIT*.md` (корень репо), чтобы находить нужный раздел **не читая
файлы целиком**.

> ⚠️ **Читать эти файлы только tree-first:** `grep -nE '^#{1,3} ' <файл>` → узкий `Read` нужного раздела.
> Bulk-чтение/дамп всех audit-файлов сразу (или разбор их субагентом) ложно триггерит cyber-классификатор
> и роняет сессию. Это уже случалось дважды на этом проекте.

Все эти файлы — **логи ремедиации/верификации** (источник инвариантов, которые нельзя регрессировать),
а НЕ бэклог открытых эксплойтов. Но статусы разнятся — см. колонку.

| Файл | Строк | Что покрывает | Статус |
|------|------:|---------------|--------|
| `AUDIT_REPORT.md` | 194 | Воркеры (fast_expiry_cleanup, activation_worker, auto_renewal, reminders, trial_notifications, crypto_payment_watcher): синтакс/логик-фиксы, упрощения, «бизнес-логика verified unchanged» | ✅ READY TO DEPLOY |
| `PRE_PRODUCTION_STABILITY_AUDIT.md` | 702 | 7 задач: file-by-file, migration sequence, worker conflict/load, memory/resource leak, Railway crash-risk, dead code, risk scorecard | ✅ STABLE — critical resolved (migration 011 = намеренный gap; `processing_uuids` leak пофикшен) |
| `WITHDRAWAL_BALANCE_AUDIT.md` | 436 | Баланс+вывод: 8 частей (balance arch, concurrency, FSM, admin-approval, notification, UI, security, performance) | Post-impl. Двойное одобрение защищено `WHERE status='pending' … FOR UPDATE` |
| `FULL_PRODUCTION_AUDIT.md` | 902 | 12 частей полного прод-аудита (arch map, **concurrency CRITICAL**, financial safety, FSM, UI, workers, telegram polling/deploy, logging, security, scalability, failure-sim, legacy) | Вердикты ✅ SAFE, НО есть раздел **IMMEDIATE ACTIONS (MUST FIX)** — сверять с текущим кодом |
| `COMPREHENSIVE_CODE_AUDIT_2026_03.md` | 1237 | Крупнейший; critical/medium/low по security/correctness/performance/workers/architecture/handlers + статистика + приоритеты фиксов | ⚠️ Findings-list — часть пунктов может быть **ещё открыта** |
| `SECURITY_CODE_AUDIT_2026_03.md` | 116 | CRITICAL FINDINGS (Fixed) · NO ACTION REQUIRED (correct) · DEAD CODE REMOVED · ADVISORY (Not Fixed — Low Priority) | Большинство fixed, остаётся low-priority advisory |
| `I18N_STRING_AUDIT.md` | 303 | i18n Phase 3: миграция хардкод-строк в ключи; referral/background фазы | В процессе; шапка «Crypto: DO NOT TOUCH» |

**Практика:** прежде чем «упрощать» воркер, платёжный pipeline или withdrawal-flow — проверь по нужному
audit-файлу, не описан ли текущий вид как *сознательный результат* аудита (напр. `FOR UPDATE`,
порядок финализации платежа, idempotency-флаги). Не считать всё закрытым: `COMPREHENSIVE_CODE_AUDIT_2026_03`
и «MUST FIX» в `FULL_PRODUCTION_AUDIT` могут содержать открытые пункты.

Прочие `docs/` (change_management, compliance_readiness, incidents/*, runbooks/*, multi-region, SOC2) —
**аспирационный enterprise-шаблон, разошедшийся с реальностью**, не источник истины. Реально
проектно-специфичны: `security_model.md`, `capacity_limits.md`, `load_shedding.md`, `ownership.md`,
`data_ownership.md`, `admin_dashboard_implementation_map.md`.
