# Pro-rebrand — что заменили и как откатить одной командой

Замена sensitive-терминов в user-facing текстах:
«обход / белые списки / блокировки / ограничения / LTE» → «Pro / полный доступ».

Внутренние идентификаторы (i18n-ключи `bypass_*`, callback_data `bypass_setup`,
колонки БД `remnawave_uuid`, поля FSM `combo_bypass_gb`, коммент/docstring
в коде) — **НЕ ТРОГАЛИ**. Только тексты, которые видит пользователь.

---

## Как откатить ВСЁ одной командой

Все правки лежат в **5 коммитах**. Порядок ревертов — обратный (от новых к старым),
чтобы не было конфликтов:

```bash
git checkout claude/pricing-strategy-update-X0FhT   # ветка с правками
git revert 8f46ac3 --no-edit    # welcome_bypass + dashboard-frontend лейблы
git revert 20e17e5 --no-edit    # экран ⚡️ Подключитесь + этот файл docs/
git revert e9a9cbf --no-edit    # user-facing cleanup (Профиль/Комбо/промо/traffic)
git revert cb4c88a --no-edit    # подчистка тарифных описаний в 5 локалях + ru
git revert 59f4026 --no-edit    # основная замена в ru.py + все локали
git push
```

Одной строкой (если удобнее):
```bash
git revert --no-edit 8f46ac3 20e17e5 e9a9cbf cb4c88a 59f4026 && git push
```

После этого все тексты вернутся ровно к состоянию **до** «Pro-rebrand».
Никакие структурные изменения кода (миграции, схемы, endpoint'ы) в этих
коммитах не участвуют — только текстовые правки в файлах i18n и user-facing
handlers, так что откат безопасный.

Если случится конфликт (маловероятно, но если параллельно ветку правили) —
`git status` покажет конкретные файлы, разрешай простой `git checkout --ours` /
`--theirs` либо ручным Edit'ом.

---

## Полный mapping «было → стало»

| Сейчас (после rebrand) | Было (revert-таргет) |
|---|---|
| Pro-трафик | обход / трафик обхода |
| полный доступ | обход блокировок / обход белых списков |
| Pro-сервера | LTE-сервера / сервера обхода |
| Pro-доступ | обход / обход блокировок |
| Pro-ключ | Ключ обхода / White List |
| Happ Pro | Happ Обход |
| Incy Pro | Incy Обход |
| «полная скорость» / «на полной» / «прежняя скорость» | «блокировки» / «ограничения» / «без ограничений» |
| «mobile & Wi-Fi» / «мобильных и Wi-Fi» | «LTE / 5G / Wi-Fi» |
| «Активные Combo … доп. устройств» (осталось словом «обхода» в описании сегмента admin dashboard) | — не тронул, admin-only |

---

## Коммиты, которые НЕ входят в rebrand и не откатываются

Все остальные фичи, которые я делал в сессии — cashback fix, промо/stat-ссылки,
segments, Incy Android/macOS, FAQ, редизайн дашборда, marketing-links, fix
чёрного экрана и т.д. — **никак не зависят от rebrand** и продолжают работать
после revert трёх коммитов выше.

---

## Точный список файлов затронутых rebrand'ом

**i18n (все локали):**
- `app/i18n/ru.py` — ~70 строк
- `app/i18n/en.py` — ~15 строк (ручной перевод)
- `app/i18n/de.py` — bulk-replace, ручные правки тарифа Plus
- `app/i18n/ar.py` — bulk-replace, ручные правки тарифа Plus
- `app/i18n/kk.py` — bulk-replace, ручные правки тарифа Plus
- `app/i18n/tj.py` — bulk-replace, ручные правки тарифа Plus
- `app/i18n/uz.py` — bulk-replace, ручные правки тарифа Plus

**Код (user-facing):**
- `app/handlers/common/screens.py` — экран выбора тарифа, экран Профиля (traffic-блок), bypass-only header
- `app/handlers/common/keyboards.py` — кнопки «Только Pro-доступ», «Купить Pro-трафик»
- `app/handlers/callbacks/navigation.py` — кнопки «Happ Pro»/«Incy Pro»
- `app/handlers/callbacks/payments_callbacks.py` — «🚧 Pro-доступ» после активации
- `app/handlers/payments/callbacks.py` — «✅ Pro-трафик уже включён» после Combo-покупки
- `app/handlers/user/start.py` — тексты промо-link награды (bypass_discount, bypass_gb)
- `app/handlers/admin/broadcast.py` — maintenance-рассылка + gift-меню (уходит юзеру)
- `app/handlers/admin/bonus.py` — сообщение админ-подарка юзеру («+N ГБ Pro-трафика»)
- `app/handlers/traffic.py` — описание balance-транзакции
- `app/services/migration_broadcast.py` — сервисная рассылка «Pro-сервера»
- `app/api/dashboard/routes/broadcasts.py` — кнопка «🌐 Включить Pro» в рассылке

**Итого:** 14 файлов, ~90 строк текста.

---

## Что осознанно НЕ трогали (admin-only, docstrings, internal)

Эти места оставлены с исходной терминологией — либо admin их видит и ему
удобнее «обход» как техтермин, либо это документация для разработчика:

- `app/handlers/admin/access.py` — «Скидка на ГБ обхода», «Трафик обхода» в
  админской карточке юзера
- `app/handlers/admin/finance.py` — все экраны админской скидки на GB
- `app/handlers/admin/bypass_gift.py` — админский preview gift-link
- `app/handlers/admin/keyboards.py` — кнопки админского меню (Скидка на ГБ
  обхода, Трафик обхода)
- `app/handlers/admin/broadcast.py:261,266,1778` — admin preview рассылки
  + label в dict пресетов кнопок
- `app/handlers/callbacks/navigation.py:1075,1210,1278,1292,1323,1637` —
  docstrings
- `app/handlers/callbacks/bypass_setup.py:1,3,8,54,102` — docstrings +
  комментарии
- `app/services/incy_crypto.py:179` — коммент «обходит модерацию и регулярки»
- `app/api/dashboard/routes/broadcasts.py:183` — описание сегмента для
  admin dashboard («GB-паки обхода / доп. устройств»)
- `app/utils/button_defaults.py` — комменты про «Обход» в TEXT_EMOJI_MAP
  (это же комменты, лейблы уже правильные)

Все i18n-ключи с `bypass_` префиксом (`bypass_gift.*`, `bypass_setup.*`,
`main.welcome_bypass`, `traffic.bypass_activated`, `trial.bypass_activated`
и т.д.) — **только имена ключей**, а не тексты. Значения этих ключей
уже показывают «Pro» юзеру.

---

## Проверка «CLEAN» после rebrand

Скрипт-audit по 18 шаблонам (все языковые варианты sensitive-терминов):

```python
python3 << 'PY'
import re
patterns = [
    r'обход(?!имо)', r'бел[ыхе] списк', r'блокировк', r'ограничени',
    r'\bLTE\b', r'заблокирован',
    r'\bBypass\b', r'\bbypass\b', r'\bWhitelist\b', r'\bwhitelist\b',
    r'white[‑\- ]list', r'Umgehung', r'айналып өту', r'ақ тізім',
    r'блоктау', r'бастаҳо', r'حجب', r'chetlab',
]
for loc in ['ru','en','de','ar','kk','tj','uz']:
    with open(f'app/i18n/{loc}.py', encoding='utf-8') as f:
        src = f.read()
    hits = [p for p in patterns if re.search(p, src)]
    print(f'{loc}: {"CLEAN ✓" if not hits else "PROBLEMS: " + str(hits)}')
PY
```

Все 7 файлов = CLEAN.
