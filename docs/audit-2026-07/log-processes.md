# Аудит логирования процессов

Дата: 2026-08-05. Ветка `codenew`. Код не менялся — только чтение.

Вопрос, на который отвечает документ: **если завтра человек напишет «я заплатил,
доступ не пришёл» — хватит ли логов, чтобы восстановить, что произошло, и не
придётся ли гадать.**

Всё, что уже закреплено `tests/services/test_log_observability.py`, в отчёт не
вынесено. Проверено отдельно: тест читает живой код, молчаливых skip не осталось
— все шесть путей `_MONEY_PATHS` и все шесть файлов `_BEARER_CODE_FILES`
существуют после разбивок, `_DELIVERY_FUNCS` находят все семь функций выдачи.

Дефекты пронумерованы **Д1…Д75** в порядке последствия для человека и для денег:
Д1 — самое дорогое, Д75 — самое дешёвое. Номер — идентификатор для ссылок, но
порядок значимый: если правится не всё, правится сверху.

---

## 1. Сводка

| | |
|---|---|
| Разобрано процессов | **72** |
| Прослеживаются полностью (старт + результат по факту + идентификатор) | **11** |
| Прослеживаются частично (цепочка рвётся в одном месте) | **34** |
| Не прослеживаются (нет записи о результате либо нет записей вовсе) | **27** |

Дефекты по шести проверкам (один дефект может попадать в несколько строк):

| Проверка | Дефектов | Из них уровня «править первыми» |
|---|---|---|
| 1. Начало и конец видны | 26 | 7 |
| 2. Запись ставится по факту, а не по намерению | 24 | 12 |
| 3. Ошибка не глохнет | 31 | 9 |
| 4. Уровень соответствует последствию | 20 | 4 |
| 5. Есть по чему связать записи | 38 | 5 (один системный — обесценивает 41 запись разом) |
| 6. Секретов в логах нет | 6 | 3 |

Всего **75 отдельных дефектов**.

### Четыре вывода, если читать только это

1. **`extra={...}` в логах не доходит до вывода вообще** (Д2). Ни текстовый
   форматтер, ни JSON-форматтер не печатают дополнительные поля — проверено
   запуском. 41 запись по дереву, включая единственную запись о невыданном
   VPN-ключе и глобальный перехватчик исключений, выходит голой строкой-константой
   без единого идентификатора.
2. **Выдача «награды» логируется и обещается человеку раньше, чем выдаётся**
   (Д1, Д7, Д13–Д16). Скидка из рассылки, скидка «подари другу», активация
   триала, привязка реферала, отправка ключа — во всех случаях запись и сообщение
   человеку идут независимо от того, легла ли строка в базу.
3. **Отзыв и продление доступа в панели пишутся по намерению** (Д3).
   `remnawave_api.update_user` возвращает `None` при отказе, а шесть вызывающих
   пишут «отключено» / «продлено» не глядя на результат. Отдельно — шесть
   записей «удалено», написанных поверх заглушек `vpn_utils`: они не могут быть
   ложными «иногда», они ложны всегда.
4. **Выдача ключа человеку и вход в дашборд не логируются вообще** (Д5, Д40).
   Самое частое обращение («купил, ключа нет») и самое дорогое событие
   безопасности (кто и когда вошёл в админку) не оставляют ни строки.

---

## 2. Таблица процессов

**да** — есть запись о старте, есть запись о результате, результат берётся из
факта, в записи есть идентификатор. **частично** — цепочка рвётся в одном месте.
**нет** — по логам исход не восстановить.

### 2.1 Покупки

| Процесс | Входная точка | Конечное состояние | Прослеживается | Чего не хватает |
|---|---|---|---|---|
| Карта через Telegram-инвойс | `app/handlers/callbacks/pay_external/telegram_invoice.py:57` | `payments` + `subscriptions`, ключ человеку | частично | `VPN_KEY_SENT` пишется и при провале отправки (Д1); `Payment service error` без telegram_id (Д24) |
| Telegram Stars (подписка) | `app/handlers/callbacks/pay_external/telegram_invoice.py:191` | то же, provider=`telegram_stars` | частично | то же |
| Platega карта РФ | `app/handlers/callbacks/pay_external/platega.py:176` | `pending_purchases` → вебхук → подписка | частично | отклонённый вебхук не привязать к покупке (Д12) |
| Platega карта международная | `app/handlers/callbacks/pay_external/platega.py:189` | то же | частично | то же |
| Platega СБП | `app/handlers/callbacks/pay_external/platega.py:202` | то же | частично | то же |
| CryptoBot | `app/handlers/callbacks/pay_external/cryptobot.py:46` | то же | частично | то же |
| Lava | `app/handlers/callbacks/pay_external/lava.py:40` | то же | частично | то же |
| Оплата с внутреннего баланса | `app/handlers/callbacks/pay_balance.py:59` | списание `users.balance` + `grant_access` | **нет** | отказ списания не пишется вовсе (Д4); комбо-ГБ помечаются начисленными при провале (Д8) |
| Пополнение баланса (СБП/Lava) | `app/handlers/callbacks/topup.py:53/136` | `balance_transactions` + `payments` | да | `BALANCE_TOPUP_SUCCESS` (`database/balance_purchases.py:524`) — образцовая запись |
| Пополнение баланса (Stars/карта) | `app/handlers/callbacks/balance_callbacks.py:162`, `app/handlers/callbacks/topup.py:244` | то же | частично | инвойс не логируется, payload нигде не появляется (Д35) |

### 2.2 Выдача и жизненный цикл

| Процесс | Входная точка | Конечное состояние | Прослеживается | Чего не хватает |
|---|---|---|---|---|
| Финализация покупки (общая) | `database/purchase_finalization.py:154` | `payments`, `subscriptions`, Remnawave | да | — |
| Провижининг в панели | `app/services/purchase_flow.py:116` | premium + bypass сущности | да | `PURCHASE_FLOW_DONE`, `BYPASS_PROVISION_FAILED` — образцовые |
| Отложенная активация | `activation_worker.py:105` | `activation_status` pending→active | частично | двойной ITERATION_END (Д59); пропуск при `VPN_ENABLED=false` на DEBUG (Д50) |
| Ручной ретрай активации (дашборд) | `app/api/dashboard/routes/activations.py:57` | повтор выдачи VPN | **нет** | ни старта, ни исхода, ни админа (Д46) |
| Продление | `database/subscription_grant.py:519` | `expires_at`, `sync_renewal_to_remnawave` | частично | `REMNAWAVE_RENEWED` без проверки результата (Д3) |
| Автопродление | `auto_renewal.py:69` | списание + `grant_access` + панель | **нет** | `items_processed=0` константой (Д58); отказ панели после списания — WARNING без payment_id (Д22) |
| Истечение (быстрая очистка) | `fast_expiry_cleanup.py:160` | `status='expired'` или bypass-only | частично | «Remnawave stays active» до попытки продления (Д11) |
| Истечение (точечная проверка) | `database/subscription_state.py:53` | то же | **нет** | все семь записей без telegram_id (Д2); `EXPIRY_REMOVE_SUCCESS` поверх заглушки (Д3) |
| Отзыв доступа админом (БД) | `database/admin_access.py:529` | `status='expired'`, `disable_premium_user` | да | кроме `ADMIN_REVOKE_UUID_REMOVED` поверх заглушки (Д3) |
| Отзыв доступа из дашборда | `app/api/dashboard/routes/users.py:389` | то же | да | START/OK/FAILED/NOOP с `admin=` — образец |
| Выдача админом (дашборд) | `app/api/dashboard/routes/users.py:281` | `grant_access` + уведомление | да | `notify_delivered` по факту — образец |
| Смена тарифа админом | `app/api/dashboard/routes/users.py:441` | смена тарифа живой подписки | **нет** | 0 записей; `admin_switch_tariff` не принимает admin_id (Д41) |
| Выдача/отзыв VIP | `app/api/dashboard/routes/users.py:713` / `:730` | `vip_users` | **нет** | 0 записей в логе (Д41) |
| Личные скидки, traffic-скидки | `app/api/dashboard/routes/users.py:467/500/526/559` | `user_discounts` | **нет** | 0 записей (Д41) |
| Фиксированный % кешбэка | `app/api/dashboard/routes/users.py:584/693`, `database/referral_rates.py:114` | ставка будущих выплат | **нет** | смена ставки 10%→45% анонимна (Д43) |
| Удаление пользователя | `app/api/dashboard/routes/users.py:761` | необратимое удаление | **нет** | 0 записей (Д41) |
| Изменение цен | `app/api/dashboard/routes/pricing.py:56/76/113/141` | `tariff_overrides` | **нет** | ни админа, ни факта; `clear_tariff_override` вообще без админа (Д42) |
| Перевыпуск ключа админом | `database/subscription_reissue.py:290` | новый uuid + panel entity | частично | `PHASE1_COMPLETE` без telegram_id в выводе (Д2) |
| Переход в bypass-only | `database/subscription_state.py:153`, `fast_expiry_cleanup.py:317` | `is_bypass_only=TRUE` | частично | запись до `extend_remnawave_for_bypass_bg` (Д11) |
| **Выдача ключа/ссылки человеку** | `app/handlers/callbacks/connect_guide/keys.py:46`, `app/handlers/user/connect.py:22` | человек видит ссылку | **нет** | ни одной записи во всём пути (Д5) |
| Управление устройствами (HWID) | `app/handlers/user/devices.py:77` | удаление устройства в панели | **нет** | 2 записи на файл, отзыв устройства не пишется |
| Сверка с панелью | `database/reconciliation_fix.py:40` | PATCH `expireAt` + журнал | да | — |
| Массовое восстановление bypass | `app/api/dashboard/routes/bypass_audit.py:122` | восстановление доступа | частично | провал по конкретному человеку только в HTTP-ответе (Д41) |
| Синхронизация с сайтом | `app/workers/site_sync_worker.py:38`, `app/services/site_sync.py:44` | POST + кешбэк на баланс | частично | отказы без telegram_id (Д36); немой простой (Д56) |
| Привязка аккаунта сайта | `app/handlers/user/start/command.py:186` | `users.site_linked=TRUE` | частично | токен печатается частично (Д71) |
| Вход в дашборд | `app/api/dashboard/auth.py:259`, `:378` | сессионная кука | **нет** | успешный вход не логируется вовсе (Д40) |

### 2.3 Подарки

| Процесс | Входная точка | Конечное состояние | Прослеживается | Чего не хватает |
|---|---|---|---|---|
| Покупка подарка с баланса | `app/handlers/callbacks/gift/payment.py:67` | `gift_subscriptions` status=paid | частично | `GIFT_PAID_BALANCE` до отправки ссылки (Д20) |
| Покупка подарка внешней оплатой | `app/handlers/callbacks/gift/payment.py:165/242/315/398` | `pending_purchases` → вебхук | **нет** | о выставленном счёте нет записи ни в одной из 4 веток (Д35) |
| Выдача подарка после оплаты | `app/handlers/payments/goods_delivery.py:75` | `gift_code` + ссылка покупателю | да | — |
| Активация `gift_` | `app/handlers/user/start/command.py:347` | `grant_access` получателю | частично | 6 причин отказа из БД без записи (Д26) |
| Активация `bgift_` | `app/handlers/user/start/command.py:232` | `bypass_gift_redemptions` + ГБ | частично | погашение многоразовой ссылки не пишется (Д25) |
| Подключение обхода после `bgift_` | `app/handlers/user/bypass_gift_setup.py:91` | человек видит ссылку | **нет** | пустая ссылка → экран без ключа, без записи (Д27) |

### 2.4 Рефералы, триал, промо

| Процесс | Входная точка | Конечное состояние | Прослеживается | Чего не хватает |
|---|---|---|---|---|
| Регистрация по реф-ссылке | `app/services/referrals/service.py:24` | `referrals`, `users.referrer_id` | **нет** | `REFERRAL_SAVED` без проверки `UPDATE 1`; одна регистрация даёт 5 записей (Д16) |
| Начисление кешбэка | `database/referral_reward.py:37` | `balance_transactions` + `referral_rewards` | да | `REFERRAL_REWARD_APPLIED` — образцовая; но множитель x2 может молча стать x1 (Д53) |
| Заявка на вывод | `app/handlers/payments/withdraw_fsm.py:53`, `app/handlers/callbacks/balance_callbacks.py:256` | списание + `withdrawal_requests` | **нет** | во `withdraw_fsm.py` 0 записей; отказ в выводе не пишется (Д9) |
| Выплата вывода | `database/users.py:645` | деньги ушли человеку | **нет** | `WITHDRAWAL_APPROVED` без суммы и без получателя (Д9) |
| Возврат вывода | `database/users.py:674` | зачисление обратно | да | `WITHDRAWAL_REJECTED` полная |
| Ставка кешбэка (админ) | `database/referral_rates.py:114/131` | процент будущих выплат | **нет** | 0 записей на файл (Д43) |
| Выдача триала | `app/handlers/callbacks/subscription.py:140` | `grant_access(source='trial')` | частично | `trial_activated` пишется при провале `mark_trial_used` (Д14) |
| Напоминания триала | `trial_notifications.py:178` | флаги `trial_notif_*` | частично | 3h-ветка не пишет потерю; `discount=15%` как факт (Д62); финальное напоминание не отправляется (Д61) |
| Окончание триала | `trial_notifications.py:476` | `status='expired'` / bypass-only | **нет** | `decision=EXECUTED` до повторной проверки (Д15); отзыв premium — WARNING (Д51) |
| Промокод | `app/handlers/payments/promo_fsm.py:28`, `database/promo.py:386` | `promo_usage_logs` | частично | `old_purchases_cancelled=True` — прямая ложь (Д13); отказы потребления без записи (Д28) |
| Скидка из рассылки | `app/handlers/callbacks/broadcast_offers/promo_discounts.py:40/102`, `gift_reveal.py:74` | `user_discounts` | **нет** | «скидка применена» в чате и ноль в БД и в логе (Д7) |
| Промо-ссылка `p-` | `app/handlers/user/start/marketing_links.py:70` | `promo_link_redemptions` + награда | частично | `grant_access` и `create_user_traffic_discount` не проверяются (Д7); `database/marketing_links.py` — 0 записей (Д29) |
| Ссылка-статистика `s-` | `app/handlers/user/start/marketing_links.py:40` | `stats_link_clicks` | частично | «ссылка выключена» неотличимо от «не приходили» (Д29) |
| «Подари другу» `refd_` | `app/handlers/user/start/share_discount.py:43` | скидка 30% + реф-связь | **нет** | `REFDC_CLAIMED pct=30` при невыданной скидке (Д7) |

### 2.5 Товары

| Процесс | Входная точка | Конечное состояние | Прослеживается | Чего не хватает |
|---|---|---|---|---|
| Apple ID | оплата `app/handlers/callbacks/apple_id.py:302/369/197`, выдача `app/handlers/admin/apple_id_delivery.py:221` | код покупателю вручную | **нет** | факт выдачи не пишется вовсе (Д6) |
| Spotify | `app/handlers/payments/spotify_purchase.py:605/652/699` | учётка вручную | **нет** | закрытие заказа админом не логируется (Д6) |
| Steam | `app/handlers/payments/steam_purchase.py:446/496/549/602` | пополнение вручную | **нет** | логин покупателя в логе (Д70); потеря заказа помечена success (Д6) |
| Telegram Premium | `app/handlers/payments/telegram_premium.py:277` | выдача вручную | частично | потеря заказа помечена success (Д6) |
| Telegram Stars (товар) | `app/handlers/payments/telegram_stars_purchase.py:375/420/469` | выдача вручную | частично | то же |
| Прокси | `app/handlers/proxy.py:284` | ссылка покупателю | **нет** | результат доставки не проверяется и не пишется (Д21) |
| Пакет трафика | `app/handlers/traffic/pay_traffic.py:62/132/218` | `add_traffic` в Remnawave | частично | у вебхучного близнеца нет записи об успехе (Д19) |
| Bypass (только ГБ) | `app/handlers/traffic/pay_bypass.py:79/136/201/253/308` | bypass-only + ГБ | частично | у 3 из 5 провайдеров нет записи о счёте (Д35) |
| Витрина пакетов | `app/handlers/traffic/packs.py` | — | **нет** | во всём файле ни одной записи |
| Плёнка от шторма | `app/services/payments/confirmation.py:584` | `farm_plots` + возврат на баланс | да | `FARM_SHIELD_CONFIRMATION` — образцовая |

### 2.6 Рассылки и автоуведомления

