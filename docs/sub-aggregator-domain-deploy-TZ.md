# ТЗ: домен и брендированная HTML-страница подписки

**Область:** развёртывание домена `subscription.palantir.uk` для sub-aggregator + брендированная HTML sub-page.

**НЕ ВХОДИТ:** код агрегатора (уже в `sub-aggregator/`), код бота (уже сделан отдельно).

---

## 1. DNS

- **Домен:** `palantirdns.uk` (у клиента уже есть).
- **Sub-домен:** `subscription.palantirdns.uk`
- **A-запись:** IP «белого» сервера (RF-1). Backup RF-2 подключается через `scripts/dns-failover.sh` — см. § 6.
- **TTL:** `60` (для быстрого failover).
- **Проксирование Cloudflare:** ⚠️ OFF (`proxied: false`) — Cloudflare WAF режет base64-подписки как «suspicious content», плюс сломает TLS passthrough со сторонних клиентов (Happ), которые ходят напрямую по TCP.

**Проверка после установки:**
```bash
dig +short subscription.palantirdns.uk
# → должен вернуть IP RF-1

curl -I https://subscription.palantirdns.uk/healthz
# → HTTP/2 200
```

---

## 2. TLS

- **Провайдер:** Let's Encrypt (webroot challenge).
- **Хранение:** `/etc/letsencrypt/live/subscription.palantirdns.uk/`.
- **Автообновление:** cron `certbot renew --quiet` раз в день; hook перезагружает nginx.
- **HSTS:** ON, `max-age=31536000; includeSubDomains`.

**Команда выпуска (одноразово):**
```bash
sudo certbot certonly --webroot -w /var/www/acme \
  -d subscription.palantirdns.uk \
  --email admin@palantirdns.uk --agree-tos --non-interactive
```

**Проверка:**
```bash
openssl s_client -connect subscription.palantirdns.uk:443 -servername subscription.palantirdns.uk < /dev/null 2>/dev/null | openssl x509 -noout -dates
# → notBefore/notAfter корректные
```

---

## 3. nginx (origin — бот-сервер)

Файл шаблона: `sub-aggregator/nginx/origin.conf`. Подстановки:

| Плейсхолдер | Значение |
|---|---|
| `SUB_DOMAIN` | `subscription.palantirdns.uk` |
| `ACME_DIR`   | `/var/www/acme` |
| `TLS_CERT`   | `/etc/letsencrypt/live/subscription.palantirdns.uk/fullchain.pem` |
| `TLS_KEY`    | `/etc/letsencrypt/live/subscription.palantirdns.uk/privkey.pem` |
| `WG_CIDR`    | `10.8.0.0/24` (или актуальная WG-подсеть) |

**Дополнительно к базовому конфигу:**
```nginx
# HSTS + security headers для HTML sub-page (в location /):
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options nosniff always;
add_header Referrer-Policy no-referrer always;
```

**Rate-limit:** уже настроен (`30r/m burst=15`). Для HTML-страницы это ок — юзер открывает раз/два, потом клиент качает подписку.

---

## 4. Front-серверы (RF-1, RF-2)

Файл: `sub-aggregator/nginx/front-stream.conf`. TCP-passthrough — фронты не терминируют TLS, не держат сертификатов.

**Требования к RF-1/RF-2:**
- nginx собран с `--with-stream` (default на Debian/Ubuntu 22+).
- WireGuard-туннель до origin установлен и живой.
- iptables/ufw: `443/tcp` open с интернета.
- Пропущенные ENV: `ORIGIN_WG_IP`, `ORIGIN_PORT=443`.

**Проверка:**
```bash
# С клиентской машины, минуя DNS:
curl -k --resolve subscription.palantirdns.uk:443:<RF1_IP> https://subscription.palantirdns.uk/healthz
curl -k --resolve subscription.palantirdns.uk:443:<RF2_IP> https://subscription.palantirdns.uk/healthz
# Оба должны вернуть {"ok":true}
```

---

## 5. HTML Sub-Page (брендинг)

Агрегатор сам рендерит страницу — код уже реализован (`sub-aggregator/src/html.js`). Настраивается через ENV.

### Дизайн (Atlas / Palantir style)

- **Фон:** тёплый off-white `#FBFBF9`, dark theme через `prefers-color-scheme` — deep slate `#0F1720`.
- **Аксцент:** `#2563EB` (blue-600) для кнопок «Скачать / Открыть в клиенте».
- **VIP-акцент:** `#7C3AED` (violet-600) — не используем на этой странице, только на дашборде.
- **Шрифт:** system-ui / Inter fallback (без Google Fonts — они блокированы в РФ).
- **Иконки клиентов:** inline SVG (без CDN — быстро и работает в РФ без прокси).

### Блоки страницы

1. **Header:** логотип + название бренда + слоган («защищённое подключение»).
2. **QR-код** (SVG, генерируется на бэкенде, кодирует сам `sub://` URL).
3. **Кнопки установки по платформам:**
   - iOS: `[Happ]` `[V2rayTun]` `[Streisand]`
   - Android: `[Happ]` `[V2rayTun]` `[v2rayNG]`
   - macOS: `[Happ]` `[V2Box]` `[Streisand]`
   - Windows: `[Hiddify]` `[Nekoray]`
   - Linux: инструкция + deb-ссылка
4. **«Скопировать ссылку»** — плоская textarea + JS-fallback.
5. **«Обновить подписку»** — сброс кеша (POST /internal/invalidate только с админ-секретом; для обычных юзеров → просто reload с cache-buster).
6. **Инструкция:** 3 шага с картинками (или без — MVP без screenshots).
7. **Footer:** ссылка на бота + поддержка.

