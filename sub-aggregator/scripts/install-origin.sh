#!/usr/bin/env bash
# scripts/install-origin.sh — provision the origin server (nginx TLS + LE cert).
#
# Ставит nginx, выпускает Let's Encrypt через DNS-01 challenge (Cloudflare),
# рендерит origin.conf, включает автообновление сертификата.
#
# Использование:
#   sudo SUB_DOMAIN=subscription.palantirdns.uk \
#        LE_EMAIL=admin@palantirdns.uk \
#        CF_API_TOKEN=<Cloudflare token> \
#        FRONT_WG_1=10.8.0.2 \
#        FRONT_WG_2=10.8.0.3 \
#        WG_CIDR=10.8.0.0/24 \
#        bash install-origin.sh
#
# После запуска: домен уже работает — DNS должен указывать на RF-1/RF-2,
# те через WG попадают сюда, TLS терминируется здесь.
set -euo pipefail

SUB_DOMAIN="${SUB_DOMAIN:?SUB_DOMAIN required, e.g. subscription.palantirdns.uk}"
LE_EMAIL="${LE_EMAIL:?LE_EMAIL required}"
CF_API_TOKEN="${CF_API_TOKEN:?CF_API_TOKEN required (Zone.DNS:Edit scope)}"
FRONT_WG_1="${FRONT_WG_1:?FRONT_WG_1 required, e.g. 10.8.0.2}"
FRONT_WG_2="${FRONT_WG_2:?FRONT_WG_2 required, e.g. 10.8.0.3}"
WG_CIDR="${WG_CIDR:-10.8.0.0/24}"
ACME_DIR="${ACME_DIR:-/var/www/acme}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_SRC="$SCRIPT_DIR/../nginx/origin.conf"

if [[ $EUID -ne 0 ]]; then echo "Run as root (sudo)." >&2; exit 1; fi

echo "[1/8] apt install nginx + certbot + cloudflare plugin"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot \
  python3-certbot-dns-cloudflare openssl curl >/dev/null

echo "[2/8] Cloudflare credentials → /root/.cf-credentials.ini"
umask 077
cat > /root/.cf-credentials.ini <<EOF
dns_cloudflare_api_token = ${CF_API_TOKEN}
EOF
chmod 600 /root/.cf-credentials.ini

echo "[3/8] LE выпуск сертификата через DNS-01 (Cloudflare)"
if [[ ! -d /etc/letsencrypt/live/${SUB_DOMAIN} ]]; then
  certbot certonly --non-interactive --agree-tos \
    --email "$LE_EMAIL" \
    --dns-cloudflare \
    --dns-cloudflare-credentials /root/.cf-credentials.ini \
    --dns-cloudflare-propagation-seconds 30 \
    -d "$SUB_DOMAIN"
else
  echo "     Cert уже есть, пропускаю выпуск."
fi

TLS_CERT="/etc/letsencrypt/live/${SUB_DOMAIN}/fullchain.pem"
TLS_KEY="/etc/letsencrypt/live/${SUB_DOMAIN}/privkey.pem"

echo "[4/8] dhparam.pem (2048 бит) — одноразово"
if [[ ! -f /etc/nginx/dhparam.pem ]]; then
  openssl dhparam -out /etc/nginx/dhparam.pem 2048
fi
TLS_DHPARAM="/etc/nginx/dhparam.pem"

echo "[5/8] mkdir ${ACME_DIR}"
mkdir -p "$ACME_DIR"

echo "[6/8] Рендер origin.conf → /etc/nginx/sites-available/sub-aggregator"
if [[ ! -f "$CONF_SRC" ]]; then echo "FATAL: не найден $CONF_SRC"; exit 1; fi

sed -e "s|SUB_DOMAIN|${SUB_DOMAIN}|g" \
    -e "s|ACME_DIR|${ACME_DIR}|g" \
    -e "s|TLS_CERT|${TLS_CERT}|g" \
    -e "s|TLS_KEY|${TLS_KEY}|g" \
    -e "s|TLS_DHPARAM|${TLS_DHPARAM}|g" \
    -e "s|WG_CIDR|${WG_CIDR}|g" \
    -e "s|FRONT_WG_1|${FRONT_WG_1}|g" \
    -e "s|FRONT_WG_2|${FRONT_WG_2}|g" \
    "$CONF_SRC" > /etc/nginx/sites-available/sub-aggregator

ln -sf /etc/nginx/sites-available/sub-aggregator /etc/nginx/sites-enabled/sub-aggregator
# Убрать дефолтный сайт (если мешает 80/443).
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl enable nginx >/dev/null
systemctl reload nginx

echo "[7/8] Автообновление LE — cron 04:00 UTC"
cat > /etc/cron.d/certbot-sub-aggregator <<EOF
0 4 * * * root certbot renew --quiet --post-hook "systemctl reload nginx"
EOF

echo "[8/8] Проверка"
sleep 1
echo "     healthz: $(curl -sk https://127.0.0.1/healthz -H "Host: $SUB_DOMAIN" 2>&1 | head -c 80)"
echo ""
echo "✅ Origin провижн завершён."
echo "   Убедись что sub-aggregator сервис запущен: cd sub-aggregator && docker compose up -d"
echo "   Проверка снаружи (через DNS/фронт):"
echo "     curl -sI https://${SUB_DOMAIN}/healthz"
