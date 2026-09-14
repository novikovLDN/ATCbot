# 10. Telegram: статическая корректность (лимиты и правила Bot API)

Обновлено: 2026-09-14. Ветка: `worktree-agent-a9f3cffa938ce131b` (от `refactor/audit-2026-09` @ `904e6cb7`).

Половина «тестируй на ошибки — это же телеграм бот» про **содержимое** сообщений: что Telegram отвергнет
400-й ошибкой ещё до доставки. Рантайм-ошибки (429/403, дубли апдейтов, конкурентность) — в соседнем отчёте.

Telegram отвергает **всё сообщение целиком**, а не только плохую часть. Одна кнопка с длинным `callback_data`
или один незакрытый тег — и экран не открывается. `safe_send_message` при этом глотает 400 и пишет в лог,
так что пользователь или админ просто ничего не получает.

## Итог

| # | Проверка | Результат | Нарушения |
|---|---|---|---|
| 1 | `callback_data` ≤ 64 байт | ✅ чисто | — |
| 2 | Deep links `/start` | ✅ чисто | 1 намеренный хардкод (STAGE) |
| 3 | Длина текстов / подписей | ❌ **1 нарушение, исправлено** | подпись главного меню с баннером инцидента |
| 4 | HTML parse_mode | ✅ чисто (i18n) | — |
| 5 | Плейсхолдеры `{…}` | ✅ чисто | — |
| 6 | Markdown / emoji | ❌ **1 нарушение, исправлено** | алерт «активация не удалась» |
| 7 | Клавиатуры | ✅ чисто | — |
| 8 | Команды | ✅ чисто | — |
| 9 | Инвойсы | ✅ чисто | — |

Новые тесты:
- `tests/test_telegram_static.py` — 18 тестов, постоянные CI-гарды на проверки 1–9;
- `tests/test_activation_alert_html.py` — 2 теста;
- `tests/handlers/test_incident_caption.py` — 8 тестов.

Валидатор Telegram-HTML — `app/utils/telegram_html.py`. Его использует и продакшн-код (баннер инцидента).

## Версии и Bot API

- Локально и в образе стоит `aiogram 3.30.0` (Bot API 10.2). В `requirements.txt` — `aiogram>=3.3.0,<4.0` без пина,
  Docker ставит последнюю 3.x. `InlineKeyboardButton.style` / `icon_custom_emoji_id` (Bot API 9.4) в 3.30
  есть как поля. На старой aiogram (< 3.2x) они прошли бы через `extra="allow"` модели, отправка не ломается.
  **Рекомендация:** поднять нижнюю границу до `aiogram>=3.25` (или пин), чтобы сборка не откатилась на
  версию без поддержки полей.
- `icon_custom_emoji_id` на кнопках и `<tg-emoji>` в текстах работают, только если у бота куплен username
  на Fragment **или** у владельца бота есть Telegram Premium (docstring aiogram, Bot API 9.4+). Без этого
  Telegram не шлёт ошибку, а показывает обычную кнопку или fallback-эмодзи внутри `<tg-emoji>`.
  **Побочный эффект:** `app/utils/button_defaults.py` при подстановке premium-иконки **удаляет** ведущий
  unicode-эмодзи из текста кнопки. Если premium-эмодзи недоступны (Premium владельца истёк), кнопки
  «Назад», «СБП», «Банковская карта» и т.п. останутся **без эмодзи вовсе**. Это косметика, отправка не ломается.
  Тест `test_premium_emoji_ids_are_numeric` проверяет формат id: только цифры.
- `style` принимает только `danger|success|primary`. Monkeypatch ставит только эти значения, явные вызовы — тоже.

## 1. `callback_data` ≤ 64 байт

Тест `test_callback_data_fits_64_bytes` делает AST-скан **всех** `callback_data=` в продакшн-коде: литералы
(байты UTF-8) и f-строки. Для f-строк считается худший случай: литералы + 20 символов на каждую подстановку
(int64 id: Telegram id, id из БД). Для 🔒 `apple_send_key:{buyer_id}:{region}:{nominal}` границы берутся
из `_APPLE_NOMINALS`.

