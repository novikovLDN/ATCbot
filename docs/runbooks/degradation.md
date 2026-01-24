# Degradation Playbook

This document provides runbooks for handling degradation in each failure domain.

## Overview

Each failure domain has:
- **Symptoms**: How to detect the issue
- **Automatic Behavior**: What the system does automatically
- **Operator Actions**: What you need to do
- **Rollback Steps**: How to revert changes
- **Data Safety Guarantees**: What data is safe

---

## Database Failure

### Symptoms

- Health check shows: `PostgreSQL подключение: Ошибка`
- Logs show: `[UNAVAILABLE] system_state — skipping iteration`
- System state: `database.status = UNAVAILABLE`
- Background workers skipping iterations
- Handlers returning errors for DB operations

### Automatic Behavior

1. System enters **UNAVAILABLE** state
2. Background workers skip iterations (logged with `[UNAVAILABLE]`)
3. Handlers continue but cannot process requests requiring DB
4. Cooldown activates after recovery (60 seconds)
5. Warm-up iterations start automatically (3 iterations with reduced load)
6. Normal operation resumes after warm-up

### Operator Actions

1. **Check Database Connection Pool Status**
   ```bash
   # Check if pool is created
   # Review logs for: "Database connection pool created"
   ```

2. **Verify PostgreSQL Service Health**
   ```bash
   # Check PostgreSQL service status
   systemctl status postgresql
   # Or check Docker container
   docker ps | grep postgres
   ```

3. **Check Network Connectivity**
   ```bash
   # Test connection to database
   psql -h <host> -U <user> -d <database> -c "SELECT 1"
   ```

4. **Review Database Logs**
   ```bash
   # Check PostgreSQL logs for errors
   tail -f /var/log/postgresql/postgresql.log
   ```

5. **Restart Database Service if Needed**
   ```bash
   # Only if service is down
   systemctl restart postgresql
   # Or
   docker restart <postgres_container>
   ```

6. **Verify Connection Pool Recovery**
   - Check logs for: `[RECOVERY] component=database recovered`
   - Verify health check returns: `PostgreSQL подключение: OK`
   - Monitor for warm-up iterations: `[RECOVERY] warm-up iteration started`

### Rollback Steps

1. **Restore from Backup if Data Corruption Detected**
   ```bash
   # Stop application
   # Restore database from backup
   pg_restore -d <database> <backup_file>
   # Verify data integrity
   # Restart application
   ```

2. **Rollback Database Schema Changes if Applicable**
   ```bash
   # Review migration history
   # Rollback specific migration if needed
   # Verify schema integrity
   ```

3. **Verify Data Integrity After Restore**
   - Check critical tables
   - Verify subscription data
   - Verify payment records
   - Check user data consistency

### Data Safety Guarantees

- ✅ **No data loss during graceful degradation**
- ✅ **Pending operations queued in memory (non-persistent)**
- ✅ **Committed transactions are safe**
- ⚠️ **Uncommitted transactions may be lost**

### MTTD (Max Tolerated Downtime)

**30 minutes**

---

## VPN API Failure

### Symptoms

- Health check shows: `VPN API: Ошибка`
- Logs show: `[DEGRADED] system_state detected`
- System state: `vpn_api.status = DEGRADED`
- VPN key generation fails
- Subscriptions created with `activation_status = 'pending'`

### Automatic Behavior

1. System enters **DEGRADED** state
2. VPN API calls fail gracefully (no crashes)
3. Subscriptions created with `activation_status = 'pending'`
4. Activation worker retries automatically (max 3 attempts)
5. Users can still use existing VPN keys
6. System recovers automatically when VPN API is available

### Operator Actions

1. **Check VPN API Endpoint Health**
   ```bash
   # Test VPN API endpoint
   curl -X GET <XRAY_API_URL>/health
   ```

2. **Verify Xray Core Service Status**
   ```bash
   # Check Xray Core service
   systemctl status xray
   # Or check Docker container
   docker ps | grep xray
   ```

3. **Check Network Connectivity to VPN Server**
   ```bash
   # Test connectivity
   ping <vpn_server_ip>
   telnet <vpn_server_ip> <port>
   ```

4. **Review VPN API Logs**
   ```bash
   # Check Xray Core logs
   tail -f /var/log/xray/access.log
   tail -f /var/log/xray/error.log
   ```

5. **Restart Xray Core if Needed**
   ```bash
   # Only if service is down
   systemctl restart xray
   # Or
   docker restart <xray_container>
   ```

### Rollback Steps

1. **Revert VPN API Configuration Changes**
   - Review recent configuration changes
   - Revert to last known good configuration
   - Restart Xray Core

2. **Restore Xray Core from Backup if Needed**
   - Stop Xray Core
   - Restore configuration from backup
   - Restart Xray Core
   - Verify VPN API health

### Data Safety Guarantees

- ✅ **Existing VPN keys remain functional**
- ✅ **New subscriptions are created but not activated**
- ✅ **No user data loss**
- ✅ **Activation retries automatically after recovery**

