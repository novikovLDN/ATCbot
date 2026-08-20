// ENV parsing with sane defaults. NO secrets inline — read from env, fail fast on required.
// See README.md §ENV for the full table.

function envInt(name, def) {
  const v = process.env[name];
  if (v === undefined || v === '') return def;
  const n = Number.parseInt(v, 10);
  if (!Number.isFinite(n)) throw new Error(`ENV ${name} must be integer, got ${JSON.stringify(v)}`);
  return n;
}

function envStr(name, def) {
  const v = process.env[name];
  return v === undefined || v === '' ? def : v;
}

export const config = {
  port: envInt('PORT', 8080),
  logLevel: envStr('LOG_LEVEL', 'info'),

  pgDsn: envStr('PG_DSN', 'postgres://aggregator:aggregator@127.0.0.1:5432/aggregator'),
  redisUrl: envStr('REDIS_URL', 'redis://127.0.0.1:6379'),

  // Caching TTLs (seconds).
  cacheTtl: envInt('CACHE_TTL', 300),      // fresh subscription body
  staleTtl: envInt('STALE_TTL', 259200),   // stale copy for SWR fallback (3 days)
  mapTtl:   envInt('MAP_TTL', 3600),       // token → sub_pairs row cache
  negMapTtl: envInt('NEG_MAP_TTL', 60),    // negative cache for unknown token

  upstreamTimeoutMs: envInt('UPSTREAM_TIMEOUT_MS', 2000),
  upstreamRetries: envInt('UPSTREAM_RETRIES', 1),

  internalSecret: envStr('INTERNAL_SECRET', ''),
  webhookSecret: envStr('WEBHOOK_SECRET', ''),
  webhookSigHeader: envStr('WEBHOOK_SIG_HEADER', 'x-remnawave-signature'),

  // Stub returned when subscription is revoked. Rendered as a single vless
  // pointing to 127.0.0.1 — client shows an "impossible" node, no crash.
  revokedRemark: envStr('REVOKED_REMARK', 'Subscription revoked. Contact support.'),

  // Metrics network — comma-separated CIDRs (used only by nginx `origin.conf`).
  metricsAllowedCidr: envStr('METRICS_ALLOWED_CIDR', '10.0.0.0/8'),
};
