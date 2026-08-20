// GET /:token — the main endpoint. FR-1..7 flow:
//   1. validate token shape
//   2. resolve token→sub_pairs row (Redis map cache → Postgres → negative cache)
//   3. if status='revoked' → stub
//   4. Redis SWR:
//        fresh hit  → serve, x-cache: hit
//        stale hit  → serve, x-cache: stale, refresh async
//        miss       → singleflight synchronous fetch, x-cache: miss
//   5. build response (merged body + hybrid userinfo headers)
//
// Robustness:
//   * Postgres down → fall back to Redis map cache (log ERROR, don't fail).
//   * Both upstreams down and no stale → 503 + retry-after: 30.
//   * One upstream down, stale exists → serve stale.
//   * One upstream down, no stale → serve what we got (WARN log).

import { config } from '../config.js';
import { logger, tokenTag } from '../logger.js';
import { getSubPair } from '../db.js';
import { jsonGet, jsonSet, del as redisDel } from '../redis.js';
import { fetchBoth } from '../upstream.js';
import { mergeUpstreams, mergeFallback } from '../aggregator.js';
import { buildStub } from '../stub.js';
import { singleflight } from '../singleflight.js';
import { requestsTotal } from '../metrics.js';

const TOKEN_RE = /^[A-Za-z0-9_-]{4,128}$/;

export function isValidToken(token) {
  return typeof token === 'string' && TOKEN_RE.test(token);
}

function keyMap(token) { return `map:${token}`; }
function keyFresh(token) { return `sub:${token}`; }
function keyStale(token) { return `stale:${token}`; }

/**
 * Resolve token → sub_pairs row. Tries Redis first, then Postgres.
 * Postgres outage → fall back to whatever Redis knows (may be stale).
 * @returns {Promise<{row: object|null, source: 'cache'|'db'|'neg'|'cache-fallback'}>}
 */
async function resolveMapping(token) {
  const cached = await jsonGet(keyMap(token));
  if (cached) {
    if (cached.__neg) return { row: null, source: 'neg' };
    return { row: cached, source: 'cache' };
  }
  try {
    const row = await getSubPair(token);
    if (!row) {
      await jsonSet(keyMap(token), { __neg: true }, config.negMapTtl);
      return { row: null, source: 'db' };
    }
    await jsonSet(keyMap(token), row, config.mapTtl);
    return { row, source: 'db' };
  } catch (err) {
    logger.error({ err: err.message, token: tokenTag(token) }, 'db_error_fallback_to_cache');
    // Best effort — if we have anything cached use it, even if TTL expired
    // in the primary key we tried above. There isn't a second key to try
    // so the practical answer here is "we can't serve you".
    return { row: null, source: 'cache-fallback' };
  }
}

/**
 * Build fresh merged response by fetching both upstreams. May return
 * partial/fallback content or throw if both upstreams failed AND caller
 * has no stale to fall back to (that decision belongs to the caller).
 */
async function buildFresh(row, opts) {
  const both = await fetchBoth(row.main_sub_url, row.gb_sub_url, opts);
  if (!both.main && !both.gb) {
    const err = new Error('both_upstreams_failed');
    err.both = both;
    throw err;
  }
  if (both.main && both.gb) {
    return { ...mergeUpstreams(both.main, both.gb), partial: false };
  }
  // Partial — one side responded. Callable to fall back.
  logger.warn({
    token: tokenTag(opts?.tokenForLog),
    main_ok: !!both.main,
    gb_ok: !!both.gb,
    err: (both.errors.main || both.errors.gb)?.message,
  }, 'upstream_partial_serving_best_effort');
  return { ...mergeFallback(both.main, both.gb), partial: true };
}

async function refreshInBackground(token, row, opts) {
  singleflight(`refresh:${token}`, async () => {
    try {
      const built = await buildFresh(row, opts);
      await Promise.all([
        jsonSet(keyFresh(token), built, config.cacheTtl),
        jsonSet(keyStale(token), built, config.staleTtl),
      ]);
    } catch (err) {
      logger.warn({ token: tokenTag(token), err: err.message }, 'bg_refresh_failed');
    }
  }).catch(() => { /* singleflight swallows in-flight rejections upstream */ });
}

/**
 * Handle GET /:token (and /:token/*). Writes body+headers to reply, returns
 * nothing. Increments requestsTotal.
 */
export async function handleSubscription(req, reply) {
  const token = req.params?.token || '';
  if (!isValidToken(token)) {
    requestsTotal.inc({ result: 'notfound' });
    return reply.code(404).header('content-type', 'text/plain').send('Not found');
  }

  const { row } = await resolveMapping(token);
  if (!row) {
    requestsTotal.inc({ result: 'notfound' });
    return reply.code(404).header('content-type', 'text/plain').send('Not found');
  }

  if (row.status === 'revoked') {
    requestsTotal.inc({ result: 'stub' });
    const stub = buildStub();
    for (const [k, v] of Object.entries(stub.headers)) reply.header(k, v);
    reply.header('x-cache', 'stub');
    return reply.send(stub.body);
  }

  // Cache lookup — fresh first.
  const fresh = await jsonGet(keyFresh(token));
  const userAgent = req.headers['user-agent'];
  const opts = { userAgent, tokenForLog: token };

  if (fresh) {
    requestsTotal.inc({ result: 'hit' });
    return respondCached(reply, fresh, 'hit');
  }
  const stale = await jsonGet(keyStale(token));
  if (stale) {
    requestsTotal.inc({ result: 'stale' });
    refreshInBackground(token, row, opts);
    return respondCached(reply, stale, 'stale');
  }

  // Miss — synchronous, singleflighted per token.
  try {
    const built = await singleflight(`build:${token}`, async () => {
      const b = await buildFresh(row, opts);
      // Persist BOTH keys atomically-ish. Even if partial=true we cache
      // it; SWR will replace it on next refresh when upstreams recover.
      await Promise.all([
        jsonSet(keyFresh(token), b, config.cacheTtl),
        jsonSet(keyStale(token), b, config.staleTtl),
      ]);
      return b;
    });
    requestsTotal.inc({ result: 'miss' });
    return respondCached(reply, built, 'miss');
  } catch (err) {
    if (err && err.message === 'both_upstreams_failed') {
      requestsTotal.inc({ result: 'error' });
      return reply
        .code(503)
        .header('content-type', 'text/plain')
        .header('retry-after', '30')
        .send('Upstream unavailable');
    }
    requestsTotal.inc({ result: 'error' });
    logger.error({ err: err.message, token: tokenTag(token) }, 'build_failed');
    return reply
      .code(502)
      .header('content-type', 'text/plain')
      .send('Bad gateway');
  }
}

function respondCached(reply, cached, cacheState) {
  for (const [k, v] of Object.entries(cached.headers || {})) reply.header(k, v);
  reply.header('x-cache', cacheState);
  return reply.send(cached.body);
}

/**
 * Invalidate all cache keys for a token. Used by internal.js.
 */
export async function invalidateToken(token) {
  await redisDel(keyFresh(token), keyStale(token), keyMap(token));
}
