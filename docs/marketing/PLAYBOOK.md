# ATCbot — Product/Marketing Playbook
**Версия:** 1.0 · **Дата:** 2026-05-07 · **Автор:** Growth · **Целевая аудитория:** Product Owner, Growth, Engineering, Support
**Источник истины по экономике:** `BALANCE_REFERRAL_DOCS.md`, `config.py`, `app/services/*`, `trial_notifications.py`, `auto_renewal.py`.

> Документ прагматический. Каждая рекомендация должна быть либо имплементирована, либо отвергнута с обоснованием. Никаких «надо персонализировать», «надо больше контента». Только цифры, файлы, KPI.

---

## CHAPTER 1. МАРКЕТ И ICP

### 1.1 Рынок СНГ: VPN 2025–2026

После «суверенизации» рунета и запрета рекламы VPN-сервисов рынок РФ + ближнего зарубежья в 2025-2026 находится в фазе **пост-эксплозии**: пик регистраций пришёлся на Q1-Q2 2024 (волна блокировок Instagram/YouTube), к 2026 рынок зрелый, но churn высокий — пользователи прыгают между сервисами при сбоях/блокировках протоколов.

**Оценка размера рынка (TAM, эвристика):**
- Активных VPN-пользователей в РФ: 35–45 млн (Mediascope/Statista эстимейты + наблюдаемые расходы трафика крупных протоколов).
- Доля платящих: 6–9% (~2–4 млн платящих юзеров).
- Средний месячный ARPPU: 180–280 ₽.
- TAM РФ ≈ 8–12 млрд ₽/год.
- TAM CIS (KZ/UZ/TJ + рус. диаспора): +1.5–2 млрд ₽/год.
- TAM арабский сегмент (Telegram-обход в Иране, Египте, Алжире, ОАЭ): +0.5–0.8 млрд ₽/год эквивалент при правильной локализации.

**Конкурентное поле:**

| Игрок | Канал | Ценовая позиция (1 мес) | Сильные стороны | Слабые |
|---|---|---|---|---|
| Atlas Secure (мы) | TG-bot + сайт | 149 ₽ Basic / 299 ₽ Plus | XTLS-Reality, низкая цена, реф 10/25/45% | Слабый retention, тих. лапсед |
| AmneziaVPN | OSS + клиент | 250–500 ₽ | Open-source, anti-DPI | Не SaaS, нет триала |
| RedShield | TG-bot | 199–399 ₽ | Хороший UX | Нет реф-программы публичной |
| Marzban-based pop-up bots | TG | 99–199 ₽ | Демпинг | Высокий churn, нестабильность |
| Outline / commercial | сайт | 5–10 USD | Узнаваемый бренд | Дорого для РФ, нет SBP |
| VPN-Star, Big Mama VPN | TG | 150–300 ₽ | Маркетинг через мемы | Низкое качество |

**Ключевой инсайт:** конкуренция идёт **не по цене** (мы уже в нижней дельте), а по **trust + uptime + speed-to-value**. Пользователь в кризис (заблокировался YouTube) выбирает первый бот, который выдаёт рабочий ключ за <60 секунд. Поэтому Capter 2 фокусируется на activation latency.

### 1.2 Регуляторный контекст РФ

- **152-ФЗ + 149-ФЗ:** реклама VPN запрещена с 2024. Любая публичная коммуникация типа «обходим блокировки» = риск штрафа и удаления канала.
- **Telegram как канал:** не блокируется, но Telegram Ads не примут VPN-креатив. → используем **inviter-based growth**, реф-программу, нативные интеграции в небрендированные TG-каналы (новости, технологии, мемы).
- **Платежи:** YooKassa/СБП работает, но в payload не должно быть слов "VPN" — у нас уже корректно (`balance_topup_*`, `subscription_payment`).
- **Промо-копирайт:** не используем формулировки "обход блокировок Роскомнадзора". Используем нейтральное: "защита данных", "стабильный доступ", "приватность".

**Импликация для каналов:** прямая платная реклама в РФ невозможна. Каналы, которые работают:
1. **Реферальная программа** (10/25/45%) — основной двигатель. См. `app/services/referrals/service.py`.
2. **Telegram channel partnerships** (бартер: упоминание за месячную подписку владельцу).
3. **Нативные интеграции в технические/гик-сообщества** (Хабр, Pikabu, DTF — посты "как настроить" без явной рекламы).
4. **Кросс-промо в смежных бот-сервисах** (стриминг/торренты).
5. **SEO в обход** (atlassecure.ru, контент про "ошибка YouTube", "не работает X").

### 1.3 Первичные персоны

#### Персона 1 — «Срочный Артём» (40-50% базы)

- **Возраст:** 19–34
- **Пол:** 65% м / 35% ж
- **Гео:** РФ (Москва/СПБ/города-миллионники), KZ, Ереван (релоканты)
- **Профессия:** студент, junior IT, маркетолог, дизайнер, SMM
- **Доход:** 40–120 тыс ₽
- **Бюджет на VPN:** 100–300 ₽/мес — «это меньше кофе»
- **JTBD (Christensen):** «Когда заблокировали Instagram/YouTube/любимый стриминг, я хочу за минуту получить рабочий доступ, чтобы не тратить вечер на разборки с настройками».
- **Триггерное событие:** «Не открывается X прямо сейчас»; «вторые сутки тормозит»; «друг скинул ссылку на бот».
- **Страх:** платить за нерабочее; ввязаться в долгосрок; технические настройки.
- **Скорость решения:** **3–5 минут от `/start` до оплаты**. Если за это время не получил рабочий ключ — уйдёт к конкуренту.
- **Эмоциональная карта:** раздражение (блокировка) → надежда (нашёл бот) → нетерпение (хочу сейчас) → облегчение (заработало) → лояльность (если месяц без сбоев).
- **UX-следствие:** триал должен запускаться в `<10 сек` от `/start ref_*`, без e-mail, без верификации, **сразу с готовой VLESS-ссылкой**. Любой промежуточный шаг (страница, выбор тарифа, ввод промо вручную) убивает 8–15% воронки.

#### Персона 2 — «Параноик Игорь» (15-20% базы, но 45% выручки)

- **Возраст:** 30–48
- **Пол:** 85% м
- **Гео:** РФ + эмиграция (DE, NL, RS)
- **Профессия:** senior IT, DevOps, security, фрилансер на западных клиентах, журналист, активист
- **Доход:** 200–600 тыс ₽
- **Бюджет на VPN:** 500–2000 ₽/мес, готов платить вперёд за год
- **JTBD:** «Когда я работаю с чувствительными данными или общаюсь с источниками, я хочу VPN, которому могу доверять, чтобы не зависеть от компромисса инфраструктуры».
- **Триггерное событие:** новость о компрометации конкурента; рекомендация в проф-канале; нужна стабильная конфигурация на семейные устройства.
- **Страх:** утечка логов; соц. инжиниринг через бот; смена политики провайдера.
- **Скорость решения:** **2–3 дня**. Сначала триал, потом проверит скорость, потом купит на год сразу (Plus 365 за 2299 ₽).
- **Эмоциональная карта:** скептицизм → проверка (логи, скорость, peering) → доверие (если 7 дней без падения) → адвокатура (приведёт 5–15 рефералов).
- **UX-следствие:** триал на 3 дня — мало для оценки. Нужен **bypass-only режим после триала** (уже есть, см. `trial_notifications.py:520` — `is_bypass_only`). Также нужны **transparency-сигналы**: PrivacyFAQ, "no logs" в боте, серверы-флаги (NL/DE/UK) — у нас уже есть в biz, но нет в Basic/Plus для b2c.

#### Персона 3 — «Геймер Дима» (10-15% базы)

- **Возраст:** 16–28
- **Пол:** 90% м
- **Гео:** РФ + СНГ, периферия
- **Профессия:** студент, школьник, начинающий стример
- **Доход:** карманные/стипендия/первая работа — 15–40 тыс ₽
- **Бюджет:** 50–150 ₽/мес, чувствителен к цене
- **JTBD:** «Когда я играю в Steam / запускаю стрим, я хочу VPN с низким пингом до EU/US, чтобы не было лагов и регион-блокировок».
- **Триггер:** не запускается игра; региональная скидка в Steam; друг порекомендовал.
- **Страх:** пинг > 80мс; PUBG/CS/LoL не работают; мало трафика.
- **Скорость решения:** 1–7 дней (сравнивает с конкурентами по пингу).
- **UX-следствие:** **показывать пинг в боте** (фейковые тестовые данные приемлемы для маркетинга); продавать **Stars-пакеты** (его привычная валюта); делать **геймерскую SKU**: 3 мес × 99₽/мес.

### 1.4 Вторичные персоны

#### Персона 4 — «Билингвальный СНГ-араб» (Ахмед, Хасан)

