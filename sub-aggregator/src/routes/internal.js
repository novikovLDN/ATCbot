// Internal management endpoints. FR-9 (invalidate) and FR-10 (webhook).
// Both are protected — invalidate by shared secret header, webhook by HMAC.
// nginx origin.conf ALSO blocks external access to /internal/ — this is
// defence-in-depth, not the primary boundary.

import crypto from 'node:crypto';
import { config } from '../config.js';
import { logger, tokenTag } from '../logger.js';
import { findTokensByUserUuid } from '../db.js';
import { invalidateToken, isValidToken } from './subscription.js';
import { webhookEventsTotal } from '../metrics.js';

function checkInternalSecret(req) {
  if (!config.internalSecret) return false;  // fail-closed if not configured
  const got = req.headers['x-internal-secret'];
  if (typeof got !== 'string' || !got) return false;
  const a = Buffer.from(got, 'utf8');
  const b = Buffer.from(config.internalSecret, 'utf8');
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

/**
 * POST /internal/invalidate/:token — FR-9.
 */
export async function handleInvalidate(req, reply) {
  if (!checkInternalSecret(req)) {
    return reply.code(401).send({ ok: false, error: 'unauthorized' });
  }
  const token = req.params?.token || '';
  if (!isValidToken(token)) {
    return reply.code(400).send({ ok: false, error: 'invalid_token' });
  }
  await invalidateToken(token);
  logger.info({ token: tokenTag(token) }, 'invalidated');
  return reply.send({ ok: true });
}

/**
 * POST /internal/webhook — FR-10. Panel sends events with HMAC-SHA256
 * of the raw body. We ack fast (200) and process async — panel doesn't
 * need to wait for cache work.
 *
 * If WEBHOOK_SECRET is empty this route falls back to the shared secret
 * (same trust boundary as /invalidate) so someone deploying without HMAC
 * can still poke it. Never allow anonymous access.
 */
export function makeWebhookHandler({ processAsync = defaultProcess } = {}) {
  return async function handleWebhook(req, reply) {
    const rawBody = req.rawBody instanceof Buffer
      ? req.rawBody
      : Buffer.from(typeof req.rawBody === 'string' ? req.rawBody : '', 'utf8');

    let authorized = false;
    if (config.webhookSecret) {
      const sigHeader = req.headers[config.webhookSigHeader.toLowerCase()];
      authorized = verifyHmac(rawBody, sigHeader, config.webhookSecret);
      if (!authorized) {
        webhookEventsTotal.inc({ result: 'bad_sig' });
        return reply.code(401).send({ ok: false, error: 'bad_signature' });
      }
    } else {
      // Fall back to shared-secret gate (documented in FR-10 last line).
      if (!checkInternalSecret(req)) {
        webhookEventsTotal.inc({ result: 'bad_sig' });
        return reply.code(401).send({ ok: false, error: 'unauthorized' });
      }
      authorized = true;
    }

    // Parse JSON — silent on failure so bad-payload doesn't leak stack.
    let payload = null;
    try { payload = JSON.parse(rawBody.toString('utf8') || 'null'); }
    catch { payload = null; }
    if (!payload || typeof payload !== 'object') {
      webhookEventsTotal.inc({ result: 'bad_payload' });
      return reply.code(400).send({ ok: false, error: 'bad_json' });
    }

    webhookEventsTotal.inc({ result: 'ok' });
    // ACK immediately, do heavy work async (FR-10). Test hook awaits the
    // returned promise to keep the deterministic-order property.
    const work = processAsync(payload).catch((err) => {
      logger.error({ err: err.message }, 'webhook_process_failed');
    });
    // Expose for tests
    reply.request.webhookWork = work;
    return reply.send({ ok: true });
  };
}

async function defaultProcess(payload) {
  const uuid = payload?.data?.uuid;
  if (!uuid || typeof uuid !== 'string') return;
  const tokens = await findTokensByUserUuid(uuid);
  if (!tokens.length) return;
  await Promise.all(tokens.map((t) => invalidateToken(t)));
  logger.info({ uuid, count: tokens.length }, 'webhook_invalidated');
}

function verifyHmac(rawBody, sigHeader, secret) {
  if (!sigHeader || typeof sigHeader !== 'string') return false;
  // Accept optional "sha256=" prefix, common convention in webhook world.
  const provided = sigHeader.startsWith('sha256=') ? sigHeader.slice(7) : sigHeader;
  const expected = crypto.createHmac('sha256', secret).update(rawBody).digest('hex');
  const a = Buffer.from(provided.toLowerCase(), 'utf8');
  const b = Buffer.from(expected, 'utf8');
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

export const _internal = { verifyHmac, checkInternalSecret };
