#!/usr/bin/env bash
# scripts/install-wg.sh — quick WireGuard setup for the RF↔origin mesh.
#
# Устанавливает WireGuard, генерирует ключи, создаёт wg0.conf.
# Два режима:
#
#   sudo ROLE=origin \
#        WG_LISTEN_PORT=51820 \
#        bash install-wg.sh
#   → генерит ключи + wg0.conf с двумя peer-ами (RF-1, RF-2).
#     Ключи RF-1 и RF-2 подсунешь через ENV RF1_PUBKEY, RF2_PUBKEY (обязательно).
#     Печатает свой PUBKEY — понадобится при provision-е фронтов.
#
#   sudo ROLE=front \
#        ORIGIN_ENDPOINT=<origin-public-ip>:51820 \
#        ORIGIN_PUBKEY=<origin-pubkey> \
#        MY_WG_IP=10.8.0.2   # или 10.8.0.3 для RF-2
#        bash install-wg.sh
#   → генерит свои ключи + wg0.conf с одним peer-ом (origin).
#     Печатает свой PUBKEY — подсунь его в конфиг origin.
set -euo pipefail

ROLE="${ROLE:?ROLE required: origin | front}"

if [[ $EUID -ne 0 ]]; then echo "Run as root (sudo)." >&2; exit 1; fi

echo "[1/4] apt install wireguard"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard resolvconf >/dev/null

mkdir -p /etc/wireguard
umask 077
cd /etc/wireguard

echo "[2/4] Генерирую ключи (если ещё нет)"
[[ -f privkey ]] || wg genkey | tee privkey | wg pubkey > pubkey
MY_PRIVKEY=$(cat privkey)
MY_PUBKEY=$(cat pubkey)

case "$ROLE" in
  origin)
    RF1_PUBKEY="${RF1_PUBKEY:?RF1_PUBKEY required (paste RF-1 pubkey content)}"
    RF2_PUBKEY="${RF2_PUBKEY:?RF2_PUBKEY required}"
    WG_LISTEN_PORT="${WG_LISTEN_PORT:-51820}"
    echo "[3/4] Пишу /etc/wireguard/wg0.conf (origin)"
    cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.8.0.1/24
ListenPort = ${WG_LISTEN_PORT}
PrivateKey = ${MY_PRIVKEY}
# Приём пакетов от фронтов, форвардить не надо (только input на этот host).

[Peer]  # RF-1
PublicKey = ${RF1_PUBKEY}
AllowedIPs = 10.8.0.2/32

[Peer]  # RF-2
PublicKey = ${RF2_PUBKEY}
AllowedIPs = 10.8.0.3/32
EOF
    ;;
  front)
    ORIGIN_ENDPOINT="${ORIGIN_ENDPOINT:?ORIGIN_ENDPOINT required, e.g. 1.2.3.4:51820}"
    ORIGIN_PUBKEY="${ORIGIN_PUBKEY:?ORIGIN_PUBKEY required}"
    MY_WG_IP="${MY_WG_IP:?MY_WG_IP required, e.g. 10.8.0.2 for RF-1 or 10.8.0.3 for RF-2}"
    echo "[3/4] Пишу /etc/wireguard/wg0.conf (front, addr=${MY_WG_IP})"
    cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = ${MY_WG_IP}/24
PrivateKey = ${MY_PRIVKEY}
# ListenPort не задаём — фронт подключается наружу, port ephemeral.

[Peer]  # origin
PublicKey = ${ORIGIN_PUBKEY}
Endpoint = ${ORIGIN_ENDPOINT}
AllowedIPs = 10.8.0.1/32
PersistentKeepalive = 25
EOF
    ;;
  *)
    echo "FATAL: ROLE must be origin or front"; exit 1
    ;;
esac

chmod 600 /etc/wireguard/wg0.conf

echo "[4/4] Enable + start wg-quick@wg0"
systemctl enable wg-quick@wg0 >/dev/null
systemctl restart wg-quick@wg0
sleep 1
wg show

echo ""
echo "✅ WireGuard настроен (role=${ROLE})."
echo "   Мой pubkey: ${MY_PUBKEY}"
if [[ "$ROLE" == "origin" ]]; then
  echo "   Проверка (после подъёма фронтов): ping 10.8.0.2 && ping 10.8.0.3"
else
  echo "   Проверка: ping 10.8.0.1"
fi
