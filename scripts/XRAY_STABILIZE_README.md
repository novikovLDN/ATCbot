# Xray Config Stabilization

**Safe, reversible script for Xray routing stabilization.**

## Requirements

- Run on the **VPN server** (Ubuntu 22.04)
- Root or sudo
- Xray installed at `/usr/local/etc/xray/config.json`
- Config must have outbounds with tags: `direct`, `block`

## What it does

1. Creates timestamped backup
2. Replaces routing section with minimal rules:
   - Ads → block (geosite:category-ads-all)
   - All TCP/UDP → direct (VPN path)
3. Validates config with `xray -test` before applying
4. Restarts Xray only if validation passes
5. Does NOT change: port, REALITY keys, inbound, client UUIDs

## How to run

```bash
# Copy to server
scp scripts/xray_config_stabilize.sh user@vpn-server:/tmp/

# Run on server (requires root)
ssh user@vpn-server
sudo bash /tmp/xray_config_stabilize.sh
```

## Restore from backup

```bash
cp /usr/local/etc/xray/config.json.BACKUP_YYYY-MM-DD_HH-MM-SS /usr/local/etc/xray/config.json
systemctl restart xray
```

## Report template

After run, fill in:
- Backup path: (from script output)
- Xray status: (active/inactive)
- Port check: (ss -tulpn | grep 443)
- Errors (if any):
