# RFC Process

This document defines the Request for Comments (RFC) process for system changes.

## Overview

**IMPORTANT:**
- ⚠️ **No production change without RFC** - All changes must be documented
- ⚠️ **RFC is mandatory** - No exceptions (except emergencies)
- ⚠️ **Emergency RFC requires post-mortem** - Learn from emergencies

---

## RFC Lifecycle

### 1. DRAFT

**Status:** Work in progress
**Actions:**
- Author creates RFC
- RFC is in draft state
- Not yet ready for review

**Requirements:**
- Problem statement
- Proposed solution
- Blast radius
- Rollback plan

---

### 2. REVIEW

**Status:** Under review
**Actions:**
- RFC submitted for review
- Reviewers assigned
- Feedback collected
- Revisions made

**Requirements:**
- All sections complete
- Reviewers assigned
- Feedback addressed

---

### 3. APPROVED

**Status:** Approved for implementation
**Actions:**
- RFC approved by required reviewers
- Implementation can begin
- Timeline confirmed

**Requirements:**
- Domain owner approval
- Engineering manager approval (if high-risk)
- Platform lead approval (if platform change)
- Security review (if security impact)

---

### 4. IMPLEMENTED

**Status:** Implemented in production
**Actions:**
- Change deployed
- Metrics verified
- Documentation updated

**Requirements:**
- Success criteria met
- Metrics reviewed
- Post-implementation review completed

---

### 5. REJECTED

**Status:** Rejected
**Actions:**
- RFC rejected
- Reasons documented
- Alternative solutions considered

**Requirements:**
- Rejection reason documented
- Alternative solutions considered
- Learnings captured

---

## RFC Types

### Standard RFC

**Process:**
1. Create RFC (DRAFT)
2. Submit for review (REVIEW)
3. Address feedback
4. Get approval (APPROVED)
5. Implement (IMPLEMENTED)

**Timeline:** 1-2 weeks

---

### Emergency RFC

**Process:**
1. Create emergency RFC (DRAFT)
2. Fast-track review (REVIEW)
3. Get approval (APPROVED)
4. Implement immediately (IMPLEMENTED)
5. Post-mortem required

**Timeline:** 1-2 days

**Requirements:**
- Problem severity documented
- Post-mortem scheduled
- Follow-up RFC created

---

### High-Risk RFC

**Process:**
1. Create RFC (DRAFT)
2. Extended review (REVIEW)
3. Additional approvals required
4. Get approval (APPROVED)
5. Implement with extra caution (IMPLEMENTED)

**Timeline:** 2-4 weeks

**Requirements:**
- Owner approval
- Rollback tested
- Metrics dashboard prepared
- Deployment window scheduled

---

## RFC Requirements

### Mandatory Sections

- [x] Problem Statement
- [x] Goals
- [x] Non-Goals
- [x] Proposed Solution
- [x] Invariants
- [x] Blast Radius
- [x] Rollback Plan
- [x] Observability Impact
- [x] Cost Impact

### Optional Sections

- [ ] Security Impact (if applicable)
- [ ] Testing Strategy (if applicable)
- [ ] Deployment Plan (if applicable)
- [ ] Timeline (if applicable)
- [ ] Dependencies (if applicable)

---

## Approval Requirements

### Low-Risk Changes

**Required Approvals:**
- Domain Owner

**Examples:**
- Bug fixes
- Documentation updates
- Non-critical improvements

---

### Medium-Risk Changes

**Required Approvals:**
- Domain Owner
- Engineering Manager

**Examples:**
- Feature additions
- Performance improvements
- Non-breaking API changes

---

### High-Risk Changes

**Required Approvals:**
- Domain Owner
- Engineering Manager
- Platform Lead (if platform change)
- Security Review (if security impact)

**Examples:**
- Breaking API changes
- Database schema changes
- Infrastructure changes
- Security changes

---

## RFC Review Process

### Review Assignment

**Reviewers:**
- Domain Owner (always)
- Engineering Manager (medium/high-risk)
- Platform Lead (platform changes)
- Security Team (security changes)

**Review Timeline:**
- Standard: 3-5 business days
- Emergency: 24 hours
- High-risk: 1-2 weeks

---

### Review Criteria

**Approval Criteria:**
- Problem clearly defined
- Solution is sound
- Blast radius understood
- Rollback plan exists
- Observability planned
- Cost impact acceptable
- Security impact acceptable (if applicable)

**Rejection Criteria:**
- Problem not clearly defined
- Solution is unsound
- Blast radius too large
- No rollback plan
- No observability
- Cost impact unacceptable
- Security impact unacceptable

---

## Emergency RFC Process

### When to Use Emergency RFC

**Triggers:**
- SEV0 incident
- SEV1 incident
- Critical security issue
- Data loss risk

**Process:**
1. Create emergency RFC immediately
2. Fast-track review (24 hours)
3. Get approval
4. Implement
5. Post-mortem within 1 week

---

### Post-Mortem Requirements

**Required:**
- Timeline of events
- Root cause analysis
- Contributing factors
- Action items
- Follow-up RFC (if needed)

---

## RFC Tracking

### RFC Registry

**Location:** `docs/rfc/`

**Format:** `RFC-YYYY-MMDD-NNN.md`

**Examples:**
- `RFC-2024-0115-001.md` - First RFC of 2024-01-15
- `RFC-2024-0115-002.md` - Second RFC of 2024-01-15

---

### RFC Status Tracking

**Status:** DRAFT | REVIEW | APPROVED | IMPLEMENTED | REJECTED

**Updates:**
- Status changes documented
- Timeline tracked
- Metrics reviewed

---

## Notes

- ⚠️ **RFC is not optional** - All changes must be documented
- ⚠️ **Emergency RFC is still required** - Even emergencies need documentation
- ⚠️ **Post-mortem is mandatory** - Learn from emergencies
- ⚠️ **RFC is a living document** - Update as implementation progresses
