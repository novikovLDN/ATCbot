# RFC Template

**RFC ID:** [RFC-YYYY-MMDD-NNN]
**Status:** [DRAFT | REVIEW | APPROVED | IMPLEMENTED | REJECTED]
**Author:** [Name]
**Date:** [YYYY-MM-DD]
**Reviewers:** [Names]

---

## Summary

**One-line summary of the change**

---

## Problem Statement

**What problem does this RFC solve?**

- Current state
- Pain points
- User impact
- Business impact

---

## Goals

**What are we trying to achieve?**

- Primary goals
- Success criteria
- Measurable outcomes

---

## Non-Goals

**What are we explicitly NOT trying to achieve?**

- Out of scope
- Future work
- Related but separate problems

---

## Proposed Solution

**How will we solve the problem?**

- Architecture changes
- Implementation approach
- Design decisions
- Alternatives considered

---

## Invariants

**What must NOT change?**

- Business logic invariants
- API contracts
- Data consistency guarantees
- Security boundaries

---

## Blast Radius

**What is affected by this change?**

- Components affected
- Services affected
- Data affected
- Users affected
- Regions affected

**Risk Assessment:**
- Low: [Description]
- Medium: [Description]
- High: [Description]

---

## Rollback Plan

**How do we revert if something goes wrong?**

- Rollback steps
- Rollback triggers
- Rollback verification
- Data migration (if any)

---

## Observability Impact

**How will we monitor this change?**

- New metrics
- New alerts
- New dashboards
- Logging changes

---

## Cost Impact

**What is the cost impact?**

- Infrastructure cost
- API cost
- Storage cost
- Compute cost

**Cost Estimation:**
- One-time: [Amount]
- Recurring: [Amount/month]

---

## Security Impact

**What are the security implications?**

- New attack vectors
- New trust boundaries
- New secrets
- Access control changes

---

## Testing Strategy

**How will we test this change?**

- Unit tests
- Integration tests
- Load tests
- Chaos tests

---

## Deployment Plan

**How will we deploy this change?**

- Deployment strategy
- Deployment windows
- Feature flags
- Gradual rollout

---

## Timeline

**When will this be implemented?**

- Design: [Date]
- Implementation: [Date]
- Testing: [Date]
- Deployment: [Date]

---

## Dependencies

**What does this depend on?**

- External dependencies
- Internal dependencies
- Infrastructure dependencies

---

## Open Questions

**What questions need to be answered?**

- [ ] Question 1
- [ ] Question 2
- [ ] Question 3

---

## Approval

**Required Approvals:**

- [ ] Domain Owner: [Name] - [Date]
- [ ] Engineering Manager: [Name] - [Date]
- [ ] Platform Lead (if platform change): [Name] - [Date]
- [ ] Security Review (if security impact): [Name] - [Date]

---

## Implementation Notes

**Notes from implementation:**

- [Implementation notes]
- [Lessons learned]
- [Follow-up items]

---

## Post-Implementation Review

**Review after implementation:**

- [ ] Success criteria met
- [ ] Metrics reviewed
- [ ] Cost verified
- [ ] Security verified
- [ ] Documentation updated

---

## Emergency RFC

**If this is an emergency RFC:**

- [ ] Problem severity: [SEV0/SEV1/SEV2]
- [ ] Post-mortem scheduled: [Date]
- [ ] Follow-up RFC created: [RFC-ID]

---

## Notes

- ⚠️ **No production change without RFC** - All changes must be documented
- ⚠️ **Emergency RFC requires post-mortem** - Learn from emergencies
- ⚠️ **RFC is a living document** - Update as implementation progresses