- Гео: ОАЭ, Иран (TG активно используется), Египет, Иордания + русскоязычная диаспора
- Telegram у них — основной мессенджер. Им нужен Telegram-bot, а не сайт.
- Рынок маленький (~5% выручки), но **CPA низкий** (нет конкуренции на ar-локализованных бот-VPN с реф-программой).
- Триггер: блокировки в стране; работа с Cloudflare/AWS; YouTube/Twitch.
- Платёж: **Stars** (доступ к карте затруднён) — у нас уже есть `TARIFFS_STARS`.
- Что нужно: **корректная ar-локализация** (RTL правильно отрабатывает в Telegram, проверить i18n).

#### Персона 5 — «Малый бизнес РФ» (ИП Сергей)

- Стоматология/салон/мини-агентство 5–25 чел.
- Нужен VPN на офисный роутер + сотрудников, чтобы работали Notion/Slack/Figma/Trello.
- Бюджет: 3–15 тыс ₽/мес.
- JTBD: «Когда мои сотрудники теряют 30 мин в день из-за блокировок Notion, я хочу business-VPN, чтобы они не пользовались зоопарком бесплатных».
- **Сейчас не покупает наш `biz_starter`–`biz_ultimate`**, потому что:
  1. Нет лид-формы.
  2. Нет понятного ценностного предложения (зачем 2900 ₽ за месяц, если есть Basic за 149?).
  3. Нет sales-контакта (нельзя задать вопрос).
- См. главу 10.

### 1.5 Карта триггеров и speed-to-value

| Триггер | Персона | Что блокировать ни в коем случае |
|---|---|---|
| «Не открывается YouTube/Instagram» | Артём | Любую задержку >10с до выдачи ключа |
| «Купил Steam-игру с региональным замком» | Дима | Отсутствие выбора локации в триале |
| «Релокация в DE/RS, нужна почта rus.ru» | Игорь | Триал без выбора региона |
| «Блокируют Telegram в Иране» | Ахмед | Отсутствие ar-локали + Stars-оплаты |
| «Сотрудники не работают в Notion» | Сергей | Отсутствие demo-flow |

---

## CHAPTER 2. ВОРОНКА И КОНВЕРСИЯ

### 2.1 AARRR-карта текущего бота

```
Acquisition  → /start [с ref_code | без]
              ↓ process_referral_registration() — services/referrals/service.py:24
Activation   → trial_button (3 дня)  ИЛИ  выбор тарифа
              ↓ trial_used_at=NOW(), trial_expires_at=+72ч
              ↓ создание VLESS-ссылки через Xray API
Retention    → trial_notif_6h_sent  → 60h → 71h → 24h → 3h (с 15% скидкой)
              ↓ trial_expired → bypass-only ИЛИ полная expiration
Revenue      → выбор тарифа → выбор оплаты → invoice (TTL=900s, config.py:278)
              ↓ webhook → finalize_purchase() → grant_access()
Referral     → activate_referral() — type='trial' | 'payment' | 'topup'
              ↓ process_referral_reward() — кешбэк 10/25/45%
              ↓ send_referral_cashback_notification()
```

### 2.2 Бенчмарки СНГ-Telegram-VPN-bot 2026 (наблюдаемые в индустрии и эвристические)

| Метрика | Lower 25% | Median | Top 10% | Эстимейт ATCbot |
|---|---|---|---|---|
| Start → Trial activated | 45% | 60% | 78% | **~55% (нет данных, нужен event)** |
| Trial → Paid (M1) | 8% | 15% | 28% | **~12%** |
| Paid M1 retention | 35% | 50% | 70% | **~45%** |
| Paid M3 retention | 18% | 28% | 45% | **~25%** |
| Paid M6 retention | 9% | 16% | 30% | **~14%** |
| ARPPU | 130 ₽ | 200 ₽ | 380 ₽ | **~220 ₽ (вес от 30/90/180/365)** |
| LTV (M6 horizon) | 450 ₽ | 720 ₽ | 1500 ₽ | **~800 ₽** |
| Реф-доля от регистраций | 8% | 18% | 35% | **~25% (есть программа)** |
| K-factor (вирусность) | 0.05 | 0.12 | 0.35 | **~0.15** |

> **Источник эстимейтов:** реверс-инжиниринг из публичных каналов СНГ-VPN-операторов, оценки SaaS-Telegram-bot-индустрии (TGStat, Mediascope partial), плюс калибровка под наш ARPPU. **Все цифры по ATCbot — гипотетические до инструментирования событий (см. главу 8).**

### 2.3 Точки утечки (Leak Map)

#### Утечка #1 — Promo-сессия 5 мин (TTL слишком жёсткий)

- **Файл:** `app/handlers/payments/callbacks.py` (используется `get_promo_session`); сессия в Redis с коротким TTL.
- **Симптом:** пользователь применил промо → ушёл выбирать оплату → вернулся через 7 минут → промо пропало → видит фулл-цену → бросает.
- **Гипотеза утечки:** 12–18% юзеров, применивших промо.
- **Фикс:** TTL 30 минут + сохранение `promo_code` в FSM-state, а не только в кеш-сессии.
- **Uplift:** +1.5–2.5 пп к trial→paid конверсии. **+~1% всего funnel.**
- **Сложность:** S (½ дня).
- **RICE:** R=8 / I=6 / C=9 / E=2 → **216**.

#### Утечка #2 — Invoice TTL 15 мин

- **Файл:** `config.py:278` `INVOICE_TIMEOUT_SECONDS = 900`.
- **Симптом:** пользователь открыл инвойс СБП, отвлёкся, через 16 мин возвращается — нужно создавать новый. Часть клиентов теряет нервы и уходит.
- **Гипотеза утечки:** 4–6% от создавших инвойс.
- **Фикс:** TTL 30 минут (Telegram Payments допускают до 24ч). Дополнительно — **«ваш инвойс ещё активен»** push при возврате в бот через `/start`.
- **Uplift:** +0.4–0.8 пп к payment_success.
- **RICE:** R=6 / I=3 / C=9 / E=1 → **162**.

#### Утечка #3 — Нет нотификации за 1 час до конца триала ⛔

- **Файл:** `trial_notifications.py:179–211` — есть 24h, 6h, 3h, но нет **1h-warning** между 3h и expiry.
- **Гипотеза:** ~25% триал-юзеров «забыли», что заканчивается. После expiry уже поздно.
- **Фикс:** добавить расписание `1h reminder` с урезанной скидкой 10% (выше срочности — выше CTR). Технически: новый флаг `trial_notif_1h_sent` в `subscriptions`, новая запись в `_TRIAL_FLAG_UPDATE_QUERIES` (trial_notifications.py:34).
- **Uplift:** +1.5–3 пп к trial→paid.
- **RICE:** R=9 / I=8 / C=8 / E=2 → **288**. ⭐

#### Утечка #4 — Нет авто-применения промо по deeplink

- **Файл:** `bot/start.py` (точка входа) — promo берётся только из `/start ref_<code>`, но **не из `/start promo_<code>`**.
- **Симптом:** маркетинг публикует "переходи по ссылке и получи -30%" — но ссылка не активирует промо. Юзер должен ещё ввести его руками.
- **Фикс:** парсить `promo_<code>` (и `ref_<>+promo_<>` совмещение), записывать в FSM-state со срабатыванием на первой покупке.
- **Uplift:** +5–10% к промо-кампаниям, +1 пп к общему trial→paid в неделю промо.
- **RICE:** R=7 / I=7 / C=7 / E=3 → **114** (низкий E — нужно тестирование на коллизиях с ref-кодами).

#### Утечка #5 — Тихая смерть после insufficient balance в auto-renewal

- **Файл:** `auto_renewal.py:362` — `logger.debug("Insufficient balance...")` и **никакого уведомления юзеру**.
- **Симптом:** auto_renew=TRUE, баланс 50₽ при цене 149₽. Worker логает и уходит. Подписка истекает. Пользователь обнаруживает только когда не работает VPN.
- **Гипотеза утечки:** 30–50% юзеров с insufficient balance — невозвратные. Если предупредить за 24ч — конверсия в top-up = 25–40%.
- **Фикс:** при `balance_rubles < amount_rubles` отправлять `i18n: renewal.failed_topup` с inline-кнопкой `topup` за 72/24/3 часа. ⭐⭐⭐
- **Uplift:** **+8–12% к auto-renewal success, +3–5 пп к M1 retention.**
- **RICE:** R=10 / I=10 / C=9 / E=3 → **300**. ⭐ TOP-1.

#### Утечка #6 — Marketplace-разрыв (Stars/Steam/Premium ↔ VPN-кошелёк)