- Самый длинный литерал — `admin:reset_password_confirm` (28 байт).
- Худший динамический — `apple_send_key:…` (47 байт), `setup_qr_app:incy:{kind}:{platform}` и
  `user:devices:cdel:{tier}:{idx}` (≤ 59 байт при 20 на подстановку, реально ≈ 35).
- `pay:wata:check:{purchase_id}`: `purchase_id = purchase_<16 hex>` → 40 байт
  (`test_purchase_id_in_callback_data_fits` фиксирует формат).
- Кнопки, где `callback_data` — переменная (`ar_data`, `renew_cb`, `back_cb`, `retry_cb`, `_invoice_back()`,
  `back_callback`), проверены вручную: все значения — короткие литералы (≤ 28 байт).

**Нарушений нет.**

## 2. Deep links

`test_deep_link_payloads_are_valid` генерирует payload'ы реальными генераторами и проверяет
`^[A-Za-z0-9_-]{1,64}$`:

| Payload | Генератор | Длина |
|---|---|---|
| `ref_<code>`, `refd_<code>` | `generate_referral_code` (6, A-Z2-7), `_random_referral_code` (8, A-Z0-9) | ≤ 13 |
| `ref_<telegram_id>` (fallback без БД) | — | ≤ 17 |
| `gift_<code>` | `generate_gift_code` (12, без O/0/I/1/L) | 17 |
| `bgift_<code>` | `generate_bypass_gift_code` (10) | 16 |
| `s-<slug>`, `p-<slug>` | `marketing_links._gen_slug` (6, a-z2-9) | 8 |

Имя бота: подарки и кэшбэк берут `bot.get_me().username`, дашборд и Platega — `config.BOT_USERNAME`.
`test_deep_links_use_the_real_bot_username` запрещает хардкод `t.me/<bot>?start=`. Единственное
разрешённое исключение — `app/handlers/user/start.py` `_show_stage_gate`: STAGE-экран намеренно ведёт
тестера в прод-бота.

**Нарушений нет.**

## 3. Длина текстов и подписей

- Все i18n-строки RU/EN после подстановки влезают в 4096 (`test_every_i18n_string_fits_a_text_message`).
  Самые длинные: `main.privacy_policy_text` (RU 1065 видимых символов), `referral.how_it_works_text` (838).
  Обе уходят **текстом**, не подписью: `safe_edit_text` на фото-сообщении удаляет фото и шлёт текст.
- Подписи к фото (`send_photo`/`answer_photo`): главное меню (`main.welcome*` ≤ 310), выбор устройства,
  капча, кэшбэк (все варианты `loyalty_pushes` при кэшбэке 55 555,55 ₽: ≤ 165 RU / 177 EN, валидный HTML),
  WATA-инвойс трафика. Всё с запасом, кроме случая ниже.
- Рассылки из дашборда идут через `send_with_long_caption_fallback`: при `caption is too long` фото и текст
  уходят двумя сообщениями.

### ❌ Нарушение 3.1 — подпись главного меню с баннером инцидента (исправлено)

`app/handlers/common/utils.py:697` `format_text_with_incident` приклеивал к подписи главного меню текст
инцидента из дашборда. Этот текст — HTML до 2000 символов (`IncidentSet.incident_text max_length=2000`,
textarea «Текст (HTML)»). Подпись к фото — максимум 1024. Главное меню шлётся `send_photo(caption=…)`
в 5 местах: `navigation.py:72`, `navigation.py:1084`, `language.py:110`, `language.py:149`, `connect.py:135`.

Сценарий: админ включает инцидент с текстом длиннее ~680 символов (или с `<` в тексте, например
«скорость < 1 Мбит»). Пока режим включён, у **всех** пользователей главное меню отвечает 400
(`caption is too long` / `can't parse entities`), экран не открывается.