| Процесс | Входная точка | Конечное состояние | Прослеживается | Чего не хватает |
|---|---|---|---|---|
| Создание рассылки в дашборде | `app/api/dashboard/routes/broadcasts/send.py:233` | `broadcasts` + фоновая отправка | **нет** | 0 записей в функции; кто запустил — нигде (Д44) |
| Отправка рассылки | `app/services/broadcast_sender.py:41` | `sent/failed/total` + аудит | частично | нет старта; итог всегда INFO; в построчных записях нет `broadcast_id` (Д52) |
| Отложенная: создание/отмена | `app/api/dashboard/routes/broadcasts/scheduled.py:93` / `:199` | `scheduled_broadcasts` | **нет** | 0 записей в обеих ручках (Д44) |
| Отложенная: исполнение | `app/services/scheduled_broadcasts_worker.py:50` | `broadcasts` + отправка | частично | `DISPATCHED` = намерение (Д18) |
| Удаление рассылки из чатов | `app/services/broadcast_deleter.py:74` | `broadcast_log.status='deleted'` | **нет** | статус ставится и неудалённым (Д10); аудит съеден `except: pass` (Д49) |
| Автоуведомления: отправка | `app/services/automated_notifications/helper.py:234` | `automated_notification_sends` | частично | молчаливый выход при недоступном пуле (Д31) |
| Автоуведомления: правка/удаление | `app/api/dashboard/routes/automated_notifications.py:149/203/288` | текст и флаг `is_enabled` | **нет** | 0 записей на 374 строки (Д45) |
| Push-уведомления | `app/services/push_notifications.py:340` | web-push подписчикам | частично | перегенерация VAPID пишется как «first time» (Д17) |

### 2.7 Воркеры и инфраструктура

| Процесс | Входная точка | Конечное состояние | Прослеживается | Чего не хватает |
|---|---|---|---|---|
| activation_worker | `activation_worker.py:516` | активация оплаченных подписок | частично | Д50, Д59, Д32 |
| auto_renewal | `auto_renewal.py:706` | списания и продления | частично | Д22, Д58 |
| fast_expiry_cleanup | `fast_expiry_cleanup.py:51` | отзыв доступа | частично | Д11, Д32 |
| reminders | `reminders.py:431` | напоминания платным | частично | Д37 |
| trial_notifications | `trial_notifications.py:727` | напоминания и гашение триалов | частично | Д15, Д51, Д61, Д62 |
| farm_notifications (шторм) | `app/workers/farm_notifications.py:317` | деньги на баланс за авто-сбор | **нет** | начисление поштучно не пишется (Д38) |
| site_sync_worker | `app/workers/site_sync_worker.py:38` | синхронизация с сайтом | частично | Д56, Д66 |
| traffic_monitor | `app/workers/traffic_monitor.py:192` | уведомления об остатке трафика | частично | Д56 |
| scheduled_broadcasts_worker | `app/services/scheduled_broadcasts_worker.py:174` | отложенные рассылки | частично | Д18 |
| subscription_watchdog | `app/services/subscription_watchdog.py:39` | журнал аномальной выдачи | частично | провал самой защиты — WARNING (Д54) |
| healthcheck | `healthcheck.py:89` | алерты по 5 категориям | да | — |
| Запуск воркеров | `main.py:428` | набор фоновых задач | частично | не запустившийся воркер — WARNING и больше нигде (Д55) |
| Глобальный перехват ошибок | `app/core/telegram_error_middleware.py:31` | ответ «произошла ошибка» | **нет** | ни user_id, ни correlation_id в выводе (Д2) |
| Авторизация запросов дашборда | `app/api/dashboard/deps.py:21` | доступ к админскому API | **нет** | ни одна отклонённая авторизация не пишется (Д40) |

---

## 3. Дефекты

### Уровень A — человек заплатил и не получил, а лог это скрывает

---

#### Д1. «VPN_KEY_SENT» пишется независимо от того, ушло ли сообщение

**Где:** `app/handlers/payments/subscription_success.py:205-208`, `:224-228`,
`:265-270`.

**Что не так.** Отправка экрана успеха обёрнута в try (`:186-203`), обе попытки
могут упасть, и это записывается ERROR-ом. Сразу после — три записи, которые
утверждают обратное:

```python
logger.info(f"process_successful_payment: VPN_KEY_SENT [user={telegram_id}, ...]")      # :205
logger.info(f"... PAYMENT_COMPLETE [... vpn_key_sent=True, subscription_visible=True]")  # :224
await database._log_audit_event_atomic_standalone(
    "telegram_payment_successful", ..., f"... vpn_key_sent=True")                        # :265
```

`vpn_key_sent=True` и `subscription_visible=True` — строковые литералы, они не
вычисляются никогда. Третья запись уходит в `audit_log`, то есть в журнал,
который админ открывает в дашборде при разборе обращения.

**Что произойдёт при разборе.** Человек пишет «оплатил картой, ключ не пришёл».
В логах есть `Failed to send payment success message` (ERROR) — и тут же
`VPN_KEY_SENT`, `PAYMENT_COMPLETE vpn_key_sent=True`, а в дашборде
`telegram_payment_successful … vpn_key_sent=True`. Две записи противоречат друг
другу, и та, которую админ увидит первой (аудит), утверждает, что ключ
отправлен. Разбор уходит в «человек не туда смотрит» вместо «сообщение не
доставлено, надо переслать».

**Срочность: высокая.** Основной путь оплаты, основной тип обращения.

**Предлагаемая запись** (шаблон уже применён в
`app/handlers/callbacks/pay_balance.py:425-434`):

```python
        delivered = False
        try:
            await message.answer(text + degradation, reply_markup=keyboard, parse_mode="HTML")
            delivered = True
            logger.info(
                f"NOTIFICATION_SENT [type=payment_success, payment_id={payment_id}, "
                f"user={telegram_id}, purchase_id={purchase_id}]"
            )
        except Exception as e:
            logger.error(f"Failed to send payment success message: user={telegram_id}, error={e}")
            try:
                await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                delivered = True
            except Exception as fallback_err:
                logger.error(f"Fallback also failed: user={telegram_id}, error={fallback_err}")

    if delivered:
        logger.info(
            f"process_successful_payment: VPN_KEY_SENT [user={telegram_id}, payment_id={payment_id}, "
            f"purchase_id={purchase_id}, expires_at={expires_str}, subscription_type={subscription_type}]"
        )
    else:
        logger.error(
            f"PAYMENT_CONFIRMATION_UNDELIVERED [user={telegram_id}, payment_id={payment_id}, "
            f"purchase_id={purchase_id}, expires_at={expires_str}] — подписка активна, "
            f"человек об этом не знает; сообщение нужно переслать вручную"
        )
```

и в `finish_payment` (`:265`) — `vpn_key_sent={fin.delivered}` вместо литерала.

---

#### Д2. `extra={...}` не попадает в вывод — 41 запись остаётся без единого идентификатора

**Где (корень):** `app/core/logging_config.py:169-171` (текстовый форматтер),
`app/core/logging_config.py:124-142` (`JSONFormatter`).

**Что не так.** Текстовый форматтер собран как
`"%(asctime)s - %(name)s - %(levelname)s - %(message)s"`, `JSONFormatter` строит
словарь из четырёх фиксированных ключей. Ни один не читает `record.__dict__`.
Всё, переданное через `extra=`, отбрасывается в обоих режимах. Проверено
запуском:

```
TEXT : 2026-08-05 18:44:34,683 - t - CRITICAL - ACTIVATION_FAILED_NO_VPN_KEY
JSON : {"timestamp": "...", "level": "CRITICAL", "logger": "t", "message": "ACTIVATION_FAILED_NO_VPN_KEY"}
```

41 вызов по дереву кладёт весь смысл записи именно в `extra`, оставляя в
сообщении голую константу. Самые дорогие:

| Файл:строка | Что теряется | Что остаётся в логе |
|---|---|---|
| `app/handlers/payments/subscription_finalize.py:183-186` | `telegram_id` | `ACTIVATION_FAILED_NO_VPN_KEY` |
| `app/core/telegram_error_middleware.py:80` | `user_id`, тип апдейта | `UNHANDLED_HANDLER_EXCEPTION` + трейс |
| `app/core/telegram_error_middleware.py:71-79` | `correlation_id`, `reason` | `telegram update_processing outcome=failed` |
| `app/handlers/notifications.py:115-125` | referrer, referred, суммы, ошибка | `NOTIFICATION_FAILED` |
| `app/handlers/callbacks/subscription.py:93-100` | то же | `NOTIFICATION_FAILED` |
| `database/subscription_state.py:91,104,114,132,169,190,210` | `telegram_id`, uuid | `EXPIRY_PHASE1`, `EXPIRY_REMOVE_SUCCESS`, `EXPIRY_REMOVE_FAILED`, `EXPIRY_DB_UPDATE_SUCCESS`, `EXPIRY_SKIPPED_RENEWED` |
| `database/admin_access.py:255,259,269,493,497,507,609,613` | `telegram_id`, uuid | `OLD_UUID_REMOVED_AFTER_COMMIT`, `ADMIN_REVOKE_UUID_REMOVED`, … |
| `database/purchase_finalization.py:963,965,975` | `telegram_id`, uuid | `RENEWAL_REMNAWAVE_SYNC_FAILED`, … |
| `database/balance_purchases.py:303,305,315` | то же | то же |
| `app/services/activation/service.py:420,441,458,480,485,492` | `subscription_id`, uuid | `ACTIVATION_ORPHAN_PREVENTED`, … |
| `vpn_utils.py:143,149,155,165` | uuid, номер попытки | `ORPHAN_CLEANUP_ATTEMPT/RETRY/SUCCESS/FAILED` |
| `database/subscription_reissue.py:243` | `telegram_id`, новый uuid | `REISSUE_PHASE1_COMPLETE` |
| `app/core/pool_monitor.py:48,55` | время ожидания, метка | предупреждение о пуле без цифр |
| `app/utils/security.py:398,428,458` | `telegram_id`, `correlation_id`, детали | `[SECURITY_WARNING] …`, `[AUDIT_EVENT] …` |
| `app/core/structured_logger.py:46-60` | `correlation_id`, `duration_ms`, `reason` | `<component> <operation> outcome=<x>` |

`log_security_warning` вызывается на денежном пути —
`app/handlers/payments/payment_preflight.py:96` и `:112`: «невалидный payload
успешной оплаты» уходит в лог без telegram_id и без payload.

**Что произойдёт при разборе.** «Заплатил, ключа нет» → в логах
`CRITICAL ACTIVATION_FAILED_NO_VPN_KEY`. Кому — неизвестно; при десяти таких
строках за день обращение этого человека от девяти других не отличить. «Почему
у меня отключился доступ 3-го числа» → `EXPIRY_DB_UPDATE_SUCCESS` × 500, все
одинаковые. Глобальный перехватчик исключений — последняя линия обороны всех
процессов — даёт трейс без указания, чей апдейт упал.

**Срочность: высокая.**

**Вариант А (рекомендуемый) — научить форматтеры печатать `extra`.** Одна правка,
чинит 41 запись, вызовы менять не надо.

```python
# app/core/logging_config.py
_STD_RECORD_FIELDS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None)))


def _extras(record: logging.LogRecord) -> dict:
    """Поля, переданные вызывающим через extra=. Ни один форматтер их не
    печатал: текстовый шаблон читает только %(message)s, JSON — четыре
    фиксированных ключа. 41 запись по дереву кладёт в extra весь смысл
    (telegram_id, uuid, correlation_id) и оставляет в сообщении константу."""
    return {
        k: v for k, v in record.__dict__.items()
        if k not in _STD_RECORD_FIELDS and k not in ("message", "asctime")
    }
```

в `JSONFormatter.format` — `log_entry.update(_extras(record))` перед `return`;
для текстового режима — подкласс `logging.Formatter`, дописывающий
`" ".join(f"{k}={v}" ...)` в хвост строки.

**Вариант Б — переписать 41 вызов** на подстановку в сообщение. Дороже и
оставляет грабли: следующий `extra=` снова уйдёт в никуда.

К любому варианту — проверка:

```python
def test_extra_fields_reach_the_output():
    """extra= не печатался ни текстовым форматтером, ни JSON-овым.
    41 запись клала туда telegram_id и uuid, а в сообщении оставляла
    константу — CRITICAL «ключ не выдан» выходил без указания, кому."""
    from app.core.logging_config import JSONFormatter
    rec = logging.LogRecord("t", logging.CRITICAL, __file__, 1,
                            "ACTIVATION_FAILED_NO_VPN_KEY", None, None)
    rec.telegram_id = 777
    assert "777" in JSONFormatter().format(rec)
```

---

#### Д3. Отзыв, продление и удаление в панели логируются без проверки результата

**Где:** `app/services/remnawave_service.py:219-237` (`REMNAWAVE_RENEWED`),
`:268-269` (`REMNAWAVE_BYPASS_EXTENDED`), `:300-302` (`REMNAWAVE_KEPT_ACTIVE`),
`:305-306` (`REMNAWAVE_DISABLED`), `:327-329` (`REMNAWAVE_DELETED`),
`:430-435` (`REMNAWAVE_TARIFF_UPDATED`).

**Что не так.** `remnawave_api.update_user` и `delete_user` по контракту модуля
(`app/services/remnawave_api.py:4-6`) **возвращают `None` при любой ошибке и не
бросают**. Все шесть вызывающих результат игнорируют:

```python
        await remnawave_api.update_user(api_uuid, status="DISABLED")   # :305
        logger.info("REMNAWAVE_DISABLED: tg=%s uuid=%s", telegram_id, api_uuid[:8])
```

**Отдельная разновидность — записи поверх заглушек `vpn_utils`.**
`vpn_utils.remove_vless_user` (`vpn_utils.py:112-123`) не делает ничего, кроме
записи `VPN_UTILS_REMOVE_NOOP`. Поверх неё шесть мест пишут «удалено»:

| Файл:строка | Запись | Что на самом деле |
|---|---|---|
| `vpn_utils.py:155` | `ORPHAN_CLEANUP_SUCCESS` | ничего не удалено |
| `database/subscription_state.py:114-117` | `EXPIRY_REMOVE_SUCCESS` | ничего не удалено |
| `database/subscription_state.py:120-127` | аудит `vpn_expire`, `result="success"` | то же, и это в БД |
| `database/admin_access.py:608-609` | `ADMIN_REVOKE_UUID_REMOVED` | ничего не удалено |
| `database/balance_purchases.py:287-292` | `ORPHAN_PREVENTED` (CRITICAL) | сирота осталась в панели |
| `app/services/activation/service.py:440-441` | `ACTIVATION_ORPHAN_PREVENTED` (CRITICAL) | то же |

Эти записи не бывают ложными «иногда» — они ложны всегда: под ними нет действия.
`ORPHAN_PREVENTED` особенно вреден: пишется CRITICAL-ом, читается как «поймали и
починили», а сущность, созданная фазой 2
(`app/services/activation/service.py:403`), остаётся в панели.

**Что произойдёт при разборе.** «Отозвали доступ, а VPN работает» → в логах
`REMNAWAVE_DISABLED` и `ADMIN_REVOKE_UUID_REMOVED`, оба говорят «отключено»,
разбор идёт в «человек путает» вместо «панель не приняла запрос». «Продлил, а
через месяц отключилось» → `REMNAWAVE_RENEWED` в логе есть, значит причину ищут
в базе, где всё правильно.

**Срочность: высокая** для `remnawave_service`; **средняя** для записей поверх
заглушек.

```python
# app/services/remnawave_service.py:305 — disable_remnawave_user
        # update_user отдаёт None на любой отказ панели и не бросает
        # (app/services/remnawave_api.py:4-6). Запись «DISABLED» без проверки
        # результата утверждала отключение доступа, которого не было.
        result = await remnawave_api.update_user(api_uuid, status="DISABLED")
        if result is None:
            logger.error(
                "REMNAWAVE_DISABLE_REJECTED: tg=%s uuid=%s — панель не приняла "
                "запрос, доступ остаётся включённым, нужен ручной разбор",
                telegram_id, api_uuid[:8],
            )
            return
        logger.info("REMNAWAVE_DISABLED: tg=%s uuid=%s", telegram_id, api_uuid[:8])
```

```python
# app/services/remnawave_service.py:219 — renew_remnawave_user
        updated = await remnawave_api.update_user(
            api_uuid, trafficLimitBytes=new_limit, expireAt=expire_str,
            deviceLimit=_device_limit_for_tariff(tariff),
        )
        if updated is None:
            logger.error(
                "REMNAWAVE_RENEW_REJECTED: tg=%s uuid=%s new_limit=%d — оплаченное "
                "продление не применено в панели, доступ отключится по старой дате",
                telegram_id, api_uuid[:8], new_limit,
            )
            return
```

Те же четыре строки — для `:268`, `:300`, `:327`, `:430`. Для записей поверх
заглушек — заменить утверждение на констатацию:

```python
# database/subscription_state.py:113-117
            await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_remove)
            # vpn_utils.remove_vless_user — заглушка (vpn_utils.py:112): xray снят
            # с эксплуатации, удалять здесь нечего. Реальный отзыв делает
            # disable_remnawave_user_bg ниже. Запись «REMOVE_SUCCESS» утверждала
            # снятие доступа, которого не происходило.
            logger.info(
                "EXPIRY_LEGACY_UUID_CLEARED telegram_id=%s uuid=%s — xray-заглушка, "
                "фактический отзыв идёт через Remnawave",
                telegram_id, (uuid_to_remove or "")[:8],
            )
```

---

#### Д4. Отказ списания с баланса не оставляет ни одной записи

**Где:** `app/handlers/callbacks/pay_balance.py:175-179`.

