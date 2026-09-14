# Кандидаты на удаление и упрощение экранов

- Дата: 2026-09-13 · База: `refactor/audit-2026-09` @ `fe4f1efc` · git-даты по прод `cf8205fc`
- Удаляем **только после «ок» владельца**, пакетами (1 пакет = 1 коммит), по правилам `docs/audit/SCOPE.md`. 🔒 магазин — только с отдельного разрешения.
- **Общий риск для всех пунктов:** кнопки в **старых сообщениях** у юзеров продолжают нажиматься. После удаления хэндлера такой клик получает «кнопка устарела» (catch-all `app/core/unknown_message_filter.py:38`). Деньги и доступ не затрагиваются.
- «Строк» — строки самих хэндлеров (от декоратора до конца функции) плюс явно названные хелперы. Реальный выигрыш больше за счёт i18n и мёртвых клавиатур.

---

## A. Можно удалить сразу (сироты и давно скрытое)

| # | Что | Доказательство | С какого времени | Риск / что сломается | Строк |
|---|---|---|---|---|---:|
| A1 | **«Вывод средств» — вся цепочка:** `withdraw_start`, `process_withdraw_amount`, `withdraw_confirm_amount`, `process_withdraw_requisites`, `withdraw_final_confirm`, `withdraw_cancel` (+ `withdraw_back_to_*`), `withdraw_approve:`, `withdraw_reject:` (`callbacks/payments_callbacks.py:306-457`, `payments/withdraw_fsm.py` целиком) | 1) `withdraw_start` никто не производит (grep без `F.data` = 0). 2) `set_state(WithdrawStates.withdraw_amount)` не встречается нигде, поэтому `process_withdraw_amount` недостижим. 3) Остальные шаги и кнопки админу «Подтвердить/Отклонить» идут только из этой цепочки. 4) Сам `withdraw_start` — заглушка-алерт «обратитесь в поддержку». 5) i18n `profile.withdraw_funds`, `withdraw.amount_prompt`, `withdraw.admin_*` мёртвые | кнопку убрали в `39f42c2a` (2026-03-16 «declutter main screen and profile UI») | низкий. Таблицу заявок в БД и `database`-функции не трогаем (Compatibility > Cleanliness). Проверить, что заявки на вывод не создаются из дашборда | ≈ 250 + `withdraw_fsm.py` 159 |
| A2 | **Каталог «🏢 Для бизнеса»:** `corporate_access_request`, `biz_country:` + biz-ветка в `callback_tariff_type` (`payments/callbacks.py:437-455`) | вход-кнопка `buy.corporate_button` мёртвая (i18n не используется). `corporate_access_request` производят только сами biz-экраны (`:449`, `:563`) — замкнутый цикл. Бизнес-тарифы больше не продаются (SCOPE.md) | кнопку убрали в `cfbf8cfe` (2026-04-13 «Complete UX overhaul: all 5 user flows rewritten»): в диффе удалены `buy.corporate_button` + `callback_data="corporate_access_request"` | низкий. **Не трогать** продление легаси `biz_*` (auto_renewal, считается как Plus по SCOPE.md) и biz-меню (§C1) | ≈ 125 |
| A3 | **«Настроить устройство»** `setup_device` (`callbacks/navigation.py:872-913`) | производителя нет. i18n `setup.device_button`, `connect.setup_device_button` мёртвые. Экран — третья копия клавиатуры выбора устройства | `5c0f8f92` (2026-04-05 «Simplify connect flow: 3 taps instead of 5») | низкий | ≈ 42 |
| A4 | **«Оплатить картой» у пакета ГБ** `traffic_pay_card:` (`traffic.py:842-926`) | экран пакета производит только `traffic_pay_wata:` и `traffic_pay_sbp:` (`traffic.py:817,824`). i18n `traffic.pay_card` мёртвый | `ec7856c4` (2026-08-30 «collapse traffic-pack payment to 2 clean buttons») | низкий. Сверить, что финализация `traffic_pack` не завязана на этот хэндлер (он только создаёт счёт) | ≈ 85 |
| A5 | **СБП-подписка Platega** `pay:sbp_sub` (`callbacks/payments_callbacks.py:1663-1799`) + команда `/platega_sub_status` (`admin/base.py:141`) | кнопка закомментирована (`payment_method_selection.py:73-81`). По SCOPE.md код рекуррента Platega удаляется целиком | `79c71a97` (2026-08-30 «hide SBP-subscription») | средний: удаляется вместе с пакетом «Platega recurring» платёжного ядра (страховка «любой колбэк подписки → алерт» остаётся) | ≈ 195 |
| A6 | **Массовый перевыпуск ключей в админке** `admin:reissue_all_active` → `admin:reissue_all_active_go` (`admin/base.py:386-526`) | кнопки `admin:reissue_all_active` нет нигде. `_go` производит только сирота | — | низкий. Легаси-операция времён Xray | ≈ 140 |
| A7 | **Дубль `noop`** в админке `noop_handler` (`admin/base.py:970`) | `noop` уже ловит `callbacks/navigation.py:39`. Роутер callbacks регистрируется раньше admin (`app/handlers/__init__.py`), поэтому этот хэндлер не срабатывает никогда | — | нулевой | ≈ 8 |
| A8 | **Мёртвые клавиатуры в `common/keyboards.py`:** `get_profile_keyboard_old` (21), `get_tariff_keyboard` (25, производит `tariff_type:`, у которого нет хэндлера), `get_service_status_keyboard` (14), `get_broadcast_type_keyboard` (9), `get_connect_button` (8), `get_vpn_key_keyboard` (7), `get_profile_keyboard_with_copy` (3) | 0 вызовов (скрипт живости: имя не упоминается вне своего тела) | — | нулевой. Убрать из реэкспорта `common/__init__.py` | ≈ 87 |
| A9 | **Копии админ-клавиатур в `common/keyboards.py`** (`get_admin_dashboard_keyboard`, `get_admin_user_keyboard`, `get_admin_user_keyboard_processing`, `get_admin_back_keyboard`, `get_admin_export_keyboard`, `get_broadcast_*_keyboard` ×3, `get_ab_test_list_keyboard`; `common/keyboards.py:593-758`) | все админ-модули импортируют эти функции из `app/handlers/admin/keyboards.py`. Копии в `common` только реэкспортируются из `common/__init__.py` и больше нигде не импортируются | — | нулевой | ≈ 114 |
| A10 | **349 мёртвых ключей i18n** | см. [dead_i18n.md](dead_i18n.md) | — | нулевой (`get_text` не падает), удалять из `ru.py` и `en.py` одновременно | ≈ 700 строк словарей |
| A11 🔒 | `stars_pay:balance` (`payments/telegram_stars_purchase.py:364`) | кнопку «Оплатить балансом» для Stars убрали | `af049828` (2026-05-28) | **магазин** — только с разрешения владельца | 13 |

