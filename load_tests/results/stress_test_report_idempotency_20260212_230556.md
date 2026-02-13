# Webhook Stress Test Report — idempotency
**Timestamp:** 20260212_230556 UTC
**Status:** FAIL

## Scenario
- **Name:** idempotency
- **Concurrency:** 20
- **Total Requests:** 100

## Results
- **Success Rate:** 4.00%
- **Error Rate:** 96.00%
- **Avg Latency:** 1987 ms
- **p95 Latency:** 2525 ms
- **p99 Latency:** 2728 ms
- **Timeout Count:** 0
- **Connection Errors:** 0

## Server Metrics (post-run)
- `atlas_db_pool_in_use`: N/A
- `atlas_db_pool_available`: 50.0
- `atlas_redis_pool_in_use`: N/A
- `atlas_worker_crash_total`: N/A
- `atlas_idempotency_degraded_total`: N/A

## Failures
- p95 latency 2525ms > 1500ms threshold
- error rate 96.00% > 1.0% threshold
- Idempotency: expected all 200 OK for duplicate update_ids

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