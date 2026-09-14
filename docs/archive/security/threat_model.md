# Threat Model

This document defines the threat model for the system, including assets, trust boundaries, attacker profiles, attack vectors, and mitigations.

## Assets

### Critical Assets

1. **User Data**
   - User IDs, Telegram IDs
   - Subscription information
   - Payment history
   - VPN access credentials

2. **Financial Data**
   - Payment records
   - Balance information
   - Transaction history
   - Payment provider credentials

3. **System Credentials**
   - Database credentials
   - VPN API keys
   - Payment provider API keys
   - Telegram bot tokens

4. **Operational Data**
   - System state
   - Metrics and logs
   - Audit trails
   - Incident records

---

## Trust Boundaries

### Internal Trust

- **Service Layer**: Trusted business logic
- **Database**: Trusted data store
- **Background Workers**: Trusted processing

### External Trust

- **Telegram API**: External, rate-limited
- **VPN API (Xray Core)**: External, authenticated
- **Payment Providers**: External, authenticated
- **Users**: Untrusted input

### Trust Boundaries

1. **Handler → Service**: Input validation required
2. **Service → Database**: Parameterized queries only
3. **Service → External API**: Authentication required
4. **User → Handler**: Input sanitization required

---

## Attacker Profiles

### 1. Payment Fraud Attacker

**Capabilities:**
- Can send payment webhooks
- Can manipulate payment amounts
- Can replay payment events

**Goals:**
- Get free subscriptions
- Manipulate payment amounts
- Double-spend payments

**Mitigations:**
- Payment webhook signature verification
- Idempotency checks (prevent double-processing)
- Amount validation (verify against expected amount)
- Payment status tracking (prevent replay attacks)

### 2. Subscription Abuse Attacker

**Capabilities:**
- Can create multiple accounts
- Can manipulate subscription data
- Can exploit trial system

**Goals:**
- Get unlimited free trials
- Extend subscription periods
- Bypass payment requirements

**Mitigations:**
- Trial availability checks (one trial per user)
- Subscription expiry validation (server-side)
- Payment verification (subscription requires payment)
- Rate limiting (prevent rapid account creation)

### 3. VPN Misuse Attacker

**Capabilities:**
- Can request VPN keys
- Can manipulate UUIDs
- Can exploit VPN API

**Goals:**
- Get VPN access without payment
- Share VPN keys
- Bypass VPN restrictions

**Mitigations:**
- Subscription validation (VPN requires active subscription)
- UUID validation (server-side generation)
- VPN API authentication (API keys required)
- UUID removal on subscription expiry (automatic cleanup)

### 4. Admin Privilege Escalation Attacker

**Capabilities:**
- Can attempt to access admin functions
- Can manipulate admin requests
- Can exploit admin workflows

**Goals:**
- Gain admin privileges
- Manipulate user data
- Bypass payment requirements

**Mitigations:**
- Admin ID verification (hardcoded admin IDs)
- Admin action audit logging (all actions logged)
- Input validation (all admin inputs validated)
- Least privilege (admin functions isolated)

### 5. Retry Amplification Attacker

**Capabilities:**
- Can trigger retries
- Can cause system overload
- Can exploit retry logic

**Goals:**
- Overload system with retries
- Cause denial of service
- Exhaust resources

**Mitigations:**
- Bounded retries (max 2-3 retries)
- Exponential backoff (prevent rapid retries)
- Cooldown mechanisms (prevent thrashing)
- Cost tracking (detect retry amplification)

---

## Attack Vectors

### 1. Payment Webhook Manipulation

**Vector:**
- Attacker sends fake payment webhook
- Manipulates payment amount
- Replays payment events

**Mitigation:**
- Webhook signature verification
- Amount validation
- Idempotency checks
- Payment status tracking

### 2. Trial System Exploitation

**Vector:**
- Attacker creates multiple accounts
- Exploits trial availability checks
- Gets unlimited free trials

**Mitigation:**
- Trial availability checks (one per user)
- User identification (Telegram ID)
- Trial expiry enforcement
- Audit logging

### 3. VPN Key Sharing

**Vector:**
- Attacker shares VPN keys
- Multiple users use same key
- Bypasses subscription requirements

**Mitigation:**
- UUID per subscription (unique keys)
- Subscription validation (active subscription required)
- Automatic UUID removal on expiry
- VPN API authentication

### 4. Admin Function Exploitation

**Vector:**
- Attacker attempts admin functions
- Manipulates admin requests
- Exploits admin workflows

**Mitigation:**
- Admin ID verification
- Admin action audit logging
- Input validation
- Least privilege

### 5. Retry Storm

**Vector:**
- Attacker triggers many retries
- Causes system overload
- Exhausts resources

**Mitigation:**
- Bounded retries
- Exponential backoff
- Cooldown mechanisms
- Cost tracking and alerts

---

## Mitigations Summary

### Payment Security

- ✅ Webhook signature verification
- ✅ Amount validation
- ✅ Idempotency checks
- ✅ Payment status tracking
- ✅ Audit logging

### Subscription Security

- ✅ Trial availability checks
- ✅ Subscription expiry validation
- ✅ Payment verification
- ✅ Rate limiting

### VPN Security

- ✅ Subscription validation
- ✅ UUID validation
- ✅ VPN API authentication
- ✅ Automatic cleanup

### Admin Security

- ✅ Admin ID verification
- ✅ Admin action audit logging
- ✅ Input validation
- ✅ Least privilege

### System Security

- ✅ Bounded retries
- ✅ Exponential backoff
- ✅ Cooldown mechanisms
- ✅ Cost tracking
- ✅ Rate limiting
- ✅ Input sanitization
- ✅ Parameterized queries
- ✅ No secrets in code

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

## Compliance Considerations

### GDPR

- PII identified and masked
- Data minimization enforced
- Deletion capability available
- Audit evidence maintained

### Payment Card Industry (PCI)

- No card data stored
- Payment provider handles card data
- Payment tokens not logged
- Audit trail for payments

### SOC 2

- Access controls enforced
- Audit logging complete
- Incident response ready
- Change management documented

---

## Threat Model Review

**Review Frequency:** Quarterly

**Review Process:**
1. Review new attack vectors
2. Update mitigations
3. Test security controls
4. Update documentation

**Last Review:** [Date]
**Next Review:** [Date + 3 months]
