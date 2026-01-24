# Backup & Restore Strategy

This document defines backup and restore procedures for disaster recovery.

## Recovery Objectives

### RPO (Recovery Point Objective)

**15 minutes**

- Database backups: Every 15 minutes (automated)
- Configuration backups: Every 1 hour (automated)
- Manual backups: Before any major deployment

### RTO (Recovery Time Objective)

**30 minutes**

- Database restore: 15 minutes
- Application restart: 5 minutes
- Verification: 10 minutes

---

## Backup Strategy

### Database Backups

**Automated Backups:**
- Frequency: Every 15 minutes
- Retention: 7 days
- Location: `/backups/database/`
- Format: PostgreSQL dump (pg_dump)

**Manual Backups:**
- Before major deployments
- Before schema changes
- Before data migrations

**Backup Command:**
```bash
pg_dump -h <host> -U <user> -d <database> -F c -f /backups/database/backup_$(date +%Y%m%d_%H%M%S).dump
```

### Configuration Backups

**Automated Backups:**
- Frequency: Every 1 hour
- Retention: 30 days
- Location: `/backups/config/`
- Includes: Environment variables, API keys, VPN config

**Manual Backups:**
- Before configuration changes
- Before deployment

### Application Code Backups

**Version Control:**
- All code in Git repository
- Tagged releases for rollback
- Deployment markers for version tracking

---

## Restore Procedures

### Database Restore

**Prerequisites:**
- Stop application
- Verify backup file exists
- Check backup file integrity

**Restore Steps:**

1. **Stop Application**
   ```bash
   systemctl stop app
   # Or
   docker stop <app_container>
   ```

2. **Drop Existing Database (if needed)**
   ```bash
   # WARNING: This deletes all data
   dropdb -h <host> -U <user> <database>
   ```

3. **Create New Database**
   ```bash
   createdb -h <host> -U <user> <database>
   ```

4. **Restore from Backup**
   ```bash
   pg_restore -h <host> -U <user> -d <database> -v /backups/database/backup_<timestamp>.dump
   ```

5. **Verify Data Integrity**
   ```bash
   # Check critical tables
   psql -h <host> -U <user> -d <database> -c "SELECT COUNT(*) FROM users;"
   psql -h <host> -U <user> -d <database> -c "SELECT COUNT(*) FROM subscriptions;"
   psql -h <host> -U <user> -d <database> -c "SELECT COUNT(*) FROM payments;"
   ```

6. **Restart Application**
   ```bash
   systemctl start app
   # Or
   docker start <app_container>
   ```

7. **Verify Application Health**
   - Check health endpoint
   - Verify database connection
   - Check background workers
   - Monitor logs for errors

### Configuration Restore

1. **Stop Application**
   ```bash
   systemctl stop app
   ```

2. **Restore Configuration Files**
   ```bash
   cp /backups/config/config_<timestamp>.env /app/.env
   ```

3. **Verify Configuration**
   - Check environment variables
   - Verify API keys
   - Check VPN configuration

4. **Restart Application**
   ```bash
   systemctl start app
   ```

### Application Code Rollback

1. **Identify Target Version**
   ```bash
   # List available tags
   git tag -l
   ```

2. **Checkout Target Version**
   ```bash
   git checkout <tag_or_commit>
   ```

3. **Deploy Rollback**
   ```bash
   # Follow standard deployment procedure
   # Verify deployment marker
   ```

4. **Verify Rollback**
   - Check application version
   - Verify health checks
   - Monitor for errors

---

## Verification Checklist

After restore:

- [ ] Database connection successful
- [ ] All critical tables have data
- [ ] User data integrity verified
- [ ] Subscription data integrity verified
- [ ] Payment data integrity verified
- [ ] Application health checks pass
- [ ] Background workers running
- [ ] No errors in logs
- [ ] System state is HEALTHY
- [ ] Monitoring shows normal metrics

---

## Backup Verification

**Daily Verification:**
- Check backup files exist
- Verify backup file sizes are reasonable
- Test restore on staging environment (weekly)

**Monthly Verification:**
- Full restore test on staging
- Verify data integrity
- Document any issues

---

## Emergency Contacts

- **Database Admin**: [contact]
- **DevOps Lead**: [contact]
- **On-Call Engineer**: [contact]

---

## Notes

- ⚠️ **No automatic restore** - All restores are manual
- ⚠️ **Always verify backups** before restore
- ⚠️ **Test restores regularly** on staging
- ⚠️ **Document all restore operations**
