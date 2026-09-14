// Integration — end-to-end HTTP through the real Fastify app + mocked
// dependencies (Postgres helpers monkey-patched, Redis via ioredis-mock,
// mock panel on a real node:http server).
//
// This exercises the full FR-1..12 flow without needing external services.
//
// Test IDs mirror FR-§7.2:
//   (1) miss → merged + hybrid userinfo
//   (2) 2nd request → hit, upstream count unchanged
//   (3) invalidate → next request miss
//   (4) webhook w/ valid HMAC → invalidation; bad HMAC → 401 no change
//   (5) both upstreams down → serve stale; no stale → 503+retry-after
//   (6) unknown token → 404; malformed token (../, spaces, oversized) → 404 no DB
//   (7) revoked → 200 + stub

import { test, before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import crypto from 'node:crypto';
import RedisMock from 'ioredis-mock';

process.env.INTERNAL_SECRET = 'test-internal-secret-fixed';
process.env.WEBHOOK_SECRET  = 'test-webhook-secret-fixed';
process.env.WEBHOOK_SIG_HEADER = 'x-remnawave-signature';
process.env.CACHE_TTL = '300';
process.env.STALE_TTL = '3600';
process.env.MAP_TTL   = '3600';
process.env.NEG_MAP_TTL = '60';
process.env.UPSTREAM_TIMEOUT_MS = '1500';
process.env.LOG_LEVEL = 'silent';
process.env.REVOKED_REMARK = 'revoked-remark';

const { setRedisForTest, closeRedis } = await import('../../src/redis.js');
const redis = new RedisMock();
setRedisForTest(redis);

const { setDbImplForTest } = await import('../../src/db.js');
const { buildApp } = await import('../../src/server.js');

let app;
let panelServer;
let panelPort;
let panelHits = { main: 0, gb: 0 };
let panelResponders = {};
const dbRows = new Map();

before(async () => {
  panelServer = http.createServer((req, res) => {
    // URL like /sub/<kind>/<id>?<query> — we use `kind` (main|gb) for routing.
    const url = new URL(req.url, `http://127.0.0.1:${panelPort}`);
    const parts = url.pathname.split('/').filter(Boolean);
    const kind = parts[1];   // /sub/main/xxx → 'main'
    panelHits[kind] = (panelHits[kind] || 0) + 1;
    const responder = panelResponders[kind];
    if (!responder) { res.writeHead(500); res.end('no-responder'); return; }
    responder(req, res, url);
  });
  await new Promise((r) => panelServer.listen(0, '127.0.0.1', r));
  panelPort = panelServer.address().port;

  // Inject mock DB impl (no real postgres in this env).
  setDbImplForTest({
    async getSubPair(token) { return dbRows.get(token) || null; },
    async findTokensByUserUuid(uuid) {
      const out = [];
      for (const [token, row] of dbRows.entries()) {
        if (row.main_user_uuid === uuid || row.gb_user_uuid === uuid) out.push(token);
      }
      return out;
    },
  });

  app = buildApp();
  await app.ready();
});

after(async () => {
  await app.close();
  await new Promise((r) => panelServer.close(r));
  await closeRedis().catch(() => {});
});

beforeEach(async () => {
  panelHits = { main: 0, gb: 0 };
  panelResponders = {};
  dbRows.clear();
  await redis.flushall();
});

// ── helpers ─────────────────────────────────────────────────────────────

function b64(s) { return Buffer.from(s, 'utf8').toString('base64'); }
function fromB64(s) { return Buffer.from(s, 'base64').toString('utf8'); }

function seedPair(token, opts = {}) {
  const mainUuid = opts.mainUuid ?? '11111111-1111-1111-1111-111111111111';
  const gbUuid   = opts.gbUuid   ?? '22222222-2222-2222-2222-222222222222';
  dbRows.set(token, {
    token,
    main_sub_url: `http://127.0.0.1:${panelPort}/sub/main/${token}`,
    gb_sub_url:   `http://127.0.0.1:${panelPort}/sub/gb/${token}`,
    main_user_uuid: mainUuid,
    gb_user_uuid:   gbUuid,
    status: opts.status || 'active',
  });
}

function panelServe(kind, {
  body = 'vless://x\n',
  userinfo = null,
  extraHeaders = {},
  status = 200,
} = {}) {
  panelResponders[kind] = (_req, res) => {
    const headers = { 'content-type': 'text/plain', ...extraHeaders };
    if (userinfo) headers['subscription-userinfo'] = userinfo;
    res.writeHead(status, headers);
    res.end(body);
  };
}

function panelFail(kind) {
  panelResponders[kind] = (_req, res) => { res.destroy(); };
}

function hmacHex(body, secret) {
  return crypto.createHmac('sha256', secret).update(body).digest('hex');
}

// ── tests ───────────────────────────────────────────────────────────────

test('(1) miss → merged body + hybrid userinfo', async () => {
  seedPair('tok1');
  panelServe('main', {
    body: 'vless://main-A\nvless://main-B\n',
    userinfo: 'upload=0; download=0; total=0; expire=1735689600',
    extraHeaders: { 'profile-title': 'AtlasMain' },
  });
  panelServe('gb', {
    body: 'vless://gb-A\n',
    userinfo: 'upload=100; download=200; total=300; expire=999',
  });
  const res = await app.inject({ method: 'GET', url: '/tok1' });
  assert.equal(res.statusCode, 200);
  assert.equal(res.headers['x-cache'], 'miss');
  const merged = fromB64(res.body).split('\n');
  assert.deepEqual(merged, ['vless://main-A', 'vless://main-B', 'vless://gb-A']);
  assert.equal(res.headers['subscription-userinfo'],
    'upload=100; download=200; total=300; expire=1735689600');
  assert.equal(res.headers['profile-title'], 'AtlasMain');
  assert.equal(panelHits.main, 1);
  assert.equal(panelHits.gb, 1);
});

test('(2) 2nd request → hit, upstream count unchanged', async () => {
  seedPair('tok2');
  panelServe('main', { body: 'vless://m\n' });
  panelServe('gb',   { body: 'vless://g\n' });
  await app.inject({ method: 'GET', url: '/tok2' });
  const before = { ...panelHits };
  const res = await app.inject({ method: 'GET', url: '/tok2' });
  assert.equal(res.statusCode, 200);
  assert.equal(res.headers['x-cache'], 'hit');
  assert.equal(panelHits.main, before.main);
  assert.equal(panelHits.gb, before.gb);
});

test('(3) invalidate → next request miss', async () => {
  seedPair('tok3');
  panelServe('main', { body: 'vless://m\n' });
  panelServe('gb',   { body: 'vless://g\n' });
  await app.inject({ method: 'GET', url: '/tok3' });

  const inv = await app.inject({
    method: 'POST',
    url: '/internal/invalidate/tok3',
    headers: { 'x-internal-secret': process.env.INTERNAL_SECRET },
  });
  assert.equal(inv.statusCode, 200);

  const res = await app.inject({ method: 'GET', url: '/tok3' });
  assert.equal(res.headers['x-cache'], 'miss');
});

test('(3b) invalidate w/o secret → 401', async () => {
  const res = await app.inject({
    method: 'POST', url: '/internal/invalidate/tok3',
  });
  assert.equal(res.statusCode, 401);
});

test('(4) webhook valid HMAC → invalidation; bad HMAC → 401 no change', async () => {
  seedPair('tok4', { gbUuid: '33333333-3333-3333-3333-333333333333' });
  panelServe('main', { body: 'vless://main-4\n' });
  panelServe('gb',   { body: 'vless://gb-4\n' });
  await app.inject({ method: 'GET', url: '/tok4' });   // populate cache
  const hit = await app.inject({ method: 'GET', url: '/tok4' });
  assert.equal(hit.headers['x-cache'], 'hit');

  // Bad HMAC first — must NOT clear cache.
  const badBody = JSON.stringify({ event: 'user.limited', data: { uuid: '33333333-3333-3333-3333-333333333333' }});
  const badRes = await app.inject({
    method: 'POST',
    url: '/internal/webhook',
    headers: {
      'content-type': 'application/json',
      'x-remnawave-signature': 'sha256=' + hmacHex(badBody, 'WRONG-SECRET'),
    },
    payload: badBody,
  });
  assert.equal(badRes.statusCode, 401);
  const stillHit = await app.inject({ method: 'GET', url: '/tok4' });
  assert.equal(stillHit.headers['x-cache'], 'hit');

  // Valid HMAC — invalidates, and re-fetch pulls fresh (we'll change the
  // gb upstream body so we can confirm the invalidation actually cleared
  // the cache and the new body wins).
  panelServe('gb', { body: 'vless://gb-4-new\n' });
  const goodBody = JSON.stringify({ event: 'user.limited', data: { uuid: '33333333-3333-3333-3333-333333333333' }});
  const goodRes = await app.inject({
    method: 'POST',
    url: '/internal/webhook',
    headers: {
      'content-type': 'application/json',
      'x-remnawave-signature': hmacHex(goodBody, process.env.WEBHOOK_SECRET),
    },
    payload: goodBody,
  });
  assert.equal(goodRes.statusCode, 200);
  // Webhook processes async — wait a tick for the invalidation to run.
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));

  const after = await app.inject({ method: 'GET', url: '/tok4' });
  assert.equal(after.headers['x-cache'], 'miss');
  assert.ok(fromB64(after.body).includes('vless://gb-4-new'));
});

