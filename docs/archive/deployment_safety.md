# Deployment Safety

This document defines deployment safety procedures and verification hooks.

## Deployment Marker

### Version Tracking

**Deployment Marker Format:**
```json
{
  "version": "1.2.3",
  "timestamp": "2024-01-15T10:30:00Z",
  "commit": "abc123def456",
  "environment": "prod",
  "deployed_by": "engineer@example.com"
}
```

**Storage:**
- File: `/app/.deployment_marker`
- Database: `deployment_history` table (optional)
- Git tag: `v1.2.3`

### Deployment Marker Creation

**Automated:**
- Created during deployment
- Includes version, timestamp, commit, environment
- Stored in application directory

**Manual:**
- Created before manual deployments
- Verified after deployment
- Documented in deployment log

---

## Rollback Safety Checklist

### Pre-Deployment

- [ ] Code reviewed and approved
- [ ] Tests passing (unit, integration)
- [ ] Staging deployment successful
- [ ] Database migrations tested
- [ ] Configuration changes reviewed
- [ ] Dependencies updated and tested
- [ ] Rollback plan documented
- [ ] Deployment marker prepared

### During Deployment

- [ ] Deployment started
- [ ] Deployment marker created
- [ ] Application started
- [ ] Health checks passing
- [ ] Database migrations applied (if any)
- [ ] Configuration loaded
- [ ] Background workers started

### Post-Deployment

- [ ] Health checks passing
- [ ] Database connection verified
- [ ] Background workers running
- [ ] No errors in logs
- [ ] System state is HEALTHY
- [ ] Metrics normal
- [ ] User-facing functionality tested
- [ ] Deployment marker verified

---

## Post-Deploy Health Verification Hooks

### Automatic Verification

**Health Check Hook:**
```python
async def post_deploy_health_check():
    """Post-deployment health verification"""
    checks = [
        check_database_connection(),
        check_connection_pool(),
        check_vpn_api(),
        check_system_state(),
    ]
    
    results = await asyncio.gather(*checks)
    
    if all(results):
        logger.info("Post-deploy health check: PASSED")
        return True
    else:
        logger.error("Post-deploy health check: FAILED")
        return False
```

**Verification Steps:**
1. Database connection: ✅
2. Connection pool: ✅
3. VPN API: ✅
4. System state: ✅
5. Background workers: ✅
6. Metrics collection: ✅

### Manual Verification

**Checklist:**
- [ ] Health endpoint returns 200
- [ ] Database queries work
- [ ] VPN API calls work
- [ ] Payment processing works
- [ ] User registration works
- [ ] Subscription creation works
- [ ] Admin functions work
- [ ] Background workers processing
- [ ] No errors in logs
- [ ] Metrics being collected

---

## Rollback Procedures

### Code Rollback

**Steps:**
1. Identify target version (previous stable version)
2. Checkout target version: `git checkout <tag>`
3. Deploy previous version
4. Verify deployment marker updated
5. Verify health checks pass
6. Monitor for stability

### Database Rollback

**Steps:**
1. Review migration history
2. Identify rollback migration
3. Apply rollback migration
4. Verify data integrity
5. Verify application functionality
6. Monitor for issues

### Configuration Rollback

**Steps:**
1. Identify previous configuration
2. Restore configuration file
3. Restart application
4. Verify configuration loaded
5. Verify health checks pass
6. Monitor for stability

---

## Deployment Safety Rules

### Never Deploy

- ❌ Without code review
- ❌ Without tests passing
- ❌ Without staging verification
- ❌ Without rollback plan
- ❌ During peak hours (if avoidable)
- ❌ Without deployment marker

### Always Deploy

- ✅ With deployment marker
- ✅ With health verification
- ✅ With rollback plan ready
- ✅ With monitoring active
- ✅ With on-call engineer available

---

## Deployment Verification

### Automated Checks

**Health Endpoint:**
```bash
curl https://api.example.com/health
# Expected: {"status": "ok", "db_ready": true}
```

**System State:**
```bash
# Check system state via metrics
# Expected: system_state_status = 0 (healthy)
```

**Background Workers:**
```bash
# Check worker logs
# Expected: No errors, workers processing
```

### Manual Checks

**User-Facing:**
- [ ] User registration works
- [ ] Payment processing works
- [ ] Subscription creation works
- [ ] VPN key generation works
- [ ] Admin functions work

**System-Facing:**
- [ ] Database queries work
- [ ] VPN API calls work
- [ ] Payment provider calls work
- [ ] Background workers processing
- [ ] Metrics being collected

---

## Deployment Log

**Log Entry Format:**
```
[YYYY-MM-DD HH:MM:SS] DEPLOYMENT
Version: 1.2.3
Commit: abc123def456
Environment: prod
Deployed by: engineer@example.com
Status: SUCCESS/FAILED
Health check: PASSED/FAILED
Rollback: N/A/ROLLED_BACK
```

**Storage:**
- Application logs
- Deployment history table (optional)
- Git commit messages

---

## Notes

- ⚠️ **Always create deployment marker** before deployment
- ⚠️ **Always verify health** after deployment
- ⚠️ **Always have rollback plan** ready
- ⚠️ **Monitor for 10 minutes** after deployment
- ⚠️ **Document all deployments** in deployment log