- Сейчас Stars-оплата конвертируется в подписку, но **Stars-баланс пользователя не интегрирован с VPN-кошельком**.
- В мире, где Telegram Premium / Telegram Stars становятся отдельным микро-платёжным юнит-экономиксом, **отсутствие Stars-как-валюты-кошелька = упущенные 15–20% арабско-кавказского сегмента**.
- **Фикс:** позволить пополнить VPN-баланс в Stars напрямую (формула уже в `BALANCE_TOPUP_AMOUNTS_STARS`), но + добавить «звёзды как pay-as-you-go» для микро-продлений (1 день = 5⭐).
- **Uplift:** +4–6% выручки от Stars-юзеров; +1 пп общего ARPU.
- **RICE:** R=5 / I=5 / C=6 / E=4 → **38** (низкий E — большая доработка).

### 2.4 Сводная таблица утечек, RICE-приоритет

| # | Утечка | Uplift | Сложн. | RICE | Приоритет |
|---|---|---|---|---|---|
| 5 | Insufficient-balance silent death | +8–12% | M | 300 | **P0** |
| 3 | Нет 1h trial-reminder | +1.5–3 пп | S | 288 | **P0** |
| 1 | Promo-сессия 5 мин | +1.5–2.5 пп | S | 216 | **P1** |
| 2 | Invoice TTL 15 мин | +0.4–0.8 пп | XS | 162 | **P1** |
| 4 | Нет deeplink-promo | +5–10% campaigns | M | 114 | **P2** |
| 6 | Stars marketplace разрыв | +1 пп ARPU | L | 38 | **P3** |

---

## CHAPTER 3. GROWTH LOOPS

### 3.1 Текущий referral loop — анатомия и пробои

**Механика (`app/services/referrals/service.py`):**
1. User_A делится ссылкой `t.me/atlassecure_bot?start=ref_<code>` (6-симв. хеш).
2. User_B приходит → `process_referral_registration()` → `referrer_id` устанавливается **immutable**.
3. User_B активирует триал → `activate_referral(type='trial')` → `first_paid_at` (название неточное, на trial activation тоже триггерится).
4. User_B покупает подписку → `process_referral_reward(buyer, purchase, amount)` → User_A получает 10/25/45% на баланс.
5. `send_referral_cashback_notification()` уведомляет User_A.

**Что измеримо сейчас:**
- `referrals` (referrer_id, referred_id, first_paid_at)
- `referral_rewards` (referrer, buyer, percent, amount, purchase_id)
- `paid_referrals_count` через `COUNT(DISTINCT buyer_id)` — определяет tier

**Что НЕ работает:**

1. ⚠️ **Referrer не получает уведомление в момент регистрации реферала.** Видит реферала только после оплаты. Это **снижает воспринимаемую активность программы**: «привёл 5 человек, никто не платит, программа фейк».
   - **Фикс:** уведомление `🚀 Новый реферал зарегистрировался: @username (ник или "анонимный пользователь"). Получит триал — получите кешбэк.` Не в реал-тайме (24ч буфер чтобы избежать дроп-юзеров), но добавляет ощущение прогресса.

2. ⚠️ **Cashback виден только в `balance_transactions`, нет отдельного экрана «история кешбэка».** Нужен экран `Profile → Реферальная программа → История начислений` с фильтром по cashback.

3. ⚠️ **Нет лидерборда top-рефереров.** Платинум-юзеры (50+ оплативших, 45% кешбэк) — это партнёры, и им нужен публичный статус. Лидерборд (топ-50 по месяцу с обнулением) может **поднять активность топ-1% на 30–50%**.

4. ⚠️ **Нет share-материалов внутри бота.** Сейчас юзер сам копирует ссылку. Нужна кнопка «Поделиться → готовый креатив для VK/TG/WhatsApp» с шаблоном текста + изображением.

### 3.2 Три новых growth loop

#### Loop 3.2.1 — Content Loop («Скриншот-бонус»)

**Идея:** пользователь делится в любой соц-сети скриншотом «работает X через Atlas» с указанным `#atlassecure` → присылает скрин в бот → получает 50 ₽ на баланс.

**Механика:**
- callback `share_screenshot` в боте → state `awaiting_screenshot`.
- Юзер шлёт фото → модерация (manual для MVP, ML-классификатор позднее).
- Approve → `+50 ₽` на баланс с лимитом 1 раз/мес/юзер.

**K-factor оценка:**
- Conv share→see: 3–5%.
- Conv see→install bot: 8–15%.
- → ~0.4–0.7% контентного юзера приводит нового. На 100 share-events = 1–4 новых реги.
- **K = 0.05–0.07** (низкий, но дёшев: CAC = 50 ₽ за регу; типичный рынок 80–200 ₽).

**Cycle time:** 5–7 дней (от screenshot до новой реги).

**LTV/CAC:** при LTV=800₽ и CAC=50₽ → **LTV/CAC = 16x**. Очень здорово, но потолок объёма низкий (~5–10% базы участвует).

#### Loop 3.2.2 — Squad Loop (групповая покупка)

**Идея:** «Купите 3 подписки одним заказом — каждому скидка 20%». Telegram-friendly: создаётся «squad» через бот, инвайт-ссылка, оплата от любого члена squad-а покрывает всех.

**Механика:**
- New tariff family: `squad_3` (3 чел, скидка 20%), `squad_5` (5 чел, скидка 30%).
- DB-новинка: `squads (id, creator_id, members[], status)`.
- callback flow: `create_squad → invite_link → 3 join → checkout → split (Telegram Payments не позволяет split, но можно списать с creator-а с пометкой group-pay)`.

**K-factor:** каждая squad-покупка = 2–4 новых реги (creator + 2–4 invitees, из которых 30–50% — новые юзеры).
- → **K ≈ 0.5–0.8 на squad-покупке.** Но squad-покупки = ~5–10% всех платежей.
- Эффективный K на всю базу: 0.04–0.08.

**Cycle time:** 1–3 дня.

**Когда применять:** ноябрь-январь (новогодний сезон), март (8 марта), сентябрь (back-to-school).

#### Loop 3.2.3 — Reactivation Loop (Win-back)

**Идея:** лапсед-юзер (триал не сконвертился, или подписка expired) получает автоматическую серию офферов с ascending discount.

**Механика (расписание):**

| День после expiry | Оффер | Канал | Доп. |
|---|---|---|---|
| +1ч | "Что-то сломалось? Попробуйте ещё 24ч бесплатно" | TG bot | reactivate_24h flag |
| +24ч | "Скидка 50% на первый месяц" | TG bot | promo_REACTIVATE50 (auto-apply) |
| +7 дней | "Кейсы: что вы пропускаете без VPN" | TG bot | образовательный, без оффера |
| +14 дней | "Скидка 60% + 2 месяца за цену 1" | TG bot | агрессивный |
| +30 дней | "Возвращайтесь — 70% скидки на любой тариф" | TG bot | финальный, потом тишина |

**K-factor:** лапсед-сегмент составляет ~50% базы. Win-back-конверсия рынка = 5–15%. → **+5–10% retention в год, +12–18% LTV**.

**Cycle time:** 30 дней.

**LTV/CAC:** CAC = 0 (юзер уже в базе), Δ LTV = +100–150 ₽/реактивированный → **бесконечный ROI**.

### 3.3 Сравнительная экономика loops

| Loop | K-factor | Cycle | CAC | LTV/CAC | Объём (% базы) |
|---|---|---|---|---|---|
| Текущий referral | 0.15 | 7d | 30₽ | 27x | 25% |
| Content (screenshot) | 0.06 | 5d | 50₽ | 16x | 5–10% |
| Squad (group buy) | 0.06 | 2d | 100₽ (скидка) | 8x | 5–10% |
| Reactivation | n/a | 30d | 0 | ∞ | 50% |

**Рекомендация:** в первую очередь — Reactivation (0 риска, ∞ ROI). Потом Squad (хорошо для сезонов). Content — pilot на 500 юзеров.

---

## CHAPTER 4. PRICING И UNIT-ЭКОНОМИКА

### 4.1 Текущие цены — что не так

**Basic (config.py:90):**
- 30 дней: 149 ₽
- 90 дней: 399 ₽ (–11% vs 30×3)
- 180 дней: 749 ₽ (–16%)
- 365 дней: 1399 ₽ (–22%)

**Plus (config.py:97):**
- 30: 299 / 90: 699 (–22%) / 180: 1199 (–33%) / 365: 2299 (–36%)

**Замечания:**
1. **Скидка за длинную подписку у Basic слабее, чем у Plus.** У Plus юзер видит "20% экономия за квартал" — это сильный hook. У Basic 90д = –11% — недостаточно мотивации.
2. **Психологическое ценообразование** (charm pricing): `149 / 399 / 749 / 1399` — все ниже круглых, корректно.
3. **Top-up `[250, 750, 999]`** — классический **decoy effect** (Wansink/Ariely): 999 анкорит юзера подальше от 250, средний чек растёт.
4. **Star-цены `+70%` от рублёвых** — это маржа, оправданная Telegram-комиссией ~30%. Но психология Stars хуже: юзер видит "1290⭐" вместо "1399₽" и не понимает, дорого это или нет. → нужен **dual pricing**: «1399₽ или 1290⭐ (~1399₽)».

