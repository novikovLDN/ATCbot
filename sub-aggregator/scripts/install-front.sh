#!/usr/bin/env bash
# scripts/install-front.sh — provision an RF-1 or RF-2 front VPS.
#
# Устанавливает nginx (stream), рендерит front-stream.conf с реальным
# ORIGIN_WG_IP, открывает 443, включает автозапуск.
#
# WireGuard-туннель (wg0) должен быть уже поднят до этого — см.
# scripts/install-wg-client.sh (или ручная установка).
#
# Использование:
#   sudo ORIGIN_WG_IP=10.8.0.1 bash install-front.sh
#
# После запуска: проверить `curl --resolve subscription.palantirdns.uk:443:<этот IP>
# https://subscription.palantirdns.uk/healthz` — 200 (при живом origin).
set -euo pipefail

ORIGIN_WG_IP="${ORIGIN_WG_IP:?ORIGIN_WG_IP required, e.g. 10.8.0.1}"
ORIGIN_PORT="${ORIGIN_PORT:-443}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_SRC="$SCRIPT_DIR/../nginx/front-stream.conf"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

echo "[1/6] apt update + nginx + iproute2 + curl"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx iproute2 curl ufw >/dev/null

echo "[2/6] Проверяю что nginx собран со stream-модулем"
if ! nginx -V 2>&1 | grep -q -- '--with-stream'; then
  echo "FATAL: nginx без --with-stream. Установи nginx-full или nginx-extras."
  exit 1
fi

echo "[3/6] WireGuard connectivity check"
if ! ping -c 1 -W 3 "$ORIGIN_WG_IP" >/dev/null 2>&1; then
  echo "WARN: не удалось пингануть $ORIGIN_WG_IP — WG-туннель не поднят?"
  echo "     Если WG ещё не настроен, поправь позже и перезапусти nginx."
fi

echo "[4/6] Устанавливаю /etc/nginx/nginx.conf (замена)"
if [[ ! -f "$CONF_SRC" ]]; then
  echo "FATAL: не найден $CONF_SRC"; exit 1
fi
BACKUP="/etc/nginx/nginx.conf.pre-sub-front.$(date +%s).bak"
[[ -f /etc/nginx/nginx.conf ]] && cp /etc/nginx/nginx.conf "$BACKUP" && \
  echo "     Оригинал сохранён в $BACKUP"

sed -e "s|ORIGIN_WG_IP|${ORIGIN_WG_IP}|g" \
    -e "s|ORIGIN_PORT|${ORIGIN_PORT}|g" \
    "$CONF_SRC" > /etc/nginx/nginx.conf

echo "[5/6] nginx -t + перезапуск"
nginx -t
systemctl enable nginx >/dev/null
systemctl restart nginx

echo "[6/6] ufw allow 443/tcp"
ufw allow 443/tcp >/dev/null || true

echo ""
echo "✅ Front провижн завершён."
echo "   IP этого сервера отдавай в DNS A-record или в failover-скрипт."
echo "   Проверка (с внешней машины):"
echo "     curl -sI --resolve subscription.palantirdns.uk:443:\$(curl -s ifconfig.me) https://subscription.palantirdns.uk/healthz"
echo "     ожидается: HTTP/1.1 200 OK (или 404 если origin/сервис ещё не поднят)"
