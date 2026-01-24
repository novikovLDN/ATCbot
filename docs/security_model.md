# Security Model

This document defines the security model for the system, including threat model, trust boundaries, and access control.

## Threat Model

### Assets

**Critical Assets:**
- User data (Telegram IDs, subscription info)
- Payment data (payment records, balances)
- System credentials (database, VPN API, payment providers)
- Operational data (logs, metrics, audit trails)

**Asset Classification:**
- Public: Documentation, status pages
- Internal: System metrics, logs
- Confidential: User data, payment data
- Restricted: Credentials, secrets

---

### Trust Boundaries

**Internal Trust:**
- Service layer: Trusted business logic
- Database: Trusted data store
- Background workers: Trusted processing

**External Trust:**
- Telegram API: External, rate-limited
- VPN API (Xray Core): External, authenticated
- Payment providers: External, authenticated
- Users: Untrusted input

**Trust Boundaries:**
1. Handler → Service: Input validation required
2. Service → Database: Parameterized queries only
3. Service → External API: Authentication required
4. User → Handler: Input sanitization required

---

### Attacker Profiles

**Payment Fraud Attacker:**
- Capabilities: Payment webhook manipulation
- Goals: Free subscriptions, payment manipulation
- Mitigations: Webhook signature verification, idempotency, amount validation

**Subscription Abuse Attacker:**
- Capabilities: Multiple accounts, trial exploitation
- Goals: Unlimited free trials, subscription manipulation
- Mitigations: Trial availability checks, subscription validation, rate limiting

**VPN Misuse Attacker:**
- Capabilities: VPN key sharing, UUID manipulation
- Goals: Free VPN access, bypass restrictions
- Mitigations: Subscription validation, UUID validation, automatic cleanup

**Admin Privilege Escalation Attacker:**
- Capabilities: Admin function exploitation
- Goals: Admin privileges, data manipulation
- Mitigations: Admin ID verification, audit logging, least privilege

**Retry Amplification Attacker:**
- Capabilities: Retry storm triggering
- Goals: System overload, denial of service
- Mitigations: Bounded retries, exponential backoff, cooldown mechanisms

---

## Secrets Lifecycle

### Secrets Management

**Secrets Types:**
- Database credentials
- VPN API keys
- Payment provider API keys
- Telegram bot tokens

**Secrets Storage:**
- Environment variables (not in code)
- Secret management service (if available)
- Encrypted at rest
- Encrypted in transit

---

### Credential Rotation

**Rotation Policy:**
- Database credentials: Every 90 days
- VPN API keys: Every 180 days
- Payment provider keys: Every 90 days
- Telegram bot tokens: As needed

**Rotation Process:**
1. Generate new credentials
2. Update configuration
3. Deploy new configuration
4. Verify functionality
5. Revoke old credentials

---

## Access Control

### Production Access

**Who Can Access Prod:**
- Platform team (full access)
- Infrastructure team (infrastructure access)
- Domain owners (domain-specific access)
- On-call engineers (read-only + incident response)

**Access Granting:**
- Request via access management system
- Approval from platform lead
- Access granted with expiration
- Access logged

**Access Revocation:**
- Automatic expiration
- Manual revocation
- Immediate on termination
- Access logged

---

### Least Privilege Rules

**Principles:**
- Minimum access required
- Role-based access
- Time-limited access
- Audit logging

**Implementation:**
- Read-only access by default
- Write access requires approval
- Admin access requires additional approval
- All access logged

---

## Security Invariants

### No Implicit Trust

- All inputs validated
- All outputs sanitized
- All external calls authenticated

### No Silent Failure

- All errors logged
- All failures tracked
- All anomalies alerted

### Retries are Bounded

- Max 2-3 retries per operation
- Exponential backoff enforced
- Cooldown prevents thrashing

### Idempotency Everywhere

- Payment processing idempotent
- Subscription operations idempotent
- VPN operations idempotent

### Least Privilege

- Services have minimal permissions
- Admin functions isolated
- No unnecessary access

---

## Security Monitoring

### Security Events

**Monitored Events:**
- Failed authentication attempts
- Unauthorized access attempts
- Privilege escalation attempts
- Suspicious activity patterns

**Alerting:**
- Immediate alerts for critical events
- Daily summaries for non-critical events
- Weekly security reviews

---

## Notes

- ⚠️ **Security is everyone's responsibility** - Not just security team
- ⚠️ **Access is granted, not assumed** - Explicit approval required
- ⚠️ **Secrets are rotated regularly** - No permanent secrets
- ⚠️ **All access is logged** - Audit trail is mandatory
