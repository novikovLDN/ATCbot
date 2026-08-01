# Аудит ATCbot — сводка

Дата: 2026-07-31 · ветка `codenew` · подпроект A (read-only)

## Числа

- Находок: **217** — P0 11, P1 61, P2 108, P3 37
- Прошли перепроверку: **25**
- Опровергнуто: **7**
- Не перепроверялись: **192** — гипотезы, а не факты

Перепроверка охватывала три самые тяжёлые находки каждого домена и **все P0 без исключения**. Остальное требует подтверждения перед тем, как по нему что-то менять.

## Критическое (P0)

1. ✅ **После исчерпания попыток активации подписка навсегда зависает в 'pending' — оплаченный VPN не выдаётся и никто об этом не узнаёт как о финальной ошибке**  
   `activation_worker.py:265` · домен workers
2. ✅ **Magic-link JWT остаётся полноценным админ-ключом на 30 дней вопреки документации**  
   `app/api/dashboard/deps.py:30` · домен dashboard
3. ✅ **Кубики + Боулинг раздают ~12,5 дней подписки в месяц бесплатно, без потолка**  
   `app/handlers/game.py:371` · домен games
4. ✅ **Гонка на кулдауне кубиков и боулинга: параллельные клики дают несколько грантов подряд**  
   `app/handlers/game.py:175` · домен games
5. ✅ **Гонка при сборе урожая: двойной клик начисляет награду несколько раз**  
   `app/handlers/game.py:1002` · домен games
6. ✅ **Оплата Spotify картой через Telegram Payments уходит в ветку VPN-подписки — деньги списаны, товар не выдан**  
   `app/handlers/payments/payments_messages.py:843` · домен payments
7. ✅ **Несовпадение суммы в вебхуке отдаёт провайдеру HTTP 200 «already_processed» — платёж молча теряется без алерта**  
   `app/services/payments/confirmation.py:177` · домен payments
8. ✅ **Удаление xray-веток вместе с XRAY_API_* переменными глушит весь провижининг**  
   `config.py:352` · домен vpn
9. ✅ **Оффлайн-режим шторма выгоднее онлайна: авто-сбор 50 % без единого действия, петля «зашёл раз в цикл»**  
   `database/farm.py:370` · домен games
10. ✅ **Авто-починка CHECK-констрейнтов в create_pending_purchase сужает список типов и ломает Steam/прокси/ферму навсегда**  
   `database/subscriptions.py:4180` · домен payments
11. ✅ **Вебхук Lava не проверяет подпись вообще — бесплатная подписка любому, кто знает purchase_id**  
   `lava_service.py:195` · домен payments

## Высокий приоритет (P1)

