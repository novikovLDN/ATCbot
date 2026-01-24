# On-Call & Human Safety

This document defines on-call procedures and human safety rules.

## On-Call Principles

1. **No Single-Person Ownership**: No single person is responsible for on-call
2. **No Silent Pages**: All pages must be acknowledged
3. **No Hero Debugging**: Team effort, not individual heroics
4. **Clear Escalation Ladder**: Escalation path is always clear
5. **Burnout Prevention**: Burnout prevention is a system requirement

---

## On-Call Rotation

### Rotation Schedule

**Primary On-Call:**
- Duration: 1 week
- Coverage: 24/7
- Handoff: Monday 9 AM

**Secondary On-Call:**
- Duration: 1 week
- Coverage: Backup for primary
- Handoff: Monday 9 AM

**Escalation:**
- Engineering Manager (if primary unavailable)
- Platform Lead (if critical)
- CTO (if business-critical)

---

### On-Call Responsibilities

**Primary On-Call:**
- Respond to alerts within 5 minutes
- Acknowledge incidents within 15 minutes
- Coordinate incident response
- Escalate if needed

**Secondary On-Call:**
- Backup for primary
- Available if primary unavailable
- Assist during incidents

---

## Escalation Ladder

### Level 1: Primary On-Call

**Responsibilities:**
- First response
- Initial assessment
- Incident coordination

**Escalation Triggers:**
- Unable to resolve within 30 minutes
- Requires domain expertise
- Requires management decision

---

### Level 2: Engineering Manager

**Responsibilities:**
- Cross-domain coordination
- Resource allocation
- Management communication

**Escalation Triggers:**
- Unable to resolve within 2 hours
- Requires business decision
- Requires customer communication

---

### Level 3: Platform Lead

**Responsibilities:**
- Platform-wide decisions
- Infrastructure changes
- Strategic decisions

**Escalation Triggers:**
- Unable to resolve within 4 hours
- Requires platform changes
- Requires strategic decisions

---

### Level 4: CTO / VP Engineering

**Responsibilities:**
- Business-critical decisions
- Customer communication
- Strategic decisions

**Escalation Triggers:**
- Business-critical impact
- Customer communication required
- Strategic decisions required

---

## Burnout Prevention

### Rules

**No Single-Person Ownership:**
- Rotate on-call weekly
- No person on-call > 2 weeks in a row
- Minimum 2 weeks between on-call shifts

**No Silent Pages:**
- All pages must be acknowledged
- No page ignored
- Escalation if no response

**No Hero Debugging:**
- Team effort, not individual
- Share knowledge
- Document solutions

**Clear Escalation:**
- Escalation path always clear
- No ambiguity
- Escalation is not failure

---

### Burnout Indicators

**Watch For:**
- Frequent on-call shifts
- Long incident resolution times
- Decreased response quality
- Increased stress levels

**Actions:**
- Adjust rotation schedule
- Provide additional support
- Reduce on-call load
- Provide training

---

## On-Call Tools

### Required Tools

**Alerting:**
- PagerDuty / Opsgenie
- Email alerts
- SMS alerts (for SEV0)

**Communication:**
- Incident channel (Slack/Teams)
- Status page
- Email

**Monitoring:**
- Metrics dashboards
- Log aggregation
- Health checks

---

## On-Call Training

### Required Training

**New On-Call Engineers:**
- System architecture overview
- Incident response procedures
- Escalation procedures
- Tools training

**Ongoing Training:**
- Post-incident reviews
- Best practices sharing
- Tool updates
- Process improvements

---

## On-Call Compensation

### Compensation Model

**On-Call Pay:**
- Base on-call pay
- Incident response pay
- Overtime pay (if applicable)

**Time Off:**
- Comp time for incidents
- Additional PTO for on-call weeks
- Flexible schedule during on-call

---

## Notes

- ⚠️ **Burnout prevention is a system requirement** - Not optional
- ⚠️ **No single person is responsible** - Team effort
- ⚠️ **Escalation is not failure** - It's a process
- ⚠️ **On-call is a shared responsibility** - Everyone participates