```python
        if not result or not result.get("success"):
            error_text = i18n_get_text(language, "errors.payment_processing")
            await callback.message.answer(error_text, parse_mode="HTML")
            await state.set_state(None)
            return
```

**Что не так.** `finalize_balance_purchase` вернула неуспех — путь заканчивается
молча. Записи о старте на этом пути тоже нет (`log_handler_entry` не
вызывается), запись об успехе (`:451`) не достигается. Тот же дефект для
подарков уже закрыт (`GIFT_BALANCE_DEBIT_FAILED`), на основном пути остался.

**Что произойдёт при разборе.** «Списали деньги, подписки нет» — проверить
нечего: неизвестно, дошёл ли человек до списания, какой тариф выбирал, сколько
стоило и что вернула база. «Баланса не хватило в момент транзакции», «grant_access
упал» и «человек вообще не нажимал кнопку» неразличимы.

**Срочность: высокая.**

```python
        if not result or not result.get("success"):
            # Списание не состоялось. Раньше ветка молчала: человек видел
            # «ошибка обработки платежа», а в логах покупки не существовало
            # вовсе — отличить недостаток баланса от упавшего grant_access
            # было нечем. Тот же дефект уже закрыт для подарков
            # (GIFT_BALANCE_DEBIT_FAILED).
            logger.error(
                "BALANCE_PURCHASE_FAILED user=%s tariff=%s period_days=%s "
                "price_rub=%.2f reason=%s — деньги могли быть списаны, "
                "подписка не выдана",
                telegram_id, tariff_type, period_days, final_price_rubles,
                (result or {}).get("reason") or "unknown",
            )
            error_text = i18n_get_text(language, "errors.payment_processing")
            await callback.message.answer(error_text, parse_mode="HTML")
            await state.set_state(None)
            return
```

Плюс запись о старте после проверки FSM (`:139`):

```python
    logger.info(
        "BALANCE_PURCHASE_START user=%s tariff=%s period_days=%s price_rub=%.2f "
        "balance_rub=%.2f",
        telegram_id, tariff_type, period_days, final_price_rubles, balance_rubles,
    )
```

---

#### Д5. Выдача ключа человеку не логируется ни одной записью

**Где:** `app/handlers/callbacks/connect_guide/keys.py:46` и `:203` (во всём
файле нет ни одного `logger`), `app/handlers/user/connect.py` (0 записей),
`app/services/user_subscription_links.py:66,71,89,102,111` — пять `return None`
без записи.

**Что не так.** Экран «добавьте ключ» берёт ссылку через
`get_user_primary_subscription_url`. Та возвращает `None` молча в пяти случаях:
Remnawave выключен, пул недоступен, строки подписки нет, `remnawave_premium_uuid`
пуст, панель не отдала `subscriptionUrl`. Экран отвечает:

```python
    if not sub_url and not bypass_url:            # keys.py:78
        await safe_edit_text(callback.message, i18n_get_text(
            language, "setup.no_subscription", "🔑 <b>Пока нечего подключать</b>…"))
        return
```

Для человека с активной оплаченной подпиской это инцидент: он заплатил, а бот
говорит «нечего подключать». Ни одной записи не остаётся.

**Что произойдёт при разборе.** «Купил, а ключа нет» — самое частое обращение.
Не видно ни того, что человек открывал экран, ни того, что ссылка оказалась
пустой, ни на каком из пяти шагов она потерялась. Разбор — только просьбой
повторить действие и чтением базы руками.

**Срочность: высокая.** Дёшево чинится, закрывает главный класс обращений.

```python
# app/handlers/callbacks/connect_guide/keys.py:78
    if not sub_url and not bypass_url:
        # Экран достижим и без подписки, но для человека с активной строкой это
        # инцидент: он заплатил, а бот отвечает «нечего подключать». Раньше ветка
        # молчала, и обращение «купил, ключа нет» разбирать было нечем.
        if subscription and subscription.get("status") == "active":
            logger.error(
                "CONNECT_KEYS_EMPTY_FOR_ACTIVE user=%s platform=%s expires_at=%s "
                "— ссылки не отдались, ручной разбор",
                telegram_id, platform, subscription.get("expires_at"),
            )
        else:
            logger.info(
                "CONNECT_KEYS_EMPTY user=%s platform=%s — подписки нет, показан "
                "экран покупки", telegram_id, platform,
            )
```

и запись об успехе перед отправкой клавиатуры (`keys.py:176`):

```python
    logger.info(
        "CONNECT_KEYS_SHOWN user=%s platform=%s premium=%s bypass=%s",
        telegram_id, platform, bool(sub_url), bool(bypass_url),
    )
```

В `app/services/user_subscription_links.py` — назвать причину пустого ответа
(строки 89, 102, 111):

```python
        if not panel_uuid:
            logger.warning(
                "USER_PREMIUM_URL_NO_PANEL_UUID: tg=%s — сущность premium не создана",
                telegram_id,
            )
            return None
```

---

#### Д6. Потеря заказа на ручную выдачу помечается успехом

**Где:** `app/handlers/payments/telegram_premium.py:451-452`,
`app/handlers/payments/telegram_stars_purchase.py:577-578`,
`app/handlers/payments/steam_purchase.py:722-723`,
`app/handlers/payments/spotify_purchase.py:829-830`,
`app/handlers/callbacks/apple_id.py:293-298`.

**Что не так.** Для пяти товаров **уведомление админу — единственный канал
выдачи**: карточка заказа с кнопкой «Выдать». Все пять `send_*_success` ловят
провал этой отправки у себя и возвращаются нормально:

```python
    except Exception as e:
        logger.error("PREMIUM_ADMIN_NOTIFY_FAILED error=%s", e)   # :451
```

После чего `deliver_premium` (`goods_delivery.py:207`) пишет
`PREMIUM_PAYMENT_FINALIZED` и закрывает span с `outcome="success"` (`:225`). В
записи об отказе нет ни `telegram_id`, ни `purchase_id`, ни номинала.

Тот же разрыв в ветках `*_SUCCESS_NO_PURCHASE` (`telegram_premium.py:399`,
`telegram_stars_purchase.py:529`, `steam_purchase.py:662`,
`spotify_purchase.py:759`): функция выходит через `return`, а вызывающий всё
равно пишет `..._PAYMENT_FINALIZED` и `outcome="success"`.

Дополнительно: сама выдача админом не логируется вовсе —
`app/handlers/admin/apple_id_delivery.py:274-296` (успешная отправка ключа
покупателю не пишется, только провал на `:280`) и
`app/handlers/admin/spotify_delivery.py:84-111` (закрытие заказа кнопкой
«Выполнено» не оставляет записи).

**Что произойдёт при разборе.** «Оплатил Apple ID неделю назад, ничего не
пришло» → в аудите `outcome="success"`, `APPLE_PAYMENT_FINALIZED`; заказ выглядит
выданным. Строка `APPLE_ADMIN_NOTIFY_FAILED error=TimeoutError` рядом — без
telegram_id и purchase_id, связать нельзя. И даже связав: выдали ли ключ потом
руками — неизвестно, потому что факт выдачи не пишется никогда.

**Срочность: высокая.**

```python
    # app/handlers/payments/telegram_premium.py:447-452
    try:
        await bot.send_message(config.ADMIN_TELEGRAM_ID, admin_text, ...)
        logger.info("PREMIUM_ADMIN_NOTIFIED purchase_id=%s user=%s", purchase_id, telegram_id)
        return True
    except Exception as e:
        # Карточка заказа админу — единственный канал выдачи Premium. Раньше
        # исключение гасилось здесь, и deliver_premium закрывал span с
        # outcome="success": оплаченный и невыданный заказ в аудите неотличим
        # от выданного.
        logger.critical(
            "PREMIUM_ORDER_LOST purchase_id=%s user=%s amount=%s error=%s: %s — "
            "оплачено, карточка заказа админу не ушла, выдать вручную",
            purchase_id, telegram_id, amount_rubles, type(e).__name__, e,
        )
        return False
```

```python
    # app/handlers/payments/goods_delivery.py:207
        notified = await send_premium_success(
            message.bot, telegram_id, purchase_id, pending_purchase,
        )
        if not notified:
            _outcome, _error_type = "degraded", "dependency_error"
        logger.info(
            "PREMIUM_PAYMENT_FINALIZED purchase_id=%s user=%s amount=%s admin_notified=%s",
            purchase_id, telegram_id, payment_amount_rubles, notified,
        )
```

Для выдачи админом (`apple_id_delivery.py:290`, `spotify_delivery.py:102`):

```python
        logger.info(
            "APPLE_KEY_DELIVERED buyer=%s purchase_id=%s admin=%s region=%s nominal=%s",
            buyer_id, purchase_id, callback.from_user.id, region, nominal,
        )
```

---

#### Д7. «Скидка применена» говорится человеку и пишется в лог до того, как скидка создана

**Где:**
`app/handlers/callbacks/broadcast_offers/promo_discounts.py:70-75` и `:91-94`,
`:122-127` и `:177`;
`app/handlers/callbacks/broadcast_offers/gift_reveal.py:136-149`;
`app/handlers/user/start/share_discount.py:143-152` и `:173-176`;
`app/handlers/user/start/marketing_links.py:242-255`, `:293-301`, `:157-163`;
`database/discounts.py:87-100`, `database/traffic.py:531-535`.

**Что не так.** Четыре независимых пути выдачи скидок сходятся к
`database.create_user_discount` / `create_user_traffic_discount`, которые
возвращают `bool` и **глотают исключение внутри** (`discounts.py:101-103`), а при
`DB_READY=False` или `pool is None` молча отдают `False`
(`database/traffic.py:531-535`) — тоже без записи. Ни один из четырёх вызывающих
результат не проверяет:

```python
    await database.create_user_discount(...)                       # promo_discounts.py:70
    ...
    await callback.message.answer(f"🎁 Скидка {discount_percent}% автоматически применена!")   # :91
```

```python
    await callback.message.answer(f"<b>Для тебя подарок {percent}% скидка…</b>")   # gift_reveal.py:136
    ...
    await database.create_user_discount(...)                                       # gift_reveal.py:144
```

```python
    # share_discount.py:143-152 — исключение ловится и глотается «не критично»
    logger.info("REFDC_CLAIMED user=%s referrer=%s pct=%s hours=%s", ..., 30, 24)   # :173
```

В `gift_reveal` сообщение отправляется **раньше** создания скидки. В
`share_discount` claim записывается навсегда (lifetime-once), то есть повторить
человек не сможет. В `marketing_links.py:242` результат `grant_access` не
проверяется вовсе, и `PROMO_LINK_ACTIVATED` пишется поверх пустого ответа.

**Что произойдёт при разборе.** Человек пишет «мне пришло, что скидка 30%
применена, а при оплате её нет». В логах — `REFDC_CLAIMED … pct=30` и
`GIFT_REVEAL_CLICK … pct=30`. Обе записи подтверждают выдачу. В базе скидки нет.
Разбор упирается в «лог говорит, что выдали» и уходит в поиск ошибки на экране
оплаты. При этом claim уже израсходован — вернуть человеку скидку тем же путём
нельзя.

**Срочность: высокая.** Это обещание, данное человеку и не выполненное, плюс
навсегда израсходованный одноразовый слот.

```python
# app/handlers/callbacks/broadcast_offers/promo_discounts.py:70
    # create_user_discount возвращает bool и глотает исключение внутри
    # (database/discounts.py:101-103), а при неготовой базе молча отдаёт False.
    # Раньше результат не читался, и человеку уходило «скидка применена»
    # при нулевом результате — а в логе не было ни строки.
    created = await database.create_user_discount(
        telegram_id=telegram_id, discount_percent=discount_percent, hours=hours,
        created_by=None, source="broadcast_offer",
    )
    if not created:
        logger.error(
            "BROADCAST_DISCOUNT_NOT_CREATED user=%s pct=%s hours=%s — человеку "
            "обещана скидка, в базе её нет",
            telegram_id, discount_percent, hours,
        )
        await callback.answer(i18n_get_text(language, "errors.try_later"), show_alert=True)
        return
    logger.info(
        "BROADCAST_DISCOUNT_APPLIED user=%s pct=%s hours=%s",
        telegram_id, discount_percent, hours,
    )
```

В `gift_reveal.py` — переставить порядок: сначала `create_user_discount`, потом
сообщение. В `share_discount.py:173` — писать фактический процент и признак
`kept_existing`, а не константу 30. В `database/discounts.py:98` — добавить
запись об успешном создании (сейчас есть только audit-событие в БД):

```python
            logger.info(
                "USER_DISCOUNT_CREATED user=%s pct=%s until=%s source=%s created_by=%s",
                telegram_id, discount_percent, expires_at, source, created_by,
            )
```

---

#### Д8. «COMBO_BYPASS_TRAFFIC_ADDED» пишется после провала начисления

**Где:** `app/handlers/callbacks/pay_balance.py:483-486`.

```python
                if not rmn_success:
                    logger.warning(f"COMBO_BYPASS_TRAFFIC_FAIL_BALANCE user={telegram_id} gb={gb}")
                await database.record_traffic_purchase(telegram_id, gb, 0)
                logger.info(f"COMBO_BYPASS_TRAFFIC_ADDED_BALANCE user={telegram_id} gb={gb}")
```

**Что не так.** `ADDED` пишется безусловно, сразу после `FAIL`.
`record_traffic_purchase` тоже выполняется при провале — в базе появляется запись
о трафике, которого в панели нет. Уровень провала — WARNING. Тот же путь у
вебхуков сделан правильно (`app/services/payments/confirmation.py:783-791`: при
`not rmn_ok` — ERROR и `TransientPaymentError`); расходится только оплата с
баланса, где повтора нет по определению.

**Что произойдёт при разборе.** «Купил комбо с баланса, гигабайты не пришли» →
поиск по `COMBO_BYPASS_TRAFFIC_ADDED_BALANCE` находит запись, база подтверждает,
панель — нет. Расхождение объясняется только строкой WARNING выше, которую при
поиске по тегу «ADDED» никто не увидит.

**Срочность: высокая.**

```python
                if not rmn_success:
                    # Оплаченные гигабайты не доехали до панели, и повтора здесь
                    # не будет: деньги уже списаны с баланса, вебхука нет.
                    # Запись ADDED ниже стояла безусловно и утверждала обратное.
                    logger.error(
                        "COMBO_BYPASS_TRAFFIC_FAIL_BALANCE user=%s gb=%s payment_id=%s "
                        "— оплачено, трафик в панель не начислен, нужен ручной разбор",
                        telegram_id, gb, payment_id,
                    )
                else:
                    await database.record_traffic_purchase(telegram_id, gb, 0)
                    logger.info(
                        "COMBO_BYPASS_TRAFFIC_ADDED_BALANCE user=%s gb=%s payment_id=%s",
                        telegram_id, gb, payment_id,
                    )
```

---

#### Д9. Вывод денег: отказ не пишется, а сама выплата — без суммы и без получателя

**Где:**
`app/handlers/payments/withdraw_fsm.py` — весь файл (`logger` объявлен на `:23`,
вызовов 0);
`app/handlers/callbacks/balance_callbacks.py:272-276`, `:285-286`, `:330-331`,
`:367-368`, `:396-397`;
`database/users.py:658-659`, `:672`, `:673`.

**Что не так.** Три разрыва подряд на пути реальных денег:

```python
        wid = await database.create_withdrawal_request(...)
        if not wid:
            await callback.answer(…insufficient_funds…)   # balance_callbacks.py:272-276
            await state.clear()
            return                                        # ни одной записи
```
```python
    logger.info(f"WITHDRAWAL_APPROVED withdrawal_id={wid} processed_by={processed_by}")   # users.py:672
```
```python
        if not row:
            return False            # users.py:658 — провал выплаты без записи
```

`WITHDRAWAL_APPROVED` — момент фактической выплаты денег человеку — **не
содержит ни telegram_id получателя, ни суммы**. Соседняя запись об отказе сделана
правильно: `users.py:724` `WITHDRAWAL_REJECTED withdrawal_id=… processed_by=…
user=… refunded=… kopecks`.

Плюс два `except Exception: pass` (`balance_callbacks.py:285-286` — внутри
подтверждения вывода, когда деньги уже списаны; `:330-331` — гасит провал
резервного audit-события сразу после `CRITICAL: Failed to send withdrawal
notification to admin`, то есть деньги списаны, админ не знает, следа нет).

**Что произойдёт при разборе.** «Подал на вывод, деньги не пришли» — нужно
понять, на каком шаге остановилось. Списание видно (`users.py:626`
`WITHDRAWAL_REQUEST_CREATED … amount=… kopecks`), выплата — по номеру заявки, без
суммы и без получателя, то есть сверить с банковской выпиской нельзя. Отказ в
выводе («недостаточно средств» на экране) — не виден вообще, и спор «у меня было
достаточно» разрешить нечем.

**Срочность: высокая.** Это единственный процесс, где деньги уходят наружу.

