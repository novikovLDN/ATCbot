# Webhook Stress Test Report — realistic
**Timestamp:** 20260212_234429 UTC
**Status:** FAIL

## Scenario
- **Name:** realistic
- **Concurrency:** 10
- **Total Requests:** 100

## Results
- **Success Rate:** 100.00%
- **Error Rate:** 0.00%
- **Avg Latency:** 720 ms
- **p95 Latency:** 1511 ms
- **p99 Latency:** 1586 ms
- **Timeout Count:** 0
- **Connection Errors:** 0

## Server Metrics (post-run)
- `atlas_db_pool_in_use`: N/A
- `atlas_db_pool_available`: 50.0
- `atlas_redis_pool_in_use`: N/A
- `atlas_worker_crash_total`: N/A
- `atlas_idempotency_degraded_total`: N/A

## Failures
- p95 latency 1511ms > 1500ms threshold

## Recommendation
Test failed. Address failures before scaling. Consider:
- Increasing DB pool size
- Tuning Redis connections
- Reviewing handler latency