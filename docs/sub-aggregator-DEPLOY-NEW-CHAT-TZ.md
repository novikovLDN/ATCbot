# ТЗ: развёртывание sub-aggregator + брендированной sub-page

**Для передачи в отдельный чат / деплой-инженеру.** Документ самодостаточный — читателю не нужен контекст предыдущих обсуждений.

---

## 0. TL;DR

Разворачиваем HTTP-сервис `sub-aggregator`, который склеивает две подписки Remnawave (main + bypass) в одну и отдаёт под единой ссылкой `https://subscription.palantirdns.uk/<token>`. Ссылка публикуется через два российских VPS (RF-1 активный, RF-2 резерв), TCP-passthrough на origin-сервер бота. Браузеры получают брендированную HTML-страницу с QR-кодом; VPN-клиенты (Happ, v2rayTun и т.д.) — сырой base64 подписки. Первый прогон — только для админа (`SUB_AGGREGATOR_ADMIN_ONLY=true`); после успешного теста флип на всех пользователей.

---

## 1. Контекст и мотивация

### 1.1 Что уже есть
- Бот-сервис ATCbot (Python/aiogram, деплой на Railway).
- Панель Remnawave 3.3 (VLESS/VPN мультиплексор), развёрнута на отдельном origin-сервере.
- У каждого клиента бота — **две** entity в панели:
  - **premium** (main servers, лимит по сроку — например 30 дней)
  - **bypass** (whitelisted servers, лимит по трафику — например 75 ГБ)
- Каждая entity даёт свою sub-ссылку (`https://<panel-domain>/api/sub/<uuid>`), клиент подписывается на неё в приложении.

### 1.2 Проблема
- Юзеру приходится **добавлять две ссылки** в клиенте → путаница, чаще звонки в поддержку.
- Обе ссылки светят домен панели → **лёгкая цель для РКН-блокировки**. Заблокировали домен → рабочий VPN превратился в «клиент не может получить подписку».
- Обновлений подписки много (renew, докупка ГБ, комбо) — каждое требует, чтобы юзер вручную «обновил подписку» в клиенте по обеим ссылкам.

### 1.3 Решение
1. **Один URL** вместо двух: `https://subscription.palantirdns.uk/<token>`.
2. **Новый домен**, зарегистрирован отдельно от домена панели — блокировка одного не роняет второе.
3. **Два фронт-VPS в РФ (RF-1, RF-2)** — если один упал, DNS переключается на второй за минуту, клиент этого не замечает.
4. **HTML-страница в браузере** с QR-кодом и кнопками «Открыть в Happ» — избавляемся от «скинь мне ссылку, я не понимаю куда её вставлять».

### 1.4 Что делает агрегатор (upstream-независимая механика)
1. Клиент шлёт `GET https://subscription.palantirdns.uk/<token>` со своим User-Agent.
2. Сервис по token достаёт из Postgres таблицы `sub_pairs` две URL (main, bypass).
3. Параллельно (`Promise.all`, 2 сек таймаут, 1 ретрай) качает обе от панели.
4. Декодирует base64 → массивы строк `vless://...`, склеивает без дублей.
5. Собирает hybrid `subscription-userinfo`: `upload/download/total` из bypass, `expire` из premium.
6. Отвечает клиенту:
   - **VPN-клиент (User-Agent = Happ/v2rayNG/Streisand/...)** → base64 + hybrid header.
   - **Браузер** → HTML-страница (QR, кнопки установки, копирование ссылки).
7. Всё кэшируется в Redis: fresh 5 мин, stale 3 дня (при падении апстрима отдаём последнюю известную склейку).

---

## 2. Инфраструктурная схема

