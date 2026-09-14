# Инвентаризация экранов бота — полная таблица

- **Обновлено 2026-09-14, раунд 3** ([01_dead_code.md](../audit/01_dead_code.md#раунд-3-2026-09-14-удаление-неиспользуемых-фич-по-решению-владельца--выполнено)): удалены бизнес-тарифы, вывод средств и старая админка в боте — их строки убраны; кнопки рассылок переехали в `app/handlers/payments/broadcast_offers.py`; строки хэндлеров, удалённых ещё раньше, тоже убраны. Остальные колонки — как на 2026-09-13.
- Дата: 2026-09-13 · База: `refactor/audit-2026-09` @ `fe4f1efc` (прод-код = `origin/main` `cf8205fc` + коммиты платёжного ядра за флагом)
- «Последний коммит» — самый свежий коммит, затронувший строки хэндлера, по `git blame` на **прод** `cf8205fc` (коммиты рефакторинга не учитываются).
- «Экран / действие» — первая строка docstring хэндлера (как её написал автор кода). «i18n» — первый ключ `get_text(...)` в теле хэндлера (часто это заголовок экрана, но не всегда).
- «Откуда производится кнопка» — файлы, где есть `callback_data`, совпадающий с фильтром хэндлера (только из живых функций). `клавиатуры меню/профиля` = `app/handlers/common/keyboards.py`, `общие экраны` = `app/handlers/common/screens.py`.
- Легенда статусов — в [README](README.md#легенда). Сводка и кандидаты — [candidates.md](candidates.md). Расхождения точек входа — [inconsistencies.md](inconsistencies.md).

На 2026-09-13 было **460 функций-хэндлеров** (464 регистрации `@router.*`, у трёх хэндлеров по несколько декораторов): 388 callback, 75 message, 1 pre_checkout. После раунда 3 в таблице **223** строки.

## Содержание

- [Старт, капча, язык](#старт-капча-язык) — 7 хэндлеров · ✅4 🔗3
- [Главное меню и навигация](#главное-меню-и-навигация) — 6 хэндлеров · ✅4 🙈2
- [Покупка VPN](#покупка-vpn) — 18 хэндлеров · ✅16 🔗2
- [Оплата VPN (способы оплаты)](#оплата-vpn-способы-оплаты) — 9 хэндлеров · ✅4 ⚙️5
- [Оплата (служебные)](#оплата-служебные) — 3 хэндлеров · ✅3
- [Подключение](#подключение) — 20 хэндлеров · ✅14 🔗6
- [Профиль и устройства](#профиль-и-устройства) — 9 хэндлеров · ✅9
- [Трафик обхода (ГБ)](#трафик-обхода-гб) — 12 хэндлеров · ✅11 🙈1
- [Баланс: пополнение](#баланс-пополнение) — 8 хэндлеров · ✅8
- [Подарки](#подарки) — 12 хэндлеров · ✅11 🔗1
- [Рефералы](#рефералы) — 5 хэндлеров · ✅4 🔗1
- [MT Proxy](#mt-proxy) — 3 хэндлеров · ✅2 🔗1
- [Игры](#игры) — 20 хэндлеров · ✅20
- [Помощь и информация](#помощь-и-информация) — 8 хэндлеров · ✅8
- [Рассылки — пользовательские кнопки](#рассылки--пользовательские-кнопки) — 19 хэндлеров · 🔗19
- [🔒 Магазин](#-магазин) — 53 хэндлеров · 🙈🔒7 👻🔒1 🔒45
- [🛠 Админка в боте](#-админка-в-боте) — 9 хэндлеров · 🛠9
- [Служебные](#служебные) — 2 хэндлеров · ✅2

## Старт, капча, язык

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Смена языка из настроек / кнопки «Изменить язык» | `lang.changed_toast` | `lang_`… | `app/handlers/callbacks/language.py:129` `callback_language` | language.py, клавиатуры меню/профиля | ✅ | 2026-08-06 `057dd6ad` feat(start): язык-picker → активация триала → эк |  |
| Изменить язык — фото-экран, как /start язык-picker (ru/en) | `start_lang.title` | `change_language` | `app/handlers/callbacks/language.py:35` `callback_change_language` | navigation.py, клавиатуры меню/профиля | ✅ | 2026-08-13 `2c2a9a0a` style(buttons): партия 1 массового апдейта цвето |  |
| Обработчик команды /language — открывает экран выбора языка | `lang.select` | /language | `app/handlers/user/language_commands.py:20` `cmd_language` | команда | ✅ | 2026-04-19 `0640e773` fix: add parse_mode="HTML" to all edit_text/send |  |
| cmd_start | `main.welcome` | /start | `app/handlers/user/start.py:28` `cmd_start` | команда | ✅ | 2026-08-19 `4cbd24b9` feat(captcha): картинки на каждое животное + кно |  |
| /start язык-picker → сохранить язык → показать главное меню | — | `start_lang_ru`, `start_lang_en` | `app/handlers/callbacks/language.py:84` `callback_start_language` | /start | 🔗 | 2026-08-06 `8e71161f` revert(start): убрать активацию триала и экран с | Язык-пикер после каждого /start |
| Проверка ответа на анти-бот капчу | — | `captcha:`… | `app/handlers/user/start.py:501` `callback_captcha` | капча | 🔗 | 2026-08-19 `4cbd24b9` feat(captcha): картинки на каждое животное + кно | Капча при первом /start (app/services/captcha.py) |
| «Разработчик» — создаём user-запись и пускаем в обычный главный экран | `main.welcome` | `stage_gate:dev` | `app/handlers/user/start.py:757` `callback_stage_gate_dev` | /start | 🔗 | 2026-05-27 `dab055f3` feat: stage-only — new-user gate and DB user bro | Только stage-бот |

## Главное меню и навигация

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Экран «Моя подписка» — краткое инфо + быстрые действия | — | `menu_my_subscription` | `app/handlers/callbacks/navigation.py:266` `callback_my_subscription` | клавиатуры меню/профиля | ✅ | 2026-08-13 `d20e7446` feat(main-menu): компактная раскладка + новые эк |  |
| Экран «Правила» — выбор правового документа | — | `menu_legal` | `app/handlers/callbacks/navigation.py:274` `callback_legal` | клавиатуры меню/профиля | ✅ | 2026-08-13 `d20e7446` feat(main-menu): компактная раскладка + новые эк |  |
| Decorative button — no action | — | `noop` | `app/handlers/callbacks/navigation.py:40` `callback_noop` | клавиатуры меню/профиля, navigation.py, steam_purchase.py | ✅ | 2026-04-10 `6e9f6bbe` Reorder setup screen: downloads first, then auto |  |
| Главное меню. Delete + answer to support navigation from photo message | — | `menu_main` | `app/handlers/callbacks/navigation.py:46` `callback_main_menu` | broadcast_trial_key.py, bypass_setup.py, gift.py, navigation.py, клавиатуры меню/профиля,  | ✅ | 2026-04-14 `f3120c91` Show main photo for ALL users (not just without  |  |
| Политика конфиденциальности | `main.privacy_policy_text` | `about_privacy` | `app/handlers/callbacks/navigation.py:251` `callback_privacy` | navigation.py, клавиатуры меню/профиля | 🙈 | 2026-02-26 `e66fed8f` fix: move callback.answer() to top of handlers t | Из «Настроек» (biz) и экрана «О сервисе»; для обычных — «Правила» (`menu_legal`) |
| Вернуться на главный экран | — | /main | `app/handlers/user/connect.py:123` `cmd_main` | команда | 🙈 | 2026-04-14 `270752f4` Fix /main command: always send photo (was text-o | Скрытая команда: нет в `set_my_commands` (main.py:552-565) |

## Покупка VPN

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Экран выбора комбо-тарифа (Basic/Plus) | `combo.screen_title` | `buy_combo` | `app/handlers/callbacks/navigation.py:1556` `callback_buy_combo` | рассылка из дашборда, рассылка, клавиатуры меню/профиля, navigation.py, общие экраны | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Выбор периода комбо-тарифа | `combo.tariff_basic` | `combo_tariff:`… | `app/handlers/callbacks/navigation.py:1597` `callback_combo_tariff` | navigation.py, callbacks.py, payments_messages.py, сообщение об оплате, purchase_flow.py,  | ✅ | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Подтверждение и оплата комбо-тарифа — используем стандартный экран опл | — | `combo_period:`… | `app/handlers/callbacks/navigation.py:1667` `callback_combo_period` | navigation.py, callbacks.py, payments_messages.py, сообщение об оплате, purchase_flow.py,  | ✅ | 2026-05-20 `5367c59c` fix: apply discount chain to combo tariffs |  |
| Спецпредложение -15% — перенаправляет на экран покупки | `errors.special_offer_expired` | `special_offer_buy` | `app/handlers/callbacks/navigation.py:280` `callback_special_offer_buy` | клавиатуры меню/профиля | ✅ | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Включить/выключить автопродление | `subscription.auto_renew_enabled_toast` | `toggle_auto_renew:`… | `app/handlers/callbacks/subscription.py:103` `callback_toggle_auto_renew` | клавиатуры меню/профиля | ✅ | 2026-02-13 `8fbdc475` refactor(admin): introduce modular admin archite |  |
| Активация пробного периода на 3 дня | `common.rate_limit_message` | `activate_trial` | `app/handlers/callbacks/subscription.py:134` `callback_activate_trial` | клавиатуры меню/профиля | ✅ | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Обработчик команды /buy — открывает экран покупки | — | /buy | `app/handlers/payments/buy.py:18` `cmd_buy` | команда | ✅ | 2026-02-13 `8fbdc475` refactor(admin): introduce modular admin archite | ⚠️ Ведёт на экран тарифов напрямую, а кнопки меню «Купить/Продлить VPN» — на «Управление подпиской» (см. inconsistencies.md) |
| Меню смены тарифа — показываем все доступные тарифы кроме текущего | `common.back` | `switch_tariff_menu` | `app/handlers/payments/callbacks.py:143` `callback_switch_tariff_menu` | callbacks.py | ✅ | 2026-08-13 `cc82e991` feat(subscription): цвета + custom emoji на экра | Только из «Управления подпиской» |
| Экран нового тарифа с описанием и выбором периода | `buy.period_24_months` | `switch_tariff:`… | `app/handlers/payments/callbacks.py:187` `callback_switch_tariff` | callbacks.py | ✅ | 2026-09-03 `92959eaf` revert(moderation): модерация перенесена — верну | Обещает «новый тариф после окончания текущего», но ведёт в тот же `period:` что и обычная покупка |
| ЭКРАН 1 — Выбор тарифа (Basic/Plus) | `errors.session_expired` | `tariff:`… | `app/handlers/payments/callbacks.py:345` `callback_tariff_type` | payments_callbacks.py, общие экраны, callbacks.py | ✅ | 2026-08-15 `29689d44` ui(wata): картинка на экране «🏦 Оплата через СБП |  |
| ЭКРАН 2 — Выбор периода тарифа | `errors.tariff` | `period:`… | `app/handlers/payments/callbacks.py:634` `callback_tariff_period` | callbacks.py | ✅ | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Управление подпиской: продлить текущий / сменить тарифный план | `common.back` | `menu_buy_vpn` | `app/handlers/payments/callbacks.py:68` `callback_buy_vpn` | рассылка из дашборда, рассылка, notifications.py, navigation.py, payments_callbacks.py, кл | ✅ | 2026-09-03 `92959eaf` revert(moderation): модерация перенесена — верну | ⚠️ Для активного Basic/Plus/Combo показывает «Управление подпиской», иначе — экран тарифов |
| Подтверждение перехода Plus→Basic: продолжаем поток оплаты Basic | `errors.session_expired` | `downgrade_confirm_basic` | `app/handlers/payments/callbacks.py:810` `callback_downgrade_confirm_basic` | callbacks.py | ✅ | 2026-02-26 `e66fed8f` fix: move callback.answer() to top of handlers t |  |
| Обработчик кнопки ввода промокода | `buy.promo_applied` | `enter_promo` | `app/handlers/payments/callbacks.py:837` `callback_enter_promo` | общие экраны | ✅ | 2026-04-19 `d6b60efd` fix: add parse_mode="HTML" to all remaining mess |  |
| Обработчик кнопки 'Назад' при ошибке промокода - возвращает на экран в | — | `promo_back` | `app/handlers/payments/callbacks.py:871` `callback_promo_back` | клавиатуры меню/профиля | ✅ | 2026-02-13 `7373eac2` production hardening: |  |
| Обработчик ввода промокода - работает ТОЛЬКО в состоянии waiting_for_p | `errors.try_later` | _PromoCodeInput.waiting_for_promo_ | `app/handlers/payments/promo_fsm.py:28` `process_promo_code` | — | ✅ | 2026-04-19 `d6b60efd` fix: add parse_mode="HTML" to all remaining mess |  |
| Скидка 15% из уведомления за 3 часа до окончания триала — автоматическ | `main.discount_applied_choose_tariff` | `trial_discount_15` | `app/handlers/callbacks/navigation.py:308` `callback_trial_discount_15` | уведомления триала | 🔗 | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско | Кнопка в уведомлении о конце триала (trial_notifications.py:159) |
| Скидка 15% из уведомления за 3 часа до окончания платной подписки | `main.discount_applied_choose_tariff` | `paid_discount_15` | `app/handlers/callbacks/navigation.py:340` `callback_paid_discount_15` | напоминания | 🔗 | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско | Кнопка в напоминании об истечении (reminders.py:86) |

## Оплата VPN (способы оплаты)

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| ЭКРАН 4D — Оплата Telegram Stars | `common.rate_limit_message` | `pay:stars` | `app/handlers/callbacks/payments_callbacks.py:1084` `callback_pay_stars` | payment_method_selection.py | ✅ | 2026-08-15 `8e80309f` ui(payments): единый lifecycle для всех способов |  |
| Принудительная проверка Wata платежа (пользовательская кнопка) | `payment.wata_check_error` | `pay:wata:check:`… | `app/handlers/callbacks/payments_callbacks.py:2027` `callback_pay_wata_check` | payments_callbacks.py, traffic.py | ✅ | 2026-08-15 `ca4ef51d` fix(wata): «invoice_id» → «provider_invoice_id»  |  |
| ЭКРАН 4A — Оплата балансом | `common.rate_limit_message` | `pay:balance` | `app/handlers/callbacks/payments_callbacks.py:462` `callback_pay_balance` | payment_method_selection.py | ✅ | 2026-09-03 `92959eaf` revert(moderation): модерация перенесена — верну |  |
| ЭКРАН 4B — Оплата картой (Telegram Payments / ЮKassa) | `common.rate_limit_message` | `pay:card` | `app/handlers/callbacks/payments_callbacks.py:937` `callback_pay_card` | payment_method_selection.py | ✅ | 2026-08-30 `56085a91` Reapply "feat(payments): route 'Банковская карта |  |
| Оплата банковской картой через Platega (paymentMethod=11) | — | `pay:card_pl` | `app/handlers/callbacks/payments_callbacks.py:1358` `callback_pay_card_pl` | payment_method_selection.py | ⚙️ | 2026-06-03 `c5327b02` feat(payments): add Platega Card (11) & Internat | Кнопка видна только при `platega_service.is_enabled()` |
| Международные платежи через Platega (paymentMethod=12) | — | `pay:intl_pl` | `app/handlers/callbacks/payments_callbacks.py:1371` `callback_pay_intl_pl` | payment_method_selection.py | ⚙️ | 2026-06-03 `c5327b02` feat(payments): add Platega Card (11) & Internat | Кнопка видна только при `platega_service.is_enabled()` |
| Оплата через СБП. Провайдер (Platega / Wata) выбирается через | `common.rate_limit_message` | `pay:sbp` | `app/handlers/callbacks/payments_callbacks.py:1384` `callback_pay_sbp` | payment_method_selection.py | ⚙️ | 2026-08-17 `b1d74f08` feat(sbp-router): live-switch SBP provider Plate | Кнопка видна только при `platega_service.is_enabled()` |
| Оплата через CryptoBot (криптовалюта) | `common.rate_limit_message` | `pay:crypto` | `app/handlers/callbacks/payments_callbacks.py:1522` `callback_pay_crypto` | payment_method_selection.py | ⚙️ | 2026-08-15 `8e80309f` ui(payments): единый lifecycle для всех способов | Кнопка видна только при `cryptobot_service.is_enabled()` |
| Оплата подписки через Wata (admin-only beta) | `common.rate_limit_message` | `pay:wata` | `app/handlers/callbacks/payments_callbacks.py:1800` `callback_pay_wata` | payment_method_selection.py | ⚙️ | 2026-08-15 `29689d44` ui(wata): картинка на экране «🏦 Оплата через СБП | Кнопка видна только при `wata_service.is_enabled()` |

## Оплата (служебные)

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Обработчик successful_payment - успешная оплата картой | `errors.try_later` | _F.successful_payment_ | `app/handlers/payments/payments_messages.py:129` `process_successful_payment` | — | ✅ | 2026-08-20 `125de9f1` fix(traffic-pack): unified confirmation path + s |  |
| Подтверждение платежа перед списанием. КРИТИЧНО: ответить в течение та | — | _любой_ | `app/handlers/payments/payments_messages.py:56` `process_pre_checkout_query` | — | ✅ | 2026-03-13 `ec152ea0` fix: payment security audit — bug fixes, 15-min  |  |
| Log file_id of incoming photos for later use (e.g. loyalty images) | — | _F.photo, ~StateFilter(BroadcastCreate.waiting_for__ | `app/handlers/payments/payments_messages.py:96` `log_incoming_photo_file_id` | — | ✅ | 2026-08-06 `fcab68c6` fix(admin): вернуть file_id админу прямо из paym |  |

## Подключение

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Ручной показ ключа обхода — крипто-обёрнутый Happ (crypt4) и | `bypass_setup.no_key_yet` | `bypass_setup_manual` | `app/handlers/callbacks/bypass_setup.py:192` `callback_bypass_setup_manual` | bypass_setup.py, traffic.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Главный экран установки обхода | `bypass_setup.no_key_yet` | `bypass_setup_open` | `app/handlers/callbacks/bypass_setup.py:54` `callback_bypass_setup_open` | bypass_setup.py, traffic.py, bypass_activation_delay.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето | Также из уведомления об активации обхода (bypass_activation_delay.py:114) |
| Экран подробной инструкции по ручной настройке (стандарт + обход) | `setup.key_happ_label` | `setup_manual:`… | `app/handlers/callbacks/navigation.py:1058` `callback_setup_manual` | navigation.py | ✅ | 2026-09-09 `76eefb86` chore(setup-manual): временно скрыть альтернатив |  |
| Готово — отправить 🎉 и через 2 сек показать главный экран | — | `setup_done` | `app/handlers/callbacks/navigation.py:1228` `callback_setup_done` | bypass_setup.py, navigation.py, bypass_gift_setup.py | ✅ | 2026-06-12 `319a825f` feat(ux): premium ⚡️ вместо 🎉 после нажатия «Гот |  |
| Экран выбора: QR обычных серверов или обхода белых списков | `get_key.no_subscription` | `setup_qr:`… | `app/handlers/callbacks/navigation.py:1277` `callback_setup_qr` | navigation.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Выбор приложения (Happ / Incy) для обычных серверов | — | `setup_qr_standard:`… | `app/handlers/callbacks/navigation.py:1338` `callback_setup_qr_standard` | navigation.py, connect.py | ✅ | 2026-06-25 `2ba3b858` feat(setup): экран выбора приложения (Incy / Hap |  |
| Выбор приложения (Happ / Incy) для обхода белых списков | — | `setup_qr_bypass:`… | `app/handlers/callbacks/navigation.py:1351` `callback_setup_qr_bypass` | navigation.py, connect.py | ✅ | 2026-06-25 `2ba3b858` feat(setup): экран выбора приложения (Incy / Hap |  |
| QR-код подписки для выбранного приложения (Happ / Incy) | `get_key.no_subscription` | `setup_qr_app:`… | `app/handlers/callbacks/navigation.py:1394` `callback_setup_qr_app` | navigation.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Подключиться → сразу выбор устройства | `setup.select_device` | `connect_instruction` | `app/handlers/callbacks/navigation.py:413` `callback_connect_instruction` | navigation.py, клавиатуры меню/профиля, общие экраны | ✅ | 2026-08-19 `88dfbd98` fix(remnawave): bypass URL всегда доступен + sit |  |
| Step 1: Install Happ app — shows photo + download buttons | `setup.install_app` | `setup_step1:`… | `app/handlers/callbacks/navigation.py:483` `callback_setup_step1` | broadcast_trial_key.py, navigation.py, connect.py | ✅ | 2026-08-22 `06a742ad` style(connect): кнопка «Дальше» → зелёная (succe |  |
| Step 2: Copy & import VPN keys into app | `setup.key_install_title_agg` | `setup_step2:`… | `app/handlers/callbacks/navigation.py:589` `callback_setup_step2` | navigation.py | ✅ | 2026-09-03 `92959eaf` revert(moderation): модерация перенесена — верну | iOS: + ряд «Karing VPN / Karing Обход» (агрегатор: «Добавить ключ в Karing») |
| Другие клиенты: ключи Premium + Обход (обычные ссылки) для v2RayTun / Karing / Stash / Clash Verge | `setup.other_title` | `setup_other:`… | `app/handlers/callbacks/navigation.py` `callback_setup_other` | navigation.py (setup_step1, над «Дальше») | ✅ | 2026-09-14 feat(setup): «Другие клиенты», Karing, Incy для Windows | Импорт в одно нажатие через `/open/{v2raytun,karing,stash,clash}` |
| Единый экран: скачать приложение + авто-настройка с кнопками | `setup.download_happ` | `setup_platform:`… | `app/handlers/callbacks/navigation.py:915` `callback_setup_platform` | navigation.py | ✅ | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Подключиться → сразу выбор устройства (new step-by-step flow) | `setup.select_device` | /connect | `app/handlers/user/connect.py:23` `cmd_connect` | команда | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| 📲 Добавить устройство → экран выбора типа подключения | `get_key.no_subscription` | /hwadd | `app/handlers/user/connect.py:66` `cmd_hwadd` | команда | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Экран установки обхода из broadcast-кнопки «🌐 Включить обход» | `bypass_setup.no_key_yet` | `broadcast_bypass` | `app/handlers/callbacks/bypass_setup.py:107` `callback_broadcast_bypass` | рассылка из дашборда, рассылка | 🔗 | 2026-09-03 `92959eaf` revert(moderation): модерация перенесена — верну | Кнопка рассылки |
| Инструкция. Entry from main menu (menu_instruction) or profile (instru | — | `menu_instruction` | `app/handlers/callbacks/navigation.py:373` `callback_instruction` | клавиатуры меню/профиля | 🔗 | 2026-02-22 `24e9cbad` Instruction screen with tariff-based copy button | `menu_instruction` — только в уведомлении о перевыпуске ключа (админ, keyboards.py:623). Алиас `instruction` — никем не производится |
| Ключи больше не отправляются в боте; показываем кнопку «Подключиться»  | `connect.press_button` | `copy_key`, `copy_vpn_key` | `app/handlers/callbacks/navigation.py:381` `callback_connect_instead_of_copy` | клавиатуры меню/профиля | 🔗 | 2026-03-28 `2237bea6` feat: add "Set up device" button and hint to /co | `copy_vpn_key` — только в уведомлении о перевыпуске ключа; `copy_key` — только в мёртвой `get_profile_keyboard_old` |
| Install-app screen (per-platform photo + download buttons) | `setup.install_app` | `bgift_step1:`… | `app/handlers/user/bypass_gift_setup.py:155` `callback_bgift_step1` | bypass_gift_setup.py | 🔗 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето | Deep link `bgift_` → настройка обхода |
| Connect screen — bypass subscription URL + import instructions | `bgift_setup.connect_screen` | `bgift_step2:`… | `app/handlers/user/bypass_gift_setup.py:262` `callback_bgift_step2` | bypass_gift_setup.py | 🔗 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето | Deep link `bgift_` → настройка обхода |
| Entry point from the gift-link success message | `bgift_setup.select_device` | `bgift_setup` | `app/handlers/user/bypass_gift_setup.py:92` `callback_bgift_setup` | bypass_gift_setup.py, /start | 🔗 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето | Deep link `/start bgift_<code>` (гифт-ссылка на ГБ) |

## Профиль и устройства

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Мой профиль - работает независимо от FSM состояния | `errors.profile_load` | `menu_profile` | `app/handlers/callbacks/subscription.py:334` `callback_profile` | gift.py, payments_callbacks.py, клавиатуры меню/профиля, общие экраны, payments_messages.p | ✅ | 2026-04-19 `d6b60efd` fix: add parse_mode="HTML" to all remaining mess |  |
| callback_devices_tier | — | `user:devices:tier:`… | `app/handlers/user/devices.py:115` `callback_devices_tier` | devices.py | ✅ | 2026-06-14 `86917a73` feat(cabinet): «Мои устройства» с HWID-управлени |  |
| callback_devices_confirm_delete | — | `user:devices:del:`… | `app/handlers/user/devices.py:180` `callback_devices_confirm_delete` | devices.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| callback_devices_do_delete | — | `user:devices:cdel:`… | `app/handlers/user/devices.py:217` `callback_devices_do_delete` | devices.py | ✅ | 2026-06-14 `86917a73` feat(cabinet): «Мои устройства» с HWID-управлени |  |
| callback_devices_confirm_delete_all | — | `user:devices:dela:`… | `app/handlers/user/devices.py:257` `callback_devices_confirm_delete_all` | devices.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| callback_devices_do_delete_all | — | `user:devices:cdela:`… | `app/handlers/user/devices.py:276` `callback_devices_do_delete_all` | devices.py | ✅ | 2026-06-14 `86917a73` feat(cabinet): «Мои устройства» с HWID-управлени |  |
| callback_devices_noop | — | `user:devices:noop` | `app/handlers/user/devices.py:299` `callback_devices_noop` | devices.py | ✅ | 2026-06-14 `86917a73` feat(cabinet): «Мои устройства» с HWID-управлени |  |
| callback_devices_main | — | `user:devices` | `app/handlers/user/devices.py:78` `callback_devices_main` | клавиатуры меню/профиля, devices.py | ✅ | 2026-06-14 `86917a73` feat(cabinet): «Мои устройства» с HWID-управлени |  |
| Обработчик команды /profile | `errors.start_command` | /profile | `app/handlers/user/profile.py:21` `cmd_profile` | команда | ✅ | 2026-04-19 `d6b60efd` fix: add parse_mode="HTML" to all remaining mess |  |

## Трафик обхода (ГБ)

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Traffic pack — Wata (admin-only beta) | `payment.wata_check_button` | `traffic_pay_wata:`… | `app/handlers/traffic.py:1022` `callback_traffic_pay_wata` | traffic.py | ✅ | 2026-08-15 `a830cf8a` ui(traffic): единый lifecycle оплаты для пакетов |  |
| Pay for bypass-only pack via SBP (Platega, +11%) | `payment.sbp_unavailable` | `bypass_pay_sbp:`… | `app/handlers/traffic.py:1126` `callback_bypass_pay_sbp` | traffic.py | ✅ | 2026-08-15 `a830cf8a` ui(traffic): единый lifecycle оплаты для пакетов |  |
| Bypass-only pack — Wata (admin-only beta) | `payment.wata_check_button` | `bypass_pay_wata:`… | `app/handlers/traffic.py:1198` `callback_bypass_pay_wata` | traffic.py | ✅ | 2026-08-15 `a830cf8a` ui(traffic): единый lifecycle оплаты для пакетов |  |
| Расширенные пакеты обхода (300+ ГБ) | `common.back` | `buy_bypass_extended` | `app/handlers/traffic.py:133` `callback_buy_bypass_extended` | traffic.py | ✅ | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Подтверждение покупки bypass-only пакета | `traffic.confirm_purchase` | `buy_bypass_pack:`… | `app/handlers/traffic.py:180` `callback_buy_bypass_pack` | traffic.py | ✅ | 2026-08-30 `75a67eb5` ui(traffic/bypass): restore basket icon on the 2 |  |
| Show traffic usage screen | `traffic.no_subscription` | `traffic_info`, `traffic_refresh` | `app/handlers/traffic.py:265` `callback_traffic_info` | рассылка, traffic.py, сообщение об оплате | ✅ | 2026-09-03 `92959eaf` revert(moderation): модерация перенесена — верну |  |
| Show traffic pack options | `traffic.no_subscription` | `buy_traffic` | `app/handlers/traffic.py:644` `callback_buy_traffic` | клавиатуры меню/профиля, общие экраны, callbacks.py, traffic.py, сообщение об оплате, мони | ✅ | 2026-08-13 `851fb2bf` fix(traffic): «Назад» с экрана «Купить трафик» → |  |
| Экран покупки только обхода белых списков (ГБ пакеты) | `traffic.btn_more_volume` | `buy_bypass_only` | `app/handlers/traffic.py:66` `callback_buy_bypass_only` | клавиатуры меню/профиля, traffic.py | ✅ | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Show extended traffic packs (300+GB) | `common.back` | `buy_traffic_extended` | `app/handlers/traffic.py:719` `callback_buy_traffic_extended` | traffic.py | ✅ | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Confirm traffic pack purchase | `traffic.confirm_purchase` | `buy_traffic_pack:`… | `app/handlers/traffic.py:767` `callback_buy_traffic_pack` | рассылка, traffic.py | ✅ | 2026-08-30 `75a67eb5` ui(traffic/bypass): restore basket icon on the 2 |  |
| Pay for traffic pack via SBP (Platega, +11% markup) | `payment.sbp_unavailable` | `traffic_pay_sbp:`… | `app/handlers/traffic.py:927` `callback_traffic_pay_sbp` | traffic.py | ✅ | 2026-08-15 `a830cf8a` ui(traffic): единый lifecycle оплаты для пакетов |  |
| Показать экран «Мой трафик» | — | /white | `app/handlers/user/connect.py:56` `cmd_white` | команда | 🙈 | 2026-04-05 `795efde1` Update /connect screen, add /white and /main com | Скрытая команда: нет в `set_my_commands`; открывает «Мой трафик» |

## Баланс: пополнение

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Пополнить баланс | `main.topup_balance_select_amount` | `topup_balance` | `app/handlers/callbacks/payments_callbacks.py:118` `callback_topup_balance` | payments_callbacks.py, клавиатуры меню/профиля, topup_fsm.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Обработка выбора суммы пополнения - показываем экран выбора способа оп | `errors.invalid_amount` | `topup_amount:`… | `app/handlers/callbacks/payments_callbacks.py:168` `callback_topup_amount` | payments_callbacks.py | ✅ | 2026-08-19 `af3298b3` feat(lava→wata): UI-подмена всех Lava-кнопок на  |  |
| Пополнение баланса через СБП. Провайдер (Platega / Wata) выбирается | `common.rate_limit_message` | `topup_sbp:`… | `app/handlers/callbacks/payments_callbacks.py:2143` `callback_topup_sbp` | payments_callbacks.py, topup_fsm.py | ✅ | 2026-08-17 `b1d74f08` feat(sbp-router): live-switch SBP provider Plate |  |
| Пополнение баланса через Wata (карта/СБП/T-Pay). Admin-only beta | `common.rate_limit_message` | `topup_wata:`… | `app/handlers/callbacks/payments_callbacks.py:2244` `callback_topup_wata` | payments_callbacks.py, topup_fsm.py | ✅ | 2026-08-15 `29689d44` ui(wata): картинка на экране «🏦 Оплата через СБП |  |
| Оплата пополнения баланса картой | `common.rate_limit_message` | `topup_card:`… | `app/handlers/callbacks/payments_callbacks.py:2336` `callback_topup_card` | payments_callbacks.py, topup_fsm.py | ✅ | 2026-08-30 `56085a91` Reapply "feat(payments): route 'Банковская карта |  |
| Оплата пополнения баланса через Telegram Stars | `common.rate_limit_message` | `topup_stars:`… | `app/handlers/callbacks/payments_callbacks.py:236` `callback_topup_stars` | payments_callbacks.py, topup_fsm.py | ✅ | 2026-04-19 `0640e773` fix: add parse_mode="HTML" to all edit_text/send |  |
| Ввод произвольной суммы пополнения баланса | `main.topup_enter_amount` | `topup_custom` | `app/handlers/callbacks/payments_callbacks.py:286` `callback_topup_custom` | payments_callbacks.py | ✅ | 2026-04-19 `d6b60efd` fix: add parse_mode="HTML" to all remaining mess |  |
| Обработка введенной суммы пополнения - показываем экран выбора способа | `main.topup_amount_invalid` | _TopUpStates.waiting_for_amount_ | `app/handlers/payments/topup_fsm.py:40` `process_topup_amount` | — | ✅ | 2026-08-19 `af3298b3` feat(lava→wata): UI-подмена всех Lava-кнопок на  |  |

## Подарки

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Выбор тарифа для подарка → показываем периоды | `errors.tariff` | `gift_tariff:`… | `app/handlers/callbacks/gift.py:132` `callback_gift_tariff` | gift.py, subscriptions.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Выбор периода → показываем способы оплаты | `errors.tariff` | `gift_period:`… | `app/handlers/callbacks/gift.py:183` `callback_gift_period` | gift.py, subscriptions.py | ✅ | 2026-08-19 `af3298b3` feat(lava→wata): UI-подмена всех Lava-кнопок на  |  |
| Оплата подарка с баланса | `common.rate_limit_message` | `gift_pay:balance` | `app/handlers/callbacks/gift.py:289` `callback_gift_pay_balance` | gift.py, subscriptions.py | ✅ | 2026-04-19 `d6b60efd` fix: add parse_mode="HTML" to all remaining mess |  |
| Оплата подарка картой через Telegram Payments | `common.rate_limit_message` | `gift_pay:card` | `app/handlers/callbacks/gift.py:370` `callback_gift_pay_card` | gift.py, subscriptions.py | ✅ | 2026-08-30 `56085a91` Reapply "feat(payments): route 'Банковская карта |  |
| Оплата подарка через Telegram Stars | `common.rate_limit_message` | `gift_pay:stars` | `app/handlers/callbacks/gift.py:452` `callback_gift_pay_stars` | gift.py, subscriptions.py | ✅ | 2026-04-19 `0640e773` fix: add parse_mode="HTML" to all edit_text/send |  |
| Оплата подарка через CryptoBot (криптовалюта) | `common.rate_limit_message` | `gift_pay:crypto` | `app/handlers/callbacks/gift.py:525` `callback_gift_pay_crypto` | gift.py, subscriptions.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Оплата подарка через СБП. Провайдер (Platega / Wata) выбирается | `errors.session_expired` | `gift_pay:sbp` | `app/handlers/callbacks/gift.py:606` `callback_gift_pay_sbp` | gift.py, subscriptions.py | ✅ | 2026-08-17 `b1d74f08` feat(sbp-router): live-switch SBP provider Plate |  |
| Оплата подарка через Wata (admin-only beta) | `errors.session_expired` | `gift_pay:wata` | `app/handlers/callbacks/gift.py:688` `callback_gift_pay_wata` | gift.py, subscriptions.py | ✅ | 2026-08-15 `1ebc0444` style(wata): переименовать «💳 Wata (тест)» → «🏦  |  |
| Экран «Мои подарки» — карусель купленных подарков | `gift.my_gifts_empty` | `my_gifts:`… | `app/handlers/callbacks/gift.py:796` `callback_my_gifts` | gift.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Экран подарочной подписки — выбор тарифа | `gift.intro` | `gift_subscription` | `app/handlers/callbacks/gift.py:80` `callback_gift_start` | gift.py, общие экраны, payments_messages.py, subscriptions.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Детальный экран подарка — ссылка + кнопка «Отправить» | `errors.tariff` | `gift_detail:`… | `app/handlers/callbacks/gift.py:895` `callback_gift_detail` | gift.py, subscriptions.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Activate the 20% personal discount tied to a gift notification | — | `gift_offer:claim` | `app/handlers/callbacks/gift.py:998` `callback_gift_offer_claim` | bonus.py, subscriptions.py | 🔗 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето | Кнопка из рассылки бонуса (admin/bonus.py) и gift-оффера |

## Рефералы

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Обработчик команды /referral — открывает экран программы лояльности | — | /referral | `app/handlers/user/referrals.py:24` `cmd_referral` | команда | ✅ | 2026-03-13 `adbd8642` fix: security hardening — private chat guards, i |  |
| Экран «Программа лояльности». Entry from inline button | — | `menu_referral` | `app/handlers/user/referrals.py:34` `callback_referral` | рассылка из дашборда, рассылка, клавиатуры меню/профиля, referrals.py | ✅ | 2026-02-13 `7373eac2` production hardening: |  |
| Экран «Подробнее» — расширенный презентационный текст. Delete + answer | `referral.status_footer` | `referral_stats` | `app/handlers/user/referrals.py:41` `callback_referral_stats` | общие экраны | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Экран «Как работает программа» для реферальной программы | `referral.how_it_works_text` | `referral_how_it_works` | `app/handlers/user/referrals.py:96` `callback_referral_how_it_works` | общие экраны | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Экран «Подари другу скидку 30%» — открывается из broadcast'а | `share_discount.share_text` | `share_discount_open` | `app/handlers/user/referrals.py:126` `callback_share_discount_open` | рассылка из дашборда, рассылка | 🔗 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето | Кнопка рассылки «Поделиться скидкой» |

## MT Proxy

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Sales screen for new buyers, delivery screen for existing owners | — | `proxy_menu` | `app/handlers/proxy.py:112` `callback_proxy_menu` | общие экраны, proxy.py | ✅ | 2026-05-21 `5fcc0420` feat: standalone Telegram-proxy product (one-tim |  |
| Pay for the proxy product via SBP (Platega) | — | `proxy_pay_sbp` | `app/handlers/proxy.py:158` `callback_proxy_pay_sbp` | proxy.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Open the proxy screen as a NEW message — used from broadcasts | — | `proxy_open` | `app/handlers/proxy.py:134` `callback_proxy_open` | рассылка из дашборда, рассылка | 🔗 | 2026-05-22 `d4d36cfb` fix: broadcast MT Proxy button opens proxy scree | Кнопка рассылки «MT Proxy» |

## Игры

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Fertilize a plant | `errors.database_unavailable` | `farm_fert_`… | `app/handlers/game.py:1037` `callback_farm_fert` | game.py | ✅ | 2026-05-30 `dd177334` fix(games): route all renders through safe_edit_ |  |
| Harvest a ready plant | `errors.database_unavailable` | `farm_harvest_`… | `app/handlers/game.py:1087` `callback_farm_harvest` | game.py | ✅ | 2026-08-25 `9fda65ba` feat(farm): market commission on harvest + fix d |  |
| Remove dead plant - show confirmation | `errors.database_unavailable` | `farm_remove_`… | `app/handlers/game.py:1127` `callback_farm_remove` | game.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Buy a new plot | `errors.database_unavailable` | `farm_buy_plot` | `app/handlers/game.py:1198` `callback_farm_buy_plot` | game.py | ✅ | 2026-05-30 `dd177334` fix(games): route all renders through safe_edit_ |  |
| Show confirmation dialog for digging up a plant | `errors.database_unavailable` | `farm_dig_`…, `farm_dig_confirm_`… | `app/handlers/game.py:1266` `callback_farm_dig` | game.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Confirm and execute digging up a plant | `errors.database_unavailable` | `farm_dig_confirm_`… | `app/handlers/game.py:1324` `callback_farm_dig_confirm` | game.py | ✅ | 2026-05-30 `dd177334` fix(games): route all renders through safe_edit_ |  |
| Games menu screen — subscription required (same check as bowling/dice/ | `games.menu_title` | `games_menu` | `app/handlers/game.py:136` `callback_games_menu` | клавиатуры меню/профиля, game.py | ✅ | 2026-05-30 `bf4ce265` feat(prod): add photo to mini-shop, gift, and ga |  |
| No-op handler for disabled buttons | — | `farm_noop` | `app/handlers/game.py:1370` `callback_farm_noop` | game.py | ✅ | 2026-02-20 `3880d1ac` feat: farm game phase 1 - planting, watering, ha |  |
| 🛡 Накрыть — pay via balance if enough, else show SBP screen | — | `farm_shield:`… | `app/handlers/game.py:1402` `callback_farm_shield` | game.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Pay shield via Платега (SBP, +11%) | — | `farm_shield_sbp:`… | `app/handlers/game.py:1462` `callback_farm_shield_sbp` | game.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| 🚜 Собрать незрелым — credits 50% of plant reward, frees the plot | — | `farm_early:`… | `app/handlers/game.py:1521` `callback_farm_early_harvest` | game.py | ✅ | 2026-08-25 `9fda65ba` feat(farm): market commission on harvest + fix d |  |
| Bowling game: cooldown → subscription check → consume cooldown → dice  | `errors.database_unavailable` | `game_bowling` | `app/handlers/game.py:172` `callback_game_bowling` | game.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Dice game: cooldown → subscription check → consume cooldown → dice → g | `errors.database_unavailable` | `game_dice` | `app/handlers/game.py:340` `callback_game_dice` | game.py | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Start Bomber game - initialize grid with 3 random mines | `games.bomber_rules` | `game_bomber` | `app/handlers/game.py:536` `callback_game_bomber` | game.py | ✅ | 2026-05-30 `dd177334` fix(games): route all renders through safe_edit_ |  |
| Handle cell click in Bomber game | `games.bomber_self_destruct` | `bomber_cell:`… | `app/handlers/game.py:566` `callback_bomber_cell` | game.py | ✅ | 2026-05-30 `dd177334` fix(games): route all renders through safe_edit_ |  |
| Safe exit from Bomber game | `games.bomber_safe_exit` | `bomber_exit` | `app/handlers/game.py:632` `callback_bomber_exit` | game.py | ✅ | 2026-05-30 `dd177334` fix(games): route all renders through safe_edit_ |  |
| Farm game main screen | `errors.database_unavailable` | `game_farm` | `app/handlers/game.py:856` `callback_game_farm` | game.py | ✅ | 2026-05-30 `dd177334` fix(games): route all renders through safe_edit_ |  |
| Show plant selection screen | — | `farm_choose_`… | `app/handlers/game.py:878` `callback_farm_choose_plant` | game.py | ✅ | 2026-08-25 `9fda65ba` feat(farm): market commission on harvest + fix d |  |
| Plant a seed | `errors.database_unavailable` | `farm_plant_`… | `app/handlers/game.py:917` `callback_farm_plant` | game.py | ✅ | 2026-05-30 `dd177334` fix(games): route all renders through safe_edit_ |  |
| Water a plant | `errors.database_unavailable` | `farm_water_`… | `app/handlers/game.py:987` `callback_farm_water` | game.py | ✅ | 2026-05-30 `dd177334` fix(games): route all renders through safe_edit_ |  |

## Помощь и информация

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Help menu — FAQ, instructions, direct support (photo screen) | — | `menu_help` | `app/handlers/callbacks/navigation.py:1832` `callback_menu_help` | navigation.py, клавиатуры меню/профиля, bypass_activation_delay.py | ✅ | 2026-05-27 `58bd525d` feat(help): unify /help command and menu_help bu |  |
| Contacts — support and sales emails (photo screen) | `help.contacts_title` | `help_contacts` | `app/handlers/callbacks/navigation.py:1838` `callback_help_contacts` | общие экраны | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| FAQ — top questions | `help.faq_title` | `faq` | `app/handlers/callbacks/navigation.py:1861` `callback_faq` | navigation.py, общие экраны | ✅ | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| FAQ — individual answer | `common.help_button` | `faq:`… | `app/handlers/callbacks/navigation.py:1879` `callback_faq_answer` | navigation.py | ✅ | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Экран «Правила» — правовые документы (соглашение + политика) | — | /docs | `app/handlers/user/connect.py:144` `cmd_docs` | команда | ✅ | 2026-09-02 `6b860208` fix(docs-cmd): /docs ведёт на экран «Правила», а |  |
| Обработчик команды /help — открывает экран помощи (FAQ / Инструкции /  | — | /help | `app/handlers/user/support.py:22` `cmd_help` | команда | ✅ | 2026-05-27 `58bd525d` feat(help): unify /help command and menu_help bu |  |
| Обработчик команды /instruction — открывает экран инструкции | — | /instruction | `app/handlers/user/support.py:32` `cmd_instruction` | команда | ✅ | 2026-03-13 `adbd8642` fix: security hardening — private chat guards, i |  |
| Обработчик команды /info — открывает экран «О сервисе» | — | /info | `app/handlers/user/support.py:42` `cmd_info` | команда | ✅ | 2026-03-13 `adbd8642` fix: security hardening — private chat guards, i |  |

## Рассылки — пользовательские кнопки

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Re-render меню тарифов (used as «back» from info / period screens) | — | `bcg1y40:menu` | `app/handlers/payments/broadcast_offers.py:743` `callback_broadcast_gift_1y_40_menu` | рассылка | 🔗 | 2026-07-04 `fad94f69` feat(broadcast): «🎁 1 год со скидкой 40%» — скид | Кнопка в рассылке (бот/дашборд) |
| Full descriptions всех четырёх годовых тарифов со скидкой | — | `bcg1y40:info` | `app/handlers/payments/broadcast_offers.py:757` `callback_broadcast_gift_1y_40_info` | рассылка | 🔗 | 2026-07-04 `fad94f69` feat(broadcast): «🎁 1 год со скидкой 40%» — скид | Кнопка в рассылке (бот/дашборд) |
| Выбран тариф — показываем экран периодов (30/90/180/365) | — | `bcg1y40:tariff:`… | `app/handlers/payments/broadcast_offers.py:771` `callback_broadcast_gift_1y_40_tariff` | рассылка | 🔗 | 2026-07-04 `fad94f69` feat(broadcast): «🎁 1 год со скидкой 40%» — скид | Кнопка в рассылке (бот/дашборд) |
| User picked tariff + period — jump to payment-method selection | — | `bcg1y40:buy:`… | `app/handlers/payments/broadcast_offers.py:794` `callback_broadcast_gift_1y_40_buy` | рассылка | 🔗 | 2026-07-04 `fad94f69` feat(broadcast): «🎁 1 год со скидкой 40%» — скид | Кнопка в рассылке (бот/дашборд) |
| User clicked 'Купить трафик промо' in broadcast — apply 1-day traffic  | `traffic.no_subscription` | `broadcast_promo_traffic:`… | `app/handlers/payments/broadcast_offers.py:865` `callback_broadcast_promo_traffic` | рассылка из дашборда, рассылка | 🔗 | 2026-06-14 `d53672b3` fix(broadcast): экран promo-трафика теперь показ | Кнопка в рассылке (бот/дашборд) |
| Кликнули «Посмотреть подарок» в рассылке — играем reveal-сценку | — | `broadcast_gift_reveal:`… | `app/handlers/payments/broadcast_offers.py:966` `callback_broadcast_gift_reveal` | рассылка из дашборда | 🔗 | 2026-07-05 `b4f00465` fix(gift-reveal): защита save + подробные логи д | Кнопка в рассылке (бот/дашборд) |
| «Назад» с экрана выбора периода → обратно на экран выбора тарифа | — | `broadcast_back_to_tariffs` | `app/handlers/payments/broadcast_offers.py:1062` `callback_broadcast_back_to_tariffs` | callbacks.py | 🔗 | 2026-06-22 `f7d57223` fix(broadcast): «Назад» с экрана периода в gift- | Кнопка в рассылке (бот/дашборд) |
| Расширенные паки трафика (300+ ГБ) со скидкой из broadcast | — | `broadcast_promo_traffic_ext:`… | `app/handlers/payments/broadcast_offers.py:1083` `callback_broadcast_promo_traffic_ext` | рассылка | 🔗 | 2026-06-14 `d53672b3` fix(broadcast): экран promo-трафика теперь показ | Кнопка в рассылке (бот/дашборд) |
| Пользователь нажал 'Купить со скидкой' в уведомлении — автоматически п | — | `broadcast_promo_buy:`… | `app/handlers/payments/broadcast_offers.py:32` `callback_broadcast_promo_buy` | рассылка из дашборда, рассылка | 🔗 | 2026-06-28 `449d0901` ui(broadcast): «Купить со скидкой» не удаляет со | Кнопка в рассылке (бот/дашборд) |
| Пользователь нажал 'Забрать подарок' в рассылке — активируем | — | `broadcast_gift_combo:`… | `app/handlers/payments/broadcast_offers.py:95` `callback_broadcast_gift_combo` | рассылка из дашборда, рассылка | 🔗 | 2026-08-11 `9cbb7ca3` feat(broadcast): кнопка «🎁 Забрать подарок» — Co | Кнопка в рассылке (бот/дашборд) |
| User clicked «🎁 −30% на 1 месяц» → экран выбора тарифа | — | `broadcast_gift_1m` | `app/handlers/payments/broadcast_offers.py:251` `callback_broadcast_gift_1m` | рассылка из дашборда, рассылка | 🔗 | 2026-07-06 `d4afd44b` feat(broadcast+user-card): gift_1m/gift_3m кнопк | Кнопка в рассылке (бот/дашборд) |
| Выбран тариф → сразу к выбору способа оплаты с overriden ценой | — | `bcg1m:buy:`… | `app/handlers/payments/broadcast_offers.py:268` `callback_broadcast_gift_1m_buy` | рассылка | 🔗 | 2026-07-06 `d4afd44b` feat(broadcast+user-card): gift_1m/gift_3m кнопк | Кнопка в рассылке (бот/дашборд) |
| User clicked the "🎁 Скидка 30% на 3 месяца" CTA in a broadcast | — | `broadcast_gift_3m` | `app/handlers/payments/broadcast_offers.py:414` `callback_broadcast_gift_3m` | рассылка из дашборда, рассылка | 🔗 | 2026-06-01 `e19c63c9` feat(broadcast/gift-3m): add 'О тарифах' info sc | Кнопка в рассылке (бот/дашборд) |
| Re-render the gift menu (used as 'back' from the info screen) | — | `bcg3m:menu` | `app/handlers/payments/broadcast_offers.py:445` `callback_broadcast_gift_3m_menu` | рассылка | 🔗 | 2026-06-01 `e19c63c9` feat(broadcast/gift-3m): add 'О тарифах' info sc | Кнопка в рассылке (бот/дашборд) |
| Show full descriptions of all four 3-month gift tariffs | — | `bcg3m:info` | `app/handlers/payments/broadcast_offers.py:460` `callback_broadcast_gift_3m_info` | рассылка | 🔗 | 2026-06-01 `e19c63c9` feat(broadcast/gift-3m): add 'О тарифах' info sc | Кнопка в рассылке (бот/дашборд) |
| User picked one of the four 3-month gift tariffs — jump straight to pa | — | `bcg3m:buy:`… | `app/handlers/payments/broadcast_offers.py:475` `callback_broadcast_gift_3m_buy` | рассылка | 🔗 | 2026-06-01 `88b6fb7c` feat(broadcast/gift-3m): replace personal_discou | Кнопка в рассылке (бот/дашборд) |
| User clicked «🎁 1 год со скидкой 40%» in a broadcast → tariff menu | — | `broadcast_gift_1y_40` | `app/handlers/payments/broadcast_offers.py:698` `callback_broadcast_gift_1y_40` | рассылка из дашборда, рассылка | 🔗 | 2026-07-06 `0a0b7ea1` feat(broadcast-1y40): reveal-сценка 🏆 перед экра | Кнопка в рассылке (бот/дашборд) |
| callback_beta_apply | — | `beta_apply:`… | `app/handlers/callbacks/beta_apply.py:42` `callback_beta_apply` | рассылка из дашборда, рассылка | 🔗 | 2026-08-17 `a909ea44` feat(beta): кнопка «🧪 Оставить заявку» в рассылк | Кнопка рассылки «🧪 Оставить заявку» |
| callback_broadcast_trial_key | `common.rate_limit_message` | `broadcast_trial_key:`… | `app/handlers/callbacks/broadcast_trial_key.py:134` `callback_broadcast_trial_key` | рассылка из дашборда | 🔗 | 2026-09-02 `c5a31fcd` fix(broadcast): trial_key gift — сообщение без к | Кнопка рассылки из дашборда (routes/broadcasts.py:936) |

## 🔒 Магазин

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| callback_stars_pack | `common.back` | `stars_pack:`… | `app/handlers/payments/telegram_stars_purchase.py:153` `callback_stars_pack` | telegram_stars_purchase.py | 🙈🔒 | 2026-08-13 `843f4453` style(buttons): партия 3 — стилизация telegram_p | Ветка Stars — вход закомментирован |
| callback_stars_recipient | `common.back` | `stars_recipient:`… | `app/handlers/payments/telegram_stars_purchase.py:195` `callback_stars_recipient` | telegram_stars_purchase.py | 🙈🔒 | 2026-08-13 `843f4453` style(buttons): партия 3 — стилизация telegram_p | Ветка Stars — вход закомментирован |
| process_stars_username | `common.back` | _StateFilter(TelegramStarsState.waiting_for_usernam_ | `app/handlers/payments/telegram_stars_purchase.py:246` `process_stars_username` | — | 🙈🔒 | 2026-08-19 `af3298b3` feat(lava→wata): UI-подмена всех Lava-кнопок на  | Ветка Stars — вход закомментирован |
| callback_stars_pay_card | `errors.payments_unavailable` | `stars_pay:card` | `app/handlers/payments/telegram_stars_purchase.py:382` `callback_stars_pay_card` | telegram_stars_purchase.py | 🙈🔒 | 2026-04-19 `c3517d48` feat: all payment methods for Stars + fix webhoo | Ветка Stars — вход закомментирован |
| Stars — Wata (admin-only beta) | `common.back` | `stars_pay:wata` | `app/handlers/payments/telegram_stars_purchase.py:420` `callback_stars_pay_wata` | telegram_stars_purchase.py | 🙈🔒 | 2026-08-15 `1ebc0444` style(wata): переименовать «💳 Wata (тест)» → «🏦  | Ветка Stars — вход закомментирован |
| callback_stars_pay_sbp | `common.back` | `stars_pay:sbp` | `app/handlers/payments/telegram_stars_purchase.py:467` `callback_stars_pay_sbp` | telegram_stars_purchase.py | 🙈🔒 | 2026-08-13 `843f4453` style(buttons): партия 3 — стилизация telegram_p | Ветка Stars — вход закомментирован |
| callback_stars_buy | `common.back` | `stars_buy` | `app/handlers/payments/telegram_stars_purchase.py:90` `callback_stars_buy` | telegram_stars_purchase.py | 🙈🔒 | 2026-08-13 `843f4453` style(buttons): партия 3 — стилизация telegram_p | Кнопка «⭐ Telegram Stars» закомментирована в мини-магазине (navigation.py:1801) |
| callback_stars_pay_balance | — | `stars_pay:balance` | `app/handlers/payments/telegram_stars_purchase.py:365` `callback_stars_pay_balance` | — | 👻🔒 | 2026-05-28 `af049828` fix(shop): remove "pay from balance" for Steam t | Оплату балансом для Stars убрали в af049828 (2026-05-28); хэндлер остался |
| cb_start_key_entry | — | `apple_send_key:`… | `app/handlers/admin/apple_id_delivery.py:117` `cb_start_key_entry` | apple_id_delivery.py | 🔒 | 2026-07-26 `adc31432` feat(apple-id): flow «отправить ключ» — админ вв |  |
| msg_receive_key | — | _StateFilter(AdminAppleKey.waiting_for_key), F.text_ | `app/handlers/admin/apple_id_delivery.py:162` `msg_receive_key` | — | 🔒 | 2026-07-26 `adc31432` feat(apple-id): flow «отправить ключ» — админ вв |  |
| cb_confirm | — | `apple_key_confirm:`… | `app/handlers/admin/apple_id_delivery.py:222` `cb_confirm` | apple_id_delivery.py | 🔒 | 2026-07-26 `adc31432` feat(apple-id): flow «отправить ключ» — админ вв |  |
| cb_edit | — | `apple_key_edit:`… | `app/handlers/admin/apple_id_delivery.py:279` `cb_edit` | apple_id_delivery.py | 🔒 | 2026-07-26 `adc31432` feat(apple-id): flow «отправить ключ» — админ вв |  |
| msg_receive_edit | — | _StateFilter(AdminAppleKey.waiting_for_edit), F.tex_ | `app/handlers/admin/apple_id_delivery.py:316` `msg_receive_edit` | — | 🔒 | 2026-07-26 `adc31432` feat(apple-id): flow «отправить ключ» — админ вв |  |
| cb_cancel | — | `apple_key_cancel` | `app/handlers/admin/apple_id_delivery.py:364` `cb_cancel` | apple_id_delivery.py | 🔒 | 2026-07-26 `adc31432` feat(apple-id): flow «отправить ключ» — админ вв |  |
| cb_spotify_done | — | `spotify_done:`… | `app/handlers/admin/spotify_delivery.py:53` `cb_spotify_done` | spotify_delivery.py, spotify_purchase.py | 🔒 | 2026-07-26 `72e533fa` feat(shop): Apple ID Russia+India + полный Spoti |  |
| Mini shop main screen — photo + caption | `shop.title` | `mini_shop` | `app/handlers/callbacks/navigation.py:1788` `callback_mini_shop` | spotify_delivery.py, navigation.py, клавиатуры меню/профиля, spotify_purchase.py, steam_pu | 🔒 | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Claude Pro/Max — placeholder until launch | `shop.claude_coming_soon` | `claude_coming_soon` | `app/handlers/callbacks/navigation.py:1825` `callback_claude_coming_soon` | navigation.py | 🔒 | 2026-05-19 `c2e06d3a` ui: mini-shop — Steam description + Claude Pro/M |  |
| Apple ID — region selection | `shop.apple_title` | `apple_region` | `app/handlers/callbacks/navigation.py:1901` `callback_apple_region` | navigation.py | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Apple ID — nominal selection | `shop.apple_amount_title` | `apple_amount:`… | `app/handlers/callbacks/navigation.py:1920` `callback_apple_amount` | navigation.py | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Apple ID — confirmation screen with payment options | `shop.apple_confirm` | `apple_confirm:`… | `app/handlers/callbacks/navigation.py:1960` `callback_apple_confirm` | navigation.py | 🔒 | 2026-08-29 `15c27d49` feat(apple-id): drop Turkey 150 TL, raise USA +1 |  |
| Apple ID — pay via Wata (admin-only beta) | — | `apple_pay_wata:`… | `app/handlers/callbacks/navigation.py:2008` `callback_apple_pay_wata` | navigation.py | 🔒 | 2026-08-15 `1ebc0444` style(wata): переименовать «💳 Wata (тест)» → «🏦  |  |
| Apple ID — pay via YooKassa (Telegram Payments) | `errors.card_payment_unavailable` | `apple_pay_card:`… | `app/handlers/callbacks/navigation.py:2110` `callback_apple_pay_card` | navigation.py | 🔒 | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| Apple ID — pay via SBP (Platega) | `errors.sbp_unavailable_toast` | `apple_pay_sbp:`… | `app/handlers/callbacks/navigation.py:2178` `callback_apple_pay_sbp` | navigation.py | 🔒 | 2026-08-13 `5bb61ec9` i18n(en): полное покрытие + вынос хардкод-русско |  |
| cb_start | — | `spotify:start` | `app/handlers/payments/spotify_purchase.py:177` `cb_start` | navigation.py, spotify_purchase.py | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| cb_info | — | `spotify:info` | `app/handlers/payments/spotify_purchase.py:199` `cb_info` | spotify_purchase.py | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| cb_plans | — | `spotify:plans` | `app/handlers/payments/spotify_purchase.py:220` `cb_plans` | spotify_purchase.py | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| cb_choose_plan | — | `spotify:plan:`… | `app/handlers/payments/spotify_purchase.py:247` `cb_choose_plan` | spotify_purchase.py | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| cb_choose_duration | — | `spotify:dur:`… | `app/handlers/payments/spotify_purchase.py:290` `cb_choose_duration` | spotify_purchase.py | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| msg_email | — | _StateFilter(SpotifyPurchaseState.waiting_for_email_ | `app/handlers/payments/spotify_purchase.py:334` `msg_email` | — | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| cb_email_edit | — | `spotify:email:edit` | `app/handlers/payments/spotify_purchase.py:369` `cb_email_edit` | spotify_purchase.py | 🔒 | 2026-07-26 `72e533fa` feat(shop): Apple ID Russia+India + полный Spoti |  |
| cb_email_ok | — | `spotify:email:ok` | `app/handlers/payments/spotify_purchase.py:386` `cb_email_ok` | spotify_purchase.py | 🔒 | 2026-07-26 `72e533fa` feat(shop): Apple ID Russia+India + полный Spoti |  |
| msg_password | — | _StateFilter(SpotifyPurchaseState.waiting_for_passw_ | `app/handlers/payments/spotify_purchase.py:407` `msg_password` | — | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| cb_pass_edit | — | `spotify:pass:edit` | `app/handlers/payments/spotify_purchase.py:449` `cb_pass_edit` | spotify_purchase.py | 🔒 | 2026-07-26 `72e533fa` feat(shop): Apple ID Russia+India + полный Spoti |  |
| cb_pass_ok | — | `spotify:pass:ok` | `app/handlers/payments/spotify_purchase.py:468` `cb_pass_ok` | spotify_purchase.py | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| cb_payment_methods | — | `spotify:pay` | `app/handlers/payments/spotify_purchase.py:526` `cb_payment_methods` | spotify_purchase.py | 🔒 | 2026-08-19 `af3298b3` feat(lava→wata): UI-подмена всех Lava-кнопок на  |  |
| cb_pay_card | — | `spotify_pay:card:`… | `app/handlers/payments/spotify_purchase.py:613` `cb_pay_card` | spotify_purchase.py | 🔒 | 2026-07-26 `72e533fa` feat(shop): Apple ID Russia+India + полный Spoti |  |
| Spotify — Wata (admin-only beta) | — | `spotify_pay:wata:`… | `app/handlers/payments/spotify_purchase.py:660` `cb_pay_wata` | spotify_purchase.py | 🔒 | 2026-08-15 `1ebc0444` style(wata): переименовать «💳 Wata (тест)» → «🏦  |  |
| cb_pay_sbp | — | `spotify_pay:sbp:`… | `app/handlers/payments/spotify_purchase.py:712` `cb_pay_sbp` | spotify_purchase.py | 🔒 | 2026-08-13 `843f4453` style(buttons): партия 3 — стилизация telegram_p |  |
| Disclaimer screen — countries + internal-rate notice | `shop.steam_disclaimer` | `steam:disclaimer` | `app/handlers/payments/steam_purchase.py:231` `callback_steam_disclaimer` | navigation.py, steam_purchase.py | 🔒 | 2026-05-04 `8812ff5d` feat(shop): add Steam top-up product |  |
| callback_steam_ack | — | `steam:ack` | `app/handlers/payments/steam_purchase.py:271` `callback_steam_ack` | steam_purchase.py | 🔒 | 2026-05-04 `8812ff5d` feat(shop): add Steam top-up product |  |
| callback_steam_page | — | `steam:page:`… | `app/handlers/payments/steam_purchase.py:280` `callback_steam_page` | steam_purchase.py | 🔒 | 2026-05-04 `8812ff5d` feat(shop): add Steam top-up product |  |
| callback_steam_back_to_amount | — | `steam:back_to_amount` | `app/handlers/payments/steam_purchase.py:293` `callback_steam_back_to_amount` | steam_purchase.py | 🔒 | 2026-05-04 `8812ff5d` feat(shop): add Steam top-up product |  |
| callback_steam_amount | `shop.steam_login_prompt` | `steam:amt:`… | `app/handlers/payments/steam_purchase.py:321` `callback_steam_amount` | steam_purchase.py | 🔒 | 2026-05-04 `8812ff5d` feat(shop): add Steam top-up product |  |
| message_steam_login | `shop.steam_invalid_login` | _SteamPurchaseState.waiting_for_login_ | `app/handlers/payments/steam_purchase.py:356` `message_steam_login` | — | 🔒 | 2026-05-04 `8812ff5d` feat(shop): add Steam top-up product |  |
| callback_steam_pay_card | `errors.payments_unavailable` | `steam:pay:card` | `app/handlers/payments/steam_purchase.py:446` `callback_steam_pay_card` | steam_purchase.py | 🔒 | 2026-05-04 `8812ff5d` feat(shop): add Steam top-up product |  |
| Steam — Wata (admin-only beta) | `common.back` | `steam:pay:wata` | `app/handlers/payments/steam_purchase.py:494` `callback_steam_pay_wata` | steam_purchase.py | 🔒 | 2026-08-15 `1ebc0444` style(wata): переименовать «💳 Wata (тест)» → «🏦  |  |
| callback_steam_pay_sbp | `common.back` | `steam:pay:sbp` | `app/handlers/payments/steam_purchase.py:538` `callback_steam_pay_sbp` | steam_purchase.py | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| callback_steam_pay_crypto | `common.back` | `steam:pay:crypto` | `app/handlers/payments/steam_purchase.py:591` `callback_steam_pay_crypto` | steam_purchase.py | 🔒 | 2026-08-13 `7c0bc3fa` style(buttons): партия 2 массового апдейта цвето |  |
| Entry point — show 'enter username' screen | `premium.enter_username` | `premium_buy` | `app/handlers/payments/telegram_premium.py:103` `callback_premium_buy` | navigation.py, telegram_premium.py | 🔒 | 2026-03-31 `d191ddad` feat: add Telegram Premium purchase flow with Yo |  |
| Validate entered username | `premium.attempts_exhausted` | _StateFilter(TelegramPremiumState.waiting_for_usern_ | `app/handlers/payments/telegram_premium.py:132` `process_premium_username` | — | 🔒 | 2026-03-31 `d191ddad` feat: add Telegram Premium purchase flow with Yo |  |
| User selected a period — show payment method | `premium.choose_payment` | `premium_period:`… | `app/handlers/payments/telegram_premium.py:194` `callback_premium_period` | telegram_premium.py | 🔒 | 2026-08-13 `843f4453` style(buttons): партия 3 — стилизация telegram_p |  |
| callback_premium_period_back | `premium.choose_period` | `premium_period_back` | `app/handlers/payments/telegram_premium.py:249` `callback_premium_period_back` | telegram_premium.py | 🔒 | 2026-08-13 `843f4453` style(buttons): партия 3 — стилизация telegram_p |  |
| Create pending purchase and send TG Payments invoice | `common.rate_limit_message` | `premium_pay:card` | `app/handlers/payments/telegram_premium.py:288` `callback_premium_pay_card` | telegram_premium.py | 🔒 | 2026-04-19 `0640e773` fix: add parse_mode="HTML" to all edit_text/send |  |

## 🛠 Админка в боте

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Start admin chat — ask for user ID | — | `admin:chat` | `app/handlers/admin/base.py:247` `callback_admin_chat_start` | apple_id_delivery.py, клавиатуры меню/профиля, spotify_delivery.py, steam_purchase.py, adm | 🛠 | 2026-04-11 `a373331f` Add admin chat: send messages to users through b |  |
| Process user ID input, enter chatting mode | — | _AdminChat.waiting_for_user_id_ | `app/handlers/admin/base.py:266` `process_admin_chat_user_id` | — | 🛠 | 2026-04-19 `d6b60efd` fix: add parse_mode="HTML" to all remaining mess |  |
| Forward admin message to target user | — | _AdminChat.chatting_ | `app/handlers/admin/base.py:318` `process_admin_chat_message` | — | 🛠 | 2026-04-19 `d6b60efd` fix: add parse_mode="HTML" to all remaining mess |  |
| Диагностика видимости кнопки СБП-подписки Platega: подхватился | — | /platega_sub_status | `app/handlers/admin/base.py:107` `cmd_platega_sub_status` | команда | 🛠 | 2026-08-12 `756d8cde` feat(platega): рекуррентные СБП-подписки (admin- | Команда статуса рекуррента Platega — по SCOPE.md код рекуррента удаляется |
| Confirm-then-clear admin web credentials + every active | — | `admin:reset_password` | `app/handlers/admin/base.py:154` `callback_reset_password` | base.py, admin.py | 🛠 | 2026-06-05 `03ba5767` feat(auth): dashboard login/password + cookie se |  |
| callback_reset_password_cancel | — | `admin:reset_password_cancel` | `app/handlers/admin/base.py:194` `callback_reset_password_cancel` | base.py, admin.py | 🛠 | 2026-06-05 `03ba5767` feat(auth): dashboard login/password + cookie se |  |
| callback_reset_password_confirm | — | `admin:reset_password_confirm` | `app/handlers/admin/base.py:208` `callback_reset_password_confirm` | base.py, admin.py | 🛠 | 2026-06-05 `03ba5767` feat(auth): dashboard login/password + cookie se |  |
| «Отмена» / «← Админ-панель» в чате (и кнопки старых админ-сообщений) → меню /admin; выходит из чата | — | `admin:main` | `app/handlers/admin/base.py:89` `callback_admin_main` | base.py (чат) | 🛠 | 2026-09-14, раунд 3 | Раньше — главный экран старой админки (удалена) |
| /admin — ссылка на дашборд, «💬 Написать пользователю», сброс пароля | — | /admin | `app/handlers/admin/base.py:80` `cmd_admin` | команда | 🛠 | 2026-09-14, раунд 3 | Вся остальная админка — веб-дашборд |

## Служебные

| Экран / действие | i18n (первый ключ в теле) | callback_data / команда | Хэндлер | Откуда производится кнопка | Статус | Последний коммит (prod `cf8205fc`) | Примечание |
|---|---|---|---|---|---|---|---|
| Ловит ВСЕ сообщения в default_state которые не были обработаны другими | — | _StateFilter(default_state)_ | `app/core/unknown_message_filter.py:23` `catch_unknown_message` | — | ✅ | 2026-03-01 `a821295a` security: unknown message filter, unicode spam p |  |
| Ловит callback'и, которые не обработал ни один хэндлер (кнопки из стар | `common.button_outdated` | _любой_ | `app/core/unknown_message_filter.py:39` `catch_unknown_callback` | — | ✅ | — |  |