Исправление (`08486d40`): текст инцидента подгоняется под остаток лимита подписи. Валидный HTML, который
влезает, остаётся как есть. Остальное показывается экранированным простым текстом, обрезанным с «…».
Тесты: `tests/handlers/test_incident_caption.py` (2000 символов текста, длинный HTML, невалидный HTML — RU/EN;
короткий валидный HTML не меняется).

**Рекомендация (не делал — UI дашборда):** в `dashboard/src/pages/Service.tsx` `maxLength={2000}` вводит
в заблуждение. На экран влезает ≈ 650 символов. Стоит показать счётчик или снизить лимит и валидировать HTML на API.

## 4. HTML parse_mode

`test_every_i18n_string_is_valid_telegram_html` рендерит **каждый** ключ RU и EN (585/587) с фиктивными
значениями и прогоняет через строгий валидатор (`app/utils/telegram_html.py`, семантика парсера
Telegram/tdlib). Валидатор проверяет:
- теги только `b strong i em u ins s strike del span tg-spoiler a tg-emoji code pre blockquote`;
- правильную вложенность и закрытие;
- `span` только с `class="tg-spoiler"`, `a` с `href`, `tg-emoji` с числовым `emoji-id`;
- `<` только как начало тега;
- неизвестные именованные сущности (`&nbsp;`).

Голый `&` и `>` Telegram принимает как текст — это не ошибка. `test_validator_rejects_what_telegram_rejects`
фиксирует поведение валидатора.

**Нарушений в i18n нет.** Замечание (🔒 магазин, только отчёт): `en.py` `shop.apple_title` содержит голый
`&` («… & Purchase»). Telegram его принимает, правка не нужна.

Экранирование пользовательских данных уже покрыто прошлыми волнами (`test_devices_label_escaped`,
`test_share_texts_html`, `test_admin_chat_html`, `test_global_discount_reason_escaped`). В этой волне
найден один неэкранированный путь — алерт активации, см. 6.1.

Косметика (не ошибка отправки): `admin.degraded_mode`, `admin.recovered`, `admin.pending_activations_*`
содержат Markdown-разметку (`**…**`, `` `…` ``), но шлются с `parse_mode=None` (`admin_notifications.py`).
Админ видит звёздочки и обратные кавычки буквально. Не трогал.

## 5. Плейсхолдеры

- `test_ru_and_en_use_the_same_placeholders`: RU и EN используют одинаковые `{…}`. Три осознанных исключения:
  EN показывает на одно значение больше — `buy.button_price{,_discount}` (`{gb}`), `trial.activated`
  (`{expires_date}`). Все вызовы их передают.
- `test_get_text_calls_supply_every_placeholder`: AST-скан **902** вызовов `get_text`/`i18n_get_text`/`_t`
  с литеральным ключом. Каждый вызов передаёт все плейсхолдеры обоих языков, иначе будет `KeyError` или
  пользователь увидит сырой `{name}`: `get_text` форматирует, только если переданы kwargs.

**Нарушений нет.** Ключи `setup.download_v2rayn`/`setup.download_v2raytun` есть только в EN, но нигде не
используются.

## 6. Markdown и emoji

### ❌ Нарушение 6.1 — алерт «активация не удалась» в legacy Markdown (исправлено)

