# Secure VPN-API Binding

**Objective:** Restrict vpn-api (uvicorn) to listen only on localhost (127.0.0.1:8000).

## Why

- **Security:** API must not be exposed to the network; access via Cloudflare Tunnel or SSH tunnel only
- **Attack surface:** 0.0.0.0:8000 exposes API to anyone on the network

## Pre-requisites

- vpn-api or xray-api systemd service
- Root/sudo on server

## Steps

### 1. Check current binding

```bash
systemctl cat vpn-api.service
ss -tulpn | grep 8000
```

If `0.0.0.0:8000` → needs change.

### 2. Backup and secure

```bash
sudo bash scripts/secure_vpn_api_binding.sh vpn-api
```

Or manually:

```bash
SERVICE_PATH="/etc/systemd/system/vpn-api.service"
BACKUP_PATH="${SERVICE_PATH}.backup.$(date +%F_%H-%M-%S)"
sudo cp $SERVICE_PATH $BACKUP_PATH
sudo sed -i 's/--host 0\.0\.0\.0/--host 127.0.0.1/g' $SERVICE_PATH
sudo systemctl daemon-reload
sudo systemctl restart vpn-api
```

### 3. Verify

```bash
ss -tulpn | grep 8000
# Expected: 127.0.0.1:8000

curl http://127.0.0.1:8000/health
# Expected: {"status":"ok"}
```

### 4. Restore (if needed)

```bash
sudo cp /etc/systemd/system/vpn-api.service.backup.YYYY-MM-DD_HH-MM-SS /etc/systemd/system/vpn-api.service
sudo systemctl daemon-reload
sudo systemctl restart vpn-api
```

## Service template

See `systemd/vpn-api.service` — uses `--host 127.0.0.1` by default.
