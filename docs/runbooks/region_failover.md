# Region Failover Policy

This document defines the region failover policy for multi-region architecture.

## Overview

**IMPORTANT:**
- ⚠️ **Failover is NOT automatic** - Requires operator decision
- ⚠️ **Failover is manual** - Operator initiates and controls
- ⚠️ **No automatic region switching** - All switches are explicit

---

## Region Configuration

### Regions

- **EU** (eu): Europe region
- **US** (us): United States region
- **ASIA** (asia): Asia region
- **FALLBACK** (fallback): Fallback region (default)

### Region Detection

Regions are determined by:
- Environment variable: `REGION=eu|us|asia|fallback`
- Primary region: `PRIMARY_REGION=eu|us|asia|fallback`
- Secondary regions: `SECONDARY_REGIONS=us,asia` (comma-separated)

---

## When to Failover

### Primary Region Failure

**Conditions:**
- Primary region unavailable for > 30 minutes
- Database unreachable
- Critical services down
- Operator decision required

**Decision Criteria:**
- Impact assessment (users affected, revenue impact)
- Recovery time estimate
- Data consistency requirements
- Operator approval

### Secondary Region Failure

**Conditions:**
- Secondary region unavailable
- No immediate action required (secondary is backup)
- Monitor for recovery

---

## Failover Process

### Pre-Failover Checklist

- [ ] Primary region confirmed unavailable
- [ ] Impact assessment completed
- [ ] Recovery time estimate > 30 minutes
- [ ] Operator approval obtained
- [ ] Secondary region verified healthy
- [ ] Data consistency verified
- [ ] Rollback plan prepared

### Failover Steps

1. **Verify Secondary Region Health**
   ```bash
   # Check secondary region health
   curl https://<secondary-region>/health
   # Expected: {"status": "ok", "db_ready": true}
   ```

2. **Update Primary Region Configuration**
   ```bash
   # Set new primary region
   export PRIMARY_REGION=<secondary-region>
   # Update deployment configuration
   ```

3. **Verify Region Configuration**
   ```bash
   # Check region status
   # Expected: is_primary=True for new primary
   ```

4. **Enable Write Operations**
   - Verify database is writable
   - Verify services are operational
   - Verify background workers running

5. **Monitor for Stability**
   - Monitor for 10 minutes
   - Check for errors
   - Verify system state is HEALTHY

### Post-Failover Verification

- [ ] System state is HEALTHY
- [ ] Database connection successful
- [ ] Services operational
- [ ] Background workers running
- [ ] No errors in logs
- [ ] User traffic routing correctly
- [ ] Metrics being collected

---

## Read-Only Mode

### When to Enable Read-Only

**Conditions:**
- Primary region degraded but not unavailable
- Data consistency concerns
- Operator decision

### Read-Only Behavior

**Services:**
- ✅ Read operations: Enabled
- ❌ Write operations: Disabled
- ✅ Background workers: Read-only (no writes)
- ✅ Handlers: Read-only responses

**Data:**
- ✅ User data: Readable
- ✅ Subscription data: Readable
- ✅ Payment data: Readable
- ❌ New subscriptions: Disabled
- ❌ Payment processing: Disabled
- ❌ Admin actions: Disabled

---

## Write-Disabled Mode

### When to Enable Write-Disabled

**Conditions:**
- Primary region unavailable
- Failover in progress
- Data consistency concerns

### Write-Disabled Behavior

**Services:**
- ✅ Read operations: Enabled
- ❌ Write operations: Disabled
- ❌ Background workers: Disabled
- ✅ Handlers: Read-only responses

**Data:**
- ✅ All reads: Enabled
- ❌ All writes: Disabled

---

## Failback Process

### When to Failback

**Conditions:**
- Primary region recovered
- Health verified for > 30 minutes
- Data consistency verified
- Operator decision required

### Failback Steps

1. **Verify Primary Region Health**
   ```bash
   # Check primary region health
   curl https://<primary-region>/health
   # Expected: {"status": "ok", "db_ready": true}
   ```

2. **Verify Data Consistency**
   - Check database integrity
   - Verify no data loss
   - Verify no duplicate data

3. **Update Primary Region Configuration**
   ```bash
   # Restore original primary region
   export PRIMARY_REGION=<original-primary-region>
   # Update deployment configuration
   ```

4. **Gradual Traffic Migration**
   - Start with read traffic
   - Verify stability
   - Migrate write traffic
   - Monitor for issues

5. **Monitor for Stability**
   - Monitor for 30 minutes
   - Check for errors
   - Verify system state is HEALTHY

### Post-Failback Verification

- [ ] System state is HEALTHY
- [ ] Database connection successful
- [ ] Services operational
- [ ] Background workers running
- [ ] No errors in logs
- [ ] User traffic routing correctly
- [ ] Metrics being collected
- [ ] Data consistency verified

---

## Data Locality Rules

### Global Data

**Users:**
- Source of truth: Primary region
- Replication: Eventual consistency
- Access: All regions can read

**Subscriptions:**
- Source of truth: Primary region
- Replication: Eventual consistency
- Access: All regions can read

**Payments:**
- Source of truth: Primary region
- Replication: Eventual consistency
- Access: All regions can read

### Region-Local Data

**Logs:**
- Storage: Region-local
- Retention: 90 days
- Access: Region-local only

**Metrics:**
- Storage: Region-local
- Retention: 30 days
- Access: Region-local only

**Analytics:**
- Storage: Region-local
- Replication: Async to global
- Access: Region-local + global (async)

---

## Emergency Procedures

### Complete Region Loss

**If entire region is lost:**

1. **Immediate Actions:**
   - Verify region is truly unavailable
   - Check network connectivity
   - Verify service status

2. **Failover Decision:**
   - Assess impact
   - Choose secondary region
   - Initiate failover

3. **Recovery:**
   - Follow failover steps
   - Monitor for stability
   - Verify data consistency

### Data Corruption

**If data corruption detected:**

1. **Immediate Actions:**
   - Stop writes immediately
   - Enable read-only mode
   - Assess corruption scope

2. **Recovery:**
   - Restore from backup
   - Verify data integrity
   - Resume operations

---

## Notes

- ⚠️ **No automatic failover** - All failovers are manual
- ⚠️ **Operator approval required** - No automatic decisions
- ⚠️ **Data consistency first** - Failover only if safe
- ⚠️ **Monitor always** - Continuous monitoring during failover
- ⚠️ **Document everything** - All failover operations documented
