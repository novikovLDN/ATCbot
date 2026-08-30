#!/usr/bin/env bash
# scripts/dns-failover.sh — DNS active/passive failover for the aggregator.
#
# Cron every minute on a third, independent host (NOT on RF-1 / RF-2 / origin).
# Probes https://<SUB_DOMAIN>/healthz through curl --resolve so we test the
# full stream + TLS + aggregator chain, not just TCP.
#
# Behaviour (per FR §6.4):
#   - 3 consecutive RF-1 failures + RF-2 alive → switch A record to RF-2.
#   - 5 consecutive RF-1 successes while on RF-2 → switch back to RF-1.
#   - State file at $STATE_FILE keeps consecutive counters + active front.
#   - Cloudflare DNS-only (proxied:false, ttl:60) via API v4.
#
# ENV (required):
#   SUB_DOMAIN         — apex + subdomain, e.g. sub.atlassecure.io
#   RF1_IP             — active front
#   RF2_IP             — backup front
#   CF_ZONE_ID         — Cloudflare zone containing SUB_DOMAIN
#   CF_RECORD_ID       — DNS record id for SUB_DOMAIN (A record)
#   CF_API_TOKEN       — token with Zone.DNS:Edit for CF_ZONE_ID
#
# ENV (optional):
#   STATE_FILE=/var/lib/sub-failover/state   — must be writable
#   PROBE_TIMEOUT=5    — curl timeout in seconds
#   FAIL_THRESHOLD=3   — switch to RF-2 after this many consecutive fails
#   HEAL_THRESHOLD=5   — switch back to RF-1 after this many consecutive OKs
#   LOG_TAG=sub-failover  — syslog tag
#
# Exit code is always 0 unless the state file is unreachable — we don't
# want cron to spam on transient CF/network issues.
set -uo pipefail

SUB_DOMAIN="${SUB_DOMAIN:?SUB_DOMAIN required}"
RF1_IP="${RF1_IP:?RF1_IP required}"
RF2_IP="${RF2_IP:?RF2_IP required}"
CF_ZONE_ID="${CF_ZONE_ID:?CF_ZONE_ID required}"
CF_RECORD_ID="${CF_RECORD_ID:?CF_RECORD_ID required}"
CF_API_TOKEN="${CF_API_TOKEN:?CF_API_TOKEN required}"
STATE_FILE="${STATE_FILE:-/var/lib/sub-failover/state}"
PROBE_TIMEOUT="${PROBE_TIMEOUT:-5}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-3}"
HEAL_THRESHOLD="${HEAL_THRESHOLD:-5}"
LOG_TAG="${LOG_TAG:-sub-failover}"

log() { logger -t "$LOG_TAG" -- "$*"; echo "[$(date -Is)] $*"; }

mkdir -p "$(dirname "$STATE_FILE")" || {
  log "FATAL cannot create state dir $(dirname "$STATE_FILE")"; exit 1; }

# --- load state (defaults on first run) ---
ACTIVE=RF1
CONS_FAIL_RF1=0
CONS_OK_RF1=0
if [[ -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  . "$STATE_FILE"
fi

probe() {
  # $1 = IP to resolve SUB_DOMAIN to. Returns 0 on 200, non-zero otherwise.
  local ip="$1"
  local code
  code=$(curl -sS -o /dev/null -m "$PROBE_TIMEOUT" \
    --resolve "${SUB_DOMAIN}:443:${ip}" \
    -w "%{http_code}" \
    "https://${SUB_DOMAIN}/healthz" 2>/dev/null || echo "000")
  [[ "$code" == "200" ]]
}

save_state() {
  cat > "$STATE_FILE" <<EOF
ACTIVE=$ACTIVE
CONS_FAIL_RF1=$CONS_FAIL_RF1
CONS_OK_RF1=$CONS_OK_RF1
EOF
}

flip_dns_to() {
  # $1 = IP
  local target_ip="$1"
  local body
  body=$(printf '{"type":"A","name":"%s","content":"%s","ttl":60,"proxied":false}' \
    "$SUB_DOMAIN" "$target_ip")
  local resp http_code
  resp=$(mktemp)
  http_code=$(curl -sS -o "$resp" -w "%{http_code}" \
    -X PUT "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/${CF_RECORD_ID}" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$body")
  if [[ "$http_code" == "200" ]]; then
    log "DNS updated: ${SUB_DOMAIN} -> ${target_ip}"
    rm -f "$resp"
    return 0
  fi
  log "DNS update FAILED http=$http_code body=$(head -c 400 "$resp")"
  rm -f "$resp"
  return 1
}

# --- probe both, decide, act ---
RF1_OK=0; probe "$RF1_IP" && RF1_OK=1
RF2_OK=0; probe "$RF2_IP" && RF2_OK=1

if [[ "$RF1_OK" -eq 1 ]]; then
  CONS_FAIL_RF1=0
  CONS_OK_RF1=$((CONS_OK_RF1 + 1))
else
  CONS_OK_RF1=0
  CONS_FAIL_RF1=$((CONS_FAIL_RF1 + 1))
fi

log "probe rf1=$RF1_OK rf2=$RF2_OK active=$ACTIVE cons_fail_rf1=$CONS_FAIL_RF1 cons_ok_rf1=$CONS_OK_RF1"

if [[ "$ACTIVE" == "RF1" && "$CONS_FAIL_RF1" -ge "$FAIL_THRESHOLD" && "$RF2_OK" -eq 1 ]]; then
  if flip_dns_to "$RF2_IP"; then
    ACTIVE=RF2
  fi
elif [[ "$ACTIVE" == "RF2" && "$CONS_OK_RF1" -ge "$HEAL_THRESHOLD" ]]; then
  if flip_dns_to "$RF1_IP"; then
    ACTIVE=RF1
  fi
fi

save_state
exit 0