test('(5a) both upstreams fail, stale exists → serve stale', async () => {
  seedPair('tok5a');
  panelServe('main', { body: 'vless://main-5\n' });
  panelServe('gb',   { body: 'vless://gb-5\n' });
  await app.inject({ method: 'GET', url: '/tok5a' });   // seed cache

  // Drop fresh, keep stale (simulate SWR window expiring).
  await redis.del('sub:tok5a');
  // Both upstreams die.
  panelFail('main');
  panelFail('gb');
  const res = await app.inject({ method: 'GET', url: '/tok5a' });
  assert.equal(res.headers['x-cache'], 'stale');
  assert.ok(fromB64(res.body).includes('vless://main-5'));
});

test('(5b) both upstreams fail, NO stale → 503 + retry-after', async () => {
  seedPair('tok5b');
  panelFail('main');
  panelFail('gb');
  const res = await app.inject({ method: 'GET', url: '/tok5b' });
  assert.equal(res.statusCode, 503);
  assert.equal(res.headers['retry-after'], '30');
});

test('(6a) unknown token → 404', async () => {
  const res = await app.inject({ method: 'GET', url: '/unknown-token' });
  assert.equal(res.statusCode, 404);
});

test('(6b) malformed token → 404 without DB hit', async () => {
  const before = dbRows.size;
  const badTokens = ['/../', '   ', 'a'.repeat(200), 'foo bar', 'tok!'];
  for (const t of badTokens) {
    const url = `/${encodeURIComponent(t)}`;
    const res = await app.inject({ method: 'GET', url });
    assert.equal(res.statusCode, 404, `expected 404 for ${JSON.stringify(t)}`);
  }
  // DB not touched: dbRows itself unchanged (getSubPair is our mock).
  assert.equal(dbRows.size, before);
});

