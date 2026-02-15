#!/bin/bash
# VPN Server Infrastructure Audit (Ubuntu 22.04)
# NON-DESTRUCTIVE: Read-only. No config changes, no restarts.
# Run on the target VPN server: bash vpn_server_audit.sh

set -e
AUDIT_LOG="/tmp/vpn_audit_$(date +%Y%m%d_%H%M%S).txt"
exec > >(tee -a "$AUDIT_LOG") 2>&1

echo "=========================================="
echo "VPN SERVER AUDIT - $(date -Iseconds)"
echo "=========================================="

echo ""
echo "===== STEP 1 — SYSTEM INFO ====="
uname -a
lsb_release -a 2>/dev/null || cat /etc/os-release
uptime
whoami

echo ""
echo "===== STEP 2 — INSTALLED VPN PACKAGES ====="
dpkg -l | grep -E "wireguard|openvpn|xray|v2ray|sing-box|shadowsocks|ocserv|softether|strongswan|wg" || true

echo ""
echo "===== RUNNING SERVICES ====="
systemctl list-units --type=service | grep -E "vpn|wireguard|wg|openvpn|xray|v2ray|sing|shadow|ipsec|ocserv" || true

echo ""
echo "===== STEP 3 — ACTIVE NETWORK PORTS ====="
ss -tulpn

echo ""
echo "===== STEP 4 — FIREWALL STATE ====="
echo "--- UFW STATUS ---"
ufw status verbose 2>/dev/null || echo "UFW not available"

echo ""
echo "--- IPTABLES RULES ---"
iptables -L -n -v 2>/dev/null || true
iptables -t nat -L -n -v 2>/dev/null || true

echo ""
echo "===== STEP 5 — NETWORK CONFIGURATION ====="
echo "--- IP ADDRESSES ---"
ip a

echo ""
echo "--- ROUTING TABLE ---"
ip route

echo ""
echo "===== STEP 6 — VPN CONFIG LOCATIONS ====="
echo "--- WIREGUARD ---"
ls -la /etc/wireguard 2>/dev/null || echo "/etc/wireguard not found"
cat /etc/wireguard/*.conf 2>/dev/null || true

echo ""
echo "--- OPENVPN ---"
ls -la /etc/openvpn 2>/dev/null || echo "/etc/openvpn not found"
ls -la /etc/openvpn/server 2>/dev/null || true
cat /etc/openvpn/server/*.conf 2>/dev/null || true

echo ""
echo "--- XRAY / V2RAY ---"
ls -la /etc/xray 2>/dev/null || echo "/etc/xray not found"
cat /etc/xray/config.json 2>/dev/null || true

ls -la /usr/local/etc/xray 2>/dev/null || echo "/usr/local/etc/xray not found"
cat /usr/local/etc/xray/config.json 2>/dev/null || true

ls -la /etc/v2ray 2>/dev/null || echo "/etc/v2ray not found"
cat /etc/v2ray/config.json 2>/dev/null || true

echo ""
echo "--- SING-BOX ---"
ls -la /etc/sing-box 2>/dev/null || echo "/etc/sing-box not found"
cat /etc/sing-box/config.json 2>/dev/null || true

echo ""
echo "===== STEP 7 — ENABLED SERVICES ====="
systemctl list-unit-files | grep enabled

echo ""
echo "===== STEP 8 — RECENT VPN LOGS ====="
journalctl -u wg-quick@wg0 --no-pager -n 50 2>/dev/null || true
journalctl -u openvpn-server@server --no-pager -n 50 2>/dev/null || true
journalctl -u xray --no-pager -n 50 2>/dev/null || true
journalctl -u v2ray --no-pager -n 50 2>/dev/null || true
journalctl -u sing-box --no-pager -n 50 2>/dev/null || true

echo ""
echo "===== STEP 9 — BOT/PROJECT FILES ====="
echo "--- HOME ---"
ls -la ~

echo ""
echo "--- BOT DIRECTORIES ---"
find ~ -maxdepth 2 -type d -iname "*bot*" 2>/dev/null || true

echo ""
echo "=========================================="
echo "AUDIT COMPLETE. Log saved to: $AUDIT_LOG"
echo "=========================================="
