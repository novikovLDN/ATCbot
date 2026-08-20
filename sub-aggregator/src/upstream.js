// HTTP fetch with per-request timeout + one retry with jitter (FR-2).
// Uses undici Agent for keep-alive pooling — main knob for tail latency.

import { request, Agent } from 'undici';
import { config } from './config.js';
import { logger, tokenTag } from './logger.js';
import { upstreamErrorsTotal } from './metrics.js';

const agent = new Agent({
  keepAliveTimeout: 30_000,
  keepAliveMaxTimeout: 60_000,
  connections: 128,
});

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * GET an upstream subscription URL. Returns {body:Buffer, headers} on success,
 * throws on final failure. `kind` is 'main' | 'gb' for metrics/logs.
 * @param {string} url
 * @param {{ userAgent?: string, kind: 'main'|'gb', tokenForLog?: string }} opts
 */
export async function fetchUpstream(url, { userAgent, kind, tokenForLog } = {}) {
  const attempts = 1 + Math.max(0, config.upstreamRetries);
  let lastErr;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), config.upstreamTimeoutMs);
      try {
        const res = await request(url, {
          method: 'GET',
          dispatcher: agent,
          signal: controller.signal,
          headers: {
            'user-agent': userAgent || 'sub-aggregator/1.0',
            accept: '*/*',
          },
        });
        const body = Buffer.from(await res.body.arrayBuffer());
        if (res.statusCode < 200 || res.statusCode >= 300) {
          throw new Error(`upstream ${kind} http ${res.statusCode}`);
        }
        return { body, headers: flattenHeaders(res.headers) };
      } finally {
        clearTimeout(t);
      }
    } catch (err) {
      lastErr = err;
      upstreamErrorsTotal.inc({ kind });
      logger.warn({
        kind,
        attempt: attempt + 1,
        err: err.message,
        token: tokenTag(tokenForLog),
      }, 'upstream_fetch_error');
      if (attempt + 1 < attempts) {
        // Jitter 100–300 ms per FR-2.
        await sleep(100 + Math.floor(Math.random() * 200));
      }
    }
  }
  throw lastErr;
}

function flattenHeaders(h) {
  const out = {};
  if (!h) return out;
  for (const [k, v] of Object.entries(h)) {
    out[String(k).toLowerCase()] = Array.isArray(v) ? v.join(', ') : String(v);
  }
  return out;
}

/**
 * Fetch both upstreams in parallel. Returns { main, gb, errors:{main?, gb?} }.
 * Never throws — caller decides what to do with the outcome.
 */
export async function fetchBoth(mainUrl, gbUrl, opts = {}) {
  const [m, g] = await Promise.allSettled([
    fetchUpstream(mainUrl, { ...opts, kind: 'main' }),
    fetchUpstream(gbUrl, { ...opts, kind: 'gb' }),
  ]);
  const out = { main: null, gb: null, errors: {} };
  if (m.status === 'fulfilled') out.main = m.value; else out.errors.main = m.reason;
  if (g.status === 'fulfilled') out.gb = g.value; else out.errors.gb = g.reason;
  return out;
}
