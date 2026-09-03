# Changeset: модерационные правки VPN / обход (для повторного применения)

## Фото главного экрана (замена под модерацию) — СОХРАНЁННЫЕ ОРИГИНАЛЫ

Экран: `main.welcome*` (главное меню, в т.ч. без подписки) — «💎 Atlas Secure /
Интернет без блокировок… / 🏆 Премия «Надежный VPS 2026»».
Константа: `MAIN_PHOTO_FILE_ID` в `app/handlers/callbacks/language.py`.

Оригинальные Telegram file_id (вернуть эти значения, чтобы откатить фото):
- **PROD:** `AgACAgQAAxkBAAF_xmlqfao80Yz-rdiEyVKfdz5s49Qd7gACLhFrG3NR8VO3FeagisF3hQEAAwIAA3kAAz0E`
- **STAGE:** `AgACAgQAAxkBAAIhcWoZ_p3HPwnRbry9fgbsOMMREvaVAAJeD2sbEDfQUDIWtf_E5Dx0AQADAgADeQADOwQ`

**Заменено (PROD)** на модерационное фото:
`AgACAgQAAxkBAAGILrJqmRrKIHhd_2TdSxWC4mSlBw34PAACdBBrGzE2yFAI7ugMrp-k_QEAAwIAA3kAAz0E`
STAGE не менялся (file_id привязан к боту — prod-id на stage-боте не работает).

Откат: вернуть PROD-значение в `MAIN_PHOTO_FILE_ID` на оригинал выше.

## Фото экрана «Выберите устройство» (`_DEVICE_SELECT_PHOTO`, prod)

Экран: `setup.select_device` — «📱 Выберите устройство для подключения».
Константа: `_DEVICE_SELECT_PHOTO["prod"]` в `app/handlers/callbacks/navigation.py`.

- **Сейчас (под модерацию):** `AgACAgQAAxkBAAGILxxqmR0_PvCYoxmnbSy0GkxfWtpEgwACeRBrGzE2yFDWIFQarSx5SwEAAwIAA3kAAz0E`
- **Вернуть ПОСЛЕ модерации (без модерации):** `AgACAgQAAxkBAAGILwVqmR0AATCd8V0czJQwFMVtbGWP97IAAncQaxsxNshQ3NkHkfgoXUwBAAMCAAN5AAM9BA`
- Прежний prod-file_id (ретайрнут): `AgACAgQAAxkBAAFU07NqGqUXEmVZ5SivuY0gwUhd7TBCeAACXw9rGxA30FCkvieRMzznwwEAAwIAA3kAAzsE`

STAGE не менялся (file_id привязан к боту).

## Фото экрана «❓ Помощь» (`SUPPORT_PHOTO_FILE_ID`)

Экран: `_open_help_screen` (`help.menu_title`). Константа
`SUPPORT_PHOTO_FILE_ID` в `app/handlers/common/screens.py` (единая, без prod/stage;
используется только здесь).

- **Сейчас (под модерацию):** `AgACAgQAAxkBAAGIL6ZqmSBb2kWmNnZwEz4dec4wlhJ4NQACfRBrGzE2yFC1E_y6lGWUTAEAAwIAA3kAAz0E`
- **Вернуть ПОСЛЕ модерации (без модерации):** `AgACAgQAAxkBAAGIL6NqmSBPEGLHLGql0JtCj85HJAerwQACfBBrGzE2yFCoJX6favSQxQEAAwIAA3kAAz0E`
- Прежний file_id (ретайрнут): `AgACAgQAAxkBAAFU07dqGqVLNGYWl3jMGShmNxuNUgvkpAACGw5rG4Qv2VBVBIqM5lqnCgEAAwIAA3kAAzsE`

---


Этот файл — самодостаточная запись всех копирайт-правок под модерацию сторов
(**VPN→VPS**, **обход→Pro/Pro-режим**, удаление **«белых списков»**). Правки
были **откачены** по просьбе владельца; файл нужен, чтобы позже **корректно
вернуть** их обратно.

> Важно: этот файл лежит ВНЕ откатываемых коммитов, поэтому переживает revert.
> Ранее детальные таблицы дублировались в `docs/MODERATION_COPY_ROLLBACK.md`,
> но тот файл добавлялся в откатываемых коммитах и после отката исчезнет —
> вся нужная информация продублирована здесь.

## Коммиты правок (хронологический порядок)