```python
# database/users.py:672
        logger.info(
            "WITHDRAWAL_APPROVED withdrawal_id=%s user=%s amount=%s kopecks "
            "processed_by=%s — выплата подтверждена",
            wid, row["user_id"], row["amount"], processed_by,
        )
```
```python
# database/users.py:658
        if not row:
            logger.error(
                "WITHDRAWAL_APPROVE_NOT_FOUND withdrawal_id=%s processed_by=%s — "
                "заявка не найдена или уже обработана, админу показана ошибка",
                wid, processed_by,
            )
            return False
```
```python
# app/handlers/callbacks/balance_callbacks.py:272
        wid = await database.create_withdrawal_request(...)
        if not wid:
            logger.error(
                "WITHDRAWAL_REQUEST_REJECTED user=%s amount_rub=%.2f — заявка не "
                "создана, человеку показано «недостаточно средств»",
                telegram_id, amount_rubles,
            )
```

---

#### Д10. Рассылка помечается удалённой у тех, у кого её удалить не удалось

**Где:** `database/broadcast_analytics.py:188-196`, вызов —
`app/services/broadcast_deleter.py:146`.

```sql
UPDATE broadcast_log SET status = 'deleted'
 WHERE broadcast_id = $1 AND status = 'sent' AND message_id IS NOT NULL
```

**Что не так.** Запрос выполняется **после** цикла удаления и переводит в
`deleted` **все** строки рассылки, включая те, где Telegram отказался удалять
сообщение (`failed`). Это персистентная запись, и она врёт: «удалено» стоит у
людей, у которых сообщение осталось в чате. Повторный прогон их уже не увидит —
`get_broadcast_message_ids` фильтрует по `status='sent'`
(`database/broadcast_analytics.py:176-185`).

**Что произойдёт при разборе.** Разослали ошибочное сообщение (не та цена, не тот
текст), нажали «удалить из чатов», получили `deleted=48000 failed=2000`. В базе
у всех 50000 стоит `deleted`. Повторить удаление для двух тысяч нельзя — они
исчезли из выборки. И узнать, у кого именно сообщение осталось, тоже нельзя:
построчные записи о провале пишутся только для первых трёх
(`broadcast_deleter.py:116-119`, под `if failed <= 3 or failed % 200 == 0`) и на
уровне INFO.

**Срочность: высокая.** Единственный механизм отзыва ошибочной рассылки
необратимо портит собственное состояние.

```python
# database/broadcast_analytics.py:188 — помечать только фактически удалённые
async def mark_broadcast_messages_deleted(broadcast_id: int, telegram_ids: list[int]) -> int:
    """Пометить удалёнными ТОЛЬКО те строки, для которых Telegram подтвердил
    удаление. Раньше запрос брал все status='sent' разом, включая те, где
    удаление провалилось: сообщение оставалось в чате, а строка уходила в
    'deleted' и выпадала из выборки повторного прогона навсегда."""
    ...
    logger.info(
        "BROADCAST_MARKED_DELETED broadcast_id=%s marked=%s requested=%s",
        broadcast_id, marked, len(telegram_ids),
    )
```

и в `broadcast_deleter.py:169`:

```python
    log = logger.error if failed else logger.info
    log(
        "BROADCAST_DELETE_DONE bid=%s admin=%s deleted=%s failed=%s total=%s — "
        "строки со статусом sent остаются доступны для повторного прогона",
        broadcast_id, admin_telegram_id, deleted, failed, total,
    )
```

---

#### Д11. «Remnawave stays active» пишется до попытки продлить обход

**Где:** `fast_expiry_cleanup.py:317-320` и `:324`;
`trial_notifications.py:557` и `:561`, `:583`;
`database/subscription_state.py:169-172` и `:176`.

**Что не так.** Все три места переводят подписку в bypass-only и пишут запись,
утверждающую, что обход продолжит работать:

```python
                    logger.info(
                        "EXPIRY_TRANSITION_TO_BYPASS_ONLY user=%s — Remnawave stays active",
                        telegram_id,
                    )
                    try:
                        extend_remnawave_for_bypass_bg(telegram_id)   # fire-and-forget
                    except Exception as rmn_err:
                        logger.warning("REMNAWAVE_BYPASS_EXTEND_FAIL: tg=%s %s", telegram_id, rmn_err)
```

Запись стоит **до** вызова, вызов — fire-and-forget, результат никуда не
возвращается. `except` ловит только сбой планирования задачи; отказ панели уходит
в чужую запись `REMNAWAVE_BYPASS_EXTEND_ERROR`
(`app/services/remnawave_service.py:271`), не связанную с этой строкой.
В `trial_notifications.py:583` та же запись ставится сразу после `UPDATE` без
проверки числа строк — при 0 строк подписка осталась trial/active.

**Что произойдёт при разборе.** Человек купил гигабайты обхода, подписка
кончилась, обход перестал работать. В логах — `TRANSITION_TO_BYPASS_ONLY …
Remnawave stays active`. Утверждение прямо противоречит жалобе, разбор уходит в
приложение и устройство вместо панели.

**Срочность: средняя-высокая** (оплаченные ГБ).

```python
                if rows > 0:
                    # Запись стояла ДО продления expireAt и утверждала, что обход
                    # продолжит работать. Продление — fire-and-forget, результата
                    # не возвращает: при отказе панели оплаченные ГБ умирали, а лог
                    # говорил обратное. Теперь говорим только то, что знаем.
                    logger.info(
                        "EXPIRY_TRANSITION_TO_BYPASS_ONLY user=%s rows=%s — строка "
                        "переведена в bypass-only, продление expireAt поставлено в очередь",
                        telegram_id, rows,
                    )
```

и в `app/services/remnawave_service.py:269` — тег, общий с этой записью:

```python
        logger.info(
            "EXPIRY_TRANSITION_BYPASS_PANEL_OK tg=%s uuid=%s — expireAt +10 лет",
            telegram_id, api_uuid[:8],
        )
```

---

#### Д12. Отклонённый вебхук провайдера не привязать ни к платежу, ни к человеку

**Где:** `platega_service.py:174-180`, `cryptobot_service.py:158-164`,
`app/api/payment_webhook.py:206` (ветка `unauthorized` Lava).

```python
    if not hmac.compare_digest(str(merchant_id), str(PLATEGA_MERCHANT_ID)) or ...:
        logger.warning("Platega webhook: auth failed")
        return {"status": "unauthorized"}
```

**Что не так.** В записи нет ничего: ни `transaction_id` (он есть в теле —
`body.get("id")`), ни суммы. Уровень WARNING. Ветка выполняется и в единственном
по-настоящему важном сценарии: секрет провайдера разъехался после ротации, и
**все** входящие уведомления об оплате отвергаются.

**Что произойдёт при разборе.** Десять человек за час пишут «заплатил, ничего не
пришло». В логах — десять одинаковых строк `Platega webhook: auth failed` без
единого различающего поля. Понять, что это те же самые десять платежей, и найти
их в кабинете провайдера, нельзя. Отличить «нас сканируют боты» от «наши
собственные платежи отвергаются» — тоже нельзя, а от этого зависит, будить ли
дежурного.

**Срочность: средняя-высокая.**

```python
    if not hmac.compare_digest(str(merchant_id), str(PLATEGA_MERCHANT_ID)) or not hmac.compare_digest(str(secret), str(PLATEGA_SECRET)):
        # Идентификатор транзакции берём из тела: без него нельзя отличить
        # сканера от собственного платежа, отвергнутого после ротации секрета,
        # и нельзя найти платёж в кабинете провайдера.
        logger.error(
            "PLATEGA_WEBHOOK_UNAUTHORIZED transaction_id=%s status=%s amount=%s — "
            "уведомление об оплате отвергнуто по заголовкам",
            body.get("id") or body.get("transactionId"),
            (body.get("status") or "").lower(),
            (body.get("paymentDetails") or {}).get("amount"),
        )
        return {"status": "unauthorized"}
```

Аналогично для CryptoBot (`cryptobot_service.py:160`, `:163`) — с
`payload_obj.get("invoice_id")`.

---

### Уровень B — запись утверждает то, чего не было

---

#### Д13. `old_purchases_cancelled=True` — прямая ложь в записи

**Где:** `app/handlers/payments/promo_fsm.py:126-129`.

```python
        logger.info(
            f"promo_applied: user={telegram_id}, promo_code={promo_code}, "
            f"discount_percent={discount_percent}%, old_purchases_cancelled=True"
        )
```

**Что не так.** На `:118` в том же обработчике стоит комментарий «КРИТИЧНО: НЕ
отменяем pending покупки», и ни одной отмены в ветке нет. Запись сообщает о
действии, которого не существует.

**Что произойдёт при разборе.** «Применил промокод, а старый счёт остался
висеть и списал полную цену» → в логе `old_purchases_cancelled=True`, и разбор
идёт искать, почему отмена не сработала, вместо того чтобы понять, что отмены
нет по замыслу.

**Срочность: средняя-высокая** — стоит копейки, а вводит в заблуждение прямо.

```python
        logger.info(
            f"promo_applied: user={telegram_id}, promo_code={promo_code}, "
            f"discount_percent={discount_percent}% — pending-покупки намеренно "
            f"не отменяются (см. комментарий выше)"
        )
```

---

#### Д14. «trial_activated» пишется при провале отметки об использованном триале

**Где:** `app/handlers/callbacks/subscription.py:261-264` (проверка `mark_ok` на
`:246`); `database/trials_queries.py:107`.

**Что не так.** Запись `trial_activated: user=…, trial_used_at=<метка>` ставится
независимо от `mark_ok`. Рядом есть честный ERROR (`:247-251`), но выданные
триалы считают по строке `trial_activated`. В самом БД-слое
(`trials_queries.py:107`) `Trial marked as used` пишется сразу после
`conn.execute` без проверки `UPDATE 1` — при отсутствующем `telegram_id` в
`users` UPDATE затрагивает 0 строк, а запись говорит «помечен».

**Что произойдёт при разборе.** Человек берёт триал повторно (флаг не встал), и
на вопрос «почему у него три триала» лог отвечает тремя записями
`trial_activated` с проставленными `trial_used_at` — то есть картиной, в которой
флаг ставился каждый раз.

**Срочность: средняя.**

```python
        if mark_ok:
            logger.info(
                f"trial_activated: user={telegram_id}, trial_used_at={now.isoformat()}, "
                f"expires_at={trial_expires.isoformat()}"
            )
        else:
            logger.error(
                f"TRIAL_ACTIVATED_UNMARKED: user={telegram_id} — подписка выдана, "
                f"trial_used_at НЕ проставлен: человек сможет взять триал повторно"
            )
```
```python
# database/trials_queries.py:107
        result = await conn.execute(...)
        if result != "UPDATE 1":
            logger.error(
                "TRIAL_MARK_USED_NOOP user=%s result=%s — строки users нет, флаг не встал",
                telegram_id, result,
            )
            return False
        logger.info(f"Trial marked as used: user={telegram_id}, expires_at={expires_at}")
```

---

#### Д15. «TRIAL_EXPIRATION_EXECUTED» пишется до повторной проверки, которая всё отменяет

**Где:** `trial_notifications.py:510-514`, повторная проверка — `:516-525`.

**Что не так.** Запись `decision=EXECUTED` ставится **до** `active_paid_recheck`,
который делает `return` без единого изменения (человек успел купить платную
подписку). Лог утверждает «EXECUTED» для триалов, которые не закрывались.

**Что произойдёт при разборе.** Подсчёт закрытых триалов по логу завышен, а на
вопрос «почему у человека триал не закрылся» лог отвечает «закрылся».

**Срочность: средняя.**

**Правка:** перенести запись за `active_paid_recheck`, а на месте проверки
добавить:

```python
        if active_paid_recheck:
            logger.info(
                "TRIAL_EXPIRATION_SKIPPED_PAID user=%s — человек оформил платную "
                "подписку между выборкой и обработкой, триал не закрываем",
                telegram_id,
            )
            return
```

---

#### Д16. Привязка реферала пишется без проверки, и одна регистрация даёт пять записей

**Где:** `app/services/referrals/service.py:195` и `:201-208`, `:205`;
`database/referral_codes.py:225`, `:229`; `app/utils/referral_middleware.py:69`.

**Что не так.** Два дефекта в одном процессе.

Первый: `success = True` ставится сразу после
`UPDATE … AND referrer_id IS NULL` **без проверки числа строк**, после чего
пишется `REFERRAL_SAVED [referrer=…, referred=…, state=REGISTERED]`. При
`UPDATE 0` (реферер уже был) лог утверждает, что привязка сохранена. Рядом, в
`database/referral_codes.py:218`, тот же случай сделан честно:
`if result == "UPDATE 1":` плюс чтение обратно.

Второй: одна регистрация порождает **три записи `REFERRAL_REGISTERED` и две
`REFERRAL_SAVED`** — из трёх разных слоёв. Подсчёт регистраций по логу завышен
втрое.

**Что произойдёт при разборе.** Спор «мой реферал не засчитался»: в логах есть
`REFERRAL_SAVED` с обоими id, значит «засчитан». В базе — прежний реферер.
И отдельно: любая сводка «сколько пришло по рефералам» по логам завышена втрое.

**Срочность: средняя** (деньги — кешбэк — считаются по базе, но разбор споров
идёт по логам).

```python
        result = await conn.execute(
            "UPDATE users SET referrer_id = $1, referred_by = $2 "
            "WHERE telegram_id = $3 AND referrer_id IS NULL",
            referrer_user_id, referral_code, telegram_id,
        )
        if result != "UPDATE 1":
            # referrer_id уже стоял: привязка иммутабельна. Раньше success
            # выставлялся безусловно, и лог сообщал о сохранении, которого не было.
            logger.info(
                "REFERRAL_NOT_SAVED_ALREADY_LINKED referred=%s attempted_referrer=%s "
                "result=%s", telegram_id, referrer_user_id, result,
            )
            return False
```

и убрать дублирующие записи, оставив одну — в
`app/services/referrals/service.py`.

---

#### Д17. Перегенерация VAPID-ключей пишется как «first time»

**Где:** `app/services/push_notifications.py:133-137`.

```python
    except Exception:
        pass
    keys = _generate_vapid_keys()
    await _write_setting("vapid_keys", json.dumps(keys))
    logger.info("VAPID keys generated (first time)")
```

**Что не так.** Битый JSON в `app_settings` проглатывается, ключи
**перегенерируются**, и все существующие push-подписки становятся мёртвыми. В
лог уходит `"(first time)"` — заведомо неверное утверждение, на уровне INFO.

**Что произойдёт при разборе.** «Перестали приходить push-уведомления у всех
разом». В логе — `VAPID keys generated (first time)`, что читается как обычная
первая инициализация, а не как «мы только что обнулили все подписки».

**Срочность: средняя.**

```python
    existing_raw = await _read_setting("vapid_keys")
    if existing_raw:
        try:
            return json.loads(existing_raw)
        except Exception as e:
            # Раньше исключение глоталось, ключи молча перегенерировались, и все
            # зарегистрированные подписки становились мёртвыми — а запись
            # утверждала «(first time)».
            logger.critical(
                "VAPID_KEYS_UNREADABLE error=%s — ключи будут перевыпущены, все "
                "существующие push-подписки станут недействительными",
                e,
            )
    keys = _generate_vapid_keys()
    await _write_setting("vapid_keys", json.dumps(keys))
    logger.warning("VAPID_KEYS_GENERATED existing=%s", bool(existing_raw))
```

---

#### Д18. Отложенная рассылка отмечается выполненной по факту постановки задачи

**Где:** `app/services/scheduled_broadcasts_worker.py:140`, `:162`, `:168-171`;
уровни — `:114`, `:125`.

**Что не так.** Порядок: `asyncio.create_task(send_broadcast(...))` (`:140`) →
`mark_ran_and_reschedule` (`:162`) → `SCHED_BROADCAST_DISPATCHED … audience=N`
(`:168`). Задание помечено выполненным и записано как отправленное до того, как
рассылка что-либо доставила; `audience` — размер выборки. Сколько человек
получили, в этой записи не появится никогда.

Плюс `SCHED_BROADCAST_DISC_FAIL` (`:114`) и `SCHED_BROADCAST_GIFTREV_FAIL`
(`:125`) — WARNING на случай, когда рассылка уйдёт с кнопкой «купить со скидкой»,
а скидки в базе нет.

**Срочность: средняя.**

```python
    logger.info(
        "SCHED_BROADCAST_STARTED sched=%s broadcast=%s audience=%s planned=%s — "
        "отправка идёт фоном, итог доставки см. BROADCAST_FINISHED broadcast_id=%s",
        sched_id, broadcast_id, audience, len(user_ids), broadcast_id,
    )
```

`:114` и `:125` — поднять до `logger.error` с текстом «рассылка уйдёт с кнопкой
скидки, которой нет в базе».

---

#### Д19. Успешная выдача пакета трафика по вебхуку не оставляет записи

**Где:** `app/services/payments/confirmation.py:794-903`
(`_handle_traffic_pack_confirmation`).

**Что не так.** Функция пишет пять записей об отказах и ни одной об успехе. Ни
`purchase_id`, ни `payment_id` (он передан и не используется нигде), ни
количества ГБ. Отдельно: `TRAFFIC_PACK_NOT_APPLIED` (`:875`) стоит в ветке
`else`, до которой bypass-покупки не доходят — у них своя ветка на `:866`; то
есть для bypass итоговой записи «не применилось» нет вообще.