```
                        Клиенты (Happ / v2rayNG / браузер / …)
                                        │
                                        ▼
                              subscription.palantirdns.uk
                                (Cloudflare DNS, proxied:false, TTL 60)
                                        │
                          ┌─────────────┴─────────────┐
                          ▼                           ▼
                   [ RF-1: <IP #1> ]          [ RF-2: <IP #2> ]
                   nginx stream (TCP-passthrough, TLS никак не терминирует)
                          │                           │
                          └──── WireGuard ────┬───────┘
                                              ▼
                             [ ORIGIN: бот-сервер, Railway или отдельный VPS ]
                                    ┌────────┴────────┐
                                    ▼                 ▼
                              nginx (TLS)       (тот же хост)
                                    │
                                    ▼
                             127.0.0.1:8080
                              sub-aggregator (Node.js)
                                    │
                          ┌─────────┴─────────┐
                          ▼                   ▼
                    Redis (localhost)   Postgres (localhost)
                                              │
                                              ▼
                                        sub_pairs table
                                        (пишет бот, читает агрегатор)
                                              │
                                              ▼
                                       Панель Remnawave
                              (upstream sub URLs, HTTPS)
```

### 2.1 Почему два фронт-VPS
- Один VPS упал / IP заблокирован → DNS автопереключение на второй за 1 мин (скрипт `dns-failover.sh`, cron).
- Никакого «оба активны» (round-robin) — TLS-сертификат живёт **только на origin**, клиенту важен один SNI/сертификат, а не два разных. Active/standby проще диагностировать.
- Оба фронта — обычные VPS с публичным белым IP, nginx stream, WireGuard-клиент. **TLS не терминируют**, сертификатов не хранят — компрометация фронта не даёт доступа к origin.

### 2.2 Почему TCP-passthrough (stream), а не HTTP-прокси
- Cloudflare/nginx-frontend с HTTP-прокси добавляют `X-Forwarded-For`, режут «suspicious» base64 как WAF, ломают HTTP/2 push, могут менять `Content-Length` — Happ/v2rayNG на такое ругаются.
- Passthrough отдаёт байты как есть → апстрим-панель и клиент общаются напрямую (TLS-туннель через WG).
- Bonus: RF-серверам вообще не нужно знать про Let's Encrypt.

### 2.3 Почему `proxied:false` в Cloudflare
- Cloudflare-proxy при `proxied:true` показывает свой IP клиенту → скрывает белые RF-IP.
- Но: CF WAF на free-плане режет base64-ответы > 100 КБ как «suspicious». А топ-подписки уже 30-60 КБ, скоро дойдут.
- Плюс: CF-proxy добавляет `cf-ray`, `cf-cache-status`, `server: cloudflare` — палит инфраструктуру.
- `proxied:false` = чистый DNS A-record, CF только резолвит имя.

### 2.4 Почему домен `palantirdns.uk`, а не поддомен основного бренда
- Отдельная регистрация → отдельная TLS-цепочка → блокировка домена панели/бота не влияет на домен подписки.
- `.uk` — стабильный регистратор, не РКН-юрисдикция; клиенты в РФ по DNS-серверам провайдера получают ответ.
- Если этот домен всё же заблокируют — регистрируется следующий (`palantirdns2.uk`, `palantirdns3.uk`), обновляется env `SUB_AGGREGATOR_URL` в боте и `server_name` в nginx origin. Клиенту приходит новая ссылка через `/aggregator` или через пуш «обновить подписку». Заблокированный домен возвращает stub-подписку с remark «URL blocked, open bot for new link».

---

## 3. Требуемые ресурсы (что нужно подготовить до старта)

### 3.1 Домены
- **palantirdns.uk** — уже зарегистрирован у клиента (данность).
- **subscription.palantirdns.uk** — субдомен, DNS создать в Cloudflare (см. § 5.1).

### 3.2 Серверы

