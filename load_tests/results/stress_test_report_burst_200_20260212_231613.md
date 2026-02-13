# Webhook Stress Test Report — burst_200
**Timestamp:** 20260212_231613 UTC
**Status:** FAIL

## Scenario
- **Name:** burst_200
- **Concurrency:** 200
- **Total Requests:** 1000

## Results
- **Success Rate:** 0.40%
- **Error Rate:** 99.60%
- **Avg Latency:** 3027 ms
- **p95 Latency:** 3932 ms
- **p99 Latency:** 4186 ms
- **Timeout Count:** 0
- **Connection Errors:** 1

## Server Metrics (post-run)
- `atlas_db_pool_in_use`: N/A
- `atlas_db_pool_available`: 50.0
- `atlas_redis_pool_in_use`: N/A
- `atlas_worker_crash_total`: N/A
- `atlas_idempotency_degraded_total`: N/A

## Failures
- p95 latency 3932ms > 1500ms threshold
- error rate 99.60% > 1.0% threshold

## Sample Errors (first 10)
- Cannot connect to host atcbot-production-9106.up.railway.app:443 ssl:default [None]
- HTTP 429: Too Many Requests
- HTTP 429: Too Many Requests
- HTTP 429: Too Many Requests
- HTTP 429: Too Many Requests
- HTTP 429: Too Many Requests
- HTTP 429: Too Many Requests
- HTTP 429: Too Many Requests
- HTTP 429: Too Many Requests
- HTTP 429: Too Many Requests

## Recommendation
Test failed. Address failures before scaling. Consider:
- Increasing DB pool size
- Tuning Redis connections
- Reviewing handler latency