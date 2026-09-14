# Incident Severity Levels

This document defines incident severity levels and response procedures.

## Severity Levels

### SEV0 - Critical

**Definition:**
- System completely unavailable
- All users affected
- No workaround available
- Data loss or corruption risk

**Examples:**
- Database completely down
- All regions unavailable
- Payment processing completely down
- Security breach

**Response Time:**
- Detection: Immediate
- Acknowledgment: < 5 minutes
- Resolution: < 1 hour

**Communication:**
- Internal: Immediate
- External: Within 30 minutes
- Status page: Updated immediately

---

### SEV1 - High

**Definition:**
- System partially unavailable
- Significant user impact
- Workaround available but limited
- Performance degradation

**Examples:**
- Single region unavailable
- Payment processing degraded
- VPN API unavailable
- Background workers down

**Response Time:**
- Detection: < 15 minutes
- Acknowledgment: < 15 minutes
- Resolution: < 4 hours

**Communication:**
- Internal: Within 30 minutes
- External: Within 1 hour
- Status page: Updated within 1 hour

---

### SEV2 - Medium

**Definition:**
- System degraded
- Limited user impact
- Workaround available
- Minor performance issues

**Examples:**
- Single service degraded
- Non-critical feature unavailable
- Increased error rate
- Latency increase

**Response Time:**
- Detection: < 1 hour
- Acknowledgment: < 1 hour
- Resolution: < 24 hours

**Communication:**
- Internal: Within 2 hours
- External: As needed
- Status page: Updated if significant impact

---

### SEV3 - Low

**Definition:**
- Minor issues
- Minimal user impact
- Workaround available
- No performance impact

**Examples:**
- Non-critical bug
- Documentation issue
- Minor UI issue
- Non-critical alert

**Response Time:**
- Detection: < 4 hours
- Acknowledgment: < 4 hours
- Resolution: < 1 week

**Communication:**
- Internal: As needed
- External: Not required
- Status page: Not required

---

## Incident Commander Role

### Responsibilities

**Incident Commander:**
- Coordinates response
- Makes decisions
- Communicates status
- Manages timeline
- Escalates if needed

**Authority:**
- Can make technical decisions
- Can enable/disable features
- Can initiate rollback
- Can escalate to management

---

### Incident Commander Selection

**Criteria:**
- Domain expertise
- Decision-making ability
- Communication skills
- Availability

**Process:**
- On-call engineer (default)
- Domain owner (if available)
- Engineering manager (if escalated)

---

## Communication Protocol

### Internal Communication

**Channels:**
- Incident channel (Slack/Teams)
- Email (for stakeholders)
- Status page (for users)

**Frequency:**
- SEV0: Every 15 minutes
- SEV1: Every 30 minutes
- SEV2: Every 2 hours
- SEV3: As needed

---

### External Communication

**Channels:**
- Status page
- Email (for enterprise customers)
- Support tickets (if applicable)

**Frequency:**
- SEV0: Every 30 minutes
- SEV1: Every 1 hour
- SEV2: As needed
- SEV3: Not required

---

## Customer Impact Rules

### Impact Assessment

**Criteria:**
- Number of users affected
- Revenue impact
- Data impact
- Security impact

**Classification:**
- Critical: > 50% users affected
- High: 10-50% users affected
- Medium: 1-10% users affected
- Low: < 1% users affected

---

### Communication Requirements

**SEV0:**
- Status page: Immediate
- Email: Within 30 minutes
- Support: Proactive outreach

**SEV1:**
- Status page: Within 1 hour
- Email: Within 2 hours
- Support: Reactive

**SEV2:**
- Status page: As needed
- Email: Not required
- Support: Reactive

**SEV3:**
- Status page: Not required
- Email: Not required
- Support: Reactive

---

## Incident Timeline

### SEV0 Timeline

**T+0: Detection**
- Incident detected
- Incident commander assigned
- Incident channel created

**T+5: Acknowledgment**
- Incident acknowledged
- Initial assessment
- Communication started

**T+15: Containment**
- Containment actions taken
- Status communicated
- Escalation if needed

**T+30: Resolution**
- Root cause identified
- Fix applied
- Verification started

**T+60: Recovery**
- System recovered
- Health verified
- Communication updated

---

### SEV1 Timeline

**T+0: Detection**
- Incident detected
- Incident commander assigned
- Incident channel created

**T+15: Acknowledgment**
- Incident acknowledged
- Initial assessment
- Communication started

**T+30: Containment**
- Containment actions taken
- Status communicated
- Escalation if needed

**T+120: Resolution**
- Root cause identified
- Fix applied
- Verification started

**T+240: Recovery**
- System recovered
- Health verified
- Communication updated

---

## Post-Incident Requirements

### Mandatory Deliverables

**Every Incident Must Produce:**
- [ ] Timeline
- [ ] Root cause
- [ ] Contributing factors
- [ ] Action items
- [ ] Owner for each action

**Timeline:**
- SEV0: Within 24 hours
- SEV1: Within 48 hours
- SEV2: Within 1 week
- SEV3: Within 2 weeks

---

## Notes

- ⚠️ **Severity is determined by impact** - Not by technical complexity
- ⚠️ **Incident commander has authority** - Can make decisions during incidents
- ⚠️ **Communication is mandatory** - Status updates required
- ⚠️ **Post-incident review is mandatory** - Learn from every incident