- ✅ **Нет никакой защиты от перебора пароля на /auth/login** — `app/api/dashboard/auth.py:183` · dashboard
- ✅ **Главный экран для неруссскоязычных юзеров без подписки показывает сырой ключ вместо текста** — `app/handlers/callbacks/navigation.py:116` · flow
- ✅ **Экран подключения (setup_step1/step2) на не-ru языках состоит из сырых ключей** — `app/handlers/callbacks/navigation.py:609` · flow
- ✅ **Шторм превращает любое растение в 50 % награды независимо от прогресса — доминирующая стратегия фермы** — `app/handlers/game.py:1523` · games
- ✅ **KeyError('gb') при построении кнопок периода для всех 6 неруских языков** — `app/handlers/payments/callbacks.py:297` · i18n
- ✅ **Заказ Spotify, оплаченный картой в Telegram, не обрабатывается: админ не получает заказ** — `app/handlers/payments/payments_messages.py:699` · notifications
- ✅ **get_text молча игнорирует «default» — 108 вызовов с третьим позиционным аргументом** — `app/i18n/__init__.py:32` · i18n
- ✅ **Подарочная подписка, оплаченная через CryptoBot/Lava, не доставляется покупателю** — `app/services/payments/confirmation.py:68` · notifications
- ✅ **Реферальный кешбэк по webhook-оплатам начисляется, но реферер не получает уведомления** — `app/services/payments/confirmation.py:129` · notifications
- ✅ **Две системы управления схемой БД: 68 SQL-миграций и 116 DDL-операторов в database/core.py на каждом старте** — `database/core.py:457` · deadcode
- ✅ **Событие payment:approved никогда не публикуется: live-лента мертва, milestone-push не приходит** — `database/subscriptions.py:2607` · dashboard
- ✅ **Нет обработчика SIGTERM — весь блок graceful shutdown никогда не выполняется при деплое** — `main.py:761` · workers
- ✅ **Умерший фоновый таск или упавший uvicorn не детектируется и не перезапускается — процесс живёт «зомби»** — `main.py:682` · workers
- ✅ **Вся компенсация при сбое провижининга — no-op: orphan-сущности остаются в панели** — `vpn_utils.py:640` · vpn
- ⚠️ **ActivationNotAllowedError не перехватывается — одна плохая подписка обрывает всю итерацию воркера** — `activation_worker.py:236` · vpn
- ⚠️ **Перезагрузка страницы или прямая ссылка на любой раздел дашборда даёт 404** — `app/api/__init__.py:92` · dashboard
- ⚠️ **/stats/overview не отдаёт business_metrics — шесть KPI на дашборде всегда «—»** — `app/api/dashboard/routes/stats.py:36` · dashboard
- ⚠️ **Дашборд открывается из бота обычной url-кнопкой — на iOS это встроенный браузер Telegram, где push и установка невозможны** — `app/handlers/admin/base.py:45` · dashboard
- ⚠️ **Комбо-тариф, оплаченный Stars, продаётся по цене обычного тарифа — трафик обхода отдаётся бесплатно** — `app/handlers/callbacks/payments_callbacks.py:1013` · payments
- ⚠️ **Промокод сгорает при оплате Stars, хотя скидка не применяется** — `app/handlers/callbacks/payments_callbacks.py:1034` · payments
- ⚠️ **Пополнение баланса через СБП зачисляет пользователю наценку 11% — комиссия оплачивается за счёт бизнеса** — `app/handlers/callbacks/payments_callbacks.py:1677` · payments
- ⚠️ **Пользовательский сценарий вывода средств полностью недостижим, но код и обработчики живы** — `app/handlers/callbacks/payments_callbacks.py:229` · payments
- ⚠️ **Имя пользователя из Telegram подставляется в HTML без экранирования — экран профиля исчезает без следа** — `app/handlers/common/screens.py:430` · i18n
- ⚠️ **276 захардкоженных русских строк в пользовательских экранах — экран «Круг Амбассадоров» локализован на 0%** — `app/handlers/common/screens.py:236` · i18n
- ⚠️ **Ферма не проверяет подписку — внутренняя валюта доступна тем, кто ничего не платит** — `app/handlers/game.py:751` · games
- ⚠️ **Баланс фермы неотличим от реальных денег и выводится на карту** — `app/handlers/game.py:1020` · games
- ⚠️ **Покупка грядки неатомарна: списание, запись массива и счётчик — три отдельных запроса** — `app/handlers/game.py:1153` · games
- ⚠️ **Зависший шторм навсегда блокирует посадку для всех пользователей** — `app/handlers/game.py:783` · games
- ⚠️ **Триал на 3 дня превращается в 4–16 дней бесплатного доступа через игры** — `app/handlers/game.py:339` · games
- ⚠️ **Ни у одной игровой механики нет потолка выгоды и нет метрик выданной ценности** — `app/handlers/game.py:34` · games
- ⚠️ **Кнопки periодов у неруских пользователей подписаны текстом «buy.button_price_badge»** — `app/handlers/payments/callbacks.py:292` · i18n
- ⚠️ **Оплата подписки через Telegram Stars записывает количество звёзд как рубли — выручка и реферальный кешбэк занижены** — `app/handlers/payments/payments_messages.py:551` · payments
- ⚠️ **Premium/Stars/Steam/Apple ID: экран успеха и уведомление админу отправляются ДО пометки покупки оплаченной, результат не проверяется** — `app/handlers/payments/payments_messages.py:619` · payments
- ⚠️ **pre_checkout подтверждает платёж при ошибке БД, после чего successful_payment не находит покупку — деньги списаны, товар не выдан** — `app/handlers/payments/payments_messages.py:74` · payments
- ⚠️ **Пароль от Spotify-аккаунта хранится в открытом виде в pending_purchases и пересылается в чат админу** — `app/handlers/payments/spotify_purchase.py:574` · payments
- ⚠️ **Первый и последний символ пароля Spotify подставляются в <code> без экранирования** — `app/handlers/payments/spotify_purchase.py:434` · i18n
- ⚠️ **104 ключа, реально запрашиваемых из кода, есть только в ru — неруские пользователи видят сырые ключи** — `app/handlers/payments/steam_purchase.py:227` · i18n
- ⚠️ **Кнопки «Карта (Lava)» и «СБП» в покупке Telegram Stars гарантированно падают — неверная сигнатура и несуществующий модуль** — `app/handlers/payments/telegram_stars_purchase.py:423` · payments
- ⚠️ **Переключатели «Telegram DM» на самом деле управляют web-push, а DM не отправляются** — `app/services/admin_notifier.py:167` · dashboard
- ⚠️ **KeyError('action_type') в уведомлении о кэшбэке для 5 языков** — `app/services/notifications/service.py:427` · i18n
- ⚠️ **Продажи Stars/Premium/Steam/Spotify/прокси не создают записей в таблице payments — выручка невидима в статистике** — `app/services/payments/confirmation.py:74` · payments
- ⚠️ **Adopt легаси bypass-сущности отдаётся как conflict_unrelated_user и валит покупку** — `app/services/remnawave_bypass.py:65` · vpn
- ⚠️ **Adopt bypass-сущности не начисляет купленный трафик** — `app/services/remnawave_bypass.py:151` · vpn
- ⚠️ **Воркер уведомлений перезаписывает грядки устаревшим снимком — урожай можно собрать дважды** — `app/workers/farm_notifications.py:107` · games
- ⚠️ **Автопродление всего батча (до 100 подписок) идёт в одной транзакции, а ошибка на одном пользователе отравляет её целиком** — `auto_renewal.py:115` · workers
- ⚠️ **Уведомление об успешном автопродлении всегда показывает сумму 0** — `auto_renewal.py:415` · workers
- ⚠️ **Автопродление считает цену по своей формуле мимо calculate_final_price и админских override** — `auto_renewal.py:215` · deadcode
- ⚠️ **На iPhone в Safari секция push показывает «Не поддерживается» вместо инструкции «На экран Домой»** — `dashboard/src/pages/Settings.tsx:309` · dashboard
- ⚠️ **Админский отзыв доступа не отключает premium в Remnawave** — `database/admin.py:3217` · vpn
- ⚠️ **Двойной учёт денег в таблице payments: пополнение баланса и покупка с баланса пишутся как две выручки** — `database/admin.py:3505` · dashboard
- ⚠️ **Тот же двойной учёт в pending_purchases завышает «Доход сегодня» и порог milestone-пуша** — `database/admin.py:594` · dashboard
- ⚠️ **Удаление пользователя из дашборда физически стирает историю платежей — выручка меняется задним числом** — `database/admin.py:4328` · dashboard
- ⚠️ **Реконсиляция режет доступ до NOW+1 день, сравнивая пересчёт с потенциально устаревшей датой из БД** — `database/reconciliation.py:721` · vpn
- ⚠️ **FOR UPDATE SKIP LOCKED в finalize_purchase выполняется вне транзакции и не блокирует ничего** — `database/subscriptions.py:4441` · payments
- ⚠️ **Админский перевыпуск ключа через reissue_subscription_key всегда падает** — `database/subscriptions.py:956` · vpn
- ⚠️ **Оплаченная картой плёнка может не примениться, деньги остаются у продавца без возврата** — `database/subscriptions.py:4847` · games
- ⚠️ **Флаги reminder_7d_sent и reminder_1d_sent никогда не сбрасываются — напоминания приходят один раз за всю жизнь пользователя** — `database/subscriptions.py:2226` · notifications
- ⚠️ **Проверка подписи Lava fail-open: без LAVA_SIGN_KEY функция возвращает True** — `lava_service.py:183` · payments
- ⚠️ **После восстановления БД перезапускаются только 5 воркеров из 9 — остальные молчат до рестарта процесса** — `main.py:354` · workers
- ⚠️ **Advisory-лок single-instance не берётся, если БД была недоступна на старте, и не берётся после её восстановления** — `main.py:228` · workers
- ⚠️ **Все автонапоминания уходят на русском независимо от языка пользователя** — `reminders.py:181` · notifications

Обозначения: ✅ подтверждено независимой проверкой, ⚠️ не перепроверялось.

## Отчёты по доменам

- [Платежи и провайдеры](payments.md) — 24
- [VPN-ядро и Remnawave](vpn.md) — 24
- [Флоу бота, экраны и кнопки](flow.md) — 24
- [Мини-игры и экономика](games.md) — 24
- [Уведомления и выдача товара](notifications.md) — 25
- [Админ-дашборд](dashboard.md) — 24
- [Локализация и рендер текста](i18n.md) — 24
- [Фоновые воркеры и надёжность](workers.md) — 24
- [Мёртвый код и дубли архитектуры](deadcode.md) — 24

- [Карта мёртвого кода](dead-code-map.md)
- [findings.json](findings.json) — машиночитаемый реестр

