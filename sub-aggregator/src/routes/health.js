// /healthz — always 200. /readyz — 200 iff Redis is reachable. /metrics — prom text.
import { getRedis } from '../redis.js';
import { registry } from '../metrics.js';

export async function handleHealthz(_req, reply) {
  return reply.send({ ok: true });
}

export async function handleReadyz(_req, reply) {
  try {
    const c = getRedis();
    const pong = await c.ping();
    if (pong !== 'PONG') throw new Error('redis_bad_pong');
    return reply.send({ ok: true });
  } catch (err) {
    return reply.code(503).send({ ok: false, error: err.message });
  }
}

export async function handleMetrics(_req, reply) {
  reply.header('content-type', registry.contentType);
  return reply.send(await registry.metrics());
}