### 4.2 Anchor pricing на витрине

**Сейчас:** «Plus 365 — 2299 ₽».

**Предлагаемое:** ~~3399 ₽~~ **2299 ₽** *(экономия 1100 ₽)*.

Anchor — это «обычная цена», от которой считаем скидку. Эффект (Tversky-Kahneman): conversion rate возрастает на 10–20% при наличии видимого якоря.

**Реализация:**
- В `config.py` ввести `TARIFFS_ANCHOR` (исходная цена ДО «вечной скидки») и в i18n добавлять `<s>3399 ₽</s>`.
- Юридически безопасно: anchor реально присутствовал в первые 4 месяца ATCbot 2024.

**Ожидаемый uplift:** +6–10% к conversion на длинных тарифах (где экономия выглядит крупно).

### 4.3 Бизнес-тарифы — почему провал

Цены `biz_starter` 2900–42900₽/мес, `biz_ultimate` 64900–989900₽/мес — нет покупок (по audit). Причины:

1. **Никто не знает, что это.** Из бота нет лида на B2B.
2. **Нет демо.** Купить за 65к ₽/мес без демо — нереально.
3. **Нет sales-контакта.** Только @support — это L1, не B2B-sales.
4. **Нет сравнения.** Юзер не понимает, чем biz_team за 5500₽ лучше Plus за 299₽.

См. главу 10 — отдельный B2B GTM.

### 4.4 LTV модель по когортам

**Допущения:**
- Чистый churn paid: 25% M1, 18% M2, 15% M3, 10% M4–M6, 8% M7–M12
- ARPPU варьируется по тарифу: Basic 149, Plus 299, среднее по базе ≈ 220₽

**Когорта 1: Trial-acknowledged (триал → платная)**
- M1 retention: 65% (самые «горячие»)
- M3 retention: 40%
- M6 retention: 25%
- M12 retention: 12%
- LTV (12-мес): 220 × (1 + 0.65 + 0.65×0.85 + 0.40×0.95 + ... ) ≈ **930 ₽ нетто за год**

**Когорта 2: Paid-direct (без триала, сразу купил)**
- M1: 80% (более коммитед)
- M3: 55%
- M6: 38%
- M12: 22%
- LTV ≈ **1450 ₽**

**Когорта 3: Referred (пришёл по реф-ссылке)**
- M1: 70%
- M3: 45%
- M6: 28%
- M12: 14%
- LTV ≈ **1050 ₽**, но минус 10–25% реф-кешбэк → нетто **820–940 ₽**.

**Вывод:** paid-direct — самая ценная когорта (но малочисленная). Главная задача — **поднять trial→paid с 12 до 18%**, тогда trial-cohort LTV вырастет на 50% за счёт когорты-сдвига.

### 4.5 Ценовая эластичность — A/B тест

**Гипотеза:** 90-дневный тариф `Basic` (399₽) имеет умеренную эластичность.

**Тест:**
- Контроль: 399₽
- Variant A (–15%): 339₽
- Variant B (+10%): 439₽

**Метрика:** revenue_per_visitor (а не conversion!).

**Ожидание:**
- –15% → conversion +20%, revenue × 1.02 (близко к нейтрали).
- +10% → conversion –10%, revenue × 0.99.
- → можно безопасно поднимать цены, если не больше +10%.

**Trial конверсия — отдельный сегмент:** в trial-cohort эластичность выше. Тестировать раздельно.

**Sample size:** 10 000 уник-визитов на тариф. При текущем потоке = 4–6 недель.

---

## CHAPTER 5. NOTIFICATION & LIFECYCLE CADENCE

> Принципы, которые применяем во всём копирайте:
> - **Maeda's "Laws of Simplicity":** один экран = одна мысль = одна кнопка.
> - **Cialdini:** loss aversion («теряете доступ»), scarcity («осталось 3 часа»), social proof («100 000 пользователей»).
> - **Fogg behavior model:** B = MAT (Motivation × Ability × Trigger). Уведомление = Trigger; CTA = Ability; loss/gain = Motivation.
> - Длина: ≤240 символов; ≤2 эмодзи; 1 CTA.

### 5.1 Расписание lifecycle-нотификаций

| # | Trigger | Time-since | Канал | i18n key (предлагаемый) | RU | EN | Поведение | Метрика |
|---|---|---|---|---|---|---|---|---|
| 1 | Registration | T+0 | TG | `lifecycle.welcome` | «Привет! Atlas Secure — твой VPN-доступ. 3 дня бесплатно, без карты. Жми кнопку.» | "Hi! Atlas Secure — your VPN. 3 days free, no card." | Активация триала | trial_started rate |
| 2 | No trial activation | T+5 мин | TG | `lifecycle.no_trial_5m` | «Не получилось активировать? Триал занимает 10 секунд — нажми кнопку.» | "Trial not started? Takes 10 seconds — tap below." | activation | activation +1 пп |
| 3 | Idle 24h post-reg | T+24h | TG | `lifecycle.educate_24h` | «Знал, что Atlas защищает не только сайты, но и Wi-Fi в кофейне? Триал 3 дня — пробуй сейчас.» | "Did you know Atlas protects you on public Wi-Fi too? Try free 3 days." | activation | activation +0.5 пп |
| 4 | Trial -24h | trial_24h | TG | `trial.reminder_24h` (есть) | «Завтра пробный период закончится. Подключи подписку — настройки сохранятся.» | "Trial ends tomorrow. Subscribe — your setup stays." | conversion | trial→paid +1 пп |
| 5 | Trial -6h | trial_71h | TG | `trial.notification_71h` (есть) | «Через 6 часов VPN отключится. Подписка от 149₽ — продлите без усилий.» | "VPN turns off in 6h. Plans from 149 ₽." | conversion | trial→paid +0.5 пп |
| 6 | Trial -3h | trial_3h | TG | `trial.reminder_3h` (есть, 15% disc) | «3 часа до отключения. Скидка 15% — успейте.» | "3h left. 15% off — last chance." | conversion | trial→paid +1 пп |
| 7 | **Trial -1h (NEW)** | trial_1h | TG | `trial.reminder_1h` ⭐ | «Час до конца. Скидка 15% действует.» | "1h to go. 15% off still valid." | conversion | trial→paid +1 пп |
| 8 | Trial expiry +1h | trial_post_1h | TG | `trial.expired_50off` | «Триал закончился. На 24 часа — скидка 50%. Возвращайся.» | "Trial ended. 50% off for 24h. Come back." | reactivation | reactivation 8% |
| 9 | Trial expiry +24h | trial_post_24h | TG | `trial.expired_cases` | «Что вы упустили вчера: YouTube, Notion, Spotify, Steam-скидки. Подпишись.» | "What you missed: YouTube, Notion, Spotify, Steam deals." | reactivation | + |
| 10 | Trial expiry +7d | trial_post_7d | TG | `trial.expired_lastchance` | «Последний шанс: скидка 50% сгорает через 24 часа.» | "Last chance: 50% off expires in 24h." | reactivation | reactivation 5% |
| 11 | Trial expiry +30d | trial_post_30d | TG | `trial.winback_30d` | «Месяц без защиты. Возьми 60% скидки на любой тариф.» | "A month unprotected. 60% off any plan." | winback | winback 3% |
| 12 | Subscription M1 | sub_m1 | TG | `milestone.m1` | «Месяц с Atlas! 🎯 Привёл друга = 10% кешбэк. Жми кнопку.» | "1 month with Atlas! Invite a friend = 10% cashback." | referral | referrals +0.5 пп |
| 13 | Subscription M3 | sub_m3 | TG | `milestone.m3` | «Quarter MVP! Бонус +50₽ на баланс. Используй для продления.» | "3 months MVP! +50 ₽ bonus to balance." | retention | retention M4 +2 пп |
| 14 | Subscription M6 | sub_m6 | TG | `milestone.m6` | «Полгода с нами. Открыт Gold tier — 25% кешбэк за каждого друга.» | "6 months. Gold tier unlocked — 25% cashback." | LTV | LTV +5% |
| 15 | Renewal -72h | renew_t72 | TG | `renewal.t72_reminder` | «Через 3 дня автопродление 149₽. Баланс: {balance}₽.» | "Auto-renewal in 3 days: 149 ₽. Balance: {balance} ₽." | balance check | – |
| 16 | Renewal -24h | renew_t24 | TG | `renewal.t24_reminder` | «Завтра спишется 149₽. Хватает: ✅/❌. Пополни если нужно.» | "Tomorrow: 149 ₽ charge. Sufficient: ✅/❌." | top-up | top-up +5% |
| 17 | Renewal -3h | renew_t3 | TG | `renewal.t3_reminder` | «Через 3 часа автопродление. Если не хочешь — отключи в профиле.» | "Auto-renewal in 3h. Disable in profile if needed." | – | churn –1 пп |
| 18 | Renewal failed (insufficient) ⭐ | renew_fail | TG | `renewal.failed_topup` | «Автопродление не прошло — не хватило {missing}₽. Пополни в один клик.» | "Auto-renewal failed — short of {missing}₽. Top up in one tap." | top-up | recovery 25–35% |
| 19 | Sub expired +1h | exp_1h | TG | `expired.now` | «Подписка истекла. Восстанови за 30 секунд — настройки сохранены.» | "Subscription expired. Restore in 30s — setup intact." | reactivation | 12% |
| 20 | Sub expired +24h | exp_24h | TG | `expired.24h_30off` | «День без VPN. Скидка 30% — возвращайся.» | "1 day without VPN. 30% off — come back." | reactivation | 8% |
| 21 | Sub expired +7d | exp_7d | TG | `expired.7d_50off` | «Неделя без защиты. 50% на месяц — финальное предложение.» | "Week unprotected. 50% off — final offer." | reactivation | 5% |
| 22 | Sub expired +30d | exp_30d | TG | `expired.30d_winback` | «Возвращайся: 60% на любой тариф. Через 7 дней предложение исчезнет.» | "Come back: 60% off any plan. Expires in 7 days." | winback | 3% |
| 23 | Referral first activation ⭐ | ref_activated | TG | `referral.first_activation` | «🚀 {name} активировал триал по твоей ссылке. Заплатит = +кешбэк тебе.» | "🚀 {name} started trial via your link. Pays = cashback to you." | engagement | referrer activation +15% |
| 24 | Cashback earned | ref_cashback | TG | `referral.cashback_credited` | «+{amount}₽ на баланс. Спасибо за {name}!» | "+{amount}₽ to balance. Thanks for {name}!" | engagement | – |
| 25 | Abandoned cart +1h | cart_1h | TG | `cart.abandoned` | «Не закончил оплату? Промо CART15 ещё активен — скидка 15%.» | "Didn't finish? Promo CART15 — 15% off." | recovery | abandoned recov 8–12% |