`activation_worker.py:294` слал админу `parse_mode="Markdown"`. В тексте без экранирования шли тариф
(`combo_basic`, `combo_plus` — `_` открывает курсив) и текст ошибки панели (`_`, `*`, `[`, `` ` ``). Telegram
отвечал `can't parse entities`, `safe_send_message` глотал 400. **Админ не узнавал, что оплаченную
подписку не удалось активировать** — ровно в тот момент, когда нужна ручная активация.

Исправление (`d360e5b9`): `build_activation_failed_alert()` строит HTML, все значения экранированы, ошибка
обрезана до 1000 символов. Ключи `admin.activation_error_*` переведены на `<b>`/`<code>`.
Тесты: `test_no_markdown_parse_mode_in_production_code` (в продакшн-коде больше нет Markdown) и
`test_activation_failed_alert_is_valid_html_with_hostile_values`.

Premium emoji: id в `CE`, `TEXT_EMOJI_MAP`, `TEXT_EMOJI_PATTERNS` — только цифры. `<tg-emoji emoji-id>` в
i18n проверяет HTML-валидатор. `convert_tg_emoji` (формат Telegram Ads `![e](tg://emoji?id=N)`) даёт
валидный тег.

## 7. Клавиатуры

- `test_every_emitted_callback_data_has_a_handler`: каждый `callback_data` (литералы и f-строки с
  подстановкой типовых значений) сверяется с фильтрами **195** callback-хендлеров корневого роутера.
  Проверяются MagicFilter и lambda, есть отрицательный контроль. Потерянных кнопок нет.
- `test_inline_buttons_have_one_action_and_valid_url`: у каждой `InlineKeyboardButton` ровно одно действие
  (нет `callback_data` вместе с `url`), литеральные `url` — только `https://` или `tg://`, текст 1–64 символа.
- `web_app` один: гайд в `keyboards.py:409`. Бот работает только в личке (`PrivateChatOnly`), ограничение
  Telegram «web_app только в private» соблюдено.
- Кнопок на клавиатуре ≤ 100: длинные списки идут с пагинацией — подарки по 6 на страницу
  (`GIFTS_PER_PAGE`), 🔒 Steam по 12 (`STEAM_AMOUNTS_PER_PAGE`), плюс кнопки навигации.

**Нарушений нет.**

## 8. Команды

`test_menu_commands_are_valid_and_handled`: 12 команд из `set_my_commands` (`main.py:445`). Имя
`[a-z0-9_]{1,32}`, описание 1–256, у каждой есть `Command(...)`-хендлер (`/support` — `support.py:21`).
`/white`, `/main`, `/admin`, `/platega_sub_status` обрабатываются, но в меню не выведены — это намеренно.

Замечание: меню команд регистрируется только на русском (без `language_code="en"`). EN-пользователи видят
русские описания. Не ошибка API. Можно добавить второй вызов `set_my_commands(..., language_code="en")`.

**Нарушений нет.**

## 9. Инвойсы

`test_send_invoice_calls_follow_the_rules` и `test_invoice_texts_fit_limits` покрывают VPN, пополнение и
🔒 магазин (последний — только чтение):
- `title` ≤ 32: «Atlas Secure VPN» 16, «Пополнение баланса Atlas Secure» 31;
- `description` ≤ 255: ≤ 70 при худших значениях;
- `payload` ≤ 128 байт: `purchase:<id>` ≈ 34, `balance_topup_<tid>_<amount>_<ts>` ≈ 45;
- Stars: `currency="XTR"`, `provider_token=""`, одна цена.

`test_stars_amount_is_a_positive_integer`: `stars_for_purchase` всегда возвращает целое ≥ 1.

**Нарушений нет.** По 🔒 магазину (`telegram_premium`, `telegram_stars_purchase`, `steam`, `spotify`)
нарушений лимитов не найдено.

## Прогоны

- `pytest tests/` (герметичные): 3496 passed, 401 skipped, 86 xfailed.
- `ruff check .`: чисто. `python -c "import tests.conftest, main"`: OK.
- e2e на настоящем Postgres (`run_e2e.py`): 343 passed, 12 xfailed, 2 xpassed.

## Открытые пункты (только отчёт)

1. `requirements.txt`: `aiogram>=3.3.0` → поднять нижнюю границу до версии с полями Bot API 9.4 (≥ 3.25) или пин.
2. `button_defaults` удаляет unicode-эмодзи при подстановке premium-иконки: без Premium владельца или
   Fragment-username кнопки остаются без эмодзи.
3. Дашборд, инцидент: `maxLength=2000` при реальном месте ≈ 650. Нет валидации HTML на API
   (рантайм теперь защищён).
4. `admin_notifications`: Markdown-разметка в текстах с `parse_mode=None` (звёздочки видны буквально).
5. `set_my_commands` только RU.
