// Unit — body merge, dedupe, header selection (FR-3, FR-4).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mergeUpstreams, decodeBodyToLines } from '../../src/aggregator.js';

function b64(s) { return Buffer.from(s, 'utf8').toString('base64'); }
function fromB64(s) { return Buffer.from(s, 'base64').toString('utf8'); }

test('decodeBodyToLines — plaintext splits on newlines', () => {
  const lines = decodeBodyToLines('vless://a\nvless://b\n\n');
  assert.deepEqual(lines, ['vless://a', 'vless://b']);
});

test('decodeBodyToLines — base64 decodes then splits', () => {
  const body = b64('vless://x\nvless://y\n');
  assert.deepEqual(decodeBodyToLines(body), ['vless://x', 'vless://y']);
});

test('decodeBodyToLines — plaintext with scheme prefix isn\'t mistaken for base64', () => {
  // A pathological string that is ALSO valid base64 mustn't be decoded if
  // it starts with a scheme. Our heuristic checks scheme first — good.
  assert.deepEqual(decodeBodyToLines('vless://YWJj\n'), ['vless://YWJj']);
});

test('mergeUpstreams — dedupe exact strings, order main first', () => {
  const main = {
    body: 'vless://main-1\nvless://shared\n',
    headers: { 'subscription-userinfo': 'upload=0; download=0; total=0; expire=100' },
  };
  const gb = {
    body: 'vless://shared\nvless://gb-1\n',
    headers: { 'subscription-userinfo': 'upload=1; download=2; total=3; expire=999' },
  };
  const out = mergeUpstreams(main, gb);
  const lines = fromB64(out.body).split('\n');
  assert.deepEqual(lines, ['vless://main-1', 'vless://shared', 'vless://gb-1']);
  // Hybrid: traffic from gb, expire from main.
  assert.equal(out.headers['subscription-userinfo'], 'upload=1; download=2; total=3; expire=100');
});

test('mergeUpstreams — preserves URL-encoded remarks inside vless line', () => {
  const remark = '%E2%9C%85%20AtlasSecure';   // ✅ AtlasSecure
  const line = `vless://uuid@host:443?type=tcp#${remark}`;
  const merged = mergeUpstreams(
    { body: line + '\n', headers: {} },
    { body: '', headers: {} },
  );
  assert.equal(fromB64(merged.body), line);
});

test('mergeUpstreams — forwards allowed main headers, drops unknown', () => {
  const main = {
    body: 'vless://a\n',
    headers: {
      'profile-title':          'MyProfile',
      'profile-update-interval':'24',
      'x-secret-panel-header':  'should-not-leak',
      'set-cookie':             'session=abc',
    },
  };
  const gb = { body: 'vless://b\n', headers: {} };
  const out = mergeUpstreams(main, gb);
  assert.equal(out.headers['profile-title'], 'MyProfile');
  assert.equal(out.headers['profile-update-interval'], '24');
  assert.equal(out.headers['x-secret-panel-header'], undefined);
  assert.equal(out.headers['set-cookie'], undefined);
  // Content-type is always set by us.
  assert.equal(out.headers['content-type'], 'text/plain; charset=utf-8');
});

test('mergeUpstreams — plaintext main + base64 gb both decoded', () => {
  const out = mergeUpstreams(
    { body: 'vless://plain-main\n', headers: {} },
    { body: Buffer.from('vless://from-b64\n', 'utf8').toString('base64'), headers: {} },
  );
  assert.deepEqual(fromB64(out.body).split('\n'), ['vless://plain-main', 'vless://from-b64']);
});

test('mergeUpstreams — empty upstreams → empty body', () => {
  const out = mergeUpstreams({ body: '', headers: {} }, { body: '', headers: {} });
  assert.equal(fromB64(out.body), '');
});

test('mergeUpstreams — panel stub line survives merge (revoked side case)', () => {
  const stubLine = 'vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443?type=tcp&security=none#Subscription%20revoked';
  const out = mergeUpstreams(
    { body: 'vless://main-a\n', headers: {} },
    { body: stubLine + '\n', headers: {} },
  );
  const lines = fromB64(out.body).split('\n');
  assert.equal(lines.length, 2);
  assert.equal(lines[1], stubLine);
});
