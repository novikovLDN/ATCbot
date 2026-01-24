# Capacity Limits & Scale Governance

This document defines capacity limits, scale guardrails, and cost-aware degradation.

## Capacity Limits

### Users

**Max Users:**
- Hard limit: 1,000,000 users
- Soft warning: 800,000 users
- Alert threshold: 900,000 users

**Current Capacity:**
- Estimated: 500,000 users (based on database size)
- Scaling: Horizontal (add database replicas)

### Active Subscriptions

**Max Active Subscriptions:**
- Hard limit: 500,000 subscriptions
- Soft warning: 400,000 subscriptions
- Alert threshold: 450,000 subscriptions

**Current Capacity:**
- Estimated: 250,000 subscriptions
- Scaling: Horizontal (add database replicas)

### Concurrent Activations

**Max Concurrent Activations:**
- Hard limit: 100 concurrent activations
- Soft warning: 80 concurrent activations
- Alert threshold: 90 concurrent activations

**Current Capacity:**
- Estimated: 50 concurrent activations
- Scaling: Vertical (increase worker capacity)

### Retries Per Minute

**Max Retries Per Minute:**
- Hard limit: 10,000 retries/minute
- Soft warning: 8,000 retries/minute
- Alert threshold: 9,000 retries/minute

**Current Capacity:**
- Estimated: 5,000 retries/minute
- Scaling: Reduce retry frequency, increase backoff

**F3.3 - Retry Storm Protection:**
- Max retries per request: 2 (hardcoded in `app/utils/retry.py`, DEFAULT_RETRIES = 2)
- Max retries per minute: 10,000 (enforced via capacity limits)
- Exponential backoff: Prevents rapid retries
- Backoff caps: Max delay 10 seconds (DEFAULT_MAX_DELAY = 10.0)

---

## Cost Drivers

### Database Connections

**Cost Driver:**
- Connection pool size
- Connection acquisition rate
- Connection duration

**Limits:**
- Max pool size: 100 connections
- Max acquisitions/minute: 1,000
- Cost per connection: Low (included in DB cost)

**Optimization:**
- Connection pooling
- Connection reuse
- Idle connection timeout

### VPN API Calls

**Cost Driver:**
- API call frequency
- API call latency
- Retry amplification

**Limits:**
- Max calls/minute: 5,000
- Max calls/hour: 100,000
- Cost per call: Low (internal API)

**Optimization:**
- Request batching
- Connection reuse
- Caching (if applicable)

### Telegram API Calls

**Cost Driver:**
- Message sending rate
- API call frequency
- Rate limit penalties

**Limits:**
- Max messages/second: 30 (Telegram limit)
- Max messages/minute: 1,800
- Cost per message: Free (Telegram API)

**Optimization:**
- Message queuing
- Rate limit compliance
- Batch sending

### CryptoBot API Calls

**Cost Driver:**
- Payment processing calls
- Webhook processing
- Status checks

**Limits:**
- Max calls/minute: 1,000
- Max calls/hour: 10,000
- Cost per call: Low (payment provider)

**Optimization:**
- Webhook batching
- Status caching
- Idempotency

### Background Workers

**Cost Driver:**
- Worker iterations
- Worker execution time
- Worker resource usage

**Limits:**
- Max iterations/minute: 1,000
- Max iterations/hour: 10,000
- Cost per iteration: Low (compute)

**Optimization:**
- Batch processing
- Reduced frequency
- Efficient algorithms

---

## Scale Guardrails

### Hard Caps

**Database:**
- Max connections: 100
- Max queries/second: 10,000
- Max data size: 1 TB

**VPN API:**
- Max concurrent calls: 50
- Max calls/minute: 5,000
- Max UUIDs: 1,000,000

**Telegram API:**
- Max messages/second: 30
- Max messages/minute: 1,800
- Max users: 1,000,000

**Background Workers:**
- Max concurrent workers: 10
- Max iterations/minute: 1,000
- Max execution time: 5 minutes

### Soft Warnings

**Triggers:**
- 80% of hard cap reached
- Cost anomaly detected
- Performance degradation

**Actions:**
- Log warning
- Send alert (INFO/TICKET)
- Monitor closely

### Alert Thresholds

**Triggers:**
- 90% of hard cap reached
- Cost spike detected
- Performance degradation

**Actions:**
- Log warning
- Send alert (TICKET)
- Review capacity planning

### Emergency Kill-Switches

**Manual Override:**
- Disable non-critical features
- Reduce background worker frequency
- Enable read-only mode
- Disable analytics

**Activation:**
- Operator decision only
- Explicit configuration change
- Documented in deployment log

---

## Cost-Aware Degradation

### Cost Spike Detection

**Triggers:**
- Cost > 2x normal
- Retry amplification > 3x
- API call spike > 5x

**Actions:**
1. **Disable Non-Critical Features**
   - Analytics: Disabled
   - Retries: Reduced
   - Background workers: Reduced frequency

2. **Reduce Frequency**
   - Background workers: 2x interval
   - Health checks: 2x interval
   - Metrics collection: 2x interval

3. **Increase Batching**
   - Payment processing: Batch size 2x
   - VPN API calls: Batch size 2x
   - Database queries: Batch size 2x

4. **Core UX Protection**
   - ✅ Payment finalization: Always on
   - ✅ Subscription activation: Always on
   - ✅ User registration: Always on
   - ❌ Analytics: Disabled
   - ❌ Retries: Reduced
   - ❌ Background workers: Reduced

### Cost Reduction Strategies

**Immediate (Automatic):**
- Reduce retry frequency
- Increase backoff delays
- Reduce background worker frequency
- Disable analytics

**Short-term (Operator):**
- Review cost drivers
- Optimize expensive operations
- Reduce batch sizes
- Enable read-only mode (if safe)

**Long-term (Planning):**
- Capacity planning
- Cost optimization
- Architecture improvements
- Resource scaling

---

## Capacity Planning

### Growth Projections

**Users:**
- Current: 50,000 users
- 6 months: 100,000 users
- 12 months: 200,000 users

**Subscriptions:**
- Current: 25,000 subscriptions
- 6 months: 50,000 subscriptions
- 12 months: 100,000 subscriptions

### Scaling Strategy

**Horizontal Scaling:**
- Database: Add read replicas
- Application: Add instances
- Workers: Add worker processes

**Vertical Scaling:**
- Database: Increase instance size
- Application: Increase instance size
- Workers: Increase worker capacity

### Capacity Monitoring

**Metrics:**
- User count
- Subscription count
- API call rates
- Database connection usage
- Cost per operation

**Alerts:**
- 80% capacity: Soft warning
- 90% capacity: Alert threshold
- 95% capacity: Critical alert

---

## Notes

- ⚠️ **Hard caps are enforced** - System will reject operations at limit
- ⚠️ **Soft warnings are informational** - No enforcement, only alerts
- ⚠️ **Emergency kill-switches are manual** - Operator control only
- ⚠️ **Cost-aware degradation protects core UX** - Never impacts critical operations
- ⚠️ **Capacity planning is ongoing** - Review quarterly