**Что произойдёт при разборе.** «Купил 15 ГБ через СБП, не начислились» —
отказов в логе нет, значит `add_traffic` вернул True, но подтверждения этому
тоже нет. При двух покупках за день определить, какая применилась, невозможно.

**Срочность: средняя.**

```python
    logger.info(
        "TRAFFIC_PACK_APPLIED provider=%s user=%s purchase_id=%s payment_id=%s "
        "gb=%s bypass=%s remnawave_ok=%s trial_activated=%s",
        provider, telegram_id, purchase_id, payment_id, traffic_gb,
        _is_bypass, rmn_success, _trial_activated,
    )
```

и перенести `TRAFFIC_PACK_NOT_APPLIED` из ветки `else` в общую проверку
`if not rmn_success:` перед сборкой текста, чтобы bypass в неё попадал.

---

#### Д20. Подарок отмечается оплаченным до отправки ссылки покупателю

**Где:** `app/handlers/callbacks/gift/payment.py:146-149`, `:151`, `:477-512`.

**Что не так.** `GIFT_PAID_BALANCE` пишется до `_send_gift_success`, а сама
`_send_gift_success` ничего не логирует и не возвращает результат. Доставка
ссылки — единственное, ради чего покупатель платил, — не подтверждается нигде.

Плюс `payment.py:154-157`: `logger.exception("Error processing gift balance
payment: user=…")` — баланс уже списан на `:111`, а в записи нет ни `purchase_id`,
ни `gift_id`, ни цены: «списали и не создали» и «создали и не отправили»
неразличимы.

**Срочность: средняя.**

```python
    if sent is None:
        logger.error(
            "GIFT_LINK_UNDELIVERED buyer=%s gift_id=%s code=%s — подарок оплачен "
            "и создан, ссылку покупатель не получил",
            telegram_id, gift_id, mask_secret(gift_code),
        )
    else:
        logger.info(
            "GIFT_LINK_DELIVERED buyer=%s gift_id=%s code=%s",
            telegram_id, gift_id, mask_secret(gift_code),
        )
```

---

#### Д21. Доставка прокси не проверяется и не логируется

**Где:** `app/handlers/proxy.py:284-298`.

```python
async def send_proxy_success(bot, telegram_id: int, purchase_id: str, pending: dict):
    await safe_send_message(bot, telegram_id, _delivery_text(), reply_markup=_delivery_keyboard())
    try:
        await database.mark_proxy_purchased(telegram_id)
    except Exception as e:
        logger.error("PROXY_MARK_FAILED user=%s purchase_id=%s: %s", telegram_id, purchase_id, e)
```

**Что не так.** `safe_send_message` возвращает `None`, если человек заблокировал
бота. Результат не проверяется, записи о выдаче нет ни успешной, ни неуспешной.

**Срочность: средняя.**

```python
    # safe_send_message отдаёт None, если человек заблокировал бота. Раньше
    # результат не проверялся и записи о выдаче не было вовсе: «оплачено» и
    # «получено» по логам не различались.
    sent = await safe_send_message(bot, telegram_id, _delivery_text(), reply_markup=_delivery_keyboard())
    if sent is None:
        logger.error(
            "PROXY_DELIVERY_UNDELIVERED user=%s purchase_id=%s — оплачено, ссылка не доставлена",
            telegram_id, purchase_id,
        )
    else:
        logger.info("PROXY_DELIVERED user=%s purchase_id=%s", telegram_id, purchase_id)
```

---

### Уровень C — ошибка глохнет или записи нет на пути денег и доступа

---

#### Д22. Отказ панели после списания при автопродлении — WARNING без payment_id

**Где:** `auto_renewal.py:593-594`, `:583-586`, `:589`.

```python
        except Exception as rmn_err:
            logger.warning("REMNAWAVE_AUTORENEW_FAIL: tg=%s %s", item["telegram_id"], rmn_err)
```

**Что не так.** Деньги с баланса сняты (`AUTO_RENEWAL_CHARGED`, `:490`),
`last_auto_renewal_at` проставлен — повтора не будет по построению. Доступ в
панели не продлён. Уровень WARNING, идентификатора платежа нет.
`AUTO_RENEWAL_COMBO_GB_FAIL` (`:583`) — то же для гигабайтов комбо. Само
продление идёт через `renew_remnawave_user_bg` (`:589`) — fire-and-forget,
результат в воркер не возвращается; единственный след отказа — чужая запись
`REMNAWAVE_RENEW_ERROR` (`app/services/remnawave_service.py:238`) без
`payment_id`.

**Что произойдёт при разборе.** «Списали за продление, доступ отключился» → есть
`AUTO_RENEWAL_CHARGED` с payment_id и где-то отдельно `REMNAWAVE_RENEW_ERROR:
tg=… 500`. Связать можно только по telegram_id и времени; при двух продлениях за
месяц — уже нельзя.

**Срочность: высокая.**

```python
        except Exception as rmn_err:
            # Деньги сняты, last_auto_renewal_at проставлен — повтора не будет.
            logger.error(
                "REMNAWAVE_AUTORENEW_FAIL tg=%s payment_id=%s period_days=%s error=%s: %s "
                "— списание прошло, продление в панели НЕ применено, повтора не будет",
                item["telegram_id"], payment_id, item.get("period_days"),
                type(rmn_err).__name__, rmn_err,
            )
```

---

#### Д23. Комбо-гигабайты и bypass-флаг при оплате с баланса теряются молча

**Где:** `app/handlers/callbacks/pay_balance.py:286-287`, `:499-506`, `:214-215`.

```python
        except Exception:
            pass                                            # :286 — чтение FSM
```
```python
                except Exception:
                    pass                                    # :505 — bypass-флаг + триал
```

**Что не так.** Первый блок: при сбое чтения FSM `combo_bypass_gb` становится
нулём, и весь блок начисления ниже не выполняется — человек оплатил комбо и не
получит ни гигабайта. Второй: bypass-флаг не поставлен и триал не активирован.
Оба случая — без записи.

**Срочность: средняя-высокая.**

```python
        except Exception as fsm_err:
            logger.error(
                "COMBO_GB_LOST_FROM_FSM user=%s payment_id=%s error=%s — комбо-"
                "гигабайты не будут начислены, оплата уже прошла",
                telegram_id, payment_id, fsm_err,
            )
```
```python
                except Exception as bypass_err:
                    logger.error(
                        "BYPASS_ONLY_SETUP_FAILED user=%s payment_id=%s gb=%s error=%s "
                        "— флаг не поставлен, триал не активирован",
                        telegram_id, payment_id, bypass_only_gb, bypass_err,
                    )
```

---

#### Д24. Ошибка платёжного сервиса на пути карты — без telegram_id, а вход в обработчик пишется после выхода

**Где:** `app/handlers/payments/payments_messages.py:131`, `:147`;
`app/handlers/payments/payment_preflight.py:210`, `:212` и `:228`.

```python
    except PaymentServiceError as e:
        logger.error(f"Payment service error: {e}")               # payments_messages.py:147
```
```python
        logger.error("Payment received but service unavailable (DB not ready)")   # preflight:210
```

**Что не так.** Три записи на пути «Telegram уже списал деньги» не содержат ни
telegram_id, ни payload, ни суммы. И отдельно: `log_handler_exit` (`:212`)
вызывается **до** `log_handler_entry` (`:228`) — на пути «база не готова» записи
о входе нет вообще, а у записи о выходе `correlation_id` пустой. Начало процесса
на самом опасном пути невидимо.

**Срочность: средняя-высокая.**

```python
    except PaymentServiceError as e:
        logger.error(
            "PAYMENT_SERVICE_ERROR user=%s payload=%s error=%s: %s",
            telegram_id, payload, type(e).__name__, e,
        )
```
```python
        logger.error(
            "PAYMENT_RECEIVED_DB_NOT_READY user=%s payload=%s amount=%s — деньги "
            "списаны Telegram, финализация невозможна, нужен ручной разбор",
            telegram_id,
            message.successful_payment.invoice_payload if message.successful_payment else None,
            (message.successful_payment.total_amount / 100) if message.successful_payment else None,
        )
```

и перенести `log_handler_entry` выше проверок kill-switch и DB-readiness.

---

#### Д25. Погашение bgift-ссылки не пишется вообще

**Где:** `database/bypass_gift_links.py:242-342` (весь
`redeem_bypass_gift_link`), `:220-237` (`rollback_bypass_gift_redemption`),
`:326-329` (откат по лимиту), `:56`, `:86`.

**Что не так.** Функция, погашающая многоразовую предъявительскую ссылку и
начисляющая гигабайты, не пишет ни строки: ни вставку в
`bypass_gift_redemptions` (`:296`), ни `DELETE` при превышении лимита. Откат при
`not DB_READY` возвращает `False` молча — наверху (`command.py:305`) получается
`rolled_back=False` без причины: гигабайты не выданы **и** повторить нельзя.

**Срочность: средняя.**

```python
            logger.info(
                "BGIFT_REDEEMED code=%s user=%s gb=%s uses=%s/%s",
                mask_secret(code), telegram_id, gb_amount, used_count, max_uses,
            )
```
```python
        logger.warning(
            "BGIFT_LIMIT_EXCEEDED code=%s user=%s — попытка сверх лимита, погашение откачено",
            mask_secret(code), telegram_id,
        )
```

---

#### Д26. Причины отказа активации подарка не выходят из БД-слоя

**Где:** `database/gift_subscriptions.py:131-150`, `:206-211`, `:177-194`;
`app/handlers/user/start/command.py:349`, `:410-411`, `:432-433`.

**Что не так.** Шесть причин отказа (`not_found`, `already_activated`,
`invalid_status`, `expired`, `self_activation`) возвращаются из базы без записи;
`UPDATE … status='expired'` (`:143-146`) — молчаливое изменение состояния.
Провижининг VPN (`:177-194`) не логируется ни до, ни после, поэтому по общей
записи `GIFT_ACTIVATION_FAILED` нельзя понять, погашен ли код и создана ли
сущность в панели. Плюс `command.py:349` — проверка формата кода без `else`:
неформатный код тихо проваливается в обычный `/start`.

Отдельно `command.py:410-411`: `REMNAWAVE_GIFT_FAIL` на WARNING — подарок
погашен, подписка активна, ключа в панели нет.

**Срочность: средняя.**

```python
# app/handlers/user/start/command.py:349
        else:
            logger.warning(
                "GIFT_CODE_MALFORMED user=%s len=%s — ссылка не распознана, "
                "человек провалился в обычный /start",
                telegram_id, len(gift_code or ""),
            )
```

---

#### Д27. Пустая ссылка после bgift-подарка не оставляет следа

**Где:** `app/handlers/user/bypass_gift_setup.py:242-244`, `:247-248`, `:276-277`.

```python
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return ""
```

**Что не так.** Человек только что получил гигабайты по подарочной ссылке,
открывает экран подключения и видит «ключа нет». Записи нет. Соседний случай
(`BGIFT_FETCH_SUB_URL_FAIL`, `:247`) — WARNING без причины и без uuid.

**Срочность: средняя.**

```python
        if not rmn_uuid:
            logger.error(
                "BGIFT_NO_PANEL_ENTITY user=%s — гигабайты выданы, сущности в "
                "панели нет, человек не получил ключ",
                telegram_id,
            )
            return ""
```

---

#### Д28. Отказы потребления промокода не пишутся

**Где:** `database/promo.py:454-455`, `:458-459`, `:468-473`, `:478-479`,
`:403-404`, `:405-408`.

**Что не так.** `_consume_promo_in_transaction` бросает
`ValueError("PROMO_NOT_FOUND" / "PROMO_INACTIVE" / "PROMO_EXPIRED" /
"PROMO_ALREADY_CONSUMED")` **без единой записи**. Это путь оплаты: цена уже
посчитана со скидкой, а промокод не списался. Рядом честная запись
`PROMO_EXHAUSTED` (`:375`) показывает, как надо. Плюс `PROMOCODE_VALIDATED`
(`:405-408`) не содержит telegram_id — кто валидировал код, неизвестно.

**Срочность: средняя.**

```python
        if not row:
            logger.error(
                "PROMO_CONSUME_FAILED code=%s user=%s reason=not_found — цена уже "
                "посчитана со скидкой, промокод не списан",
                mask_secret(code_normalized), telegram_id,
            )
            raise ValueError("PROMO_NOT_FOUND")
```

---

#### Д29. `database/marketing_links.py`: логгер объявлен, вызовов ноль

**Где:** `database/marketing_links.py` — `logger` на `:18`, вызовов 0. Ключевые
точки: `:377` `try_redeem_promo_link`, `:456` `rollback_promo_link_redemption`,
`:147` `record_stats_link_click`, `:405-425` (шесть причин отказа), `:444-445`
(`except Exception: meta = {}`), `:477-478`.

**Что не так.** Файл резервирует материальную награду (подписка, скидка, ГБ),
инкрементит `used_count`, откатывает резерв и проставляет атрибуцию — без единой
записи. Все шесть отказов (`not_found`/`inactive`/`expired`/`exhausted`/
`already_redeemed_by_user`/`db_not_ready`) возвращаются молча. Потеря
`reward_meta` (`:444`) меняет размер награды без записи. Откат, вернувший
`False` (`:477`), означает навсегда потерянный слот активации — тоже молча.

Наверху `app/handlers/user/start/marketing_links.py:251/278/300/328` пишет
`PROMO_APPLY_*_FAIL` **без telegram_id и без slug**.

**Срочность: средняя.**

```python
        logger.info(
            "PROMO_LINK_REDEEMED slug=%s user=%s reward=%s value=%s used=%s/%s",
            slug, telegram_id, reward_type, reward_value, used_count, max_uses,
        )
```
```python
        logger.warning(
            "PROMO_LINK_REDEEM_REJECTED slug=%s user=%s reason=%s",
            slug, telegram_id, reason,
        )
```
```python
        # rollback вернул False — слот активации потерян навсегда
        logger.error(
            "PROMO_LINK_ROLLBACK_NOOP slug=%s link_id=%s user=%s — резерв не снят, "
            "слот активации потерян",
            slug, link_id, telegram_id,
        )
```

---

#### Д30. `withdraw_fsm.py` и `topup_fsm.py`: логгер объявлен, вызовов ноль

**Где:** `app/handlers/payments/withdraw_fsm.py` (`logger` на `:23`, вызовов 0),
`app/handlers/payments/topup_fsm.py` (`logger` на `:18`, вызовов 0).

**Что не так.** Ввод суммы вывода, превышение баланса, отказ по игровым деньгам
(`withdraw_fsm.py:108-124`), пять неудачных попыток (`:81`, `:164`), ввод суммы
пополнения и отказы по мин/макс — ничего не записывается. Начало обеих денежных
операций невидимо.

**Срочность: средняя.**

```python
        logger.info(
            "WITHDRAW_AMOUNT_ENTERED user=%s amount_rub=%.2f balance_rub=%.2f "
            "withdrawable_rub=%.2f",
            telegram_id, amount_rubles, balance_rubles, withdrawable_rubles,
        )
```
```python
        logger.warning(
            "WITHDRAW_REJECTED_GAME_BALANCE user=%s requested_rub=%.2f "
            "withdrawable_rub=%.2f — игровые деньги выводу не подлежат",
            telegram_id, amount_rubles, withdrawable_rubles,
        )
```

---

#### Д31. Факт отправки автоуведомления теряется при недоступном пуле

**Где:** `app/services/automated_notifications/helper.py:242-244`.

```python
    pool = await get_pool()
    if pool is None:
        return
```

**Что не так.** `log_notification_send` — единственный источник статистики
автоуведомлений. При недоступном пуле она выходит молча, статистика занижается,
и об этом нет ни строки. Плюс `helper.py:220-231` (`is_user_in_segment`
fail-open: `except … return True` с WARNING) — при отказе БД уведомление уходит
вне сегмента, а лог сообщает только «is_user_in_segment failed». Плюс
`helper.py:190-194` — человеку уходит текст с неподставленными `{placeholder}`, и
в `automated_notification_sends` это пишется как `sent`.

**Срочность: средняя.**

```python
    pool = await get_pool()
    if pool is None:
        logger.warning(
            "AUTONOTIF_SEND_NOT_RECORDED key=%s user=%s status=%s — пул недоступен, "
            "статистика автоуведомлений занижена",
            key, telegram_id, status,
        )
        return
```

---

#### Д32. `except Exception: pass` вокруг алерта об упавшем воркере

**Где:** `activation_worker.py:633-634`, `fast_expiry_cleanup.py:532-533`,
`trial_notifications.py:831-832`, `app/workers/farm_notifications.py:365-366`.

**Что не так.** Воркер выдачи доступа упал **и** позвать админа не получилось —
и это никуда не записано. В `auto_renewal.py:824-831` этот же блок пишет ERROR;
четыре других воркера молчат.

**Срочность: средняя.**

```python
            except Exception as alert_err:
                logger.error(
                    "WORKER_FAILURE_ALERT_FAILED worker=activation_worker "
                    "iteration=%s error=%s — воркер упал и админа позвать не удалось",
                    iteration_number, alert_err,
                )
```