### 5.2 Anti-fatigue rules

- **Max 1 промо** в неделю.
- **Max 2 educational** в неделю.
- **Max 3 нотификации в сутки.**
- **Дублирование триал-уведомлений и lifecycle-нотификаций запрещено.** Если юзер на триале — lifecycle 1/2/3 не шлём.
- **DND timezone**: не шлём 23:00–08:00 локального времени (нужно поле `users.timezone` или эвристика по гео-флагу).

### 5.3 Метрики per-нотификации

Все нотификации логируем (см. главу 8) с полями:
- `notification_id`, `type`, `user_id`, `sent_at`, `channel`, `lang`
- `clicked_at`, `dismissed_at`, `converted_to` (тариф если купил в течение 24ч после клика)

---

## CHAPTER 6. SEGMENTATION & TARGETING

### 6.1 Сегменты для broadcast

| # | Сегмент | SQL-критерий | RFM | Размер ожид. | Response rate | Месседж | Частота |
|---|---|---|---|---|---|---|---|
| S1 | Trial-active | `users.trial_used_at > now-72h, no paid sub` | – | 3–8% базы | – | – (lifecycle) | Только lifecycle |
| S2 | Trial-expired-7d | `trial_expires_at BETWEEN now-7d AND now, no paid` | R=4, F=0, M=0 | 8–12% | 5–10% | -50% promo | 1×/7д |
| S3 | Paid-active-renewing | `subs.status='active' AND auto_renew=TRUE` | R=5, F=2+, M=1+ | 30–40% | 30%+ | educational, milestones | 1×/14д |
| S4 | Paid-active-cancelled-autorenew | `subs.status='active' AND auto_renew=FALSE` | R=5, F=1, M=1 | 5–10% | 20% | «продли вручную, скидка» | 1×/7д перед expiry |
| S5 | Paid-expired-7d | `subs.status='expired' AND expires_at > now-7d` | R=3, F=1, M=1 | 5–10% | 8–12% | exp_7d_50off | 1×/7д |
| S6 | Paid-expired-30d | `expires_at BETWEEN now-30d AND now-7d, no active sub` | R=2, F=1, M=1 | 10–20% | 3–5% | winback | 1×/30д |
| S7 | Top-referrers (top 10%) | `paid_referrals_count > 10` | – | 1–3% | 50%+ | партнёрский флоу | по событию |
| S8 | High-balance idle | `balance > 100000 копеек, no purchase 30d` | – | 1–2% | 25–40% | «у тебя 1000₽ — потрати на год» | 1×/30д |
| S9 | Business-tier-active | `subscription_type IN BIZ_TARIFFS` | – | <1% | 60%+ | aftercare, upsell | 1×/30д |

### 6.2 Anti-fatigue в broadcast

Дополнительно к лимитам §5.2:
- **Между двумя broadcast** одного пользователя — минимум 7 дней.
- Для S3 (renewing) — never broadcast в день auto-renewal ±1д.
- Для S7 (top-refs) — индивидуальные сообщения, не bulk.

### 6.3 KPI каждого сегмента

| Сегмент | KPI | Цель Q3 |
|---|---|---|
| S2 | trial-expired→paid (7d) | 7→12% |
| S5 | expired→reactivated (7d) | 8→14% |
| S6 | expired-30d→reactivated (30d) | 3→6% |
| S7 | top-ref активность (msg/нед) | 0.5→2.0 |
| S8 | high-balance→spend (30d) | 25→45% |

---

## CHAPTER 7. PROMO & DISCOUNT ENGINE — ARCHITECTURE

### 7.1 Текущее состояние

- **Manual promo:** `admin.create_promocode` (i18n:122–134) — админ создаёт код, юзер вводит руками.
- **VIP discount:** 30% хардкод в `auto_renewal.py:219` (`amount_rubles = round(base_price * 0.70, 2)`).
- **Personal discount:** `database.get_user_discount(telegram_id)` — конкретный % per-user.
- **Loyalty cashback:** 10/25/45% (`app/constants/loyalty.py`).
- **Stacking-rules сейчас:** в `database.calculate_final_price()` применяется один из (promo, vip, personal) — наибольший. Loyalty-cashback всегда сверху (это не скидка, а возврат на баланс).

### 7.2 Auto-apply promo по deeplink

**Спека:**

1. Парсинг payload `/start <payload>`:
   - `ref_<6chars>` → existing
   - `promo_<UPPERCASE_CODE>` → **NEW**
   - `ref_<code>__promo_<CODE>` → совмещённый
   - `<utm-style>_<src>_<camp>` → tracking (см. §8.4)

2. При `promo_<CODE>`:
   - Валидация кода: ≤32 символов, A-Z0-9.
   - `database.validate_promo_code(code, telegram_id)` — проверка существования, лимита, активности.
   - При успехе: `state.update_data(applied_promo_code=CODE)` + сообщение `🎟 Промокод {CODE} применён автоматически. Скидка {N}% активна до окончания сессии.`
   - TTL: вместо 5 минут — **до закрытия следующей покупки или 30 минут idle**.

**Поля в БД:**
- В `pending_purchases` уже есть `promo_code` (см. `subscription_service.create_subscription_purchase`). Достаточно.
- Возможно: добавить `promo_applied_via TEXT` ('manual', 'deeplink', 'auto-banner') для аналитики.

**Callback_data:** не меняется, FSM обрабатывает auto-apply.

### 7.3 Time-bombed offers

**Спека:**

1. В админке: `create_promocode` + checkbox `time_bombed=TRUE`, поле `expires_at TIMESTAMP`.
2. В UI на тарифах: динамический countdown `«Скидка действует ещё 47:23:11»`.
3. Реализация: **rerender-button-text каждые N сек** через `bot.edit_message_reply_markup` — но это рейт-лимит-проблема. Альтернатива: countdown в момент открытия экрана (статика), без авто-рендера.
4. Вычисление: `time_left = expires_at - now()` форматируется HH:MM:SS.

**Поля в БД:**
```sql
ALTER TABLE promocodes ADD COLUMN time_bombed BOOLEAN DEFAULT FALSE;
ALTER TABLE promocodes ADD COLUMN expires_at TIMESTAMP;
```

**Эффект:** scarcity (Cialdini) → +15–25% conversion на промо-кампаниях.

### 7.4 Stacking rules (UX-defended)

**Правила:**

1. ✅ Loyalty cashback **+** любая скидка → стэкается (cashback применяется к финальной цене).
2. ❌ Promo **+** VIP → НЕ стэкается, выбирается max.
3. ❌ Promo **+** Personal discount → НЕ стэкается, max.
4. ✅ VIP **+** Personal — невозможно (одно из двух per-user).
5. ✅ Auto-renewal discount (например, 5% за auto_renew=TRUE) — отдельный bucket, стэкается с promo.

**UI:** на экране оплаты показываем breakdown:
```
Базовая цена:    149 ₽
Промо CART15:    −22 ₽
─────────────────────
К оплате:        127 ₽
+ кешбэк (10%):  12.7 ₽ → на баланс после оплаты
```