### ENV для брендинга (уже в `sub-aggregator/src/config.js`)

```
BRAND_NAME=Atlas Secure
BRAND_SLOGAN=Защищённое подключение
BRAND_PRIMARY_COLOR=#2563EB
BRAND_LOGO_SVG=<inline SVG-строка или URL>
SUPPORT_URL=https://t.me/AtlasSecureSupport
BOT_URL=https://t.me/YourBotUsername
```

### Клиентское разделение

Одна и та же URL `https://subscription.palantirdns.uk/<token>`:
- **User-Agent = браузер (Chrome/Safari/Firefox/Edge)** → HTML-страница.
- **User-Agent = VPN-клиент (Happ, v2rayNG, Streisand, v2Box, Hiddify, Clash, sing-box, Shadowrocket)** → сырое base64-тело подписки.
- **User-Agent неизвестен** → сырое тело (безопаснее: клиент видит подписку, а не HTML-мусор).

Логика в `sub-aggregator/src/routes/subscription.js`, регекс UA — в `sub-aggregator/src/ua.js`.

---

## 6. DNS failover

Скрипт: `sub-aggregator/scripts/dns-failover.sh` (bash + curl + Cloudflare API).

**Установка (на независимом хосте, не на RF-1/RF-2/origin):**
```bash
sudo install -m 0755 sub-aggregator/scripts/dns-failover.sh /usr/local/sbin/sub-failover
sudo mkdir -p /var/lib/sub-failover /etc/sub-failover

sudo tee /etc/sub-failover/env <<'EOF'
SUB_DOMAIN=subscription.palantirdns.uk
RF1_IP=<белый IP #1>
RF2_IP=<белый IP #2>
CF_ZONE_ID=<zone id из Cloudflare dashboard>
CF_RECORD_ID=<record id субдомена — GET /zones/{zone_id}/dns_records>
CF_API_TOKEN=<токен со scope Zone.DNS:Edit>
EOF

sudo tee /etc/cron.d/sub-failover <<'EOF'
* * * * * root . /etc/sub-failover/env && /usr/local/sbin/sub-failover
EOF
```

**Порог переключения:** 3 fail подряд → RF-2; 5 ok подряд → возврат на RF-1 (см. FAIL_THRESHOLD / HEAL_THRESHOLD в скрипте).

**Мониторинг:** `journalctl -t sub-failover -f`.

---

## 7. Ключи в ENV бота

После деплоя сервиса **добавить в ENV бота**:
```
SUB_AGGREGATOR_ENABLED=true
SUB_AGGREGATOR_URL=https://subscription.palantirdns.uk
SUB_AGGREGATOR_INTERNAL_SECRET=<то же, что INTERNAL_SECRET в сервисе>
SUB_AGGREGATOR_ADMIN_ONLY=true   # оставляем true до окончания beta
```

Ключи проверяются в `config.py` — если пусто, `sub_aggregator.py` no-op'ит.

---

## 8. Тестовый прогон

**Порядок:**
1. Задеплоить `sub-aggregator/docker-compose up -d` на origin.
2. `psql -f migrations/079_sub_pairs.sql` (уже в репо бота).
3. Установить nginx origin.conf + LE cert.
4. Установить nginx front-stream.conf на RF-1 (RF-2 позже).
5. DNS A-record: `subscription.palantirdns.uk` → RF-1 IP, `proxied: false`, TTL 60.
6. Перезапустить бота с новыми ENV.
7. Админ пишет `/aggregator` в боте → получает URL типа `https://subscription.palantirdns.uk/abc123...`.
8. **Тест 1 — сырая подписка (VPN-клиент):**
   ```bash
   curl -H 'User-Agent: Happ/1.0' https://subscription.palantirdns.uk/<token>
   # → base64 body, x-cache: miss/hit/stale
   ```
9. **Тест 2 — HTML (браузер):** открыть в Chrome/Safari → страница с QR + кнопками.
10. **Тест 3 — invalidate:**
    ```bash
    curl -X POST -H "x-internal-secret: $INTERNAL_SECRET" \
      https://subscription.palantirdns.uk/internal/invalidate/<token>
    # → {"ok":true}, followed by curl miss на подписке
    ```
11. **Тест 4 — Happ реально ест:** открыть URL в Happ → добавляется подписка → показаны серверы обоих типов (main + bypass).
12. **Тест 5 — failover:** остановить nginx на RF-1 → 3 минуты подождать → DNS переключается на RF-2 → URL продолжает работать без edit'а клиентской подписки.

---

## 9. Rollout после успешного теста

- Флип: `SUB_AGGREGATOR_ADMIN_ONLY=false`.
- Wire-up в user-facing screens (профиль, покупка, /white) — код уже подготовлен, использует `sub_aggregator.get_url(tg_id)`, который возвращает URL при флипе.
- Постепенное отключение отдачи «сырых» ссылок Remnawave — как отдельный follow-up (после того, как убедимся, что 100% клиентов не сломались).

---

## 10. Rollback plan

Если что-то поломается в проде:
1. `SUB_AGGREGATOR_ENABLED=false` в ENV бота → рестарт → все юзеры автоматически возвращаются к старым двум ссылкам (helper становится no-op).
2. Сервис можно оставить работать — он не мешает; клиенты, уже добавившие агрегатор-ссылку, продолжат её тянуть, пока не сменят.
3. Крайний случай: `UPDATE sub_pairs SET status='revoked' WHERE ...` — сервис отдаст stub всем; клиенты увидят «Subscription revoked» remark в приложении.
