#!/bin/bash
# Xray Config Stabilization — Safe, non-destructive
# - Removes unsafe routing rules
# - Blocks ads via geosite
# - Routes all other traffic through VPN (direct outbound)
# - Full backup before any change
# - Validation before restart

set -e
CONFIG="/usr/local/etc/xray/config.json"

echo "=========================================="
echo "XRAY CONFIG STABILIZATION"
echo "=========================================="

# STEP 1 — Verify Xray binary
echo ""
echo "===== STEP 1 — VERIFY XRAY BINARY ====="
if ! command -v xray >/dev/null 2>&1; then
  echo "ERROR: xray binary not found"
  exit 1
fi
which xray
xray version

# STEP 2 — Create safe backup
echo ""
echo "===== STEP 2 — CREATE SAFE BACKUP ====="
if [ ! -f "$CONFIG" ]; then
  echo "ERROR: config.json not found at $CONFIG"
  exit 1
fi
BACKUP_PATH="/usr/local/etc/xray/config.json.BACKUP_$(date +%F_%H-%M-%S)"
cp "$CONFIG" "$BACKUP_PATH"
echo "Backup created at: $BACKUP_PATH"

# STEP 3 — Ensure jq is available
echo ""
echo "===== STEP 3 — CHECK JQ ====="
if ! command -v jq >/dev/null 2>&1; then
  echo "Installing jq (required for safe JSON patching)"
  apt-get update -y
  apt-get install -y jq
fi

# STEP 4 — Apply safe routing via jq
echo ""
echo "===== STEP 4 — APPLY ROUTING PATCH ====="
TMP_CONFIG="/usr/local/etc/xray/config.tmp.json"
jq '
  .routing = {
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      {
        "type": "field",
        "domain": ["geosite:category-ads-all"],
        "outboundTag": "block"
      },
      {
        "type": "field",
        "network": "tcp,udp",
        "outboundTag": "direct"
      }
    ]
  }
' "$CONFIG" > "$TMP_CONFIG"

# STEP 5 — Validate config before applying
echo ""
echo "===== STEP 5 — VALIDATE CONFIG ====="
VALIDATE_OUTPUT=$(xray -test -config "$TMP_CONFIG" 2>&1)
VALIDATE_EXIT=$?
if [ $VALIDATE_EXIT -ne 0 ]; then
  echo "ERROR: Config validation failed:"
  echo "$VALIDATE_OUTPUT"
  echo "Backup preserved at $BACKUP_PATH"
  rm -f "$TMP_CONFIG"
  exit 1
fi
echo "Validation OK"

# STEP 6 — Apply new config
echo ""
echo "===== STEP 6 — APPLY CONFIG ====="
mv "$TMP_CONFIG" "$CONFIG"
echo "Config applied"

# STEP 7 — Restart Xray
echo ""
echo "===== STEP 7 — RESTART XRAY ====="
systemctl restart xray
sleep 2
systemctl status xray --no-pager || true

# STEP 8 — Verify port 443
echo ""
echo "===== STEP 8 — VERIFY PORT 443 ====="
ss -tulpn | grep 443 || echo "Port 443 check: run manually if ss not available"

echo ""
echo "=========================================="
echo "STABILIZATION COMPLETE"
echo "=========================================="
echo "Backup path: $BACKUP_PATH"
echo "Xray status: $(systemctl is-active xray 2>/dev/null || echo 'unknown')"
echo "To restore: cp $BACKUP_PATH $CONFIG && systemctl restart xray"
