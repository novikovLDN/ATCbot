# ТЗ: флоу подключения (экраны) — как устроено у нас

**Для агента на другом боте.** Описывает пошаговый флоу «пользователь нажал
Подключиться → установил ключ», все экраны, кнопки, callback'и и техническую
обвязку (deep-link'и, шифрование, ветка агрегатора). Цель — чтобы другой бот
повторил ту же логику, особенно **ветку агрегатора** (единый ключ вместо
двух «VPN/Обход»).

Файлы-источники у нас:
- `app/handlers/callbacks/navigation.py` — все `setup_*` хендлеры
- `app/handlers/common/keyboards.py` — `get_connect_keyboard`
- `app/api/deeplink_redirect.py` — эндпоинт `/open/{client}`
- `app/services/{happ_crypto,incy_crypto}.py` — шифрование ключей
- `app/services/sub_aggregator.py` — гейт + `ensure_pair`

---

## 0. Ключевая идея (что поменялось с агрегатором)

**Раньше** у юзера было ДВА ключа: `sub_url` (VPN, main-сервера) и
`bypass_url` (Обход, gb-сервера). На экране установки — 4 кнопки
(Happ/Incy × VPN/Обход) и в ручной установке 4 ключа.

**С агрегатором** — ОДИН ключ (`agg_url`, склеенная подписка). На экране —
2 кнопки (Happ/Incy), в ручной — 2 зашифрованных ключа. Никакого деления
«VPN vs Обход»: оба типа серверов внутри одной ссылки.

Переключение — через гейт `sub_aggregator.is_enabled_for(tg)`
(admin-only на бете → флип на всех). Оба флоу сосуществуют в коде: если
гейт пускает → aggregator-ветка, иначе → legacy dual-key.

---

## 1. Точка входа: «Подключиться»

Кнопка `get_connect_keyboard()` (главное меню, экран профиля, после оплаты)
→ `callback_data="connect_instruction"`.

---

## 2. Экран 1 — Выбор устройства (`connect_instruction`)

**Что показываем:** фото + текст `setup.select_device` + сетка кнопок:
```
[📱 iPhone / iPad]   [🤖 Android]
[🍎 Mac]             [🪟 Windows]
[← Назад → menu_main]
```
Каждая device-кнопка → `callback_data="setup_step1:{platform}"`
(`ios | android | macos | windows`).

**Побочный эффект (важно):** для юзера, у которого УЖЕ есть remnawave_uuid,
fire-and-forget:
- `extend_remnawave_for_bypass(tg)` — продлить expireAt bypass-энтити до
  far-future (иначе панель пометит expired при израсходованном трафике),
- `ensure_squad(tg)` — убедиться, что энтити в нужном squad.

Для новичков без uuid — ничего не форсим, ленивый provision произойдёт
на шаге 2 при чтении ключа.

---

## 3. Экран 2 — Установка приложения (`setup_step1:{platform}`)

**Что показываем:** фото (кроме Windows) + текст `setup.install_app` +
кнопки-ссылки на сторы, зависят от платформы:

| Платформа | Кнопки (порядок) |
|---|---|
| iOS / macOS | Incy (первой) · Happ RU · Happ Global |
| Android | Happ · Incy |
| Windows | Happ `.exe` (github releases) |

Стор-ссылки — единая точка `_DOWNLOAD_LINKS` / `_IOS_HAPP_LINKS`.
Happ iOS: RU-стор и **глобальный** (не-РФ) отдельными кнопками.

Низ: `[Далее → setup_step2:{platform}]` `[← Назад → connect_instruction]`.

---

## 4. Экран 3 — Ключ / установка (`setup_step2:{platform}`)  ← ГЛАВНОЕ

Хендлер сначала резолвит ключи и проверяет гейт агрегатора:

```python
sub_url    = await get_user_primary_subscription_url(tg)   # legacy VPN
bypass_url = await get_user_bypass_url(tg)                  # legacy Обход
agg_url = None
if sub_aggregator.is_enabled_for(tg):
    agg_url = await sub_aggregator.ensure_pair(tg)          # единый ключ
```

`base_url` для deep-link'ов = `config.PUBLIC_BASE_URL` или host из
`WEBHOOK_URL`. Deep-link кнопки НЕ используют схемы `happ://` напрямую
(Telegram их запрещает в url-кнопках) — идут через `/open/{client}?url=...`.

### 4a. Ветка АГРЕГАТОРА (`agg_url` есть) — ЦЕЛЕВАЯ

Текст `setup.key_install_title_agg`. Кнопки:
```
[📥 Добавить ключ в Happ]  → {base_url}/open/happ?url={quote(agg_url)}
[💚 Добавить ключ в Incy]  → {base_url}/open/incy?url={quote(agg_url)}   (iOS/Android/macOS)
[Готово → setup_done]
[⚙️ Настроить вручную → setup_manual:{platform}]
[Нужна помощь → t.me/atlas_suppbot]
[← Назад → setup_step1:{platform}]
```
Incy-кнопку НЕ показываем на Windows (нет Incy-клиента под Windows).
`quote(agg_url, safe='')` — URL-энкод обязателен.

### 4b. Ветка LEGACY (dual-key, если гейт не пускает)

Текст `setup.key_install_title`. Две строки кнопок:
```
[Happ VPN]    [Incy VPN]      → /open/{client}?url={sub_url}
[Happ Обход]  [Incy Обход]    → /open/{client}?url={bypass_url}   (если bypass_url есть)
[Готово] [Установить вручную] [Помощь] [Назад]
```
Incy — только iOS/Android/macOS. Row «Обход» — только если `bypass_url` не пуст.

---

## 5. Эндпоинт `/open/{client}?url=...` (шифрование + редирект)

`GET /open/{client}` где client ∈ {happ, incy}. Что делает:
1. Запечатывает `url` в клиент-специфичный deep-link:
   - **Happ**: `happ_crypto.to_crypt_link(url)` → `happ://crypt4/<base64>`
     (pure-Python RSA-4096/PKCS#1v1.5, всегда работает; fallback
     `happ://add/<plain>`).
   - **Incy**: `await incy_crypto.to_incy_link(url)` → `incy://crypt1/<payload>`
     (AES-256-GCM через Node-сайдкар `@incy/link-encoder`; если недоступен —
     чистая error-страница, не 500).
2. Возвращает HTML-страницу, которая **авто-редиректит** браузер на
   deep-link (`window.location = deep_link`) → открывается приложение и
   импортирует подписку. Плюс fallback: сам deep-link показан моноширинным
   блоком с кнопкой «Скопировать» (на случай, если iOS in-app браузер
   блокирует `happ://`).

**Почему через шифрование:** ключ в ссылке не светится открытым текстом,
клиент принимает его в своём формате. Это работает и для агрегатор-ссылки,
и для legacy-ссылок — эндпоинт про содержимое URL не знает.

---

## 6. Экран 4 — Ручная установка (`setup_manual:{platform}`)

Для тех, у кого авто-импорт не сработал. Снова проверяет гейт.

### 6a. Ветка АГРЕГАТОРА
Текст `setup.connect_{platform}` (инструкция «куда вставить») + **2
зашифрованных ключа** одной агрегатор-ссылки, каждый в сворачиваемой цитате:
```
🔑 Ключ Happ:
<blockquote expandable><code>happ://crypt4/…</code></blockquote>
💚 Ключ Incy:            (iOS/Android/macOS)
<blockquote expandable><code>incy://crypt1/…</code></blockquote>
```
Ключи строятся: `happ_crypto.format_for_user(agg_url)` и
`await incy_crypto.to_incy_link(agg_url)`.
Низ: `[Готово → setup_done]` `[← Назад → setup_step2:{platform}]`.

### 6b. Ветка LEGACY
То же, но 4 ключа (Happ/Incy × VPN/Обход) из `sub_url` + `bypass_url`.

---

## 7. Экран 5 — Готово (`setup_done`)

Финальный экран-подтверждение (успех + возврат в меню).

---

## 8. Что должен сделать агент на другом боте

Чтобы повторить наш **агрегатор-флоу** (главное):

1. **Гейт**: функция `is_enabled_for(tg)` (enabled + url задан + admin-only
   на бете). На старте — только админ.
2. **`ensure_pair(tg) -> agg_url|None`**: получить единый ключ агрегатора
   (у вас — live-резолв из subscriptions/bypass; вернуть публичный
   `https://<домен>/sub/<token>` или `/a/<token>`). None → откат на legacy.
3. **Экран ключа (setup_step2 аналог)**: при `agg_url` — 2 кнопки
   Happ/Incy на `/open/{client}?url={quote(agg_url)}`, БЕЗ деления VPN/Обход.
   Incy скрыть на Windows.
4. **Эндпоинт `/open/{client}`**: запечатать URL (Happ crypt4 / Incy crypt1)
   и вернуть авто-редирект HTML + copy-fallback. Это ключевой мостик:
   Telegram не пускает `happ://` в кнопки → идём через свой `/open`.
5. **Ручная установка**: при `agg_url` — 2 зашифрованных ключа (Happ+Incy)
   одной ссылки в сворачиваемых цитатах.
6. **Побочный эффект на входе**: для существующих юзеров — extend bypass
   expireAt + ensure squad (fire-and-forget), чтобы энтити не «протухла».

Legacy dual-key оставить как fallback (когда гейт не пускает) — плавный
переход.

---

## 9. Тексты (i18n-ключи) — для справки

| Ключ | Экран |
|---|---|
| `setup.select_device` | Экран 1 (выбор устройства) |
| `setup.install_app` | Экран 2 (установка приложения) |
| `setup.key_install_title_agg` | Экран 3 — агрегатор |
| `setup.key_install_title` | Экран 3 — legacy |
| `setup.btn_add_happ` / `setup.btn_add_incy` | кнопки «Добавить ключ в …» |
| `setup.btn_manual_setup` / `setup.btn_manual` | «Настроить вручную» |
| `setup.connect_{platform}` | Экран 4 (ручная, инструкция) |
| `setup.key_happ_label` / `setup.key_incy_label` | подписи ключей (агрегатор) |
| `setup.btn_done` / `setup.done_button` | «Готово» |

---

## 10. Схема переходов (кратко)

```
[Подключиться]  connect_instruction
      │
      ▼
Экран 1: Выбор устройства ──setup_step1:{plat}──▶ Экран 2: Установка приложения
                                                        │ (Далее)
                                                        ▼
                                          Экран 3: Ключ  setup_step2:{plat}
                                          ├─ agg: 2 кнопки Happ/Incy → /open/{client}?url=agg_url
                                          └─ legacy: 4 кнопки VPN/Обход
                                                        │
                              ┌─────────────────────────┼───────────────────────┐
                        (Настроить вручную)         (Готово)               (кнопка Happ/Incy)
                              ▼                          ▼                        ▼
                    Экран 4: Ручная            Экран 5: setup_done      /open/{client} → HTML
                    setup_manual:{plat}                                  авто-редирект happ://crypt4
                    ├─ agg: 2 ключа                                      или incy://crypt1 → импорт
                    └─ legacy: 4 ключа
```
