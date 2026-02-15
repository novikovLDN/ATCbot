# VPN Server Infrastructure Audit

**Non-destructive, read-only audit for Ubuntu 22.04 VPN servers.**

## How to Run

1. Copy the script to your VPN server:
   ```bash
   scp scripts/vpn_server_audit.sh user@your-vpn-server:/tmp/
   ```

2. SSH to the server and run:
   ```bash
   ssh user@your-vpn-server
   bash /tmp/vpn_server_audit.sh
   ```

3. Output is saved to `/tmp/vpn_audit_YYYYMMDD_HHMMSS.txt` and printed to stdout.

## Project Integration (ATCS Bot)

Based on the codebase, the bot expects:

| Component | Details |
|-----------|---------|
| **VPN type** | Xray Core (VLESS + REALITY) |
| **Config path** | `/usr/local/etc/xray/config.json` (or `XRAY_CONFIG_PATH` env) |
| **Xray API** | FastAPI on 127.0.0.1:8000 (or behind Cloudflare Tunnel) |
| **Port** | 443 (VLESS) |
| **Fallback** | `xray_manager.create_vless_user()` uses SSH + paramiko to modify config |

## Expected Audit Findings

- **Xray package**: `xray` or `v2ray-core`
- **Service**: `xray` or `v2ray`
- **Listen ports**: 443 (VLESS)
- **Config**: JSON with `inbounds` → VLESS protocol, `settings.clients`

## Final Report Template

After running the audit on the server, fill in:

```
- Detected VPN type:
- Listening ports:
- Client connection method:
- Firewall status:
- Autostart services:
- Potential issues:
- Recommended next step (no changes yet).
```
