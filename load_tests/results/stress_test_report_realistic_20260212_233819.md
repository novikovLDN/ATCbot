# Webhook Stress Test Report — realistic
**Timestamp:** 20260212_233819 UTC
**Status:** FAIL

## Scenario
- **Name:** realistic
- **Concurrency:** 10
- **Total Requests:** 100

## Results
- **Success Rate:** 7.00%
- **Error Rate:** 93.00%
- **Avg Latency:** 366 ms
- **p95 Latency:** 979 ms
- **p99 Latency:** 1577 ms
- **Timeout Count:** 0
- **Connection Errors:** 0

## Server Metrics (post-run)
- `atlas_db_pool_in_use`: N/A
- `atlas_db_pool_available`: 50.0
- `atlas_redis_pool_in_use`: N/A
- `atlas_worker_crash_total`: N/A
- `atlas_idempotency_degraded_total`: N/A

## Failures
- error rate 93.00% > 1.0% threshold

## Sample Errors (first 10)
- HTTP 429: Too Many Requests
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