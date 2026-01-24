# Ownership Model

This document defines the ownership model for system domains, ensuring clear responsibility and accountability.

## Ownership Principles

1. **One Owner Per Domain**: Each domain has exactly ONE owner
2. **No Shared Ownership**: No shared ownership without explicit SLA
3. **Escalation Path**: Every owner has a clear escalation contact
4. **SLO Responsibility**: Owner is responsible for domain SLOs
5. **Incident Authority**: Owner has authority to make decisions during incidents

---

## Domain Ownership

### Subscriptions Domain

**Owner:** Subscription Service Team
**Escalation Contact:** [Engineering Manager]
**SLO Responsibility:**
- Subscription creation: P95 ≤ 300ms
- Subscription activation: P95 ≤ 500ms
- Subscription renewal: P95 ≤ 300ms

**Incident Authority:**
- Can disable subscription creation during incidents
- Can enable read-only mode
- Can rollback subscription service changes

**Responsibilities:**
- Subscription lifecycle management
- Subscription status tracking
- Subscription expiry handling
- Subscription renewal logic

---

### Payments Domain

**Owner:** Payment Service Team
**Escalation Contact:** [Engineering Manager]
**SLO Responsibility:**
- Payment processing: P95 ≤ 500ms
- Payment finalization: P95 ≤ 300ms
- Payment verification: P95 ≤ 200ms

**Incident Authority:**
- Can disable payment processing during incidents
- Can enable payment queue mode
- Can rollback payment service changes

**Responsibilities:**
- Payment processing
- Payment verification
- Payment finalization
- Payment idempotency
- Payment provider integration

---

### VPN Domain

**Owner:** Infrastructure Team
**Escalation Contact:** [Infrastructure Lead]
**SLO Responsibility:**
- VPN key generation: P95 ≤ 500ms
- VPN key removal: P95 ≤ 300ms
- VPN API availability: ≥ 99.9%

**Incident Authority:**
- Can disable VPN API during incidents
- Can enable delayed activation mode
- Can rollback VPN service changes

**Responsibilities:**
- VPN API integration
- VPN key management
- VPN UUID lifecycle
- VPN API health monitoring

---

### Trials Domain

**Owner:** Product Team
**Escalation Contact:** [Product Manager]
**SLO Responsibility:**
- Trial activation: P95 ≤ 300ms
- Trial expiry: P95 ≤ 200ms
- Trial notification: P95 ≤ 500ms

**Incident Authority:**
- Can disable trial activation during incidents
- Can enable trial queue mode
- Can rollback trial service changes

**Responsibilities:**
- Trial availability logic
- Trial expiration handling
- Trial notification logic
- Trial completion tracking

---

### Notifications Domain

**Owner:** Product Team
**Escalation Contact:** [Product Manager]
**SLO Responsibility:**
- Notification delivery: P95 ≤ 1s
- Notification idempotency: 100%
- Notification reliability: ≥ 99.9%

**Incident Authority:**
- Can disable notifications during incidents
- Can enable notification queue mode
- Can rollback notification service changes

**Responsibilities:**
- Notification scheduling
- Notification delivery
- Notification idempotency
- Reminder logic

---

### Admin Domain

**Owner:** Platform Team
**Escalation Contact:** [Platform Lead]
**SLO Responsibility:**
- Admin operations: P95 ≤ 500ms
- Admin audit logging: 100%
- Admin access control: 100%

**Incident Authority:**
- Can disable admin operations during incidents
- Can enable admin read-only mode
- Can rollback admin service changes

**Responsibilities:**
- Admin user management
- Admin actions
- Admin audit logging
- Admin access control

---

### Background Workers Domain

**Owner:** Infrastructure Team
**Escalation Contact:** [Infrastructure Lead]
**SLO Responsibility:**
- Worker reliability: ≥ 99.9%
- Worker latency: P95 ≤ 5min
- Worker throughput: ≥ 1000 ops/hour

**Incident Authority:**
- Can disable workers during incidents
- Can enable worker queue mode
- Can rollback worker changes

**Responsibilities:**
- Background task scheduling
- Background task execution
- Background task monitoring
- Background task recovery

---

### Infrastructure Domain

**Owner:** Infrastructure Team
**Escalation Contact:** [Infrastructure Lead]
**SLO Responsibility:**
- System availability: ≥ 99.9%
- Database availability: ≥ 99.95%
- VPN API availability: ≥ 99.9%

**Incident Authority:**
- Can enable read-only mode
- Can enable degraded mode
- Can initiate failover
- Can rollback infrastructure changes

**Responsibilities:**
- Database management
- VPN API management
- System state management
- Health monitoring
- Disaster recovery

---

### Observability Domain

**Owner:** Platform Team
**Escalation Contact:** [Platform Lead]
**SLO Responsibility:**
- Metrics collection: 100%
- Log aggregation: 100%
- Alert delivery: P95 ≤ 30s

**Incident Authority:**
- Can disable non-critical metrics during incidents
- Can enable metrics queue mode
- Can rollback observability changes

**Responsibilities:**
- Metrics collection
- Log aggregation
- Alert management
- SLO tracking
- Performance monitoring

---

## Escalation Ladder

### Level 1: Domain Owner
- First point of contact
- Handles routine issues
- Makes domain decisions

### Level 2: Engineering Manager
- Escalation for domain owner
- Handles cross-domain issues
- Makes architectural decisions

### Level 3: Platform Lead / Infrastructure Lead
- Escalation for critical issues
- Handles platform-wide issues
- Makes platform decisions

### Level 4: CTO / VP Engineering
- Escalation for business-critical issues
- Handles organization-wide issues
- Makes strategic decisions

---

## Ownership Transfer

### When Ownership Changes

**Triggers:**
- Team reorganization
- Domain owner leaves
- Domain scope changes
- Performance issues

**Process:**
1. Document current ownership
2. Identify new owner
3. Transfer knowledge
4. Update documentation
5. Notify stakeholders

**Requirements:**
- Knowledge transfer session
- Documentation update
- Stakeholder notification
- Escalation contact update

---

## Shared Ownership (Exception)

**When Allowed:**
- Explicit SLA defined
- Clear responsibility boundaries
- Escalation paths defined
- Regular sync meetings

**Example:**
- **Subscriptions + Payments**: Shared ownership for payment finalization
  - Subscription team: Subscription creation
  - Payment team: Payment processing
  - Shared SLA: Payment finalization P95 ≤ 300ms

---

## Notes

- ⚠️ **No orphaned domains** - Every domain has an owner
- ⚠️ **No shared ownership without SLA** - Explicit agreement required
- ⚠️ **Ownership is responsibility** - Owner is accountable for domain health
- ⚠️ **Escalation is mandatory** - No silent failures
