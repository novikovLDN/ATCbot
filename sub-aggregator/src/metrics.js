// Prometheus counters. Text-encoded output at /metrics.
// FR-11: exactly these three counters (plus default node metrics).
import { Registry, Counter, collectDefaultMetrics } from 'prom-client';

export const registry = new Registry();
collectDefaultMetrics({ register: registry });

export const requestsTotal = new Counter({
  name: 'aggregator_requests_total',
  help: 'Subscription requests, split by cache outcome',
  labelNames: ['result'],  // hit | stale | miss | stub | error | notfound
  registers: [registry],
});

export const upstreamErrorsTotal = new Counter({
  name: 'aggregator_upstream_errors_total',
  help: 'Upstream fetch failures (per attempt) split by upstream kind',
  labelNames: ['kind'],  // main | gb
  registers: [registry],
});

export const webhookEventsTotal = new Counter({
  name: 'aggregator_webhook_events_total',
  help: 'Panel webhook events received',
  labelNames: ['result'],  // ok | bad_sig | bad_payload
  registers: [registry],
});
