#!/usr/bin/env bash
# scripts/install-failover-cron.sh — install DNS-failover cron on a monitor host.
#
# ⚠️ НЕ запускай на RF-1/RF-2/origin (иначе не сможешь заметить, что RF-1
# упал — сам с него тоже отвалишься). Ставь на любой независимый VPS
# (или на origin — если origin сам за фронтами, это OK).
#
# Использование:
#   sudo SUB_DOMAIN=subscription.palantirdns.uk \
#        RF1_IP=1.2.3.4 \
#        RF2_IP=5.6.7.8 \
#        CF_ZONE_ID=<zone id> \
#        CF_RECORD_ID=<record id> \
#        CF_API_TOKEN=<token> \
#        bash install-failover-cron.sh
set -euo pipefail

SUB_DOMAIN="${SUB_DOMAIN:?SUB_DOMAIN required}"
RF1_IP="${RF1_IP:?RF1_IP required}"
RF2_IP="${RF2_IP:?RF2_IP required}"
CF_ZONE_ID="${CF_ZONE_ID:?CF_ZONE_ID required}"
CF_RECORD_ID="${CF_RECORD_ID:?CF_RECORD_ID required}"
CF_API_TOKEN="${CF_API_TOKEN:?CF_API_TOKEN required}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FO_SRC="$SCRIPT_DIR/dns-failover.sh"

if [[ $EUID -ne 0 ]]; then echo "Run as root (sudo)." >&2; exit 1; fi
[[ -f "$FO_SRC" ]] || { echo "FATAL: не найден $FO_SRC"; exit 1; }

echo "[1/4] apt install curl bsdmainutils (logger)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y curl bsdmainutils >/dev/null

echo "[2/4] Копирую dns-failover.sh → /usr/local/sbin/sub-failover"
install -m 0755 "$FO_SRC" /usr/local/sbin/sub-failover
mkdir -p /var/lib/sub-failover /etc/sub-failover

echo "[3/4] /etc/sub-failover/env"
umask 077
cat > /etc/sub-failover/env <<EOF
SUB_DOMAIN=${SUB_DOMAIN}
RF1_IP=${RF1_IP}
RF2_IP=${RF2_IP}
CF_ZONE_ID=${CF_ZONE_ID}
CF_RECORD_ID=${CF_RECORD_ID}
CF_API_TOKEN=${CF_API_TOKEN}
FAIL_THRESHOLD=3
HEAL_THRESHOLD=5
PROBE_TIMEOUT=5
EOF
chmod 600 /etc/sub-failover/env

echo "[4/4] Crontab /etc/cron.d/sub-failover — раз/мин"
cat > /etc/cron.d/sub-failover <<'EOF'
* * * * * root set -a && . /etc/sub-failover/env && set +a && /usr/local/sbin/sub-failover 2>&1 | logger -t sub-failover
EOF

# Пробный прогон.
echo ""
echo "[dry-run] Первый пробный запуск:"
set -a; . /etc/sub-failover/env; set +a
/usr/local/sbin/sub-failover || true

echo ""
echo "✅ Cron установлен. Мониторинг:"
echo "     journalctl -t sub-failover -f"
echo ""
echo "Как проверить, что failover работает:"
echo "  1. На RF-1: sudo systemctl stop nginx"
echo "  2. Через 3 мин (3 fail × 1 мин): dig +short ${SUB_DOMAIN} → ${RF2_IP}"
echo "  3. На RF-1: sudo systemctl start nginx"
echo "  4. Через 5 мин (5 OK): dig +short ${SUB_DOMAIN} → ${RF1_IP}"