| Роль | Требования | Кол-во |
|---|---|---|
| **RF-1** (active) | Ubuntu 22.04 / Debian 12, 1 vCPU, 1 GB RAM, 20 GB SSD, публичный **белый** IP (не сер, не CGNAT). Провайдер должен разрешать VPN-трафик. Локация — Россия. Нужен nginx с `--with-stream` (default на официальных пакетах). | 1 |
| **RF-2** (standby) | Такой же. Разный ISP от RF-1 (чтоб один блэкаут не убил обоих). | 1 |
| **ORIGIN** (бот + сервис) | Существующий хост бота (Railway или VPS). +512 MB RAM для Node.js сервиса + Redis. Публичный IP не нужен — доступ только через WG от RF-1/RF-2. | 1 (уже есть) |
| **FAILOVER-РАННЕР** | Любой хост НЕ на RF-1/RF-2/origin (иначе не заметит падения). Может быть тот же ORIGIN (там cron `dns-failover.sh` раз/мин). | 1 (можно ORIGIN) |

### 3.3 WireGuard-туннель
- Один WG-сервер на origin, два клиента (RF-1, RF-2), одна общая подсеть (например `10.8.0.0/24`).
- Origin имеет `10.8.0.1`, RF-1 → `10.8.0.2`, RF-2 → `10.8.0.3`.
- От RF-1/RF-2 доступ к origin **только** по WG-адресу — публичный IP origin никому не светит.

### 3.4 Секреты и токены
Собрать перед стартом:

| Что | Где взять | Куда положить |
|---|---|---|
| `CF_API_TOKEN` | Cloudflare → My Profile → API Tokens → «Create Token» → template «Edit zone DNS», scope: только `palantirdns.uk` | `/etc/sub-failover/env` на failover-раннере |
| `CF_ZONE_ID` | Cloudflare dashboard → домен → sidebar «API» | тот же файл |
| `CF_RECORD_ID` | `curl -H "Authorization: Bearer $CF_API_TOKEN" https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records?name=subscription.palantirdns.uk` | тот же файл |
| `INTERNAL_SECRET` | `openssl rand -hex 32` | ENV сервиса + ENV бота (одинаковые) |
| `WEBHOOK_SECRET` | `openssl rand -hex 32` | ENV сервиса + ENV панели Remnawave (webhook config) |

---

## 4. Артефакты (уже в репо, ничего писать не надо)

Всё готово в `/sub-aggregator/`. Проверить наличие:

```bash
ls sub-aggregator/
# Ожидается:
# Dockerfile
# docker-compose.yml
# package.json
# README.md
# migrations/001_sub_pairs.sql
# nginx/origin.conf              ← шаблон для ORIGIN
# nginx/front-stream.conf        ← шаблон для RF-1, RF-2
# scripts/dns-failover.sh        ← cron-скрипт для DNS failover
# src/…                          ← код сервиса
# test/…                         ← тесты (unit + integration)
```

**На бот-стороне** также готово:
- `migrations/079_sub_pairs.sql` — таблица `sub_pairs` (создать в общем Postgres, бот пишет, сервис читает)
- `app/services/sub_aggregator.py` — модуль-хелпер (`ensure_pair`, `invalidate`, `revoke`)
- `app/handlers/admin/sub_aggregator_cmd.py` — команда `/aggregator` для админа
- `config.py` — vars `SUB_AGGREGATOR_ENABLED`, `SUB_AGGREGATOR_URL`, `SUB_AGGREGATOR_ADMIN_ONLY`, `SUB_AGGREGATOR_INTERNAL_SECRET`

---

## 5. Пошаговый деплой

### 5.1 DNS (Cloudflare)

1. Cloudflare → домен `palantirdns.uk` → **DNS → Records → Add record**:
   - Type: **A**
   - Name: `subscription`
   - IPv4: `<RF-1 IP>`
   - Proxy status: **DNS only** (серая туча — ⚠️ НЕ оранжевая)
   - TTL: **Auto** (Cloudflare выставит 60с если proxy off)
2. Проверить:
   ```bash
   dig +short subscription.palantirdns.uk @1.1.1.1
   # → <RF-1 IP>
   ```

### 5.2 WireGuard (если ещё нет)