---

#### Д33. `except Exception: pass` на пути отзыва доступа в сервисе активации

**Где:** `app/services/activation/service.py:445-446`, `:462-463`, `:517-518`.

**Что не так.** Три блока `except Exception: pass` вокруг откатной очистки
сущности. Вместе с Д3 это означает: сирота в панели осталась, попытка её убрать
провалилась, и обо всём этом в логе стоит CRITICAL «предотвращено».

**Срочность: средняя.**

```python
                    except Exception as cleanup_err:
                        logger.critical(
                            "ACTIVATION_ORPHAN_CLEANUP_FAILED subscription_id=%s uuid=%s "
                            "error=%s — сущность осталась в панели, нужен ручной разбор",
                            subscription_id, uuid_to_cleanup_on_failure[:8], cleanup_err,
                        )
```

---

#### Д34. `except Exception: pass` вокруг сохранения invoice_id товаров

**Где:** `app/handlers/callbacks/apple_id.py:237-240`, `:414-417`;
`app/handlers/payments/spotify_purchase.py:679-684`, `:728-733`.

```python
    try:
        await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_data["invoice_id"]))
    except Exception:
        pass
```

**Что не так.** Без сохранённого invoice_id вебхук не сопоставит платёж с
покупкой: деньги придут, заказ останется `pending`, никто не узнает.

**Срочность: средняя.**

```python
    except Exception as e:
        logger.error(
            "INVOICE_ID_NOT_SAVED provider=lava user=%s purchase_id=%s invoice_id=%s "
            "error=%s — вебхук не сопоставит оплату с покупкой",
            telegram_id, purchase_id, invoice_data.get("invoice_id"), e,
        )
```

---

#### Д35. Выставленный счёт не логируется у части процессов

**Где:** `app/handlers/callbacks/gift/payment.py:165/242/315/398` (все четыре
ветки подарка), `app/handlers/traffic/pay_bypass.py:136/253/308` (СБП,
CryptoBot, Lava), `app/handlers/callbacks/balance_callbacks.py:162` (пополнение
Stars), `app/handlers/callbacks/topup.py:244` (пополнение картой). Отказы
сохранения без самого invoice_id: `gift/payment.py:367`, `:450`,
`pay_bypass.py:184`, `:352`, `pay_traffic.py:189`, `:272`.

**Что не так.** Обычные покупки пишут `invoice_created` с provider, telegram_id,
purchase_id и invoice_id (`pay_external/platega.py:143` — образец). У подарков,
у трёх из пяти способов оплаты bypass и у двух способов пополнения такой записи
нет: цепочка «счёт → вебхук → выдача» начинается только с вебхука.

**Срочность: средняя.**

```python
        logger.info(
            "invoice_created: provider=cryptobot, kind=gift, user=%s, purchase_id=%s, "
            "invoice_id=%s, price=%.2f",
            telegram_id, purchase_id, invoice_data["invoice_id"], price_rubles,
        )
```

---

#### Д36. Отказы синхронизации с сайтом — без telegram_id

**Где:** `app/services/site_sync.py:53`, `:57`, `:61`, `:237-238`.

**Что не так.** Во всех трёх записях есть только endpoint. Чей кешбэк не доехал —
неизвестно. `full_sync_after_payment` (`:237`) пишет `SITE_SYNC_EXTEND` только
при успехе; ветки `else` нет, и при лежащем сайте функция завершается молча.

**Срочность: средняя.**

```python
async def _post(endpoint: str, payload: dict, *, telegram_id: int | None = None) -> Optional[dict]:
    ...
        logger.error(
            "SITE_SYNC_ERROR: endpoint=%s tg=%s status=%d — тело ответа не пишем, "
            "оно приходит от стороннего сайта",
            endpoint, telegram_id, resp.status_code,
        )
```
```python
        else:
            logger.error(
                "SITE_SYNC_EXTEND_FAILED: user=%s purchase_id=%s days=%d plan=%s — "
                "оплата в боте прошла, на сайт не доехала, повтора нет",
                telegram_id, purchase_id, days, plan,
            )
```

---

#### Д37. Недоставленное напоминание платному не оставляет собственной записи

**Где:** `reminders.py:384-393`, `:391-392`, `:398-422`, `:348-349`, `:407-408`.

**Что не так.** Ветка `if sent is None:` (человек заблокировал бота) не пишет
ничего своего, а попытка записать метрику внутри неё обёрнута в
`except Exception: pass`. Для admin/legacy-типов (`notif_key is None`) не пишется
вообще ничего. Отдельно: `mark_reminder_sent` (`:398`) и запись в `audit_log`
(`:411`) стоят после фактической отправки, но внутри общего try; их падение
уводит в `:422 logger.error("Error sending reminder…")` — сообщение ушло, лог
говорит «ошибка отправки», флаг не встал, на следующем витке придёт дубль.

**Срочность: средняя.**

```python
        if sent is None:
            logger.warning(
                "REMINDER_UNDELIVERED user=%s type=%s — бот заблокирован или чат "
                "недоступен, флаг не ставим, повтор придёт на следующем витке",
                telegram_id, reminder_type,
            )
            continue
```

и разделить блоки: отправка — в своём try, `mark_reminder_sent` + аудит — в
своём.

---

#### Д38. Начисление денег в шторме фермы не пишется поштучно

**Где:** `app/workers/farm_notifications.py:266-277`, `:311-314`, `:305`, `:357`.

**Что не так.** `execute_storm_for_user` начисляет каждому рубли за авто-сбор.
Ни одной записи с telegram_id и суммой; есть только агрегат `STORM_EXECUTED …
auto_rub=…`. Исключение внутри цикла уходит в `:357` без `storm_id` и без
указания, скольким уже начислили, а `mark_storm_executed` (`:305`) не
выполняется — на следующем витке шторм исполнится повторно, и по логам это
неотличимо.

**Срочность: средняя.**

```python
            if result.get("autoharv_kopecks"):
                logger.info(
                    "STORM_AUTOHARVEST_CREDITED storm_id=%s user=%s plots=%s rub=%.2f",
                    storm_id, telegram_id, result.get("harvested_plots"),
                    result["autoharv_kopecks"] / 100,
                )
```

---

#### Д39. Провижининг перед оплатой с баланса падает на WARNING

**Где:** `database/balance_purchases.py:137-141`.

**Что не так.** Фаза 1 (создание сущностей в Remnawave) провалилась, покупка
продолжается с `pre_provisioned_uuid=None`. Уровень WARNING, и в записи не
сказано, что произойдёт дальше.

**Срочность: средняя.**

```python
                logger.error(
                    "BALANCE_PURCHASE_PROVISION_FAILED user=%s tariff=%s error=%s: %s "
                    "— покупка продолжается без предварительного провижининга, "
                    "выдачу возьмёт на себя grant_access",
                    telegram_id, tariff_norm, type(phase1_err).__name__, phase1_err,
                )
```

---

### Уровень D — админские действия и вход в дашборд без следа

---

#### Д40. Успешный вход в дашборд не логируется, отклонённая авторизация — тоже

**Где:** `app/api/dashboard/auth.py:282-285` (вход по паролю), `:249-255`
(установка пароля), `:378-392` (вход по passkey), `:350-363` (регистрация
passkey), `:401-407` (удаление passkey), `:288-296` (logout), `:199-203`
(`except Exception: pass` на пути аутентификации);
`app/api/dashboard/deps.py:34-43` (все отказы авторизации).

**Что не так.** Провалы входа по паролю пишутся (`DASHBOARD_LOGIN_FAILED`,
`:279`), а **успех — нет**:

```python
    _reset_login_attempts(client_ip)
    token = await admin_auth.create_session(config.ADMIN_TELEGRAM_ID)
    _set_session_cookie(response, token)
    return {"ok": True}                       # :282-285 — ни IP, ни времени, ни админа
```

Вход по passkey не логируется ни при успехе, ни при провале, и не ограничен
`_login_attempts` — брутфорс по этому пути невидим. Установка пароля
(`set_credentials` + немедленная сессия) — захват аккаунта в один вызов — без
записи. Удаление и регистрация фактора аутентификации — без записи. И
`deps.py:34-43`: ни одна отклонённая авторизация к админскому API не пишется, то
есть перебор токенов по любому эндпоинту дашборда невидим.

**Что произойдёт при разборе.** «Кто-то поменял цены / выдал себе VIP / удалил
пользователя» — начинать разбор не с чего: неизвестно, кто и когда входил в
дашборд, и был ли вообще вход. Подобрали пароль или нет — по логам не отличить,
потому что видны только неудачные попытки.

**Срочность: высокая** — это единственная дверь во всю административную часть.

```python
# app/api/dashboard/auth.py:282
    _reset_login_attempts(client_ip)
    token = await admin_auth.create_session(config.ADMIN_TELEGRAM_ID)
    _set_session_cookie(response, token)
    # Провалы входа писались, успех — нет. Разбор «кто поменял цены» начинать
    # было не с чего: подобранный пароль и штатный вход по логам неотличимы.
    _log.warning(
        "DASHBOARD_LOGIN_OK admin=%s ip=%s method=password",
        config.ADMIN_TELEGRAM_ID, client_ip,
    )
    return {"ok": True}
```
```python
# app/api/dashboard/auth.py:384
    ok, err = await admin_passkeys.verify_authentication(...)
    if not ok:
        _log.warning("DASHBOARD_PASSKEY_AUTH_FAILED ip=%s reason=%s", client_ip, err)
        raise HTTPException(401, f"auth_failed: {err}")
    _log.warning("DASHBOARD_LOGIN_OK admin=%s ip=%s method=passkey", tg, client_ip)
```
```python
# app/api/dashboard/deps.py:34
    if not payload:
        _log.warning(
            "DASHBOARD_AUTH_REJECTED path=%s ip=%s reason=invalid_or_expired_token",
            request.url.path, request.client.host if request.client else "?",
        )
        raise HTTPException(401, "Invalid or expired token")
```

---

#### Д41. Действия админа над доступом и деньгами не оставляют записи

**Где:** `app/api/dashboard/routes/users.py:441-459` (смена тарифа),
`:467-497`, `:500-514`, `:526-556`, `:559-573` (личные и traffic-скидки),
`:584-617`, `:693-710` (фиксированный % кешбэка), `:713-727`, `:730-744`
(VIP выдать/отозвать), `:761-789` (удаление пользователя);
`app/api/dashboard/routes/bypass_audit.py:122-128` (массовое восстановление).

**Что не так.** Девять ручек, меняющих доступ и деньги, не содержат ни одного
`logger`. Все девять оканчиваются на `except Exception as e: raise
HTTPException(500, …)` — провал уходит только в HTTP-ответ. `bus.publish`,
который стоит в нескольких из них, — это in-memory очередь (`app/events.py:43-52`),
она **дропает событие при переполнении** и никуда не персистится; журналом она не
является.

Отдельно: `database/subscription_state.py:325` `admin_switch_tariff` **не
принимает admin_id** и не пишет аудит; `database/traffic.py:550`
`delete_user_traffic_discount(telegram_id)` — тоже без админа.

Для сравнения, `user_grant` (`:281`) и `user_revoke` (`:389`) сделаны образцово:
START / OK / FAILED / NOOP, `admin=` везде, `notify_delivered` по факту отправки.

**Что произойдёт при разборе.** «У человека пропал доступ» — оказывается, админ
сменил ему тариф. Ни кто, ни когда, ни на что — не записано. «Почему выручка
просела» — оказывается, кому-то выдали VIP и 45% кешбэка. Следа нет.
И необратимое `user_delete` — тоже.

**Срочность: высокая** для удаления и смены тарифа, **средняя** для остального.

```python
# app/api/dashboard/routes/users.py:761 — user_delete
    logger.warning(
        "DASHBOARD_USER_DELETE_START admin=%s user=%s", admin.get("sub"), telegram_id,
    )
    try:
        ok = await database.admin_delete_user_complete(telegram_id, int(admin["sub"]))
    except Exception as e:
        logger.exception(
            "DASHBOARD_USER_DELETE_FAILED admin=%s user=%s error=%s",
            admin.get("sub"), telegram_id, e,
        )
        raise HTTPException(500, f"delete_failed: {e}")
    logger.warning(
        "DASHBOARD_USER_DELETE_OK admin=%s user=%s deleted=%s — персданные и доступ "
        "удалены, финансовая история сохранена",
        admin.get("sub"), telegram_id, ok,
    )
```

Тот же шаблон (START / OK / FAILED с `admin=`) — для смены тарифа, VIP, скидок и
cashback-fix. Плюс провести `admin_id` в `admin_switch_tariff` и
`delete_user_traffic_discount`.

---

#### Д42. Изменение цен полностью анонимно

**Где:** `app/api/dashboard/routes/pricing.py:56`, `:76`, `:113`, `:141`;
`app/services/pricing/__init__.py:189`, `:212`, `:242`.

**Что не так.** Ни одного `logger` ни в маршрутах, ни в сервисе. Единственный
след — колонка `updated_by` в таблице переопределений. При этом
`clear_tariff_override` (`routes/pricing.py:76`) **вообще не принимает `admin`**,
и `pricing.clear_override` (`:212`) не получает admin_id — снятие
переопределения цены анонимно полностью, даже в базе.

**Что произойдёт при разборе.** «Почему вчера тариф Plus продавался за 199 ₽» —
ответа нет. Кто поставил, кто снял, когда — неизвестно.

**Срочность: средняя-высокая** (прямое влияние на выручку, ноль следов).

```python
    logger.warning(
        "PRICING_OVERRIDE_SET admin=%s tariff=%s period_days=%s old_kopecks=%s "
        "new_kopecks=%s until=%s",
        admin.get("sub"), tariff, period_days, old_price, new_price, until,
    )
```
```python
    logger.warning(
        "PRICING_OVERRIDE_CLEARED admin=%s tariff=%s period_days=%s was_kopecks=%s",
        admin.get("sub"), tariff, period_days, old_price,
    )
```

и провести `admin` в `clear_tariff_override` / `pricing.clear_override`.

---

#### Д43. Смена ставки кешбэка не оставляет записи

**Где:** `database/referral_rates.py:114-127` (`set_cashback_fixed_percent`),
`:131-144` (`clear_cashback_fixed_percent`), `:81-88`;
`app/api/dashboard/routes/users.py:584-616`, `:693-707`.

**Что не так.** Ноль вызовов `logger` в файле. Функции жёстко переопределяют
процент кешбэка для всех будущих начислений (`database/referral_reward.py:198-207`
применяет его безусловно). Ни «кто», ни «было → стало». В HTTP-ручках тоже нет
`logger` — только `bus.publish`. Ирония: в логе есть **уведомление** человеку о
смене ставки (`CASHBACK_FIX_CONGRATS_SENT`, `users.py:679`), но нет **самой
смены**.

Отдельно `:81-88`: `except … return 10` — при недоступности `referral_rewards`
процент молча падает до 10 (WARNING), а начисление идёт по своей копии шкалы
(`referral_reward.py:173-182`), так что расхождение витрины и выплаты в лог не
попадает.

**Срочность: средняя-высокая** (прямое влияние на выплаты).

```python
    logger.warning(
        "CASHBACK_FIXED_PERCENT_SET admin=%s user=%s old_percent=%s new_percent=%s",
        admin_id, telegram_id, old_percent, percent,
    )
```

---

#### Д44. Создание, планирование и отмена рассылки не оставляют записи

**Где:** `app/api/dashboard/routes/broadcasts/send.py:233-321` (0 `logger` в
функции, кроме двух веток скидки), `:113-114`, `:207-216`, `:140-157`;
`app/api/dashboard/routes/broadcasts/scheduled.py:93-168`, `:199-218`,
`:117-118`, `:151-152`, `:209-210`;
`database/scheduled_broadcasts.py:137-150`, `:172-217`.

**Что не так.** Ни создание рассылки, ни планирование, ни отмена задания, ни
нажатие «Стоп» не пишут ни строки. Кто запустил рассылку на 50 000 человек —
неизвестно; `admin_telegram_id` попадает только в аудит-таблицу, и только по
завершении отправки (`broadcast_sender.py:194`). Если процесс упал между
`create_broadcast` и завершением, следов «кто и что запустил» нет нигде.
Плюс девять `except … raise HTTPException(…)` без записи.

**Срочность: средняя-высокая.**

```python
    logger.warning(
        "BROADCAST_CREATE admin=%s broadcast_id=%s segment=%s audience=%s has_photo=%s "
        "buttons=%s discount=%s",
        admin.get("sub"), broadcast_id, segment, len(user_ids),
        bool(photo_file_id), len(buttons or []), discount_percent,
    )
```
```python
    logger.warning(
        "BROADCAST_SCHEDULE_CANCEL admin=%s sched=%s", admin.get("sub"), sched_id,
    )
```

---

#### Д45. Правка, отключение и удаление автоуведомлений не оставляют записи

**Где:** `app/api/dashboard/routes/automated_notifications.py` — весь файл,
374 строки, 0 вызовов `logger`; особенно `:149` (PATCH), `:203` (reset),
`:288-313` (DELETE вместе со всей историей отправок),
`:331` (test-send), `:361-373` (два `except … raise HTTPException`);
`app/services/automated_notifications/helper.py:283-329` (`update_notification`,
0 `logger`, `edited_by` только в колонке).

