// Fastify entry. Registers routes, wires request-id / access-log, sets up
// graceful shutdown on SIGTERM. Reused by integration tests via buildApp().
import Fastify from 'fastify';
import { config } from './config.js';
import { logger } from './logger.js';
import { closeRedis } from './redis.js';
import { closeDb } from './db.js';
import { handleSubscription } from './routes/subscription.js';
import { handleInvalidate, makeWebhookHandler } from './routes/internal.js';
import { handleHealthz, handleReadyz, handleMetrics } from './routes/health.js';

/**
 * Build a Fastify app (no listen). Exposed for tests.
 * @param {{ processWebhook?: Function }} opts
 */
export function buildApp(opts = {}) {
  const app = Fastify({
    logger: false,      // we log manually below to control the shape
    trustProxy: true,   // stream-front → origin, X-Forwarded-For preserved
    bodyLimit: 1 << 20, // 1 MiB — webhooks are small
    genReqId: () => crypto.randomUUID(),
  });

  // Preserve the raw body for webhook HMAC verification. Runs BEFORE JSON
  // parser so we can compute HMAC over the exact bytes the panel signed.
  app.addContentTypeParser('application/json', { parseAs: 'buffer' },
    (req, body, done) => {
      req.rawBody = body;
      try {
        const parsed = body.length ? JSON.parse(body.toString('utf8')) : null;
        done(null, parsed);
      } catch (err) {
        done(err);
      }
    });

  app.addHook('onRequest', async (req) => {
    req._started = process.hrtime.bigint();
  });
  app.addHook('onResponse', async (req, reply) => {
    const durMs = Number(process.hrtime.bigint() - (req._started || 0n)) / 1e6;
    logger.info({
      req_id: req.id,
      method: req.method,
      path: req.routeOptions?.url || req.url,
      status: reply.statusCode,
      duration_ms: Math.round(durMs * 100) / 100,
      x_cache: reply.getHeader('x-cache') || null,
      ua: req.headers['user-agent'] || null,
    }, 'access');
  });

  // Subscription route — /:token and /:token/*  (FR-1).
  app.get('/:token', handleSubscription);
  app.get('/:token/*', handleSubscription);

  // Internal.
  app.post('/internal/invalidate/:token', handleInvalidate);
  app.post('/internal/webhook',
    makeWebhookHandler(opts.processWebhook ? { processAsync: opts.processWebhook } : {}));

  // Service.
  app.get('/healthz', handleHealthz);
  app.get('/readyz', handleReadyz);
  app.get('/metrics', handleMetrics);

  // Root — polite empty response so a naked domain hit doesn't blow up as 404.
  app.get('/', async (_req, reply) => reply.type('text/plain').send('sub-aggregator\n'));

  return app;
}

/**
 * Start the server. Only called when this file is the entry (not from tests).
 */
export async function start() {
  const app = buildApp();
  const stop = async (signal) => {
    logger.info({ signal }, 'shutdown_start');
    try { await app.close(); } catch (err) { logger.warn({ err: err.message }, 'app_close_error'); }
    try { await closeRedis(); } catch { /* ignore */ }
    try { await closeDb(); } catch { /* ignore */ }
    logger.info('shutdown_done');
    process.exit(0);
  };
  process.on('SIGTERM', () => stop('SIGTERM'));
  process.on('SIGINT', () => stop('SIGINT'));

  await app.listen({ host: '0.0.0.0', port: config.port });
  logger.info({ port: config.port }, 'listening');
}

// Only auto-start when this file is the process entry point.
if (import.meta.url === `file://${process.argv[1]}`) {
  start().catch((err) => {
    logger.fatal({ err: err.message, stack: err.stack }, 'boot_failed');
    process.exit(1);
  });
}