На **ORIGIN**:
```bash
sudo apt install wireguard
wg genkey | sudo tee /etc/wireguard/privatekey | wg pubkey | sudo tee /etc/wireguard/publickey
# Записать оба ключа.
```

Конфиг `/etc/wireguard/wg0.conf` (пример):
```ini
[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = <origin-privkey>

[Peer]  # RF-1
PublicKey = <rf1-pubkey>
AllowedIPs = 10.8.0.2/32

[Peer]  # RF-2
PublicKey = <rf2-pubkey>
AllowedIPs = 10.8.0.3/32
```

На **RF-1** и **RF-2** — симметрично, `AllowedIPs = 10.8.0.1/32`, `Endpoint = <origin-public-ip>:51820`.

Поднять: `sudo systemctl enable --now wg-quick@wg0` на всех трёх.

Проверить: `ping -c 3 10.8.0.1` с RF-1 и RF-2.

### 5.3 Сервис на ORIGIN

**Postgres миграция (в общей БД бота):**
```bash
psql "$DATABASE_URL" -f migrations/079_sub_pairs.sql
# И (безопасно, идемпотентно):
psql "$DATABASE_URL" -f sub-aggregator/migrations/001_sub_pairs.sql
```

**Запуск сервиса:**
```bash
cd sub-aggregator/
cp .env.example .env  # если нет — создать вручную:
cat > .env <<EOF
PORT=8080
PG_DSN=postgres://postgres:...@localhost:5432/atcbot
REDIS_URL=redis://redis:6379
INTERNAL_SECRET=<openssl rand -hex 32>
WEBHOOK_SECRET=<openssl rand -hex 32>
WEBHOOK_SIG_HEADER=x-remnawave-signature
CACHE_TTL=300
STALE_TTL=259200
MAP_TTL=3600
UPSTREAM_TIMEOUT_MS=2000
REVOKED_REMARK=Subscription revoked — open the bot for a new link
LOG_LEVEL=info

# Брендинг HTML-страницы:
BRAND_NAME=Atlas Secure
BRAND_SLOGAN=Защищённое подключение
BRAND_PRIMARY_COLOR=#2563EB
SUPPORT_URL=https://t.me/AtlasSecureSupport
BOT_URL=https://t.me/YourBotUsername
EOF

docker compose up -d
docker compose ps  # оба (aggregator + redis) должны быть Up healthy
```

Проверка локально на origin:
```bash
curl -s http://127.0.0.1:8080/healthz
# {"ok":true}

curl -s http://127.0.0.1:8080/readyz
# {"ok":true,"redis":"pong"}
```

### 5.4 TLS + nginx на ORIGIN

**Let's Encrypt:**
```bash
sudo apt install certbot
sudo mkdir -p /var/www/acme

# ⚠️ Для валидации LE нужно, чтобы 80/tcp на RF-1 проксировался на origin ИЛИ
# использовать DNS challenge. Для стрим-фронта — DNS challenge проще:
sudo certbot certonly --dns-cloudflare \
  --dns-cloudflare-credentials /root/.cf-credentials.ini \
  -d subscription.palantirdns.uk --agree-tos --email admin@...
# файл /root/.cf-credentials.ini:
#   dns_cloudflare_api_token = <CF_API_TOKEN>
```

**nginx-config:**
```bash
sudo cp sub-aggregator/nginx/origin.conf /etc/nginx/sites-available/subscription
# Подставить в файле:
#   SUB_DOMAIN     → subscription.palantirdns.uk
#   TLS_CERT       → /etc/letsencrypt/live/subscription.palantirdns.uk/fullchain.pem
#   TLS_KEY        → /etc/letsencrypt/live/subscription.palantirdns.uk/privkey.pem
#   WG_CIDR        → 10.8.0.0/24
sudo ln -s /etc/nginx/sites-available/subscription /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Автообновление сертификата:
```bash
sudo tee /etc/cron.d/certbot-renew <<'EOF'
0 4 * * * root certbot renew --quiet --post-hook "systemctl reload nginx"
EOF
```

### 5.5 Фронт-стрим на RF-1

```bash
# На RF-1:
sudo apt install nginx
sudo cp /path/to/front-stream.conf /etc/nginx/nginx.conf
# ⚠️ front-stream.conf идёт в /etc/nginx/nginx.conf (stream должен быть верхнеуровневый),
# а не в sites-enabled/. Пример содержимого:

# Подставить в файле:
#   ORIGIN_WG_IP → 10.8.0.1
sudo nginx -t && sudo systemctl restart nginx

# Firewall — открыть 443:
sudo ufw allow 443/tcp
```

Проверка снаружи (с любой машины):
```bash
curl -sI --resolve subscription.palantirdns.uk:443:<RF-1_IP> \
  https://subscription.palantirdns.uk/healthz
# → HTTP/2 200
```

### 5.6 Фронт-стрим на RF-2 (пока standby)

**Идентично § 5.5, но DNS указывает на RF-1** — до момента failover'а RF-2 просто ждёт с работающим nginx. Убедиться что WG-туннель поднят и `curl --resolve …:<RF-2_IP>` тоже отвечает 200.

### 5.7 DNS failover cron

На **любом** хосте кроме RF-1/RF-2/origin (обычно на самом origin):

```bash
sudo install -m 0755 sub-aggregator/scripts/dns-failover.sh /usr/local/sbin/sub-failover
sudo mkdir -p /var/lib/sub-failover /etc/sub-failover

sudo tee /etc/sub-failover/env <<EOF
SUB_DOMAIN=subscription.palantirdns.uk
RF1_IP=<белый IP #1>
RF2_IP=<белый IP #2>
CF_ZONE_ID=<...>
CF_RECORD_ID=<...>
CF_API_TOKEN=<...>
FAIL_THRESHOLD=3      # 3 подряд провала → failover
HEAL_THRESHOLD=5      # 5 подряд OK на RF-1 → возврат
EOF

sudo tee /etc/cron.d/sub-failover <<'EOF'
* * * * * root . /etc/sub-failover/env && /usr/local/sbin/sub-failover 2>&1 | logger -t sub-failover
EOF
```

Мониторинг:
```bash
journalctl -t sub-failover -f
```

### 5.8 Бот-сервис (Railway)

Добавить в Railway → Variables:
```
SUB_AGGREGATOR_ENABLED=true
SUB_AGGREGATOR_URL=https://subscription.palantirdns.uk
SUB_AGGREGATOR_INTERNAL_SECRET=<тот же, что в sub-aggregator .env INTERNAL_SECRET>
SUB_AGGREGATOR_ADMIN_ONLY=true   # ⚠️ обязательно true для бета-фазы
```

Рестарт: Railway → сервис бота → Redeploy.

### 5.9 (Опционально) Вебхук от панели → агрегатор

Если панель Remnawave умеет слать вебхуки на `user.limited`:

- В Remnawave → Webhooks → Create:
  - URL: `https://subscription.palantirdns.uk/internal/webhook`
  - Secret: `<WEBHOOK_SECRET>` из .env сервиса
  - Signature header: `x-remnawave-signature`
  - Events: `user.limited`, `user.updated`, `user.deleted`

Проверить:
```bash
# Тест с валидной подписью (пример):
BODY='{"event":"user.limited","data":{"uuid":"00000000-0000-0000-0000-000000000000"}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | awk '{print $2}')
curl -X POST https://subscription.palantirdns.uk/internal/webhook \
  -H "Content-Type: application/json" \
  -H "x-remnawave-signature: $SIG" \
  -d "$BODY"
# → 200
```

---

## 6. Верификация (обязательные проверки)

