// Structured JSON logging via pino. Every request gets a correlation id in
// server.js; log lines include: request-id, token (first 6 chars only —
// don't leak the full opaque secret), x-cache, duration_ms.
import pino from 'pino';
import { config } from './config.js';

export const logger = pino({
  level: config.logLevel,
  base: { service: 'sub-aggregator' },
  timestamp: pino.stdTimeFunctions.isoTime,
  redact: {
    // Never log the raw secrets — even if a caller passes them through.
    paths: [
      'req.headers.authorization',
      'req.headers["x-internal-secret"]',
      'req.headers["x-remnawave-signature"]',
      'headers.authorization',
    ],
    remove: true,
  },
});

// Return only the first 6 chars of a token for logs.
export function tokenTag(token) {
  if (!token || typeof token !== 'string') return '?';
  return token.length <= 6 ? token : token.slice(0, 6) + '…';
}
