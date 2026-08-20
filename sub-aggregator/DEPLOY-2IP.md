# Deploy: white domain + 2 IP (RF-1 + RF-2)

Быстрый рецепт разворачивания домена `subscription.palantirdns.uk` на двух белых российских VPS с автоматическим failover.

## Заранее приготовить

| Что | Где взять |
|---|---|
| **Два белых VPS в РФ** | Разные ISP (Selectel/Timeweb/Reg.ru/etc). Требования: Ubuntu 22.04 / Debian 12, публичный IP, порт 443 открыт наружу, nginx с `--with-stream` |
| **Origin-сервер бота** | Уже есть (Railway или VPS). Нужен публичный IP + порт 51820/udp наружу для WireGuard |
| **`CF_API_TOKEN`** | Cloudflare → My Profile → API Tokens → «Create Token» → template «Edit zone DNS», scope: `palantirdns.uk` |
| **`CF_ZONE_ID`** | Cloudflare dashboard → домен → sidebar «API» |
| **`CF_RECORD_ID`** | `curl -H "Authorization: Bearer $CF_API_TOKEN" "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records?name=subscription.palantirdns.uk" \| jq '.result[0].id'` |

## Порядок деплоя (~30 мин)

### Шаг 1 — DNS

Cloudflare → `palantirdns.uk` → DNS → Add record:
- Type: **A**
- Name: `subscription`
- IPv4: **IP RF-1**
- Proxy status: **DNS only** (серая туча, НЕ оранжевая)
- TTL: **Auto** (60с при proxy off)

### Шаг 2 — WireGuard mesh

Сначала генерим ключи на всех трёх хостах — потом собираем в конфиги.

**На RF-1:**
```bash
sudo apt install wireguard -y
sudo bash -c 'wg genkey | tee /etc/wireguard/privkey | wg pubkey > /etc/wireguard/pubkey'
sudo cat /etc/wireguard/pubkey  # ← RF1_PUBKEY, запомни
```

**На RF-2:**
```bash
sudo apt install wireguard -y
sudo bash -c 'wg genkey | tee /etc/wireguard/privkey | wg pubkey > /etc/wireguard/pubkey'
sudo cat /etc/wireguard/pubkey  # ← RF2_PUBKEY, запомни
```

**На origin:**
```bash
cd sub-aggregator/scripts
sudo ROLE=origin \
     RF1_PUBKEY=<RF1_PUBKEY> \
     RF2_PUBKEY=<RF2_PUBKEY> \
     bash install-wg.sh
# ← выведет ORIGIN_PUBKEY, запомни
```

**Обратно на RF-1:**
```bash
sudo ROLE=front \
     ORIGIN_ENDPOINT=<origin-public-ip>:51820 \
     ORIGIN_PUBKEY=<ORIGIN_PUBKEY> \
     MY_WG_IP=10.8.0.2 \
     bash install-wg.sh
ping -c 3 10.8.0.1  # должно работать
```

**Обратно на RF-2:**
```bash
sudo ROLE=front \
     ORIGIN_ENDPOINT=<origin-public-ip>:51820 \
     ORIGIN_PUBKEY=<ORIGIN_PUBKEY> \
     MY_WG_IP=10.8.0.3 \
     bash install-wg.sh
ping -c 3 10.8.0.1
```

### Шаг 3 — сервис на origin

```bash
cd sub-aggregator
cp .env.example .env
# Заполни .env: PG_DSN, INTERNAL_SECRET (openssl rand -hex 32), BRAND_*, SUPPORT_URL, BOT_URL
docker compose up -d
docker compose ps  # Up healthy
curl -s http://127.0.0.1:8080/healthz  # {"ok":true}
```

Миграция:
```bash
psql "$DATABASE_URL" -f ../migrations/079_sub_pairs.sql
```

### Шаг 4 — nginx origin (TLS + LE)

```bash
cd sub-aggregator/scripts
sudo SUB_DOMAIN=subscription.palantirdns.uk \
     LE_EMAIL=admin@palantirdns.uk \
     CF_API_TOKEN=<Cloudflare token> \
     FRONT_WG_1=10.8.0.2 \
     FRONT_WG_2=10.8.0.3 \
     WG_CIDR=10.8.0.0/24 \
     bash install-origin.sh
```

Скрипт: apt install → certbot DNS-01 (LE выпустит через Cloudflare API, DNS-only) → dhparam 2048 → nginx-config → reload → cron автообновления.

### Шаг 5 — nginx front на RF-1

```bash
cd sub-aggregator/scripts
sudo ORIGIN_WG_IP=10.8.0.1 bash install-front.sh
```