**Что не так.** Админ выключил автоуведомление (`is_enabled=false`) или переписал
текст, который увидят все покупатели, — в логе нет ничего. `DELETE /{key}`
удаляет ключ **вместе со всей историей отправок**
(`DELETE FROM automated_notification_sends WHERE key = $1`, `:308-313`) — тоже
молча.

**Что произойдёт при разборе.** «Люди перестали получать письмо об успешной
оплате» — причина в выключенном флаге, но когда и кто его выключил, узнать
нельзя. А если ключ удалён, то и статистика, по которой это можно было бы
заметить, удалена вместе с ним.

**Срочность: средняя.**

```python
    logger.warning(
        "AUTONOTIF_UPDATED admin=%s key=%s enabled=%s text_changed=%s",
        admin.get("sub"), key, body.is_enabled, body.text is not None,
    )
```
```python
    logger.warning(
        "AUTONOTIF_DELETED admin=%s key=%s sends_deleted=%s — история отправок "
        "удалена вместе с ключом",
        admin.get("sub"), key, deleted_sends,
    )
```

---

#### Д46. Ручной ретрай выдачи доступа из дашборда не логируется

**Где:** `app/api/dashboard/routes/activations.py:57-109`, `:90-91`, `:93`.

**Что не так.** Ручка повторной выдачи VPN-доступа не пишет ни старта, ни
исхода, ни админа; `except Exception as e: raise HTTPException(500,
f"retry_failed: {e}")`. Единственный след — `bus.publish` (`:94`).

**Что произойдёт при разборе.** «Админ нажал „повторить выдачу", человек всё
равно без доступа» — было ли нажатие вообще, и чем оно кончилось, по логам не
восстановить.

**Срочность: средняя.**

```python
    logger.info(
        "DASHBOARD_ACTIVATION_RETRY_START admin=%s user=%s subscription_id=%s",
        admin.get("sub"), telegram_id, subscription_id,
    )
    ...
    logger.info(
        "DASHBOARD_ACTIVATION_RETRY_DONE admin=%s user=%s success=%s reason=%s",
        admin.get("sub"), telegram_id, success, getattr(result, "reason", None),
    )
```

---

#### Д47. Деактивация промокода не содержит админа

**Где:** `database/promo.py:284` (`PROMO_REACTIVATED`), `:329`
(`PROMO_DEACTIVATED`), `:289` (`deactivate_promocode(promo_id=…)` не принимает
admin_id); `app/api/dashboard/routes/promo.py:74-90` (0 `logger`).

**Что не так.** Создание промокода записано с админом
(`PROMO_CREATED … created_by=…`, `:228-235`), а деактивация и реактивация — без.
Сама функция админа не принимает.

**Срочность: средняя.**

```python
    logger.warning(
        "PROMO_DEACTIVATED code=%s id=%s admin=%s", mask_secret(row["code"]), promo_id, admin_id,
    )
```

---

#### Д48. Прочие админские действия без следа

Сводно, одним блоком — по каждому нужен тот же шаблон «START / OK / FAILED с
`admin=`»:

| Действие | Где | Чего нет |
|---|---|---|
| bgift-ссылки: создание, удаление | `app/api/dashboard/routes/bgift.py:83`, `:108`; `database/bypass_gift_links.py:45`, `:187` | 0 `logger`; `soft_delete_bypass_gift_link(link_id)` не принимает админа |
| promo/stats-ссылки: создание, деактивация, удаление | `app/api/dashboard/routes/links.py` (357 строк, 0 `logger`), `:179`, `:351` | вызовы без admin_id |
| Режим инцидента (баннер всем) | `app/api/dashboard/routes/incident.py:27-38` | 0 `logger`; `set_incident_mode` без админа |
| Отключение каналов алертов | `app/api/dashboard/routes/settings.py:28-33`; `app/services/admin_settings.py:56-69` | нет параметра `admin`, нет `logger` |

Последняя строка особенно неприятна: отключив канал `payment_error`, админ
выключает оповещения о сбоях оплаты — и об этом не остаётся записи.

**Срочность: средняя.**

---

#### Д49. `except Exception: pass` на аудите удаления рассылки

**Где:** `app/services/broadcast_deleter.py:159-160`, `:191-192`.

**Что не так.** Два `except Exception: pass` вокруг аудит-записей
`broadcast_deleted` и `broadcast_delete_cancelled` — кто и сколько удалил,
теряется молча. Вместе с Д10 это означает: состояние испорчено, кто это сделал —
неизвестно.

**Срочность: средняя.**

```python
        except Exception as audit_err:
            logger.error(
                "BROADCAST_DELETE_AUDIT_FAILED bid=%s admin=%s deleted=%s error=%s — "
                "действие выполнено, в журнале его не будет",
                broadcast_id, admin_telegram_id, deleted, audit_err,
            )
```

---

### Уровень E — уровень записи не соответствует последствию, метрики врут

---

#### Д50. Простой воркера выдачи доступа записан на DEBUG

**Где:** `activation_worker.py:146`, `:150`; `admin_notifications.py:137`.

**Что не так.** Корневой логгер стоит на INFO
(`app/core/logging_config.py:162`), значит этих строк в проде нет. При
`VPN_ENABLED=false` воркер не выдаёт доступ **никому**, и очередь оплаченных
подписок растёт беззвучно. Резервный сигнал — алерт о зависших активациях —
давится кулдауном, и факт подавления тоже пишется на DEBUG.

**Срочность: средняя-высокая.**

```python
        if not config.VPN_ENABLED:
            # Не DEBUG: при выключенном флаге воркер не выдаёт доступ никому, и
            # очередь оплаченных активаций растёт беззвучно — корневой логгер
            # стоит на INFO, DEBUG в прод не доезжает.
            logger.error(
                "ACTIVATION_WORKER_DISABLED: VPN_ENABLED=false — очередь оплаченных "
                "активаций не разбирается",
            )
```

---

#### Д51. Отзыв premium при истечении триала — WARNING, хотя повтора не будет

**Где:** `trial_notifications.py:647-650`, `:652`.

**Что не так.** `uuid` в базе уже занулён (`:584`), вернуться к этой подписке
нечем: доступ остаётся включённым навсегда. Тот же сценарий в
`fast_expiry_cleanup.py:457` и `:467` поднят до **ERROR** именно с этим
обоснованием — здесь остался WARNING, и `exc_info` нет.

**Срочность: средняя-высокая** (бесплатный доступ после окончания триала —
прямая потеря выручки).

```python
            logger.error(
                "trial_expired: PREMIUM_DISABLE_SKIPPED user=%s — uuid уже занулён, "
                "повтора не будет; сущность в панели осталась активной, отключите вручную",
                telegram_id,
            )
```

---

#### Д52. Итог рассылки всегда INFO, а построчные записи не привязаны к рассылке

**Где:** `app/services/broadcast_sender.py:179-182`, `:137-140`, `:201-205`;
`app/services/broadcast_delivery.py:105-108`, `:115-118`;
`app/services/broadcast_deleter.py:116-119`, `:169-172`;
`app/api/dashboard/routes/bypass_audit.py:130-133`.

**Что не так.** Единственный итог рассылки — `BROADCAST_PROGRESS`, и он INFO при
любом исходе: `sent=0 failed=50000` выглядит так же, как успешная отправка;
порога `failed/total` нет. Записи о конкретных сбоях
(`BROADCAST_RATE_LIMITED`, `BROADCAST_SEND_FAILED`) **не содержат
`broadcast_id`** — привязать их к рассылке нельзя. Заблокировавший бота человек
пишется 3 раза (по числу попыток) на WARNING — тем же уровнем, что «битая
разметка у всех». `BROADCAST_TASK_ERROR` (`:137`) содержит `broadcast_id`, но не
`uid` — неизвестно, кому не дошло.

**Срочность: средняя.**

```python
    level = logger.error if failed > total * 0.2 else logger.info
    level(
        "BROADCAST_FINISHED broadcast_id=%s admin=%s sent=%s failed=%s total=%s",
        broadcast_id, admin_telegram_id, sent, failed, total,
    )
```
```python
# app/services/broadcast_delivery.py:115
    logger.warning(
        "BROADCAST_SEND_FAILED broadcast_id=%s user=%s attempt=%s err=%s",
        broadcast_id, user_id, _attempt + 1, e,
    )
```

(параметр `broadcast_id` придётся провести в `broadcast_delivery`).

---

#### Д53. Множитель кешбэка молча падает с x2 до x1

**Где:** `database/referral_reward.py:256-257`.

```python
        except Exception as e:
            logger.warning(f"Failed to check cashback multiplier for {referrer_id}: {e}")
```

**Что не так.** Сбой чтения множителя превращает x2-акцию в x1: реферер получает
вдвое меньше денег. Уровень WARNING, начисление продолжается, и в
`REFERRAL_REWARD_APPLIED` не видно, что множитель не проверялся.

**Что произойдёт при разборе.** «В акцию обещали двойной кешбэк, начислили
обычный» → в логе `REFERRAL_REWARD_APPLIED … percent=10`, и понять, что это
результат сбоя, а не настройки, можно только найдя рядом WARNING по имени
реферера.

**Срочность: средняя.**

```python
        except Exception as e:
            logger.error(
                "CASHBACK_MULTIPLIER_UNAVAILABLE referrer=%s buyer=%s purchase_id=%s "
                "error=%s — начисляем по одинарной ставке, акционный множитель не применён",
                referrer_id, buyer_id, purchase_id, e,
            )
```

и добавить `multiplier=` в `REFERRAL_REWARD_APPLIED` (`:353`).

---

#### Д54. Fail-open проверки записываются как рядовое предупреждение

**Где:** `database/discounts.py:126-134` (`REFDC_CHECK_FAIL … treating as
not-claimed`); `app/services/automated_notifications/helper.py:220-231`
(`is_user_in_segment` → `return True`); `app/services/subscription_watchdog.py:136`,
`:198-201`, `:158-160`, `:211-212`.

**Что не так.** Во всех трёх случаях при отказе базы защита **отключается**, а
запись об этом — WARNING:

- `REFDC_CHECK_FAIL` снимает lifetime-ограничение: скидку «подари другу» можно
  получать повторно, сколько угодно раз;
- `is_user_in_segment` пропускает уведомление вне сегмента;
- `subscription_watchdog` обнаружил выдачу доступа на 10 лет, но запись в журнал
  не легла или админа позвать не удалось — WARNING; а `_resolve_bot()`
  (`:211-212`) возвращает `None` через `except Exception: return None`, после чего
  `_dispatch` выходит молча.

**Срочность: средняя.**

```python
# database/discounts.py:126
    except Exception as e:
        logger.error(
            "REFDC_CHECK_FAIL user=%s error=%s — lifetime-ограничение снято на эту "
            "попытку, скидка может быть выдана повторно",
            telegram_id, e,
        )
        return False
```
```python
# app/services/subscription_watchdog.py:158
        bot = _resolve_bot()
        if bot is None:
            logger.error(
                "SUBSCRIPTION_WATCHDOG: bot недоступен, алерт об аномальной выдаче "
                "user=%s log_id=%s не отправлен",
                telegram_id, log_id,
            )
            return
```

---

#### Д55. Не запустившийся воркер — одна запись WARNING и больше нигде

**Где:** `main.py:453-454`, `main.py:467`.

**Что не так.** Воркер, упавший при старте, не попадает в `started_workers` —
значит `healthcheck.watch_tasks` (`:467`) его не наблюдает. Единственный след —
одна строка WARNING в момент запуска бота. Через час её не найти, и «воркер
активации не работает вторые сутки» превращается в «непонятно, почему активации
висят».

**Срочность: средняя-высокая** для `activation_worker`, `auto_renewal`,
`fast_expiry_cleanup`.

```python
            except Exception as e:
                # Не запустившийся воркер не попадает в started_workers, значит
                # healthcheck его не наблюдает: единственный след — эта строка.
                logger.critical(
                    "WORKER_START_FAILED worker=%s reason=%s error=%s: %s — задача "
                    "не наблюдается health-check'ом, доступ по ней не выдаётся",
                    name, reason, type(e).__name__, e,
                )
                try:
                    from app.services.admin_alerts import send_alert
                    await send_alert(
                        bot, "worker",
                        f"Воркер {name} не запустился: {type(e).__name__}: {str(e)[:200]}",
                        force=True,
                    )
                except Exception as alert_err:
                    logger.error("WORKER_START_ALERT_FAILED worker=%s: %s", name, alert_err)
```

---

#### Д56. Немой простой воркеров

**Где:** `app/workers/site_sync_worker.py:51-52`, `:54-55`;
`app/workers/traffic_monitor.py:204-206`.

**Что не так.** Воркер крутится вхолостую без единой записи. Для site_sync это
значит, что кешбэк с сайта не начисляется часами; для traffic_monitor —
уведомления «трафик кончается» не идут никому. «Простаиваем» неотличимо от
«синхронизировать некого». Плюс `traffic_monitor` не пишет `ITERATION_START`
вообще, и оба воркера не пользуются `log_worker_iteration_*`, поэтому не
попадают в общие метрики итераций.

**Срочность: средняя.**

```python
        if not is_enabled():
            if not _idle_reported:
                logger.warning(
                    "SITE_SYNC_IDLE: SITE_API_URL/SITE_BOT_API_KEY не заданы — "
                    "кешбэк с сайта не начисляется",
                )
                _idle_reported = True
            await asyncio.sleep(INTERVAL_SECONDS)
            continue
```

---

#### Д57. `outcome="timeout"` и `"cancelled"` пишутся уровнем INFO

**Где:** `app/utils/logging_helpers.py:262-273`.

**Что не так.** Ветвление по `outcome` знает `failed`, `degraded`, `skipped`;
всё остальное падает в `else` и пишется INFO. Значение `"timeout"` используется
в шести воркерах (`activation_worker.py:600`, `auto_renewal.py:793`,
`fast_expiry_cleanup.py:517`, `reminders.py:476`, `trial_notifications.py:806`,
`app/workers/farm_notifications.py:351`). Отменённая посреди работы итерация —
это недоданный оплаченный доступ и неотозванный доступ, а в машиночитаемой
записи она помечена как обычный успех. Соседняя текстовая запись
`WORKER_TIMEOUT` при этом ERROR — текст и JSON расходятся по уровню на одном
событии.

**Срочность: средняя.**

```python
    if outcome in ("failed", "timeout"):
        # timeout попадал в else и писался INFO, при том что соседняя текстовая
        # запись WORKER_TIMEOUT — ERROR: машинный и человекочитаемый вывод
        # расходились по уровню на одном и том же событии.
        log_data["level"] = "ERROR"
        logger.error(json.dumps(log_data))
    elif outcome in ("degraded", "cancelled"):
        log_data["level"] = "WARNING"
        logger.warning(json.dumps(log_data))
```

---

#### Д58. `items_processed` в итоге итерации — жёсткий ноль

**Где:** `auto_renewal.py:838`, `trial_notifications.py:839`,
`app/workers/farm_notifications.py:372`; в другую сторону —
`fast_expiry_cleanup.py:216` и `:540`.

**Что не так.** `process_auto_renewals` (`auto_renewal.py:69`) ничего не
возвращает, и в метрику уходит константа `items_processed=0`. Итерация, списавшая
деньги у сотни человек, и пустая итерация в логе неразличимы. В
`fast_expiry_cleanup` наоборот: счётчик растёт до всех проверок, то есть пишется
размер выборки; честное число лежит в `items_revoked` (`:545`), но внешние
дашборды читают `items_processed`.

**Срочность: низкая-средняя.**

**Правка:** вернуть счётчик из тела (`return charged_count`) и передать его.

---

#### Д59. Двойная запись об окончании итерации воркера

**Где:** `activation_worker.py:560` и `:643` (`finally`);
`trial_notifications.py:775` и `:836` (`finally`).

**Что не так.** Ветки пропуска итерации вызывают `log_worker_iteration_end` явно
и делают `continue`, при котором `finally` пишет вторую запись. В
`fast_expiry_cleanup.py:131-133` этот же случай уже исправлен. Счёт итераций и
пропусков завышен вдвое.

**Срочность: низкая-средняя.**

**Правка:** убрать явный вызов перед `continue`, оставив только `finally` —
`outcome`/`reason` уже присвоены выше.

---

#### Д60. Отказ панели при сверке записан на DEBUG

**Где:** `database/reconciliation_panel.py:185-189`, `:194-198`.

**Что не так.** При недоступной панели сверка молча падает на значения из базы, и
экран кандидатов показывает «всё в порядке» — то есть противоположное правде. На
INFO-логгере записи нет.

**Срочность: средняя.**

```python
        except Exception as e:
            logger.warning(
                "RECONCILIATION_PANEL_LOOKUP_FAILED tg=%s uuid=%s error=%s — строка "
                "сверена по базе без панели, результат неполный",
                telegram_id, (uuid or "")[:8], e,
            )
```

---

#### Д61. Финальное напоминание триала вычисляется и не отправляется