### 7.5 Limited-edition tiers

**Идея:** «Black Friday Plus 6 мес — только 100 продаж по 599₽ (вместо 1199₽)».

**Спека:**

1. Новая таблица `limited_offers`:
```sql
CREATE TABLE limited_offers (
    id SERIAL PRIMARY KEY,
    name TEXT,
    tariff TEXT,
    period_days INT,
    price_kopecks INT,
    max_sales INT,
    sold_count INT DEFAULT 0,
    expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
```

2. На экране тарифов — отдельный блок с countdown + «осталось {max-sold} мест».

3. Логика покупки: при checkout проверяется `sold_count < max_sales` атомарно.

**Эффект:** раз в квартал такая акция = +15–25% месячной выручки за 3-7 дней.

---

## CHAPTER 8. ATTRIBUTION & ANALYTICS

### 8.1 Что измеряем (Definitive list)

**Acquisition:**
- `daily_signups`, разбивка по `source` (utm/ref).
- CAC by channel (где есть платный).

**Activation:**
- `trial_start_rate = trial_started / signups`
- Median time `signup → trial_started`.

**Retention (cohort):**
- D1 / D7 / D30 / M3 / M6 active rate.

**Revenue:**
- DAU paid, ARPPU, ARPU.
- Conversion `trial → paid`.
- Average order value (AOV).

**Referral:**
- K-factor (виральность): `new_referred_signups / total_signups`.
- Cycle time (от рефа до первой реги от него).
- Top-10% контрибуция (% выручки от топ-10% реферов).

**Churn:**
- Voluntary (отключил auto_renew) vs involuntary (insufficient balance).
- Reactivation rate (60d window).

**NPS-proxy:**
- После 2-й оплаты — мини-опрос (1–5 звёзд) → сохранять в `feedback`.
- Игры (Bowling/Dice/Bomber/Farm) как proxy: средняя длительность сессии.

### 8.2 Events to instrument now

```
trial_started            (user_id, ts, source, lang)
trial_converted          (user_id, ts, days_since_trial, tariff, period, amount)
payment_succeeded_first  (user_id, ts, tariff, period, provider, amount, promo)
payment_succeeded_repeat (user_id, ts, tariff, period, provider, amount, n_payments_total)
subscription_expired     (user_id, ts, tariff, total_paid)
referral_registered      (referrer_id, referred_id, ts, source)
referral_activated       (referrer_id, referred_id, ts, type)
referral_reward_credited (referrer_id, buyer_id, ts, amount, percent, tier)
winback_clicked          (user_id, ts, segment, message_id)
notification_sent        (user_id, ts, type, channel)
notification_clicked     (user_id, ts, notification_id, button)
notification_dismissed   (user_id, ts, notification_id)
balance_topup            (user_id, ts, amount, method)
balance_purchase         (user_id, ts, amount, tariff)
auto_renewal_attempted   (user_id, ts, success, balance_before, amount, tariff)
auto_renewal_failed      (user_id, ts, reason='insufficient_balance', missing)
```

### 8.3 Структура таблицы events

```sql
CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    event_name TEXT NOT NULL,           -- из списка выше
    user_id BIGINT,                     -- nullable для системных
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    properties JSONB NOT NULL DEFAULT '{}',
    session_id UUID,                    -- для группировки внутри сессии
    source TEXT,                        -- 'bot', 'webhook', 'worker'
    correlation_id UUID                 -- для tracing
);

CREATE INDEX idx_events_user_occurred ON events (user_id, occurred_at DESC);
CREATE INDEX idx_events_name_occurred ON events (event_name, occurred_at DESC);
CREATE INDEX idx_events_properties_gin ON events USING GIN (properties);
-- Партиционирование по месяцу при объёме >10M строк
```

**Retention policy:** 13 месяцев (для year-over-year), потом агрегация в `events_monthly`.

### 8.4 UTM-параметры в реф-ссылке

**Формат:**
```
t.me/atlassecure_bot?start=ref_<code>__src_<source>__c_<campaign>
```

Примеры:
- `ref_a1b2c3__src_tg__c_BlackFri` → партнёрский TG-канал, кампания BlackFri.
- `ref_a1b2c3` → классический реф (без атрибуции).
- `promo_FRIDAY30__src_external` → промо без рефа.

**Декодер:**

```python
def parse_start_payload(payload: str) -> dict:
    """
    Returns: {ref_code, promo_code, source, campaign}
    """
    parts = payload.split("__")
    out = {"ref_code": None, "promo_code": None, "source": None, "campaign": None}
    for p in parts:
        if p.startswith("ref_"):
            out["ref_code"] = p[4:]
        elif p.startswith("promo_"):
            out["promo_code"] = p[6:]
        elif p.startswith("src_"):
            out["source"] = p[4:]
        elif p.startswith("c_"):
            out["campaign"] = p[2:]
    return out
```

Сохраняем в `users.acquisition_source`, `users.acquisition_campaign` (новые колонки).

**Telegram limit:** payload ≤64 символа — нужно внимательно к сокращениям.

---

## CHAPTER 9. GAMIFICATION DEEP-DIVE

### 9.1 Текущие игры — диагностика

В коде упомянуты Bowling/Dice/Bomber/Farm. По характеристике:

- **Bowling/Dice** — Telegram dice/bowl emoji-rolls с детерминированным результатом → simple slot-machine с bonus в баланс. **Работает как DAU-driver**, но только для уже-payed-юзеров. Trial и lapsed её не видят.
- **Bomber/Farm** — более сложные mini-games, скорее всего инвестиция времени для получения cashback. **Низкая retention внутри игры** (<7 дней).

**Что не работает (предположения):**

1. Игры **не привязаны к воронке**: выигрыш не направляет на покупку.
2. Нет **streak-bonus** за ежедневный заход.
3. Нет **leaderboard** → нет соц. давления.
4. Нет **сезонности** → одинаковые игры ноябрь/январь/июнь.

### 9.2 Octalysis (Yu-kai Chou) — какие 8 core drives задействованы

| # | Core Drive | Сейчас | Целевое |
|---|---|---|---|
| 1 | Epic meaning & calling | ❌ | «Защищаем интернет от блокировок — миссия» |
| 2 | Development & accomplishment | ⚠️ Частично (loyalty tiers) | Achievement-система с бэйджами |
| 3 | Empowerment of creativity & feedback | ❌ | – (не критично для VPN) |
| 4 | Ownership & possession | ⚠️ Балансы есть | Mystery-box, accumulating points |
| 5 | Social influence & relatedness | ⚠️ Реф-система | Лидерборд, squad |
| 6 | Scarcity & impatience | ❌ | Time-bombed promos, limited offers |
| 7 | Unpredictability & curiosity | ⚠️ (Игры дают элемент) | Mystery-box после N покупок |
| 8 | Loss & avoidance | ⚠️ (Notif про expiration) | Streak-loss («ты потеряешь streak бонус») |

### 9.3 Предложения

#### 9.3.1 Daily streak bonus

- Каждый день, когда юзер открывает бот → +5₽ на баланс при streak 1, +10₽ при streak 7, +25₽ при streak 30.
- Streak ломается при пропуске дня. Опционально: «freeze» 1 раз/мес.
- Внедряется через `users.streak_count`, `users.last_seen_date`.
- Эффект: +15–25% DAU/MAU ratio.

#### 9.3.2 Сезонные ивенты

| Месяц | Ивент | Механика |
|---|---|---|
| Январь | Новый год | "Подари VPN другу со скидкой 50%" + анимация |
| Март | 8 марта | "Подари маме/подруге безопасность" + female persona-icons |
| Май | День рождения сервиса | "1 год с нами — забери бесплатные 2 недели" |
| Июнь–Июль | Лето в роуминге | "Едешь в отпуск — бесплатные 3 дня доп" |
| Сентябрь | Back-to-school | "Скидка 30% студентам по @stud_id check" |
| Ноябрь | Black Friday | Limited offer (см. §7.5) |
| Декабрь | Адвент-календарь | 24 дня = 24 micro-rewards |

#### 9.3.3 Squad-leaderboard для рефереров

- Top-50 за месяц, обнуление 1-го числа.
- Награды: 1-е место — Plus 365 бесплатно (2299₽), 2-3 — Plus 90, 4-10 — Basic 30.
- Отдельный i18n блок `leaderboard.*` (нет сейчас).

#### 9.3.4 Achievement-система

| Бэйдж | Условие | Награда |
|---|---|---|
| 🥈 First Trial | Активировал триал | +0₽ (статус) |
| 🥇 First Payment | Купил подписку | +30₽ |
| 🛡️ Loyal Wolf | 6 мес подряд | +100₽ |
| 🚀 Inviter | 5 рефералов | +50₽ |
| 👑 Platinum | 50+ платящих рефов | Перманентная скидка 15% |
| 🎯 Annual | Купил 365д | Эксклюзивный sticker pack |