Проверка (с любой машины НЕ на RF-1):
```bash
curl -sI --resolve subscription.palantirdns.uk:443:<RF-1_IP> https://subscription.palantirdns.uk/healthz
# HTTP/1.1 200 OK
```

### Шаг 6 — nginx front на RF-2 (готовый backup)

```bash
cd sub-aggregator/scripts
sudo ORIGIN_WG_IP=10.8.0.1 bash install-front.sh
```

Проверка:
```bash
curl -sI --resolve subscription.palantirdns.uk:443:<RF-2_IP> https://subscription.palantirdns.uk/healthz
# HTTP/1.1 200 OK
```

### Шаг 7 — DNS failover cron

**Где ставить:** любой хост НЕ на RF-1/RF-2/origin (проще — на origin, но убедись, что он сам не пропадает вместе с фронтами).

```bash
cd sub-aggregator/scripts
sudo SUB_DOMAIN=subscription.palantirdns.uk \
     RF1_IP=<белый IP #1> \
     RF2_IP=<белый IP #2> \
     CF_ZONE_ID=<zone id> \
     CF_RECORD_ID=<record id> \
     CF_API_TOKEN=<token> \
     bash install-failover-cron.sh
```

Мониторинг:
```bash
journalctl -t sub-failover -f
```

### Шаг 8 — бот-ENV

Railway → ATCbot → Variables:
```
SUB_AGGREGATOR_ENABLED=true
SUB_AGGREGATOR_URL=https://subscription.palantirdns.uk
SUB_AGGREGATOR_INTERNAL_SECRET=<тот же, что в sub-aggregator/.env INTERNAL_SECRET>
SUB_AGGREGATOR_ADMIN_ONLY=true   # ← бета-фаза
```
Redeploy бота.

## Приёмочный чеклист

- [ ] `dig +short subscription.palantirdns.uk @1.1.1.1` → RF-1 IP
- [ ] `curl -sI --resolve subscription.palantirdns.uk:443:<RF-1> https://subscription.palantirdns.uk/healthz` → 200
- [ ] `curl -sI --resolve subscription.palantirdns.uk:443:<RF-2> https://subscription.palantirdns.uk/healthz` → 200
- [ ] `openssl s_client -connect subscription.palantirdns.uk:443 </dev/null 2>&1 | grep 'CN ='` → правильный домен, срок валиден
- [ ] Админ в боте пишет `/aggregator` → получает URL
- [ ] URL открывается в Chrome (не РФ) → HTML-страница с QR
- [ ] URL открывается в Chrome (РФ, без VPN) → та же HTML-страница ← «отбеленность»
- [ ] URL в Happ (РФ, без VPN) → подписка добавилась, серверы обоих типов видны
- [ ] Тест failover: `sudo systemctl stop nginx` на RF-1 → через 3 мин DNS переключился на RF-2 → URL продолжает работать без правки клиента
- [ ] Восстановление: `sudo systemctl start nginx` на RF-1 → через 5 мин DNS вернулся на RF-1

## Что делает «отбеливание»

1. **Домен отдельный** (`palantirdns.uk` ≠ домен панели) → блокировка одного не роняет второе.
2. **DNS `proxied:false`** → нет Cloudflare-proxy → нет CF-fingerprint (`cf-ray`, `server:cloudflare`) → нет CF WAF, режущего base64 подписки.
3. **Белые российские IP** → провайдеры РФ не блокируют → клиенты Happ/v2rayNG заходят напрямую.
4. **TLS 1.3 + X25519** → стандартный fingerprint, не палит инфраструктуру DPI.
5. **HTTP/1.1** (без H2) → не палится как «сервис с длинными multiplex-стримами», обычный HTTP.
6. **Passthrough на фронтах** → sertifikat живёт только на origin, компрометация фронта не даёт доступа.
7. **PROXY protocol v2** → origin видит реальный IP клиента для rate-limit, но фронт не смотрит содержимое.

## Rollback

```bash
# На боте (Railway):
SUB_AGGREGATOR_ENABLED=false  # → бот возвращается к отдаче двух ссылок
# Redeploy

# Оставить агрегатор работающим — уже выданные URL продолжают работать,
# новые не создаются. Ничего больше делать не надо.
```

## Диагностика

```bash
# На origin — что стучится в сервис
docker compose logs -f aggregator | jq .

# На origin — что видит nginx (реальные IP клиентов через PROXY-protocol)
sudo tail -f /var/log/nginx/access.log

# На фронте — TCP-статистика
sudo tail -f /var/log/nginx/sub-stream.access.log

# Failover-раннер — состояние
sudo cat /var/lib/sub-failover/state
```
