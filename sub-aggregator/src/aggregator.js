// Core body merge: decode each upstream body (base64 OR plaintext), split
// by newline, dedupe, reorder (main first, gb second), re-encode base64.
// FR-3, FR-4. Also picks which headers from main to forward (FR-4 §3).

import { mergeUserinfo } from './userinfo.js';

// Headers copied verbatim from the MAIN upstream response (FR-4).
// `subscription-userinfo` is handled specially by mergeUserinfo.
const FORWARDED_MAIN_HEADERS = [
  'profile-title',
  'profile-update-interval',
  'profile-web-page-url',
  'support-url',
  'announce',
  'routing',
];

/**
 * True if a string is entirely base64 chars (with optional whitespace/padding).
 * Cheap heuristic — we don't want to false-positive plaintext vless:// lines.
 * Rules: length ≥ 8, only chars from [A-Za-z0-9+/=\s], and — key part —
 * if we drop whitespace, length must be divisible by 4 after any padding.
 */
function looksBase64(s) {
  if (typeof s !== 'string') return false;
  const trimmed = s.trim();
  if (trimmed.length < 8) return false;
  // Any vless://, ss://, trojan:// prefix → definitely plaintext.
  if (/^(vless|vmess|ss|trojan|hysteria2?|tuic|wireguard):\/\//im.test(trimmed)) {
    return false;
  }
  const compact = trimmed.replace(/\s+/g, '');
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(compact)) return false;
  return compact.length % 4 === 0;
}

/**
 * Decode an upstream body to a plaintext line list (already split, trimmed,
 * non-empty). If the body is base64 → decode to utf-8 first, then split.
 * Preserves URL-encoded remarks (%20, %E2%9C%85, etc) since we never
 * re-encode individual lines — just split & join.
 */
export function decodeBodyToLines(rawBody) {
  if (rawBody == null) return [];
  const bodyStr = Buffer.isBuffer(rawBody) ? rawBody.toString('utf8') : String(rawBody);
  const trimmed = bodyStr.trim();
  if (!trimmed) return [];
  let text = trimmed;
  if (looksBase64(trimmed)) {
    try {
      const decoded = Buffer.from(trimmed.replace(/\s+/g, ''), 'base64').toString('utf8');
      // If decoded produces printable text with newlines OR at least a
      // scheme prefix, accept it. Otherwise fall back to raw plaintext.
      if (/^\s*(vless|vmess|ss|trojan|hysteria2?|tuic|wireguard):\/\//im.test(decoded)
          || /^[\x09-\x7e\n\r]+$/.test(decoded)) {
        text = decoded;
      }
    } catch { /* keep raw plaintext */ }
  }
  return text.split(/\r?\n/).map((s) => s.trim()).filter((s) => s.length > 0);
}

/**
 * Merge two upstream responses per FR-3 & FR-4.
 * @param {{body:Buffer|string, headers:Record<string,string>}} main
 * @param {{body:Buffer|string, headers:Record<string,string>}} gb
 * @returns {{body:string, headers:Record<string,string>}} body is BASE64.
 */
export function mergeUpstreams(main, gb) {
  const mainLines = decodeBodyToLines(main?.body);
  const gbLines = decodeBodyToLines(gb?.body);

  // Dedupe: exact-string equality only (per FR-3). Preserve order.
  const seen = new Set();
  const merged = [];
  for (const line of mainLines) {
    if (seen.has(line)) continue;
    seen.add(line);
    merged.push(line);
  }
  for (const line of gbLines) {
    if (seen.has(line)) continue;
    seen.add(line);
    merged.push(line);
  }
  const bodyUtf8 = merged.join('\n');
  const bodyB64 = Buffer.from(bodyUtf8, 'utf8').toString('base64');

  const mh = normalizeHeaders(main?.headers);
  const gh = normalizeHeaders(gb?.headers);

  const outHeaders = {
    'content-type': 'text/plain; charset=utf-8',
    'subscription-userinfo': mergeUserinfo(mh['subscription-userinfo'], gh['subscription-userinfo']),
  };
  for (const h of FORWARDED_MAIN_HEADERS) {
    if (mh[h] != null) outHeaders[h] = mh[h];
  }
  return { body: bodyB64, headers: outHeaders };
}

/**
 * Merge a single-upstream fallback (only one side responded) — FR-5 tail.
 * Same shape as mergeUpstreams but tolerates a null side.
 */
export function mergeFallback(main, gb) {
  if (main && !gb) return mergeUpstreams(main, { body: '', headers: {} });
  if (gb && !main) return mergeUpstreams({ body: '', headers: {} }, gb);
  return mergeUpstreams(main || { body: '', headers: {} }, gb || { body: '', headers: {} });
}

function normalizeHeaders(h) {
  if (!h) return {};
  const out = {};
  for (const [k, v] of Object.entries(h)) {
    out[String(k).toLowerCase()] = Array.isArray(v) ? v.join(', ') : String(v);
  }
  return out;
}