Реализация: `user_achievements (user_id, badge_key, earned_at)`.

#### 9.3.5 Mystery-box

После каждой 3-й покупки → callback `open_mystery_box` → **анимация 5 сек** → выпадает один из:
- 5% chance — 50% promo на следующую покупку
- 15% chance — 100₽ на баланс
- 30% chance — стикер
- 50% chance — мотивационная фраза + 10₽

Эффект: dopamine loop, +5–8% repeat purchase в 30-day window.

---

## CHAPTER 10. BUSINESS-TIER GO-TO-MARKET

### 10.1 Текущее состояние

- **6 SKU:** `biz_starter` (2900–42900₽) ... `biz_ultimate` (64900–989900₽).
- Цена: см. `config.py:103-145`.
- Спецификации: `BIZ_TIER_SPECS` (cpu/ram/traffic/users) + страны NL/RU/UK/FR/US.
- Cashback / promo: те же правила, что для b2c.
- **Ноль продаж** (по audit). Причины — гл. 1 п.1.5.

### 10.2 Предлагаемая GTM-стратегия

#### Шаг 1 — Lead form (Sprint 9)

Отдельный flow `/business`:

1. Кнопка в главном меню «🏢 Для бизнеса».
2. → screen `business.intro` с кейсами (3 примера: «Стоматология X выросла productivity на 12%», «Агентство Y защищает 25 удалёнщиков», «ИП Z работает в Notion из РФ»).
3. → форма (FSM `BusinessLeadForm`):
   - Имя (Telegram username pre-fill)
   - Сколько сотрудников (1-5 / 6-15 / 16-50 / 50+)
   - Что блокируется (free-text + чек-лист топ-10 сервисов)
   - Контакт (TG / email)
4. → submit → запись в `business_leads` + alert админу + auto-reply: «Спасибо! @sales свяжется в течение 4 часов».