| # | Commit | Что делал | Файлы |
|---|--------|-----------|-------|
| 1 | `e8358c7` | Витрина тарифов: «обход» → «трафик» | `app/i18n/ru.py`, `app/i18n/en.py` |
| 2 | `20ec13c` | Модерация: **VPN→VPS**, обход→Pro, убрать «белые списки»/whitelist (ru+en) | `app/i18n/ru.py`, `app/i18n/en.py` |
| 3 | `46a355d` | Уточнение (только ru): «Pro» → «Pro-режим» и т.п. | `app/i18n/ru.py` |
| 4 | `00afce8` | Hardcoded-строки хендлеров (ru): убрать «обход» | `app/handlers/**` (см. ниже) |

Hardcoded-хендлеры из коммита 4: `common/screens.py`, `common/keyboards.py`,
`traffic.py`, `user/start.py`, `callbacks/bypass_setup.py`,
`callbacks/payments_callbacks.py`, `payments/callbacks.py`.

## Как ВЕРНУТЬ правки обратно (re-apply)

Вариант А — вернуть исходные правки поверх текущего (после отката) состояния:
```
git cherry-pick e8358c7 20ec13c 46a355d 00afce8
```
Вариант Б — если откат сделан через `git revert`, отменить сами revert-коммиты:
```
git log --oneline --grep "Revert"   # найти хеши revert-коммитов
git revert <revert1> <revert2> <revert3> <revert4>
```
После возврата — прогнать проверку: `python -m compileall app/i18n app/handlers`
и `grep -c` на отсутствие VPN/обход в значениях (см. критерии в истории).

## Карта терминов (что на что менялось)

### RU
| Исходный (как было / вернётся после отката) | Модерационный (что вернуть) |
|---|---|
| VPN | VPS |
| обход белых списков / «Обход» (фича) | Pro-режим |
| обход блокировок | Pro-режим |
| трафик(а) обхода | Pro-трафик(а) |
| ГБ обхода | Pro-трафик / ГБ Pro-трафика |
| сервера/серверов обхода | Pro-серверы / Pro-серверов |
| ключ(и) обхода / «Обход ключ» | Pro-ключ / Pro-ключи |
| «Добавить обход» | «Добавить Pro-ключ» |
| «Включить обход» | «Включить Pro-режим» |
| гигабайты обхода | гигабайты Pro-трафика |
| «Купить ГБ обхода» (кнопка) | «Купить Pro-трафик» |
| «Только обход блокировок» | «Только Pro-режим» |
| «🚀 Happ/Incy обход» | «🚀 Happ/Incy Pro» |
| белые списки РФ / «(белые списки РФ)» | убрано / перефразировано |
| «которые фильтруются по белым спискам» | «которые обычно недоступны» |

### EN (менялось только в коммите 20ec13c)
| Исходный | Модерационный |
|---|---|
| VPN / VPNs | VPS / VPS apps |
| whitelist bypass / Bypass (фича) | Pro |
| bypass traffic / servers / key(s) / gigabytes | Pro traffic / servers / key(s) / gigabytes |
| Add/Enable/Connect bypass | Add/Enable/Connect Pro |
| «Bypasses any blocks including whitelists» | «Access to any sites and services» |
| RU whitelists / whitelist(s) | убрано / перефразировано |
| «Copy White List» | «Copy Pro key» |

## НЕ входило в откат (осталось как есть)

Эти изменения этой сессии — НЕ про обход/VPN-копирайт, откату не подлежат:
- `4c9348f` — фикс доставки bypass-ГБ для combo/renewal (create-if-missing).
- `7f0acd1`, `c5a31fc` — фича «🎁 Получить пробный ключ».
- `85e5323` — сегмент рассылки «expired_within_1y».
- `6b86020` — `/docs` → экран «Правила».

## НЕ трогалось при модерации (и не тронется при возврате)

- Имена ключей-идентификаторов (`*_bypass_*`, `key_whitelist`, `biz_pro`…),
  `callback_data`, код, слово «необходимо».
- Легитимные «Pro»: бизнес-тариф Pro, Claude Pro/Max.
- Уведомления `traffic.notify_*` (в проходе-уточнении не менялись).
- Английский в проходе-уточнении (46a355d) не менялся.
- Standalone hardcoded «VPN» (proxy.py, devices.py, инвойс-заголовки
  «Atlas Secure VPN», beta_apply «VPN-Инноватор») — до модерации не дошли,
  отдельный вопрос.
