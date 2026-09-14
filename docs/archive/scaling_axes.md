# Scaling Axes Definition

This document explicitly defines scaling axes for system components.

## Scaling Axes

### Scale by Users

**Components:**
- User data storage
- User authentication
- User profile management
- User session management

**Bottleneck:**
- Database read capacity
- Database write capacity
- Session storage

**Hard Limit:**
- 1,000,000 users (current)
- Scaling: Horizontal (database replicas)

**Scaling Strategy:**
- Add database read replicas
- Add database write replicas
- Add session storage capacity

---

### Scale by Subscriptions

**Components:**
- Subscription data storage
- Subscription lifecycle management
- Subscription activation
- Subscription renewal

**Bottleneck:**
- Database read capacity
- Database write capacity
- Activation worker capacity

**Hard Limit:**
- 500,000 subscriptions (current)
- Scaling: Horizontal (database replicas, worker instances)

**Scaling Strategy:**
- Add database replicas
- Add worker instances
- Optimize activation logic

---

### Scale by Traffic

**Components:**
- HTTP handlers
- API endpoints
- Background workers
- Message processing

**Bottleneck:**
- Application server capacity
- Database connection pool
- Message queue capacity

**Hard Limit:**
- 10,000 requests/second (current)
- Scaling: Horizontal (application instances)

**Scaling Strategy:**
- Add application instances
- Add load balancers
- Optimize request processing

---

### Scale by Regions

**Components:**
- Multi-region deployment
- Region failover
- Data replication
- Regional routing

**Bottleneck:**
- Data replication latency
- Region failover time
- Regional capacity

**Hard Limit:**
- 3 regions (current: EU, US, ASIA)
- Scaling: Add regions (with data replication)

**Scaling Strategy:**
- Add regions
- Optimize data replication
- Optimize region failover

---

### Scale by Teams

**Components:**
- Code ownership
- Deployment frequency
- Change velocity
- Team coordination

**Bottleneck:**
- Deployment coordination
- Change conflicts
- Team communication

**Hard Limit:**
- 5 teams (current)
- Scaling: Team structure optimization

**Scaling Strategy:**
- Clear ownership boundaries
- Independent deployments
- Clear communication protocols

---

## Component Scaling Declarations

### Database

**Scaling Axis:** Users, Subscriptions, Traffic
**Bottleneck:** Connection pool, query performance
**Hard Limit:** 1,000,000 users, 500,000 subscriptions
**Scaling Strategy:** Horizontal (read replicas, write replicas)

---

### VPN API

**Scaling Axis:** Subscriptions, Traffic
**Bottleneck:** API call rate, UUID management
**Hard Limit:** 5,000 calls/minute, 1,000,000 UUIDs
**Scaling Strategy:** Horizontal (API instances, load balancing)

---

### Payment Processing

**Scaling Axis:** Traffic, Subscriptions
**Bottleneck:** Payment provider API rate, processing capacity
**Hard Limit:** 1,000 payments/minute
**Scaling Strategy:** Horizontal (processing instances, queue scaling)

---

### Background Workers

**Scaling Axis:** Subscriptions, Traffic
**Bottleneck:** Worker capacity, processing time
**Hard Limit:** 1,000 iterations/minute
**Scaling Strategy:** Horizontal (worker instances, batch optimization)

---

### Application Servers

**Scaling Axis:** Traffic
**Bottleneck:** CPU, memory, connection handling
**Hard Limit:** 10,000 requests/second per instance
**Scaling Strategy:** Horizontal (application instances, load balancing)

---

## Scaling Limits

### Current Limits

**Users:** 1,000,000
**Subscriptions:** 500,000
**Traffic:** 10,000 requests/second
**Regions:** 3
**Teams:** 5

---

### Projected Limits (12 months)

**Users:** 2,000,000
**Subscriptions:** 1,000,000
**Traffic:** 50,000 requests/second
**Regions:** 5
**Teams:** 8

---

### Scaling Roadmap

**Q1:**
- Database read replicas (2x capacity)
- Application instances (2x capacity)
- Worker instances (2x capacity)

**Q2:**
- Database write replicas (2x capacity)
- VPN API scaling (2x capacity)
- Payment processing scaling (2x capacity)

**Q3:**
- Additional region (4th region)
- Database sharding (10x capacity)
- Application auto-scaling

**Q4:**
- Additional region (5th region)
- Full auto-scaling
- Capacity planning automation

---

## Notes

- ⚠️ **Every component declares its scaling axis** - No assumptions
- ⚠️ **Bottlenecks are identified** - No guessing
- ⚠️ **Hard limits are known** - No surprises
- ⚠️ **Scaling strategy is defined** - No ad-hoc scaling