```sql
CREATE TABLE business_leads (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT,
    company_size TEXT,
    use_case TEXT,
    contact TEXT,
    status TEXT DEFAULT 'new',  -- new | contacted | demo_scheduled | won | lost
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### Шаг 2 — Demo-flow

- **Free demo:** активируется на 7 дней biz_starter без оплаты после звонка/чата с sales.
- Лид-magnet: «Посчитаем экономию: сколько часов теряют ваши сотрудники?» — простой Excel-калькулятор в gist.

#### Шаг 3 — Manual closing

- Sales-cycle: 7–21 день для biz_starter–biz_team, 30–60 для biz_business+.
- Sales: одного человека хватает на 30–50 лидов/мес.
- Бот не закрывает сделку — отправляет инвойс через интеграцию с YooKassa B2B (счёт на ИП/ООО).

### 10.3 Pricing-обоснование

Текущие цены: `biz_starter` 30д = 2900₽. Это ~20× Basic. Расчёт:

- 2 vCPU + 8 GB RAM + 20 TB трафик у Hetzner NL = ~25 EUR/мес = 2500₽.
- Margin 16% — слишком тонко. **Рекомендация: 3490₽** (margin ~30%).

`biz_team` 4 vCPU = ~50 EUR = 5000₽. Текущая цена 5500₽ → margin 10% **слишком тонко**, конкурентам не страшно. **Рекомендация: 7900₽** (margin 36%).

**Таблица рекомендаций:**

| Tariff | Текущая 30д | Себестоимость | Рекомендуемая 30д | Margin |
|---|---|---|---|---|
| biz_starter | 2900 | ~2500 | **3490** | 28% |
| biz_team | 5500 | ~5000 | **7900** | 36% |
| biz_business | 10900 | ~9500 | **14900** | 36% |
| biz_pro | 21500 | ~18000 | **27900** | 35% |
| biz_enterprise | 42900 | ~35000 | **54900** | 36% |
| biz_ultimate | 64900 | ~52000 | **84900** | 38% |

**Discounts:**
- 6 мес = -10% от 30д × 6
- 12 мес = -20%
- 24 мес = -30%

### 10.4 Sales-cycle для B2B Telegram-VPN в СНГ

| Этап | Длительность | Конверсия |
|---|---|---|
| Lead → Discovery call | 2-5 дней | 60-70% |
| Discovery → Demo | 1-3 дня | 40-50% |
| Demo → Proposal | 1-7 дней | 50-65% |
| Proposal → Won | 7-21 день | 25-40% |

**Net L→W:** 4-9% — нормально для нишевого B2B SaaS в СНГ.

### 10.5 KPI

- 30 квалифицированных лидов/мес (Q1 после запуска).
- Сделок: 3-5/мес.
- Average Contract Value: 9000-25000₽/мес.
- B2B доля выручки: 15-25% к концу года 1.

---

## CHAPTER 11. РОАДМАП НА 12 НЕДЕЛЬ

### Sprint 1-2 (Weeks 1-2): ФУНДАМЕНТ

| Инициатива | RICE | Эффект | Файлы |
|---|---|---|---|
| Event tracking infrastructure (table + helper) | R=10/I=10/C=10/E=2 → **500** | Visibility всего | `app/services/analytics.py` (new), `events` table |
| Auto-apply promo по deeplink | 114 | +5-10% campaign | `bot/start.py`, `parse_start_payload()` |
| Trial 1h reminder | 288 | +1.5-3 пп trial→paid | `trial_notifications.py:179`, new flag `trial_notif_1h_sent` |
| Renewal-failed top-up notification | 300 | +8-12% AR success | `auto_renewal.py:362` (там сейчас debug-log) |

**Owner:** 1 backend, 1 designer для копи.

### Sprint 3-4 (Weeks 3-4): ВОРОНКА

| Инициатива | RICE | Эффект |
|---|---|---|
| Anchor pricing on tariffs screen | 220 | +6-10% conversion |
| Time-bombed offers infra | 180 | +15-25% promo CR |
| Abandoned-cart recovery (+1h promo) | 200 | +8-12% recovery |
| Promo-session TTL 5→30 мин | 216 | +1.5-2.5 пп |

### Sprint 5-6 (Weeks 5-6): RETENTION

| Инициатива | RICE | Эффект |
|---|---|---|
| Auto-renewal recovery sequence (T-72/24/3, fail-recovery) | 250 | +5 пп M1 retention |
| M1/M3/M6 milestone notifications | 150 | +2-5 пп LTV |
| Daily streak bonus | 120 | +15-25% DAU/MAU |

### Sprint 7-8 (Weeks 7-8): РОСТ

| Инициатива | RICE | Эффект |
|---|---|---|
| Squad/group buy mechanics | 100 | k-factor +0.04 |
| Content loop (screenshot bonus) | 80 | k-factor +0.03 |
| Top-referrer leaderboard | 130 | top-10% activity +30% |
| Referral-first-activation notification | 250 | referrer engagement +15% |

### Sprint 9-10 (Weeks 9-10): B2B

| Инициатива | RICE | Эффект |
|---|---|---|
| Business lead form & flow | 400 | 30 квал. лидов/мес |
| Demo-flow (7-day free biz_starter) | 200 | 25-40% demo→close |
| B2B pricing alignment (см. 10.3) | 250 | margin 28→35% |
| Business marketing collateral | 100 | conv. в boilерплате |

### Sprint 11-12 (Weeks 11-12): ОПТИМИЗАЦИЯ

| Инициатива | RICE | Эффект |
|---|---|---|
| A/B test 90-day pricing (контроль / -15% / +10%) | 180 | calibration |
| A/B test trial reminder copywriting (3 variants) | 150 | +1-2 пп trial→paid |
| A/B test cadence (2 variants of notification frequency) | 120 | retention +1-2 пп |
| Cohort analysis dashboard | 200 | data-driven decisions |

### Сводный KPI roadmap

| Метрика | Baseline | Q1 (W12) | Q2 | Q4 |
|---|---|---|---|---|
| Trial activation rate | 55% | 62% | 68% | 73% |
| Trial→Paid (M1) | 12% | 16% | 20% | 22% |
| Paid M1 retention | 45% | 52% | 58% | 62% |
| Paid M3 retention | 25% | 30% | 35% | 38% |
| Auto-renewal success | ~70% | 80% | 85% | 88% |
| LTV (M6) | 800₽ | 950₽ | 1100₽ | 1250₽ |
| K-factor | 0.15 | 0.18 | 0.22 | 0.28 |
| Reactivation 30d | 3% | 5% | 7% | 9% |
| B2B revenue share | 0% | 3% | 8% | 18% |

---

## CHAPTER 12. КОПИРАЙТ-БИБЛИОТЕКА

> Все тексты ≤240 символов, 1 CTA, эмоционально точные. Перед deploy — A/B тест на 1000 юзеров на сегмент.

### 12.1 Trial и lifecycle

| i18n key | RU | EN |
|---|---|---|
| `trial.reminder_1h` ⭐NEW | ⏳ Час до конца триала. Скидка 15% активна — продли в один клик. | ⏳ 1 hour left on trial. 15% off — extend in one tap. |
| `trial.expired_50off` ⭐NEW | 🎁 Триал закончился. -50% на месяц — только 24 часа. Возьми, пока не сгорело. | 🎁 Trial ended. 50% off for 24 hours. Grab it before it's gone. |
| `trial.expired_cases` ⭐NEW | 📵 Без VPN: YouTube тормозит, Notion недоступен, Spotify платит за лицензию РФ. Подпишись от 149₽. | 📵 Without VPN: YouTube lags, Notion blocked, Spotify limited. Subscribe from 149₽. |
| `trial.expired_lastchance` | ⚠️ Последний шанс. -50% сгорает через 24 часа. Дальше — фулл-цена. | ⚠️ Last chance: 50% off expires in 24h. Then full price. |
| `trial.winback_30d` | 💎 Месяц без защиты. -60% на любой тариф — финальное предложение. | 💎 A month unprotected. 60% off any plan — final offer. |

### 12.2 Renewal

| i18n key | RU | EN |
|---|---|---|
| `renewal.t72_reminder` | 📅 Через 3 дня автопродление {amount}₽. Баланс: {balance}₽. Всё ок? | 📅 Auto-renewal in 3 days: {amount}₽. Balance: {balance}₽. All set? |
| `renewal.t24_reminder` | ⏰ Завтра автопродление {amount}₽. Не хватает {missing}₽? Пополни в 1 клик. | ⏰ Renewal tomorrow: {amount}₽. Missing {missing}₽? Top up in 1 tap. |
| `renewal.t3_reminder` | 🕒 Через 3 часа спишется {amount}₽. Отключить — в профиле. | 🕒 In 3h we'll charge {amount}₽. Disable in profile. |
| `renewal.failed_topup` ⭐NEW | ❗ Автопродление не прошло — нужно ещё {missing}₽. Пополни сейчас и не теряй доступ. | ❗ Renewal failed — short by {missing}₽. Top up now to keep access. |
| `renewal.success_balance` | ✅ Подписка продлена. Списано {amount}₽. Активна до {date}. | ✅ Renewed. {amount}₽ charged. Active until {date}. |

### 12.3 Subscription expired

| i18n key | RU | EN |
|---|---|---|
| `expired.now` | 🔓 Подписка истекла. Восстанови за 30 секунд — настройки сохранены. | 🔓 Subscription expired. Restore in 30s — your setup is saved. |
| `expired.24h_30off` | 📉 День без VPN. -30% на восстановление, активно 24 часа. | 📉 1 day without VPN. 30% off — valid 24 hours. |
| `expired.7d_50off` | 🚨 Неделя без защиты. -50% на месяц — последняя цена недели. | 🚨 Week unprotected. 50% off — last price of the week. |
| `expired.30d_winback` | 💔 Месяц без нас. -60% на любой тариф — 7 дней до сгорания. | 💔 Month without us. 60% off any plan — 7 days left. |

### 12.4 Milestones

| i18n key | RU | EN |
|---|---|---|
| `milestone.m1` | 🎯 Месяц с Atlas! Поделись с другом = 10% кешбэк навсегда. | 🎯 1 month with Atlas! Invite a friend = 10% lifetime cashback. |
| `milestone.m3` | ✨ Quarter MVP! +50₽ на баланс — спасибо за доверие. | ✨ 3-month MVP! +50₽ to balance — thanks for trusting us. |
| `milestone.m6` | 👑 Полгода. Открыт Gold tier — 25% кешбэк за каждого друга. | 👑 6 months. Gold tier unlocked — 25% cashback per friend. |
| `milestone.m12` | 💎 Год вместе. Эксклюзивный стикерпак + 100₽ на баланс. | 💎 1 year together. Exclusive sticker pack + 100₽ to balance. |

### 12.5 Referral

| i18n key | RU | EN |
|---|---|---|
| `referral.first_activation` ⭐NEW | 🚀 {name} активировал триал по твоей ссылке. Заплатит — получишь кешбэк. | 🚀 {name} started trial via your link. Pays = cashback to you. |
| `referral.cashback_credited` | 💰 +{amount}₽ на баланс — кешбэк за {name}. Всего платящих: {count}. | 💰 +{amount}₽ balance — cashback for {name}. Total paying: {count}. |
| `referral.tier_upgrade_gold` | 🥇 Gold Access открыт! Теперь кешбэк 25% — спасибо за {count} друзей. | 🥇 Gold Access unlocked! 25% cashback now — thanks for {count} friends. |
| `referral.tier_upgrade_platinum` | 👑 Platinum! 45% кешбэк навсегда. Связь через @support_b2b — ты партнёр. | 👑 Platinum! 45% cashback forever. Partner contact: @support_b2b. |
| `referral.share_template` | 🛡 Atlas Secure — VPN, который работает. 3 дня бесплатно: {ref_link} | 🛡 Atlas Secure — VPN that works. 3 days free: {ref_link} |

### 12.6 Cart & promo

| i18n key | RU | EN |
|---|---|---|
| `cart.abandoned` ⭐NEW | 🛒 Не закончил оплату? Промо CART15 ещё активен — скидка 15%. | 🛒 Didn't finish? Promo CART15 still active — 15% off. |
| `promo.auto_applied` | 🎟 Промо {code} применено. Скидка {percent}% активна. | 🎟 Promo {code} applied. {percent}% off active. |
| `promo.timebombed_active` | ⏰ Промо {code}: -{percent}%, осталось {time}. | ⏰ Promo {code}: -{percent}% off, {time} left. |
| `promo.limited_stock` | 🔥 Limited: {tariff} за {price}₽. Осталось {left}/{total}. | 🔥 Limited: {tariff} for {price}₽. {left}/{total} left. |

### 12.7 Business

| i18n key | RU | EN |
|---|---|---|
| `business.intro` ⭐NEW | 🏢 Atlas Business: выделенный VPN-сервер для команды. От 5 до 500 человек. Демо за 5 минут. | 🏢 Atlas Business: dedicated VPN server for your team. 5-500 users. Demo in 5 min. |
| `business.lead_thanks` | ✅ Заявка принята. Менеджер свяжется в течение 4 часов. | ✅ Request received. Manager will contact you within 4 hours. |
| `business.demo_active` | 🚀 Демо biz_starter активно 7 дней. Доступ на {users} человек. | 🚀 biz_starter demo active for 7 days. Access for {users} users. |

### 12.8 Welcome / education

| i18n key | RU | EN |
|---|---|---|
| `lifecycle.welcome` | 👋 Atlas Secure — VPN за 10 секунд. 3 дня бесплатно, без карты. | 👋 Atlas Secure — VPN in 10 seconds. 3 days free, no card. |
| `lifecycle.no_trial_5m` | ⚡ Триал занимает 10 секунд. Жми кнопку и получи рабочий ключ. | ⚡ Trial takes 10 seconds. Tap and get a working key. |
| `lifecycle.educate_24h` | 💡 Atlas защищает не только сайты, но и Wi-Fi в кофейне. Включи защиту бесплатно. | 💡 Atlas protects you on public Wi-Fi too. Enable protection free. |

---

## ПРИЛОЖЕНИЯ

### A. Принципы при копирайте (cheat-sheet)

1. **Loss > Gain:** «потеряешь доступ» сильнее, чем «получишь доступ» (Kahneman).
2. **Конкретное число > общее:** «100 000 пользователей» > «много пользователей».
3. **Время > деньги:** «10 секунд» > «бесплатно» в hook.
4. **Социальное доказательство > описательное:** «друзья выбирают» > «лучший выбор».
5. **One CTA per message.** Никогда два.
6. **Эмоция > характеристика.** «не выпадает в стрессовый момент» > «99.9% uptime».

### B. Чек-лист релиза любой фичи из роадмапа

- [ ] Event-track инструментирован (главу 8.2)
- [ ] i18n RU+EN (минимум; AR/KZ — по приоритету)
- [ ] Anti-fatigue правила соблюдены (§5.2)
- [ ] A/B-тест запущен (если применимо)
- [ ] Admin-alert на исключения (см. `app/services/admin_alerts.py`)
- [ ] Документ обновлён в `/docs/marketing/`

### C. Глоссарий

- **AARRR** — Acquisition, Activation, Retention, Revenue, Referral.
- **CAC** — Customer Acquisition Cost.
- **LTV** — Lifetime Value.
- **K-factor** — коэффициент вирусности (новых юзеров на 1 существующего).
- **JTBD** — Jobs To Be Done (Christensen).
- **RFM** — Recency, Frequency, Monetary segmentation.
- **RICE** — Reach, Impact, Confidence, Effort приоритизация.

---

**Конец документа.**
*Версионируется в git. Изменения через PR с тегом `[playbook]`. Owner: growth lead.*
