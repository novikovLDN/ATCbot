# VPN API server: rename link display names

Apply on the server in `/opt/vpn-api/main.py` (or wherever VLESS link names are set).

**Change 1 — basic link name (URL fragment / remark):**  
Replace any occurrence of the basic inbound display name from `"Atlas Secure"` to `"Atlas DE"`.

Example (exact pattern depends on your code):
- If you have a string like `"Atlas Secure"` or `remark="Atlas Secure"` or `#Atlas%20Secure` when building the basic VLESS link, change it to `"Atlas DE"` / `remark="Atlas DE"` / `#Atlas%20DE`.

**Change 2 — plus link name:**  
Keep `"White List"` as is (no change).

**Example sed (run on server after backup):**
```bash
# Backup
sudo cp /opt/vpn-api/main.py /opt/vpn-api/main.py.bak

# Replace Atlas Secure with Atlas DE in basic link generation (adjust pattern if your code differs)
sed -i 's/Atlas Secure/Atlas DE/g' /opt/vpn-api/main.py

# Restart service
sudo systemctl restart vpn-api
```
If only the basic link uses "Atlas Secure" and plus uses "White List", the above sed replaces only the basic name. If "Atlas Secure" appears in comments or plus code, refine the sed or edit manually so only the basic_link remark/fragment is changed.
