# Incident Response Plan

This document defines the incident response plan for security and operational incidents.

## Incident Classification

### Severity Levels

**P0 - Critical (Page)**
- System unavailable
- Data breach
- Payment processing down
- Security incident

**P1 - High (Ticket)**
- System degraded
- Partial functionality loss
- Performance degradation
- Cost anomalies

**P2 - Medium (Info)**
- Minor issues
- Non-critical errors
- Informational alerts

---

## Incident Response Steps

### 1. Detection

**Sources:**
- Health checks
- Alerts (PAGE, TICKET, INFO)
- Metrics anomalies
- User reports
- Monitoring dashboards

**Detection Actions:**
- Review alert details
- Check system state
- Review metrics
- Check logs for errors
- Verify incident context (correlation ID)

### 2. Containment

**Immediate Actions:**
- Identify affected components
- Isolate affected systems if needed
- Activate incident context (correlation ID)
- Notify on-call engineer
- Document timeline

**Containment Strategies:**
- **Database Failure**: Stop writes, continue reads if possible
- **VPN API Failure**: Degrade gracefully, queue activations
- **Payment Failure**: Stop payment processing, queue payments
- **Security Incident**: Isolate affected systems, preserve evidence

### 3. Eradication

**Actions:**
- Identify root cause
- Fix underlying issue
- Verify fix
- Test in staging if possible

**Eradication Steps:**
1. Review logs with correlation ID
2. Identify root cause
3. Apply fix
4. Verify fix works
5. Monitor for recurrence

### 4. Recovery

**Actions:**
- Restore normal operation
- Verify system health
- Monitor for stability
- Clear incident context

**Recovery Steps:**
1. Verify system state is HEALTHY
2. Check all health checks pass
3. Verify background workers resumed
4. Check for any stuck operations
5. Review logs for errors
6. Verify data integrity
7. Monitor for 10 minutes
8. Clear incident context

### 5. Post-Mortem

**Actions:**
- Document incident
- Identify improvements
- Update runbooks
- Schedule follow-up

**Post-Mortem Template:**
- See `docs/postmortem/template.md`

---

## Incident Response Roles

### On-Call Engineer

**Responsibilities:**
- Respond to alerts
- Contain incident
- Escalate if needed
- Document actions

### Incident Commander

**Responsibilities:**
- Coordinate response
- Make decisions
- Communicate status
- Manage timeline

### Subject Matter Experts

**Responsibilities:**
- Provide expertise
- Assist with resolution
- Review fixes
- Update documentation

---

## Communication Plan

### Internal Communication

**Slack/Email:**
- Incident channel created
- Status updates every 15 minutes
- Resolution announcement

### External Communication

**Users:**
- Status page updated
- User notifications if needed
- Support responses

**Stakeholders:**
- Executive summary
- Impact assessment
- Resolution timeline

---

## Incident Timeline

**T+0: Detection**
- Alert received
- Incident context created
- On-call engineer notified

**T+5: Containment**
- Incident assessed
- Containment actions taken
- Status communicated

**T+15: Eradication**
- Root cause identified
- Fix applied
- Verification started

**T+30: Recovery**
- System recovered
- Health verified
- Monitoring active

**T+60: Post-Mortem**
- Incident documented
- Improvements identified
- Follow-up scheduled

---

## Escalation Path

1. **On-Call Engineer** (T+0)
   - Initial response
   - Containment

2. **Team Lead** (T+15 if unresolved)
   - Escalation decision
   - Additional resources

3. **Engineering Manager** (T+30 if unresolved)
   - Strategic decisions
   - Resource allocation

4. **CTO/VP Engineering** (T+60 if unresolved)
   - Executive decisions
   - External communication

---

## Incident Response Checklist

### Detection
- [ ] Alert received
- [ ] Incident context created (correlation ID)
- [ ] System state checked
- [ ] Metrics reviewed
- [ ] Logs reviewed

### Containment
- [ ] Affected components identified
- [ ] Containment actions taken
- [ ] On-call engineer notified
- [ ] Timeline documented
- [ ] Status communicated

### Eradication
- [ ] Root cause identified
- [ ] Fix applied
- [ ] Fix verified
- [ ] Testing completed

### Recovery
- [ ] System state HEALTHY
- [ ] Health checks pass
- [ ] Background workers running
- [ ] No errors in logs
- [ ] Data integrity verified
- [ ] Monitoring active

### Post-Mortem
- [ ] Incident documented
- [ ] Timeline reconstructed
- [ ] Root cause analyzed
- [ ] Improvements identified
- [ ] Runbooks updated
- [ ] Follow-up scheduled

---

## Lessons Learned

After each incident:

1. **What went well?**
   - Document successful actions
   - Identify effective mitigations

2. **What could be improved?**
   - Identify gaps
   - Propose improvements

3. **Action Items**
   - Assign owners
   - Set deadlines
   - Track progress

---

## Emergency Contacts

- **On-Call Engineer**: [contact]
- **Team Lead**: [contact]
- **Engineering Manager**: [contact]
- **CTO/VP Engineering**: [contact]
- **Security Team**: [contact]

---

## Notes

- ⚠️ **Preserve evidence** for security incidents
- ⚠️ **Document everything** during incident
- ⚠️ **Communicate frequently** with stakeholders
- ⚠️ **No blame** in post-mortems