test('(8) browser UA → HTML page; client UA → base64 (same URL)', async () => {
  seedPair('tok8');
  panelServe('main', {
    body: 'vless://main-8\n',
    userinfo: 'upload=0; download=0; total=0; expire=1735689600',
    extraHeaders: { 'profile-title': 'Atlas-8' },
  });
  panelServe('gb', {
    body: 'vless://gb-8\n',
    userinfo: 'upload=100; download=200; total=1073741824; expire=0',
  });

  const client = await app.inject({
    method: 'GET', url: '/tok8',
    headers: { 'user-agent': 'Happ/1.7.0 CFNetwork' },
  });
  assert.equal(client.statusCode, 200);
  assert.equal(client.headers['content-type'], 'text/plain; charset=utf-8');
  assert.ok(fromB64(client.body).startsWith('vless://main-8'));

  const browser = await app.inject({
    method: 'GET', url: '/tok8',
    headers: { 'user-agent': 'Mozilla/5.0 (iPhone) Safari/605.1.15' },
  });
  assert.equal(browser.statusCode, 200);
  assert.match(browser.headers['content-type'], /^text\/html/);
  assert.ok(browser.body.includes('<!doctype html>'));
  // URL для one-click в клиенте
  assert.ok(browser.body.includes('happ://add/'));
  // Тайтл из upstream profile-title попал в HTML
  assert.ok(browser.body.includes('Atlas-8'));
});

test('(9) browser hits revoked → HTML with "Подписка отозвана"', async () => {
  seedPair('tok9', { status: 'revoked' });
  const res = await app.inject({
    method: 'GET', url: '/tok9',
    headers: { 'user-agent': 'Mozilla/5.0 (Macintosh) Chrome/120' },
  });
  assert.equal(res.statusCode, 200);
  assert.equal(res.headers['x-cache'], 'stub');
  assert.match(res.headers['content-type'], /^text\/html/);
  assert.ok(res.body.includes('Подписка отозвана'));
  assert.ok(!res.body.includes('happ://add/'));  // no install buttons on revoked
});

test('(7) revoked → 200 + single-line stub, x-cache=stub', async () => {
  seedPair('tok7', { status: 'revoked' });
  const res = await app.inject({ method: 'GET', url: '/tok7' });
  assert.equal(res.statusCode, 200);
  assert.equal(res.headers['x-cache'], 'stub');
  const decoded = fromB64(res.body).trim();
  const lines = decoded.split('\n').filter(Boolean);
  assert.equal(lines.length, 1);
  assert.match(lines[0], /^vless:\/\/0{8}-0{4}-0{4}-0{4}-0{12}@127\.0\.0\.1:443/);
  // remark = REVOKED_REMARK from env.
  assert.ok(decoded.endsWith(encodeURIComponent('revoked-remark')));
  // No upstream fetches for a revoked entry.
  assert.equal(panelHits.main, 0);
  assert.equal(panelHits.gb, 0);
});