### MTTD (Max Tolerated Downtime)

**60 minutes**

---

## Payment Provider Failure

### Symptoms

- Payment provider API calls fail
- Logs show: `Payment provider error`
- Payment status: `pending` (not finalized)
- Users see payment pending status

### Automatic Behavior

1. Payment provider calls fail gracefully
2. Payment status tracked but not finalized
3. Retries bounded (max 2 retries) and logged
4. Users see payment pending status
5. Idempotency prevents double-processing

### Operator Actions

1. **Check Payment Provider API Status**
   - Visit payment provider status page
   - Check for known outages

2. **Verify Payment Provider Credentials**
   - Review payment provider configuration
   - Verify API keys are valid

3. **Review Payment Logs for Errors**
   ```bash
   # Check application logs for payment errors
   grep -i "payment" /var/log/app.log
   ```

4. **Check Network Connectivity**
   ```bash
   # Test connectivity to payment provider
   curl -X GET <payment_provider_api_url>
   ```

5. **Contact Payment Provider Support if Needed**
   - Report issue to payment provider
   - Request status update
   - Follow up on resolution

### Rollback Steps

1. **Revert Payment Provider Configuration**
   - Review recent configuration changes
   - Revert to last known good configuration
   - Restart application if needed

2. **Reconcile Payment Status Manually if Needed**
   - Review pending payments
   - Manually finalize payments if provider confirms
   - Update payment status in database

### Data Safety Guarantees

- ✅ **Payment records are created but not finalized**
- ✅ **No duplicate charges**
- ✅ **Idempotency prevents double-processing**
- ✅ **Manual reconciliation possible after recovery**

### MTTD (Max Tolerated Downtime)

**15 minutes**

---

## Telegram API Failure

### Symptoms

- Telegram API calls fail
- Logs show: `Telegram API error`
- Messages not delivered to users
- Bot responses delayed or missing

### Automatic Behavior

1. Telegram API calls fail gracefully
2. Messages queued in memory (non-persistent)
3. System continues processing business logic
4. Retries bounded and logged

### Operator Actions

1. **Check Telegram Bot API Status**
   - Visit Telegram Bot API status page
   - Check for known outages

2. **Verify Bot Token Validity**
   - Review bot token configuration
   - Verify token is not expired

3. **Check Network Connectivity**
   ```bash
   # Test connectivity to Telegram API
   curl -X GET https://api.telegram.org/bot<token>/getMe
   ```

4. **Review Telegram API Rate Limits**
   - Check if rate limits are exceeded
   - Wait for rate limit reset if needed

### Rollback Steps

1. **Revert Bot Token Changes if Applicable**
   - Review recent configuration changes
   - Revert to last known good token
   - Restart application if needed

### Data Safety Guarantees

- ✅ **No data loss**
- ✅ **Messages may be delayed but not lost**
- ✅ **System continues processing business logic**

### MTTD (Max Tolerated Downtime)

**10 minutes**

---

## Background Workers Failure

### Symptoms

- Background workers skipping iterations
- Logs show: `[UNAVAILABLE] system_state — skipping iteration`
- Subscriptions not activating
- Expired subscriptions not cleaned up
- Payment watching not working

### Automatic Behavior

1. Workers skip iterations during system unavailability
2. Workers resume automatically after recovery
3. Warm-up iterations prevent overload (3 iterations with reduced load)
4. Cooldown prevents thrashing (60 seconds)

### Operator Actions

1. **Check Worker Process Status**
   ```bash
   # Check if worker processes are running
   ps aux | grep python | grep worker
   ```

2. **Review Worker Logs for Errors**
   ```bash
   # Check worker logs
   tail -f /var/log/app/worker.log
   ```

3. **Verify System State Transitions**
   - Check system state: `system_state.is_unavailable`
   - Verify component statuses
   - Check for stuck state

4. **Check for Stuck Iterations**
   - Review logs for stuck iterations
   - Check for infinite loops
   - Verify retry counts

### Rollback Steps

1. **Restart Worker Processes if Needed**
   ```bash
   # Restart worker processes
   systemctl restart app-worker
   # Or
   docker restart <worker_container>
   ```

2. **Clear Stuck State if Applicable**
   - Review stuck state
   - Clear if safe to do so
   - Restart workers

### Data Safety Guarantees

- ✅ **No data loss**
- ✅ **Delayed processing only**
- ✅ **All operations eventually processed**
- ✅ **Idempotency ensures correctness**

### MTTD (Max Tolerated Downtime)

**5 minutes**

---

## General Recovery Checklist

After any degradation:

1. ✅ Verify system state is HEALTHY
2. ✅ Check all health checks pass
3. ✅ Verify background workers resumed
4. ✅ Check for any stuck operations
5. ✅ Review logs for errors
6. ✅ Verify data integrity
7. ✅ Monitor for 10 minutes to ensure stability