**Итого A (без магазина):** ≈ 1 050 строк хэндлеров + `withdraw_fsm.py` + ≈ 200 строк клавиатур + словари.

---

## B. Дубли — выбрать один

| # | Дубль | Где | Рекомендация | Строк уйдёт |
|---|---|---|---|---:|
| B1 | **`/buy` против «Купить/Продлить VPN»** — разные первые экраны | `payments/buy.py:17` против `payments/callbacks.py:67` | канон — `callback_buy_vpn`; `/buy` вызывает его. Подробно в [inconsistencies.md §1](inconsistencies.md#1-покупка-и-продление) | ~0 (правка, не удаление) |
| B2 | **Два экрана «Инструкция»:** старый `_open_instruction_screen` (`/instruction`, `menu_instruction`, алиас `instruction` без производителя, `get_instruction_keyboard`) против нового выбора устройства `connect_instruction` | `common/screens.py:227`, `navigation.py:371-376` против `navigation.py:412` | канон — `connect_instruction`; `/instruction` вызывает его; в уведомлении о перевыпуске ключа (`common/keyboards.py:620`) заменить `menu_instruction`→`connect_instruction`, `copy_vpn_key`→ убрать | ≈ 60 |
| B3 | **Заглушка «ключи больше не отправляются»** `copy_key`/`copy_vpn_key` (`navigation.py:380-395`) | `copy_key` производит только мёртвая `get_profile_keyboard_old`; `copy_vpn_key` — только уведомление о перевыпуске | удалить вместе с B2 | ≈ 16 |
| B4 | **Три копии клавиатуры «выбор устройства»** | `navigation.py:442-457`, `user/connect.py:29-43`, `navigation.py:~900` (сирота A3) | одна функция `device_select_keyboard()`; `/connect` вызывает `connect_instruction` (сейчас он пропускает синхронизацию Remnawave) | ≈ 30 |
| B5 | **Два потока покупки ГБ:** bypass-only (`buy_bypass_only/_extended/_pack`, `bypass_pay_sbp/_wata`) против подписчика (`buy_traffic/_extended/_pack`, `traffic_pay_sbp/_wata`) | `traffic.py:65-240` против `traffic.py:643-1105` | один поток с флагом `bypass_only` | ≈ 300 (из ≈ 690) |
| B6 | **Смена тарифа двумя путями:** `switch_tariff_menu`/`switch_tariff:` (экран А) против кнопок «Перейти на Plus/Basic» на экране тарифов (`tariff:`) | `payments/callbacks.py:139-338` против `:345` | оставить один путь с правдивым текстом (см. inconsistencies.md §1) | ≈ 190 или правка текстов |
| B7 | **Политика конфиденциальности:** `about_privacy` против «Правил» (`menu_legal`, `/docs`) | `navigation.py:250` против `common/screens.py:944` | канон — «Правила» | ≈ 15 |
| B8 | **`biz_copy_login` и `biz_copy_password`** копируют один и тот же `vpn_key` | `navigation.py:182-207` | одна кнопка (или удалить вместе с biz-меню, §C1) | ≈ 13 |
| B9 | **Три воронки настройки:** `setup_step1/2` + `setup_manual/qr*`, `bypass_setup_open/_manual`, `bgift_setup/_step1/_step2` | `navigation.py:482-1550`, `callbacks/bypass_setup.py`, `user/bypass_gift_setup.py` | общий движок шагов с параметром «что подключаем» — при переписывании экранов | ≈ 300+ (при переписывании) |
| B10 | **Способы оплаты скопированы под каждый продукт:** VPN `pay:*`, пополнение `topup_*`, подарок `gift_pay:*`, ГБ `traffic_pay_*` и `bypass_pay_*`, прокси `proxy_pay_sbp`, ферма `farm_shield_sbp`, магазин 🔒 | `callbacks/payments_callbacks.py`, `callbacks/gift.py`, `traffic.py`, `proxy.py`, `game.py` | общий экран «способ оплаты» поверх платёжного ядра (фаза 2) — это не удаление, а упрощение | ≈ 1 500+ (при переходе на ядро) |
| B11 | **«Моя подписка» и «Профиль»** оба показывают тариф и дату | `common/screens.py:812` против `:401` | решить с владельцем, какой экран главный | — |

---

## C. Проверить с владельцем (есть в коде или меню, но выглядит заброшенным)

| # | Что | Факты | Вопрос |
|---|---|---|---|
| C1 | **Biz-меню** (`biz_profile`, `biz_ecosystem`, `biz_control_panel`, `biz_copy_*`) и доступные только из него **«⚙️ Настройки» → «Экосистема» → «О сервисе»** (`menu_settings`, `menu_ecosystem`, `menu_about`, `about_privacy`) | показывается только юзерам с активной `biz_*`-подпиской (`common/keyboards.py:102-103`). Бизнес больше не продаётся. Обычный юзер эти экраны не видит (кроме `/info`) | Сколько активных `biz_*` подписок? `SELECT count(*) FROM subscriptions WHERE subscription_type LIKE 'biz_%' AND expires_at > now()`. Если 0 — удалить (≈ 140 строк) |
| C2 | Скрытые команды **`/main`**, **`/white`** | нет в `set_my_commands` (`main.py:552-565`) | Нужны? Добавить в меню или удалить |
| C3 | **`/hwadd`** «📲 Добавить устройство» | ведёт на выбор QR с захардкоженной платформой `ios`; «Мои устройства» в профиле — другой экран | Оставить команду? Переименовать? |
| C4 | **`/support`** | в меню команд, хэндлера нет | Добавить `/support` → Помощь или убрать из меню |
| C5 | **Скидки −15 %:** «Продлить со скидкой 15 %» в меню, `trial_discount_15`, `paid_discount_15` | живые (кнопки в меню и уведомлениях), но создают отдельные сообщения и ведут мимо «Управления подпиской» | Акция ещё нужна? |
| C6 | **Разовые сценарии рассылок:** `broadcast_gift_1m`, `bcg3m:*`, `broadcast_gift_1y_40` + `bcg1y40:*`, `broadcast_gift_combo:`, `broadcast_gift_reveal:` + `gift_reveal_pct:`, `broadcast_promo_buy:`, `broadcast_promo_traffic*`, `promo_trial_claim` (всё в `admin/broadcast.py:317-1470`, `admin/promo_trial.py`) | кнопки остаются в старых рассылках, часть до сих пор ставится из дашборда (`routes/broadcasts.py:821-936`) | Какие кампании ещё запускаются? Остальные — в пакет удаления (≈ 1 200 строк) |
| C7 | **Экосистема → «✍️ Трекер Only»** (внешняя ссылка) | только в biz-настройках | Актуально? |
| C8 | **MT Proxy** (`proxy_menu`, `proxy_open`, `proxy_pay_sbp`) | живой продукт (кнопка на экране тарифов и в «Моей подписке») | Продаётся? |
| C9 | **Stars в магазине 🔒** | вход закомментирован, код ветки жив (7 хэндлеров, ≈ 330 строк) | Вернуть или удалить (магазин — по вашему решению) |
| C10 | **Stage-only экраны:** `stage_gate:dev`, `admin:stage_users*` | работают только на stage | Оставить (не мешают), вынести в отдельный модуль |
| C11 | Экран «О сервисе» только через `/info` | кнопкой доступен лишь из biz-меню | Нужна кнопка в «Помощи» или экран лишний? |

---

## D. Админ-экраны, дублирующие дашборд

### Главный факт

С **2026-06-05** (`03ba5767` «feat(auth): dashboard login/password…») команда `/admin` строит сообщение `_build_admin_menu` (`admin/base.py:27-70`) всего с двумя кнопками: «🛡 Открыть дашборд» и «Сбросить/Установить пароль». В docstring прямо сказано: *«The full set of in-bot admin tools has moved to the web dashboard»*.

Старое меню админки (`get_admin_dashboard_keyboard`, `admin/keyboards.py:11`) и **220 хэндлеров (≈ 9 900 строк; вместе с клавиатурами и хелперами файлы `app/handlers/admin/` — 16 774 строки)** остались в коде. Попасть в них можно только через «чёрный ход»:
- кнопка «Назад» у **`/promo_stats`** (`admin/stats.py:30` → `admin:main`);
- «❌ Отмена» / «← Админ-панель» в **чате с пользователем** `admin:chat` (`admin/base.py:1050,1122`), куда ведут кнопки из уведомлений о доставке магазина (Apple ID, Spotify, Steam).

### Разделы старой админки → аналог в дашборде

| Раздел в боте | Callback-префиксы / команды | Хэндлеров · строк | В дашборде | Рекомендация |
|---|---|---|---|---|
| Статистика, аналитика, метрики, покупки по тарифам | `admin:stats`, `admin:analytics*`, `admin:metrics`, `admin:growth*`, `admin:purchase_stats`, `admin:dashboard` | 9 · ≈ 560 | `routes/stats.py` (`/overview`, `/revenue`, `/daily`, `/hourly`, `/purchase-breakdown`, `/by-provider`) | удалить |
| Рефералы (статистика, поиск, история, топ) | `admin:referral_*` | 7 · ≈ 1 080 | `routes/referrals.py` | удалить |
| Пользователь: поиск, карточка, история, выдача/отзыв доступа, VIP, скидки, смена тарифа, баланс, перевыпуск, удаление | `admin:user*`, `admin:show_user:`, `admin:grant*`, `admin_grant_*`, `admin:revoke*`, `admin:vip_*`, `admin:discount_*`, `admin:tdiscount_*`, `admin_switch_*`, `admin:credit_*`, `admin:debit_*`, `admin:balance_management`, `admin:delete_user*`, `admin:user_reissue:` | 63 (53 callback + 10 FSM-ввода) · ≈ 2 650 | `routes/users.py`: `/grant`, `/grant-minutes`, `/revoke`, `/vip`, `/discount`, `/traffic-discount`, `/switch-tariff`, `/balance`, `/history`, `DELETE /{id}`, `/reissue-aggregator` | удалить после сверки, что в дашборде есть **перевыпуск ключа** (в боте `admin:user_reissue:` и `/reissue_key`) |
| Трафик юзера (добавить/убавить ГБ) | `admin:traffic*` | 6 · ≈ 170 | карточка юзера в дашборде (`/traffic`) | сверить, удалить |
| Рассылки (создание, A/B, сегменты, удаление у юзеров) | `admin:broadcast`, `broadcast:*`, `broadcast_test_type:`, `broadcast_segment:`, `broadcast_btn:`, `promo_duration:`, `/notify_no_subscription` | 22 · ≈ 1 070 | `routes/broadcasts.py` (создание, сегменты, `/delete-from-users`, `/analytics`, `/schedule`) | удалить. **Пользовательские** кнопки рассылок (§C6) — отдельно |
| Центр уведомлений (промо, ретеншн, x2-кешбэк, подписочные) | `admin:notifications`, `admin:notif_*`, `admin:promo_tpl:`, `admin:ret_*`, `admin:referral_x2_*` | 17 · ≈ 700 | `routes/automated_notifications.py` — частично | сверить x2-кешбэк, затем удалить |
| Гифт-ссылки на ГБ | `admin:bgift*` | 15 · ≈ 400 | `routes/bgift.py` | удалить |
| Промокоды | `admin:create_promocode`, `admin:promocode_*`, `admin_promo_stats`, `admin:deactivate_promo*`, `/promo_stats` | 12 · ≈ 380 | `routes/promo.py` | удалить |
| Экспорт CSV | `admin:export*` | 2 · ~100 | `/users.csv`, `/subscriptions.csv` | удалить |
| Аудит-лог | `admin:audit`, `/admin_audit` | 2 · ≈ 190 | `/audit-log` | удалить |
| Инцидент (баннер) | `admin:incident*` | 4 · ≈ 130 | `routes/incident.py` | удалить |
| Бонус-раздача | `admin:bonus*` | 6 · ≈ 200 | не найдено | уточнить, перенести или оставить |
| Шторм фермы | `admin:storm*` | 3 · ≈ 65 | по коммиту `c77125d3` («dashboard wiring») | сверить, удалить |
| **Разовые инструменты** (фиксы 2026-06…08) | `admin:audit_db_dates*` (239), `admin:premium_recovery*` (93), `admin:audit_subs*` (224), `admin:promo_trial*` (кампания, 316) | 19 · ≈ 930 | частично `routes/reconciliation.py`, `traffic_audit.py` | удалить (задачи выполнены), кроме `promo_trial_claim`, пока живы старые рассылки |
| Легаси ключи Xray / QoDev / системные тесты | `admin:keys*` (252), `admin:qodev` (56, site_sync удалён в `c5f2b1e7`), `admin:system`, `admin:test_menu`, `admin:test:*`, `admin:remnawave_mass_provision` | 8 · ≈ 620 | — | удалить keys/qodev/tests; `system`/mass provision — уточнить |
| `/pending_activations`, `/reissue_key` | команды | 2 · ≈ 130 | `routes/activations.py` (`/pending`, `/retry`) | сверить перевыпуск, удалить |

### Что оставить в боте

- `/admin` (вход в дашборд) и сброс пароля: `admin:reset_password*`.
- 🔒 Доставку магазина: `apple_id_delivery.py`, `spotify_delivery.py`.
- Чат с пользователем `admin:chat` — используется из уведомлений доставки магазина. Кнопку «← Админ-панель» заменить на «Готово».
- Диагностику: `/aggregator`, `/aggstats`, `/aggflush`, `/aggcheck`, `/wata_status`, `/id` + автоэхо file_id.

Размеры в таблице — строки самих хэндлеров, замеренные скриптом (от декоратора до конца функции).

**Оценка пакета D:** хэндлеры разделов, дублирующих дашборд, — **≈ 8,5 тыс. строк**. Вместе с их клавиатурами (`admin/keyboards.py`) и хелперами из `app/handlers/admin/` уйдёт ориентировочно **11–13 тыс. строк из 16,8 тыс.** Это самый крупный выигрыш по объёму в проекте. Риск низкий: админ один, дашборд уже основной инструмент. Но нужно пройти таблицу выше вместе с владельцем («этим пользуюсь в боте?»).