### 6.1 Инфраструктура
```bash
# DNS
dig +short subscription.palantirdns.uk    # → RF-1 IP

# Health через оба фронта
curl -sI --resolve subscription.palantirdns.uk:443:<RF-1_IP> https://subscription.palantirdns.uk/healthz  # 200
curl -sI --resolve subscription.palantirdns.uk:443:<RF-2_IP> https://subscription.palantirdns.uk/healthz  # 200

# TLS
openssl s_client -connect subscription.palantirdns.uk:443 -servername subscription.palantirdns.uk </dev/null 2>/dev/null | openssl x509 -noout -subject -dates
# subject=CN=subscription.palantirdns.uk, даты валидны
```

### 6.2 Флоу «админ получает URL»
1. В боте → `/aggregator`.
2. Бот отвечает URL типа `https://subscription.palantirdns.uk/<32-char-token>`.
3. Открыть URL в браузере (Chrome/Safari) → должна отобразиться **брендированная HTML-страница** с QR + кнопки.
4. Открыть URL в Happ (iOS/Android) → должна **добавиться подписка**, показать серверы обоих типов (main + bypass).

### 6.3 Инвалидация
1. Купить любой пакет / докупить ГБ.
2. Бот вызовет `POST /internal/invalidate/<token>` (в логах бота: `SUB_AGGREGATOR_INVALIDATED`).
3. Следующий GET в клиенте → `x-cache: miss` (проверить через `curl -I ...`), новые данные.

### 6.4 Failover
1. На RF-1: `sudo systemctl stop nginx`.
2. Через 3 минуты (3 fail × 1 мин cron) — DNS переключится на RF-2:
   ```bash
   dig +short subscription.palantirdns.uk  # → RF-2 IP
   ```
3. Клиент продолжает работать без изменений подписки (URL не менялся).
4. Восстановление: `sudo systemctl start nginx` на RF-1 → через 5 мин (5 OK подряд) DNS вернётся.

### 6.5 Тест SWR при падении апстрима
1. Прогреть кеш: `curl https://subscription.palantirdns.uk/<token>` → `x-cache: hit`.
2. Отключить панель Remnawave (или заблокировать её через iptables).
3. Прождать `CACHE_TTL + 1` (301с) → следующий запрос: `x-cache: stale`, тело от прошлого раза.
4. Восстановить панель → через `CACHE_TTL` кэш обновляется в фоне.

---

## 7. Rollout после успешного теста

1. Убедиться что все 4 теста § 6 прошли.
2. Флип: Railway → бот → `SUB_AGGREGATOR_ADMIN_ONLY=false` → Redeploy.
3. Все юзеры автоматически получают агрегатор-URL после следующей покупки/продления (бот-логика `sub_aggregator.ensure_pair()` создаёт `sub_pairs` строку).
4. **Не удалять** сразу старые ссылки — грейс-период 30 дней, юзеры сами обновят подписку через клиент.

---

## 8. Rollback

Если что-то сломалось после rollout:

| Проблема | Действие |
|---|---|
| Клиенты не читают склеенную подписку | Railway → `SUB_AGGREGATOR_ENABLED=false` → Redeploy. Бот сразу вернётся к отдаче двух старых ссылок. Уже выданные aggregator-URL продолжат работать (сервис не выключается). |
| Сервис агрегатора упал | Клиенты, кеш которых прогрет, ещё 3 дня видят stale. Успеть починить. |
| Оба фронта упали | Клиенты не получают подписку, но VPN-соединение **работает** (клиент кэширует конфиги локально). Успеть починить фронты. |
| Домен `subscription.palantirdns.uk` заблокирован в РФ | Регистрировать `subscription.palantirdns2.uk`, обновить `SUB_AGGREGATOR_URL` в боте, отправить broadcast-сообщение «обновите ссылку в клиенте» с новым `/aggregator`. |

Полный форс-мажор: `UPDATE sub_pairs SET status='revoked' WHERE ...` → все клиенты получат stub-конфиг с remark «Subscription revoked, open bot for new link».

---

## 9. Диагностика

