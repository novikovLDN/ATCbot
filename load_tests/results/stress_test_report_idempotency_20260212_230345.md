# Webhook Stress Test Report — idempotency
**Timestamp:** 20260212_230345 UTC
**Status:** FAIL

## Scenario
- **Name:** idempotency
- **Concurrency:** 20
- **Total Requests:** 100

## Results
- **Success Rate:** 0.00%
- **Error Rate:** 100.00%
- **Avg Latency:** 0 ms
- **p95 Latency:** 0 ms
- **p99 Latency:** 0 ms
- **Timeout Count:** 0
- **Connection Errors:** 100

## Server Metrics (post-run)
- `atlas_db_pool_in_use`: N/A
- `atlas_db_pool_available`: N/A
- `atlas_redis_pool_in_use`: N/A
- `atlas_worker_crash_total`: N/A
- `atlas_idempotency_degraded_total`: N/A

## Failures
- error rate 100.00% > 1.0% threshold
- Idempotency: expected all 200 OK for duplicate update_ids

## Sample Errors (first 10)
- Timeout context manager should be used inside a task
- Timeout context manager should be used inside a task
- Timeout context manager should be used inside a task
- Timeout context manager should be used inside a task
- Timeout context manager should be used inside a task
- Timeout context manager should be used inside a task
- Timeout context manager should be used inside a task
- Timeout context manager should be used inside a task
- Timeout context manager should be used inside a task
- Timeout context manager should be used inside a task

## Recommendation
Test failed. Address failures before scaling. Consider:
- Increasing DB pool size
- Tuning Redis connections
- Reviewing handler latency