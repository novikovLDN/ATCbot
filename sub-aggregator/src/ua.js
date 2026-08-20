// User-Agent detection: browser vs VPN client vs unknown.
// One request URL, two response shapes — browsers get pretty HTML,
// clients get the raw base64 subscription body.

// Known VPN client User-Agent substrings. Case-insensitive match.
// Add new clients here — we err on the side of "give raw sub" when
// unsure, so this list favours breadth over precision.
const CLIENT_UA_PATTERNS = [
  /\bhapp\b/i,
  /\bv2rayng\b/i,
  /\bv2raytun\b/i,
  /\bv2box\b/i,
  /\bstreisand\b/i,
  /\bshadowrocket\b/i,
  /\bfoxray\b/i,
  /\bhiddify\b/i,
  /\bnekoray\b/i,
  /\bnekobox\b/i,
  /\bclash\b/i,
  /\bsing-?box\b/i,
  /\bstash\b/i,
  /\bshadowlink\b/i,
  /\boneclick\b/i,
  /\bconnectsettings\b/i,
];

// Browser UA hints. If ANY match AND no client pattern matches → HTML.
const BROWSER_UA_PATTERNS = [
  /\bmozilla\b/i,
  /\bchrome\b/i,
  /\bsafari\b/i,
  /\bfirefox\b/i,
  /\bedge\b/i,
  /\bopr\/|\bopera\b/i,
];

/**
 * @param {string|undefined|null} ua
 * @returns {'client'|'browser'|'unknown'}
 */
export function classifyUA(ua) {
  if (!ua || typeof ua !== 'string') return 'unknown';
  for (const re of CLIENT_UA_PATTERNS) {
    if (re.test(ua)) return 'client';
  }
  // Browser check ONLY if not a known client — some clients embed
  // "Mozilla/..." user agents.
  for (const re of BROWSER_UA_PATTERNS) {
    if (re.test(ua)) return 'browser';
  }
  return 'unknown';
}

/**
 * Should this UA get HTML? True for browsers, false for clients + unknown.
 * "Unknown gets raw" — safer default: if we misclassify a new client, it
 * still works; if we misclassify a scraper/monitor, they get a small body
 * instead of a full HTML page.
 */
export function wantsHtml(ua) {
  return classifyUA(ua) === 'browser';
}