**Логи сервиса:**
```bash
docker compose logs -f aggregator | jq .
# JSON structured logs: request_id, token_prefix, x_cache, duration_ms, upstream_status
```

**Метрики (Prometheus):**
```bash
curl -s http://127.0.0.1:8080/metrics
# aggregator_requests_total{result="hit"} 12345
# aggregator_requests_total{result="miss"} 42
# aggregator_requests_total{result="stale"} 5
# aggregator_upstream_errors_total 0
# aggregator_webhook_events_total 12
```

**Здоровье пары для конкретного юзера:**
```sql
SELECT token, status, updated_at, main_user_uuid, gb_user_uuid
FROM sub_pairs
WHERE telegram_id = <tg>;
```

**Ручная инвалидация:**
```bash
curl -X POST -H "x-internal-secret: $INTERNAL_SECRET" \
  https://subscription.palantirdns.uk/internal/invalidate/<token>
```

---

## 10. FAQ / решения гарантированных вопросов

**Q: Почему base64 не сжимается? Ответ 60 КБ большой.**
A: Клиенты Happ/v2rayNG требуют строго `Content-Type: text/plain` без Content-Encoding. gzip ломает Streisand. Смиряемся.

**Q: Что если у юзера ещё нет bypass entity (только premium/trial)?**
A: `sub_aggregator.ensure_pair()` вернёт `None` — бот отдаст обычную одну premium-ссылку. Не проблема.

**Q: Почему у сервиса `PORT=8080`, а публично 443?**
A: Сервис слушает только localhost. nginx на origin проксирует 443 → 127.0.0.1:8080 после TLS-терминации. RF-1/RF-2 в TLS не лезут вообще.

**Q: Что если Cloudflare API упал во время failover?**
A: Скрипт логирует ERROR через logger → в syslog. При следующем cron-тике (через минуту) повторит. Клиенты в это время всё ещё ходят по прежнему IP.

**Q: Как узнать, что клиент реально получил склеенную подписку?**
A: `curl -A 'Happ/1.0' https://subscription.palantirdns.uk/<token> | base64 -d | wc -l` — должно быть строк = (кол-во main серверов) + (кол-во bypass серверов). Плюс header `subscription-userinfo` со значениями upload/download/total > 0 и expire в будущем.

---

## 11. Что не входит в этот ТЗ (feature-scope out)

- Поддержка форматов `clash`, `sing-box` — только XRAY base64 (MVP).
- Custom-таблица роутинга — рендерится ровно то, что отдаёт панель.
- UI-панель управления агрегатором — все манипуляции через бот + SQL.
- Автоматическая ротация домена при блокировке — ручная (обновление ENV + broadcast).
- Мониторинг падения апстримов с алертами в Telegram — базовые логи есть, алерт добавлять по мере необходимости.

---

## 12. Definition of Done

- [ ] DNS настроен, `dig` возвращает RF-1 IP.
- [ ] `curl --resolve …:<RF-1_IP>` и `--resolve …:<RF-2_IP>` оба отвечают 200 на `/healthz`.
- [ ] TLS-сертификат валиден, `openssl s_client` показывает правильный CN.
- [ ] Docker-compose поднят, `docker compose ps` = Up healthy.
- [ ] `psql -c "SELECT count(*) FROM sub_pairs;"` работает (таблица существует).
- [ ] Админ в боте написал `/aggregator` → получил URL, открыл в Chrome (видит HTML), открыл в Happ (получил подписку).
- [ ] Тест failover: остановил nginx на RF-1 → через 3 мин DNS переключился, клиент работает без изменения ссылки.
- [ ] Тест invalidate: покупка → `x-cache: miss` в следующем запросе.
- [ ] `journalctl -t sub-failover` даёт «OK» каждую минуту.
- [ ] Rollback-инструкция проверена: `SUB_AGGREGATOR_ENABLED=false` → бот возвращается к старой отдаче двух ссылок.
