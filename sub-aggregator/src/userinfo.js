// subscription-userinfo header parse + hybrid-build for FR-4.
// Wire format (RFC-ish): "upload=..; download=..; total=..; expire=..".
// Missing numeric field → 0 in output (never write NaN/undefined).

/**
 * Parse a subscription-userinfo header value into a plain object.
 * Unknown keys are preserved on the returned object but ignored by builder.
 * @param {string|undefined|null} raw
 * @returns {{upload:number, download:number, total:number, expire:number}}
 */
export function parseUserinfo(raw) {
  const out = { upload: 0, download: 0, total: 0, expire: 0 };
  if (!raw || typeof raw !== 'string') return out;
  for (const part of raw.split(';')) {
    const s = part.trim();
    if (!s) continue;
    const eq = s.indexOf('=');
    if (eq <= 0) continue;
    const key = s.slice(0, eq).trim().toLowerCase();
    const val = s.slice(eq + 1).trim();
    if (!(key in out)) continue;
    const n = Number.parseInt(val, 10);
    out[key] = Number.isFinite(n) ? n : 0;
  }
  return out;
}

/**
 * Build a subscription-userinfo string from an object. Order fixed for
 * predictability, values always coerced to integers ≥ 0.
 */
export function buildUserinfo({ upload = 0, download = 0, total = 0, expire = 0 }) {
  const parts = [
    `upload=${Math.max(0, Math.trunc(upload) || 0)}`,
    `download=${Math.max(0, Math.trunc(download) || 0)}`,
    `total=${Math.max(0, Math.trunc(total) || 0)}`,
    `expire=${Math.max(0, Math.trunc(expire) || 0)}`,
  ];
  return parts.join('; ');
}

/**
 * Hybrid userinfo (FR-4): traffic counters from the GB upstream, expiry
 * from the MAIN upstream. If MAIN has no expire, fall back to GB.
 * Passing an already-parsed object OR the raw header — both work.
 */
export function mergeUserinfo(mainRaw, gbRaw) {
  const main = typeof mainRaw === 'string' ? parseUserinfo(mainRaw) : (mainRaw || {});
  const gb = typeof gbRaw === 'string' ? parseUserinfo(gbRaw) : (gbRaw || {});
  const expire = (main.expire && main.expire > 0) ? main.expire : (gb.expire || 0);
  return buildUserinfo({
    upload: gb.upload || 0,
    download: gb.download || 0,
    total: gb.total || 0,
    expire,
  });
}
