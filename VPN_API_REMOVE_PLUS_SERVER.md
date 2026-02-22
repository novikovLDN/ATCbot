# VPN API: add POST /remove-plus/{uuid} (server-side)

Run on the VPN API server. Add the following endpoint to `/opt/vpn-api/main.py`.

## Endpoint behavior

- **Method/Path:** `POST /remove-plus/{uuid}`
- **Auth:** same as existing API (e.g. `x-api-key` header).
- **Action:** Remove `uuid` **only** from the plus-white2 inbound. Do **not** remove from the basic inbound.
- **Response:**
  - `200`: `{"status": "removed"}`
  - `404`: `{"status": "not_found"}` (uuid not in plus inbound — idempotent)

## Example implementation (FastAPI)

```python
# In /opt/vpn-api/main.py

@app.post("/remove-plus/{uuid:path}")
async def remove_plus(uuid: str):
    # 1. Validate x-api-key (reuse your existing dependency)
    # 2. Find plus-white2 inbound in Xray config
    # 3. Remove the client with this uuid from that inbound only (leave basic inbound unchanged)
    # 4. Apply config / reload if needed
    # 5. Return {"status": "removed"} or {"status": "not_found"}
    removed = await remove_uuid_from_plus_inbound_only(uuid)  # your internal helper
    return {"status": "removed" if removed else "not_found"}
```

## Commands to run on server

1. SSH to VPN API server.
2. Edit `/opt/vpn-api/main.py` and add the `POST /remove-plus/{uuid}` handler as above.
3. Restart the API service, e.g.:
   ```bash
   sudo systemctl restart vpn-api
   # or
   sudo systemctl restart your-vpn-api-service-name
   ```

No changes are required in the bot repository for this endpoint; the bot already calls `POST {XRAY_API_URL}/remove-plus/{uuid}` via `vpn_utils.remove_plus_inbound(uuid)`.
