# Webhook Stress Test Report — burst_200
**Timestamp:** 20260212_231454 UTC
**Status:** FAIL

## Scenario
- **Name:** burst_200
- **Concurrency:** 200
- **Total Requests:** 1000

## Results
- **Success Rate:** 30.30%
- **Error Rate:** 69.70%
- **Avg Latency:** 2911 ms
- **p95 Latency:** 5108 ms
- **p99 Latency:** 6310 ms
- **Timeout Count:** 0
- **Connection Errors:** 0

## Server Metrics (post-run)
- `atlas_db_pool_in_use`: N/A
- `atlas_db_pool_available`: 50.0
- `atlas_redis_pool_in_use`: N/A
- `atlas_worker_crash_total`: N/A
- `atlas_idempotency_degraded_total`: N/A

## Failures
- p95 latency 5108ms > 1500ms threshold
- error rate 69.70% > 1.0% threshold

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