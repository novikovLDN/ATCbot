// ioredis client + tiny helpers around JSON get/set.
import Redis from 'ioredis';
import { config } from './config.js';
import { logger } from './logger.js';

let client;

export function getRedis() {
  if (client) return client;
  client = new Redis(config.redisUrl, {
    lazyConnect: false,
    maxRetriesPerRequest: 2,
    enableAutoPipelining: true,
  });
  client.on('error', (err) => {
    // Log-only; caller decides how to degrade.
    logger.warn({ err: err.message }, 'redis_error');
  });
  return client;
}

/**
 * Test helper — inject a preconstructed client (ioredis-mock).
 */
export function setRedisForTest(inst) {
  client = inst;
}

export async function jsonGet(key) {
  const c = getRedis();
  const raw = await c.get(key);
  if (raw == null) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

export async function jsonSet(key, value, ttlSec) {
  const c = getRedis();
  const raw = JSON.stringify(value);
  if (ttlSec > 0) await c.set(key, raw, 'EX', ttlSec);
  else await c.set(key, raw);
}

export async function del(...keys) {
  if (!keys.length) return 0;
  const c = getRedis();
  return c.del(...keys);
}

export async function closeRedis() {
  if (client) {
    try { await client.quit(); } catch { /* already closed */ }
    client = null;
  }
}
