#!/bin/bash
# Secure VPN-API binding: Restrict to localhost (127.0.0.1:8000)
# NON-DESTRUCTIVE: Only modifies vpn-api service, not Xray or config.json

set -e
SERVICE_NAME="${1:-vpn-api}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

echo "=========================================="
echo "SECURE VPN-API BINDING"
echo "Service: $SERVICE_NAME"
echo "=========================================="

# STEP 1 — Check service exists
echo ""
echo "===== STEP 1 — CHECK SERVICE ====="
if [ ! -f "$SERVICE_PATH" ]; then
  echo "ERROR: Service file not found: $SERVICE_PATH"
  echo "Usage: sudo bash $0 [vpn-api|xray-api]"
  exit 1
fi
systemctl cat "$SERVICE_NAME" 2>/dev/null || true

# STEP 2 — Backup
echo ""
echo "===== STEP 2 — BACKUP ====="
BACKUP_PATH="${SERVICE_PATH}.backup.$(date +%F_%H-%M-%S)"
cp "$SERVICE_PATH" "$BACKUP_PATH"
echo "Backup created at: $BACKUP_PATH"

# STEP 3 — Replace 0.0.0.0 with 127.0.0.1
echo ""
echo "===== STEP 3 — EDIT BINDING ====="
if grep -q "\-\-host 0\.0\.0\.0" "$SERVICE_PATH"; then
  sed -i 's/--host 0\.0\.0\.0/--host 127.0.0.1/g' "$SERVICE_PATH"
  echo "Replaced --host 0.0.0.0 with --host 127.0.0.1"
elif grep -q "\-\-host 127\.0\.0\.1" "$SERVICE_PATH"; then
  echo "Already bound to 127.0.0.1 (no change needed)"
else
  echo "WARNING: No --host parameter found in ExecStart. Ensure uvicorn uses --host 127.0.0.1"
  echo "Current ExecStart:"
  grep -E "ExecStart|uvicorn" "$SERVICE_PATH" || true
fi

# STEP 4 — Reload systemd
echo ""
echo "===== STEP 4 — RELOAD SYSTEMD ====="
systemctl daemon-reload
echo "daemon-reload OK"

# STEP 5 — Restart vpn-api
echo ""
echo "===== STEP 5 — RESTART $SERVICE_NAME ====="
systemctl restart "$SERVICE_NAME"
sleep 2
systemctl status "$SERVICE_NAME" --no-pager || true

# STEP 6 — Verify port binding
echo ""
echo "===== STEP 6 — VERIFY PORT ====="
ss -tulpn 2>/dev/null | grep 8000 || netstat -tulpn 2>/dev/null | grep 8000 || echo "Run: ss -tulpn | grep 8000"

# STEP 7 — Health check
echo ""
echo "===== STEP 7 — HEALTH CHECK ====="
if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health | grep -q 200; then
  echo "Health check OK:"
  curl -s http://127.0.0.1:8000/health
else
  echo "WARNING: Health check failed. Verify service manually."
  curl -s http://127.0.0.1:8000/health || true
fi

echo ""
echo "=========================================="
echo "REPORT"
echo "=========================================="
echo "Backup path: $BACKUP_PATH"
echo "Service status: $(systemctl is-active $SERVICE_NAME 2>/dev/null || echo 'unknown')"
echo "To restore: sudo cp $BACKUP_PATH $SERVICE_PATH && sudo systemctl daemon-reload && sudo systemctl restart $SERVICE_NAME"
