# Re-apply модерации: VPN/обход + фото (МОДЕРАЦИЯ ПЕРЕНЕСЕНА)

Статус: **модерация перенесена**, всё возвращено в обычное состояние
(обход/VPN в текстах восстановлены, экранные фото — обычные). Этот файл —
как **заново применить** модерацию, когда она возобновится.

Текущее (обычное) состояние получено ревертом хендлеров + восстановлением
pre-moderation i18n; НЕ откачены (остаются): новые legal-ссылки, фикс
/start-коллизии referral_code, экран «Правила» на /docs.

---

## A. ТЕКСТ (VPN→VPS, обход→Pro/Pro-режим, убрать «белые списки»)

Исходные копирайт-коммиты модерации (хронологически):

| # | Commit | Что делал | Файлы |
|---|--------|-----------|-------|
| 1 | `0c347a0` | витрина тарифов: обход→трафик | `app/i18n/ru.py`, `en.py` |
| 2 | `4ad1930` | **VPN→VPS**, обход→Pro, убрать whitelist/белые списки (ru+en) | `app/i18n/ru.py`, `en.py` |
| 3 | `3a87776` | уточнение (ru): Pro→**Pro-режим** | `app/i18n/ru.py` |
| 4 | `58a8e2d` | hardcoded-строки хендлеров: обход | `app/handlers/**` |
| 5 | `f38bacf` | кнопки: VPN→VPS | `app/handlers/{proxy,common/keyboards,common/screens,callbacks/navigation}.py` |

**Как применить заново:**
- Вариант А (git): `git cherry-pick 0c347a0 4ad1930 3a87776 58a8e2d f38bacf`.
  Если конфликт в `app/i18n/ru.py` (частый случай) — разрешить по таблице
  терминов ниже (в i18n значениях: VPN→VPS, обход-формы→Pro/Pro-режим, убрать
  «белые списки»), затем `git cherry-pick --continue`.
- Вариант Б (скрипт): повторить замены по таблицам терминов ниже (только
  ЗНАЧЕНИЯ i18n и hardcoded-строки хендлеров; ключи/callback_data/`необходимо`
  не трогать). Критерий приёмки: 0 вхождений VPN/обход/белые списки/whitelist/
  bypass в пользовательских значениях i18n.

### Карта терминов — RU
| было (обычное) | стало (модерация) |
|---|---|
| VPN | VPS |
| обход белых списков / «Обход» (фича) / обход блокировок | Pro-режим |
| трафик(а) обхода | Pro-трафик(а) |
| ГБ обхода | Pro-трафик / ГБ Pro-трафика |
| сервера/серверов обхода | Pro-серверы/-ов |
| ключ(и) обхода / «Обход ключ» | Pro-ключ(и) |
| «Добавить обход» → «Добавить Pro-ключ»; «Включить обход» → «Включить Pro-режим» | |
| «Купить ГБ обхода» (кнопка) | «Купить Pro-трафик» |
| «🚀 Happ/Incy обход» | «🚀 Happ/Incy Pro» |
| белые списки РФ / «(белые списки РФ)» | убрать / перефразировать |
| «фильтруются по белым спискам» | «которые обычно недоступны» |

### Карта терминов — EN
| было | стало |
|---|---|
| VPN / VPNs | VPS / VPS apps |
| whitelist bypass / Bypass (фича) | Pro |
| bypass traffic / servers / key(s) / gigabytes | Pro traffic / servers / key(s) / gigabytes |
| Add/Enable/Connect bypass | Add/Enable/Connect Pro |
| «Bypasses any blocks including whitelists» | «Access to any sites and services» |
| RU whitelists / whitelist(s) | убрать / перефразировать |
| «Copy White List» | «Copy Pro key» |

---

## B. ФОТО ЭКРАНОВ (prod). Постановка file_id.

file_id привязан к боту → значения только для prod-бота. STAGE не менять.

| Экран | Константа (файл) | ОБЫЧНОЕ (сейчас) | МОДЕРАЦИЯ (ставить) |
|---|---|---|---|
| Главный/приветствие `main.welcome*` | `MAIN_PHOTO_FILE_ID` prod (`app/handlers/callbacks/language.py`) | `AgACAgQAAxkBAAF_xmlqfao80Yz-rdiEyVKfdz5s49Qd7gACLhFrG3NR8VO3FeagisF3hQEAAwIAA3kAAz0E` | `AgACAgQAAxkBAAGILrJqmRrKIHhd_2TdSxWC4mSlBw34PAACdBBrGzE2yFAI7ugMrp-k_QEAAwIAA3kAAz0E` |
| «Выберите устройство» `setup.select_device` | `_DEVICE_SELECT_PHOTO["prod"]` (`app/handlers/callbacks/navigation.py`) | `AgACAgQAAxkBAAGILwVqmR0AATCd8V0czJQwFMVtbGWP97IAAncQaxsxNshQ3NkHkfgoXUwBAAMCAAN5AAM9BA` | `AgACAgQAAxkBAAGILxxqmR0_PvCYoxmnbSy0GkxfWtpEgwACeRBrGzE2yFDWIFQarSx5SwEAAwIAA3kAAz0E` |
| «❓ Помощь» `help.menu_title` | `SUPPORT_PHOTO_FILE_ID` (`app/handlers/common/screens.py`) | `AgACAgQAAxkBAAGIL6NqmSBPEGLHLGql0JtCj85HJAerwQACfBBrGzE2yFCoJX6favSQxQEAAwIAA3kAAz0E` | `AgACAgQAAxkBAAGIL6ZqmSBb2kWmNnZwEz4dec4wlhJ4NQACfRBrGzE2yFC1E_y6lGWUTAEAAwIAA3kAAz0E` |

Прежний prod-file_id «Выберите устройство» (ретайрнут, не использовать):
`AgACAgQAAxkBAAFU07NqGqUXEmVZ5SivuY0gwUhd7TBCeAACXw9rGxA30FCkvieRMzznwwEAAwIAA3kAAzsE`

---

## Что НЕ трогать (при обоих переключениях)

- Имена ключей/`callback_data`/код, слово «необходимо».
- Легитимные «Pro» (бизнес-тариф Pro, Claude Pro/Max, Happ/Incy Pro-метки).
- Уведомления `traffic.notify_*` (в проходе-уточнении Pro-режим не применялся).
- Английский в проходе-уточнении (3a87776) — только ru.
- STAGE-файлы фото (bot-specific file_id).
- Standalone hardcoded «VPN» вне кнопок (инвойс-заголовки «Atlas Secure VPN»,
  текст proxy-экрана и т.п.) — до модерации не доводили, отдельный вопрос.
