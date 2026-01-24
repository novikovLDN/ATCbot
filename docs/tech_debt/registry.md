# Tech Debt Registry

This document tracks technical debt, interest, and payoff plans.

## Tech Debt Principles

1. **Debt is Allowed**: Technical debt is acceptable if tracked
2. **Untracked Debt is Forbidden**: All debt must be registered
3. **Debt Interest is Tracked**: Cost of debt is measured
4. **Debt Payoff is Budgeted**: Debt payoff is planned

---

## Debt Classification

### High-Interest Debt

**Characteristics:**
- High maintenance cost
- High risk
- High impact on velocity
- High impact on reliability

**Examples:**
- Untested critical paths
- Known security vulnerabilities
- Performance bottlenecks
- Reliability issues

**Payoff Priority:** High
**Payoff Timeline:** 1-3 months

---

### Medium-Interest Debt

**Characteristics:**
- Moderate maintenance cost
- Moderate risk
- Moderate impact on velocity
- Moderate impact on reliability

**Examples:**
- Code duplication
- Suboptimal algorithms
- Missing documentation
- Technical improvements

**Payoff Priority:** Medium
**Payoff Timeline:** 3-6 months

---

### Low-Interest Debt

**Characteristics:**
- Low maintenance cost
- Low risk
- Low impact on velocity
- Low impact on reliability

**Examples:**
- Code style issues
- Minor refactoring
- Documentation improvements
- Non-critical optimizations

**Payoff Priority:** Low
**Payoff Timeline:** 6-12 months

---

## Debt Registry

### Debt Entry Format

**ID:** TD-YYYY-NNN
**Title:** [Short description]
**Category:** [High/Medium/Low interest]
**Owner:** [Team/Person]
**Created:** [Date]
**Interest:** [Cost per month]
**Payoff Plan:** [Timeline and approach]

---

### Current Debt Registry

**TD-2024-001: Legacy Handler Code**
- **Category:** Medium-Interest
- **Owner:** Platform Team
- **Created:** 2024-01-15
- **Interest:** 2 hours/month (maintenance)
- **Payoff Plan:** Refactor to service layer (Q2 2024)
- **Status:** Planned

**TD-2024-002: Missing Unit Tests**
- **Category:** High-Interest
- **Owner:** Platform Team
- **Created:** 2024-01-15
- **Interest:** 4 hours/month (debugging)
- **Payoff Plan:** Add unit tests (Q1 2024)
- **Status:** In Progress

**TD-2024-003: Documentation Gaps**
- **Category:** Low-Interest
- **Owner:** Platform Team
- **Created:** 2024-01-15
- **Interest:** 1 hour/month (onboarding)
- **Payoff Plan:** Documentation sprint (Q2 2024)
- **Status:** Planned

---

## Debt Interest Tracking

### Interest Calculation

**Formula:**
- Interest = Maintenance Cost + Risk Cost + Velocity Cost

**Components:**
- Maintenance Cost: Time spent maintaining debt
- Risk Cost: Probability × Impact
- Velocity Cost: Impact on development velocity

---

### Interest Measurement

**Monthly Review:**
- Track maintenance time
- Assess risk changes
- Measure velocity impact
- Update interest calculations

**Quarterly Review:**
- Review debt registry
- Update payoff priorities
- Adjust payoff timelines
- Budget debt payoff

---

## Debt Payoff Budgeting

### Payoff Allocation

**Budget Allocation:**
- High-Interest Debt: 40% of engineering time
- Medium-Interest Debt: 30% of engineering time
- Low-Interest Debt: 10% of engineering time
- New Features: 20% of engineering time

**Quarterly Planning:**
- Identify debt to payoff
- Allocate engineering time
- Set payoff timelines
- Track payoff progress

---

### Payoff Process

**Steps:**
1. Identify debt to payoff
2. Allocate engineering time
3. Create payoff plan
4. Execute payoff
5. Verify payoff
6. Update debt registry

---

## Debt Prevention

### Prevention Rules

**Before Taking Debt:**
- Document debt
- Estimate interest
- Plan payoff
- Get approval

**Debt Approval:**
- Low-Interest: Domain owner
- Medium-Interest: Engineering manager
- High-Interest: Platform lead

---

## Notes

- ⚠️ **Debt is allowed** - But must be tracked
- ⚠️ **Untracked debt is forbidden** - All debt must be registered
- ⚠️ **Debt interest is tracked** - Cost is measured
- ⚠️ **Debt payoff is budgeted** - Payoff is planned
