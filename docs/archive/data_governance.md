# Data Governance

This document defines data governance policies, including PII classification, retention, and deletion.

## PII Classification

### Personal Identifiable Information (PII)

**PII Types:**
- **Direct PII**: Telegram ID, user ID
- **Financial PII**: Payment amounts, transaction history
- **Behavioral PII**: Subscription history, usage patterns
- **Temporal PII**: Subscription dates, payment dates

**Non-PII:**
- Aggregated metrics
- System state
- Error codes
- Timestamps (without user context)

---

### PII Handling Rules

**Storage:**
- PII stored in database (encrypted at rest)
- PII not stored in logs (masked)
- PII not stored in metrics (aggregated)
- PII not stored in events (sanitized)

**Access:**
- PII access requires authorization
- PII access is logged
- PII access is audited
- PII access is time-limited

---

## Retention Periods

### User Data

**Active Users:**
- Retention: Indefinite
- Deletion: On user request
- Backup: 30 days after deletion

**Inactive Users:**
- Retention: 2 years
- Deletion: After 2 years of inactivity
- Backup: 30 days after deletion

**Deleted Users:**
- Retention: 30 days (soft delete)
- Deletion: After 30 days (hard delete)
- Backup: 30 days after deletion

---

### Subscription Data

**Active Subscriptions:**
- Retention: Indefinite
- Deletion: On subscription cancellation + 1 year
- Backup: 30 days after deletion

**Expired Subscriptions:**
- Retention: 1 year
- Deletion: After 1 year
- Backup: 30 days after deletion

**Cancelled Subscriptions:**
- Retention: 1 year
- Deletion: After 1 year
- Backup: 30 days after deletion

---

### Payment Data

**All Payments:**
- Retention: 7 years (compliance requirement)
- Deletion: After 7 years (if allowed by law)
- Backup: 7 years after deletion

**Failed Payments:**
- Retention: 1 year
- Deletion: After 1 year
- Backup: 30 days after deletion

---

### Logs

**Application Logs:**
- Retention: 90 days
- Deletion: After 90 days
- Backup: Not required

**Audit Logs:**
- Retention: 7 years (payments), 1 year (others)
- Deletion: After retention period
- Backup: 7 years after deletion

---

## Deletion Guarantees

### User Data Deletion

**Process:**
1. User requests deletion
2. Verify user identity
3. Soft delete (mark as deleted)
4. Retain for 30 days
5. Hard delete (remove from database)
6. Remove from backups (after 30 days)

**Guarantees:**
- Deletion within 30 days
- Backup removal within 60 days
- Audit trail retained (anonymized)

---

### Subscription Data Deletion

**Process:**
1. Subscription cancelled/expired
2. Retain for 1 year
3. Soft delete (mark as deleted)
4. Retain for 30 days
5. Hard delete (remove from database)
6. Remove from backups (after 30 days)

**Guarantees:**
- Deletion within 1 year + 30 days
- Backup removal within 1 year + 60 days
- Audit trail retained (anonymized)

---

### Payment Data Deletion

**Process:**
1. Payment record > 7 years old
2. Verify legal requirements
3. Soft delete (mark as deleted)
4. Retain for 30 days
5. Hard delete (remove from database)
6. Remove from backups (after 30 days)

**Guarantees:**
- Deletion after 7 years (if allowed)
- Backup removal after 7 years + 30 days
- Audit trail retained (anonymized)

---

## Anonymization Rules

### Anonymization Process

**User Data:**
- Telegram ID: Replaced with hash
- User ID: Replaced with hash
- Subscription data: Aggregated
- Payment data: Amounts only (no user ID)

**Logs:**
- User IDs: Masked in logs
- Payment tokens: `[REDACTED]`
- Card numbers: Never logged
- VPN UUIDs: First 8 characters only

**Events:**
- All PII masked
- Only aggregated data
- Correlation IDs for linking (no PII)

---

## Data Minimization

### Principles

**Collect Only What's Needed:**
- Minimum data collection
- No unnecessary fields
- No redundant storage

**Use Only What's Collected:**
- Data used for stated purpose only
- No data sharing without consent
- No data selling

**Retain Only As Long As Needed:**
- Retention periods enforced
- Automatic deletion
- Backup cleanup

---

## Data Sharing

### Internal Sharing

**Rules:**
- Services can access data they need
- Access is logged
- Access is audited
- No unnecessary sharing

---

### External Sharing

**Rules:**
- No external sharing without consent
- No data selling
- No data sharing with third parties
- Compliance with regulations

---

## Compliance

### GDPR Compliance

**Rights:**
- Right to access
- Right to rectification
- Right to erasure
- Right to data portability

**Obligations:**
- Data minimization
- Purpose limitation
- Storage limitation
- Security of processing

---

### PCI DSS Compliance

**Requirements:**
- No card data stored
- Payment provider handles card data
- Payment tokens not logged
- Audit trail for payments

---

## Notes

- ⚠️ **No undeclared data** - All data must be classified
- ⚠️ **No infinite retention** - Retention periods enforced
- ⚠️ **No shared secrets across environments** - Environment isolation
- ⚠️ **Deletion is guaranteed** - Process is automated