**Где:** `trial_notifications.py:328-367`, `:357`.

**Что не так.** `should_send_final` и `payload_final` вычисляются, payload
собирается на `:351-355` — и функция заканчивается на `:367` без единого
действия. Логируется только противоположный случай (`:357`
`trial_reminder_skipped`, DEBUG).

**Что произойдёт при разборе.** «Почему упала конверсия триала в оплату» —
отличить «не было кому отправлять» от «код до отправки не доходит» нельзя.

**Срочность: средняя** (это поведение, но обнаруживается через отсутствие лога).

```python
    if should_send_final:
        logger.error(
            "TRIAL_FINAL_REMINDER_NOT_SENT user=%s expires_at=%s — ветка приняла "
            "решение отправить и завершилась без отправки",
            telegram_id, trial_expires_at,
        )
```

---

#### Д62. 3-часовое напоминание триала: потеря без записи и выдуманная скидка

**Где:** `trial_notifications.py:317`, `:318-322`, `:213-222`.

**Что не так.** 24-часовая ветка исправлена
(`trial_reminder_24h_undelivered`), 3-часовая — нет: `else:` пишет
`status="blocked"` только в БД-метрику, в лог — ничего. Плюс сама запись об
успехе содержит `discount=15%` как факт, хотя в этой ветке **никакая скидка в БД
не создаётся** — отправляется только клавиатура. И `:213-222`: результат
резервной отправки `try_send_bypass_activated` не проверяется, успешная отправка
не логируется вовсе.

**Срочность: средняя.**

```python
        else:
            logger.warning(
                "trial_reminder_3h_undelivered: user=%s — бот заблокирован, флаг "
                "однократности уже выставлен, повтора не будет",
                telegram_id,
            )
```

и убрать `discount=15%` из записи об успехе — либо писать реальный результат
создания скидки.

---

#### Д63. Ранний выход «уже финализировано» не закрывает span обработчика

**Где:** `app/handlers/payments/goods_delivery.py:196-202` (premium), `:262-268`
(stars), `:319-325` (steam), `:376-382` (spotify), `:438-444` (apple).

**Что не так.** `log_handler_entry` открывается в
`app/handlers/payments/payment_preflight.py:228`, а эти пять веток делают
`return True` без `log_handler_exit`. По логам обработчик обрывается на входе.
Тест `test_every_goods_delivery_branch_closes_its_span` проверяет только наличие
вызова в теле функции и такой выход не ловит.

**Срочность: низкая-средняя.**

```python
            await state.clear()
            log_handler_exit(
                handler_name="process_successful_payment",
                outcome="success",
                telegram_id=telegram_id,
                operation="payment_finalization",
                duration_ms=(time.time() - start_time) * 1000,
                payment_type="telegram_premium",
                purchase_id=purchase_id,
                reason="already_finalized",
            )
            return True
```

---

#### Д64. Записи об отказе выдачи трафика — без трейсбэка и без purchase_id

**Где:** `app/handlers/payments/goods_delivery.py:526-530`, `:548-552`,
`:568-569`, `:625-626`.

**Что не так.** `logger.error` вместо `logger.exception` на пути оплаченной
выдачи — трейсбэк отказа панели теряется; `purchase_id` в области видимости есть
и в запись не попадает, хотя в `log_handler_exit` ниже попадает.

**Срочность: низкая-средняя.**

---

#### Д65. Алерты админу не содержат ни тега, ни идентификатора

**Где:** `app/services/admin_notifier.py:212-213`, `:218-219`, `:223-224`,
`:243-244`, `:264-265`; `app/services/admin_alerts.py:129`, `:136`, `:144`.

**Что не так.** `admin_notifier telegram fallback failed: %s` — без `tag`/`title`:
какое именно уведомление потеряно (ошибка платежа? завершение рассылки?),
неизвестно, а это последний канал оповещения, и уровень WARNING. Подавление
алерта настройкой (`:218`) и 60-секундным троттлингом (`:223`) не оставляет
записи и счётчика — в `admin_alerts.py:81-84` счётчик подавленных есть, здесь
нет. `ADMIN_ALERT_SENT/FAILED` содержат только категорию, хотя `telegram_id` и
`purchase_id` есть в теле (`:183-184`).

**Срочность: средняя.**

```python
        logger.error(
            "ADMIN_NOTIFIER_FALLBACK_FAILED tag=%s title=%s error=%s — последний "
            "канал оповещения не сработал, уведомление потеряно",
            tag, title, e,
        )
```

---

#### Д66. Отмена итерации site_sync смешана с нормальным выходом

**Где:** `app/workers/site_sync_worker.py:124-126`.

**Что не так.** Остальные воркеры на этом месте делают `raise` (исправляли
специально). Здесь `break`, и «остановлен по shutdown» неотличим от «вышел
навсегда сам».

**Срочность: низкая.**

---

#### Д67. `startup_jitter` пишет момент старта воркера на DEBUG

**Где:** `app/core/worker_startup.py:39`. Плюс два воркера не пользуются
хелпером вовсе: `app/workers/farm_notifications.py:320` и
`app/workers/traffic_monitor.py:195` спят напрямую.

**Срочность: низкая.** Момент фактического старта воркера в проде не виден.

---

#### Д68. Восстановление зависших триалов пишется в stdout скрипта

**Где:** `scripts/recover_stuck_trials.py:110-112`, `:36`, `:167`.

**Что не так.** Скрипт снимает флаг использованного триала (человек получает
повторный бесплатный доступ). Провал записи в `audit_log` — WARNING; сами записи
о восстановлении идут в собственный `basicConfig` и в журнал сервиса не попадают.

**Срочность: низкая.**

---

#### Д69. `path=delayed_task` зашит константой

**Где:** `app/services/trials/bypass_activation_delay.py:134`; резервный вызов —
`trial_notifications.py:217`.

**Что не так.** Та же функция вызывается резервным путём, и запись всё равно
помечена как основной. Разобрать, работает ли основной путь, по логу нельзя.
Плюс `:105-108` — проигранная гонка и «нет активного триала» слиты в одну
DEBUG-запись.

**Срочность: низкая.**

---

### Уровень F — секреты и персональные данные в логах

---

#### Д70. Логин Steam-аккаунта покупателя пишется в лог на INFO

**Где:** `app/handlers/payments/steam_purchase.py:413-416` и `:718-721`.

```python
    logger.info(
        "STEAM_PURCHASE_CREATED user=%s purchase_id=%s amount=%s login=%s price=%s",
        telegram_id, purchase_id, amount, login, price,
    )
```

**Что не так.** `login` — то же значение, которое кладётся в колонку `country`
(`:411`) и которое `database/pending_purchases.py:176-184` специально редактирует
до `<redacted:account>` именно потому, что это учётная запись покупателя. Здесь
она печатается целиком, рядом с telegram_id, на каждой покупке. На `:718` — то же
значение, прочитанное обратно из `purchase.get("country")`. Проект уже принял
решение по этому классу данных (email Spotify в той же колонке); эти две строки
его обходят.

**Срочность: средняя-высокая.**

```python
    from app.utils.security import mask_secret
    logger.info(
        "STEAM_PURCHASE_CREATED user=%s purchase_id=%s amount=%s login=%s price=%s",
        telegram_id, purchase_id, amount, mask_secret(login), price,
    )
```

---

#### Д71. Токен привязки сайта печатается первыми 16 символами

**Где:** `app/handlers/user/start/command.py:198`, `:226`.

```python
    logger.info("SITE_LINK_SUCCESS user=%s token=%s", telegram_id, payload[:16])
```

**Что не так.** Длина токена проверяется как `len(payload) >= 10` (`:188`), то
есть при токене в 10–16 символов в лог уходит **весь** токен. Токен
предъявительский: кто его прочитал, тот привязал свой Telegram к чужому аккаунту
на сайте, а следом идут `sync_balance` и `sync_referrals` — то есть чужие деньги.

**Срочность: средняя-высокая.**

```python
    from app.utils.security import mask_secret
    logger.info("SITE_LINK_SUCCESS user=%s token=%s", telegram_id, mask_secret(payload))
```

то же на `:226`.

---

#### Д72. Промокод пишется в лог целиком в четырнадцати местах

**Где:** `database/promo.py:195`, `:229`, `:234`, `:239`, `:242`, `:284`, `:329`,
`:346`, `:375`, `:381`, `:406`, `:411`, `:511`;
`app/handlers/payments/promo_fsm.py:127`, `:142`;
`app/handlers/common/utils.py:622`, `:663`;
`app/handlers/payments/callbacks/purchase_flow.py:182`, `:423`.

**Что не так.** Промокод — предъявительский код на скидку с лимитом `max_uses`,
обычно больше единицы: кто прочитал лог, тот получил рабочую скидку. Это тот же
класс, что уже закрыт тестом для `gift_code` и `bgift_code`, но `mask_secret`
здесь не применяется нигде.

**Срочность: средняя** — и требует явного решения владельца (см. §5).

```python
    from app.utils.security import mask_secret
    logger.info(
        f"PROMOCODE_CONSUMED code={mask_secret(code_normalized)} user={telegram_id} "
        f"used_count={used_count}/{max_uses}"
    )
```

Плюс расширить `_BEARER_CODE_FILES` в `tests/services/test_log_observability.py`:

```python
    "database/promo.py": ("code", "code_normalized", "promo_code"),
    "app/handlers/payments/promo_fsm.py": ("promo_code",),
```

---

#### Д73. Сырое тело ответа стороннего сайта пишется в лог

**Где:** `app/services/site_sync.py:53` — `body=%s … resp.text[:300]`.

**Что не так.** Если сайт вернёт в теле ошибки подписочную ссылку, токен или
учётные данные, единственная защита — `PIISanitizingFilter`. Он покрывает
подписочные ссылки и bearer-токены, но не покрывает, например, коды подарков.

**Срочность: низкая-средняя.** Правка — убрать `body`, см. Д36.

---

#### Д74. Имя пользователя дашборда попадает в запись о неудачном входе

**Где:** `app/api/dashboard/auth.py:279`.

```python
    _log.warning("DASHBOARD_LOGIN_FAILED ip=%s user=%s", client_ip, body.username[:40])
```

**Что не так.** В лог попадает присланное имя пользователя — половина учётной
записи. Пароль не пишется, и это правильно. Само имя полезно для разбора
брутфорса, поэтому решение неоднозначное; как минимум стоит ограничить длину до
8 символов или маскировать.

**Срочность: низкая.**

---

#### Д75. `mask_secret` показывает последние 4 символа

**Где:** `app/utils/security.py:306-326`.

**Что не так.** Для 12-символьного `gift_code` и 10-символьного bgift-кода
последние четыре символа — стабильный частичный слепок. Тестами закреплено
применение маски, но не её форма. Для предъявительских кодов разумнее показывать
первые 2–3 символа и длину либо короткий хеш.

**Срочность: низкая.** Текущая форма не является утечкой сама по себе, но сужает
перебор.

---

## 4. Что уже хорошо (не сломать при следующей правке)

- **`app/services/payments/confirmation.py`** — эталон для остальных путей:
  `PAYMENT_REJECTED` / `PAYMENT_TRANSIENT_ERROR` / `PAYMENT_PERMANENT_ERROR` /
  `PAYMENT_DELIVERY_FAILED_SILENTLY` / `ORDER_LOST`, каждая с provider,
  telegram_id, purchase_id; `TransientPaymentError` разбирается отдельной веткой
  и не глохнет; алерт админу поднимается на каждом отказе.
- **`app/api/dashboard/routes/users.py:281-330` и `:389-419`** — выдача и отзыв
  доступа из дашборда: START / OK / FAILED / NOOP, `admin=` везде,
  `notify_delivered` вычисляется по факту отправки, а недоставленное уведомление
  получает собственную запись. Это шаблон, который нужно перенести на остальные
  ручки дашборда (Д41–Д48).
- **`app/services/purchase_flow.py`** — `PURCHASE_FLOW_DONE` печатает наличие
  ссылок (`bool(premium_sub_url)`), а не сами ссылки; `BYPASS_PROVISION_FAILED`
  на CRITICAL с telegram_id и причиной отказа панели;
  `PURCHASE_FLOW_UUID_UNVERIFIED` честно помечает неподтверждённую связь.
- **`database/referral_reward.py:353`** — `REFERRAL_REWARD_APPLIED` с обоими
  участниками, `purchase_id`, процентом и суммой в рублях; на `:342` рядом
  копейки. Образец денежной записи.
- **`database/balance_purchases.py:524-529`** — `BALANCE_TOPUP_SUCCESS`:
  telegram_id, payment_id, provider, provider_charge_id, сумма, новый баланс,
  исход кешбэка, correlation_id.
- **`app/api/subscription_proxy.py:47-62`** — `_redact_target` режет целевую
  ссылку до схемы и хоста; правильный ответ на «`split("?")[0]` не прячет
  секрет, лежащий в пути». Не заменять обратно.
- **`app/core/logging_config.py:46-142`** — санитайзер работает и в трейсбэках, и
  в JSON-режиме; покрывает vless/happ/incy/ss/trojan/vmess и подписочные ссылки
  обоих форматов.
- **`database/pending_purchases.py:165-184`** — `_country_for_log`: колонка,
  переиспользованная под учётку покупателя, редактируется по типу покупки.
- **`app/handlers/payments/precheckout.py:61-65`** —
  `PRE_CHECKOUT_DB_ERROR_ALLOWED` на CRITICAL с прямым «платёж пропущен без
  проверки, проверьте выдачу вручную»; `PRE_CHECKOUT_APPROVED` строго после
  `answer(ok=True)`.
- **`app/handlers/payments/payments_messages.py:221-246`** —
  `PURCHASE_ROUTE_UNHANDLED`: предохранитель на случай товара без обработчика, с
  алертом и явным «выдача НЕ выполнена».
- **`database/reconciliation_fix.py:183-276`** — порядок «панель → журнал → лог»,
  исход вычисляется из `panel_updated`, отказ панели даёт ERROR.
- **`database/referral_codes.py:218`** — `if result == "UPDATE 1":` плюс чтение
  обратно. Это тот шаблон, которого не хватает в
  `app/services/referrals/service.py:195` (Д16).
- **`app/handlers/callbacks/pay_balance.py:425-445`** — переменная `delivered` и
  статистика автоуведомлений по факту доставки; шаблон для Д1.
- **`app/services/broadcast_sender.py:143,159`** — `sent`/`failed` считаются по
  факту наличия `msg_id`, а не по размеру выборки. Уровень записи чинить надо
  (Д52), сам подсчёт — правильный.
- **Единицы денег в записях.** Во всех денежных строках единица указана явно
  (`RUB` / `kopecks`), в `database/users.py:155` и `:400` — обе сразу. Путаницы
  «рубли вместо копеек» в самих записях не найдено ни в одном месте.

---

## 5. Чего проверить не смог

1. **Реальный формат вывода в проде.** Разбор сделан по коду, а не по журналу
   работающего бота. Проверить, включён ли `LOG_FORMAT=json` на боевой площадке
   и попадают ли записи в агрегатор, из репозитория нельзя. Вывод Д2 (потеря
   `extra`) проверен запуском форматтеров локально и от площадки не зависит.
2. **Персональность промокодов** (Д72) — нужно решение владельца. Если коды в
   `database/promo.py` бывают одноразовыми и персональными, их печать на INFO —
   утечка того же класса, что уже закрыта для подарков; если все коды
   кампанийные и раздаются публично, дефект снимается. По коду отличить нельзя:
   `max_uses` задаётся при создании и может быть любым.
3. **Объём логов после правок.** Предложенные записи добавляют строки на горячих
   путях (экран подключения, витрина пакетов, построчные записи рассылки).
   Оценить, во что это выльется на боевой нагрузке, из репозитория нельзя. При
   применении разумно начать с ERROR/CRITICAL и добавлять INFO по мере
   необходимости; для рассылки — сначала провести `broadcast_id` в существующие
   записи, а новых не добавлять.
4. **Поведенческие дефекты, найденные попутно.** Три пункта — Д61 (финальное
   напоминание триала вычисляется и не отправляется), Д10 (статус `deleted`
   ставится всем строкам) и Д13 (обещанная отмена pending-покупок отсутствует) —
   это дефекты поведения, а не логирования; они попали в отчёт потому, что
   обнаруживаются только по логам. Решение, чинить ли поведение или привести лог
   в соответствие с ним, за владельцем.
5. **`load_tests/`, `graphify-out/`, `migrations/`** не разбирались: это не
   боевые пути выполнения.
6. **Номера строк в `app/api/dashboard/routes/users.py`, `database/analytics*.py`
   и `app/api/dashboard/__init__.py` могут сместиться.** На момент разбора эти
   файлы правились параллельно другими исполнителями (сводка дашборда,
   `routes/summary.py`, `database/dashboard_summary.py`). Дефекты Д41, Д43 и
   ссылки на образцовые записи `user_grant`/`user_revoke` искать по тегам
   (`DASHBOARD_GRANT_START`, `DASHBOARD_REVOKE_OK`) и именам функций, а не по
   номерам строк. Все остальные ссылки в отчёте сверены с текущим деревом.
