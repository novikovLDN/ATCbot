// Unit — revoked stub (FR-7).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildStub } from '../../src/stub.js';

test('buildStub — default remark, single vless line, base64-encoded body', () => {
  const s = buildStub();
  const decoded = Buffer.from(s.body, 'base64').toString('utf8').trim();
  assert.match(decoded, /^vless:\/\/00000000-0000-0000-0000-000000000000@127\.0\.0\.1:443/);
  assert.match(decoded, /security=none/);
  assert.equal(s.headers['content-type'], 'text/plain; charset=utf-8');
  assert.equal(s.headers['subscription-userinfo'], 'upload=0; download=0; total=0; expire=0');
});

test('buildStub — remark URL-encoded in fragment', () => {
  const s = buildStub('Русский текст with spaces');
  const decoded = Buffer.from(s.body, 'base64').toString('utf8');
  // Cyrillic + space must appear as percent-encoded octets.
  assert.match(decoded, /#[%A-Za-z0-9]+$/m);
  // Original spaces must NOT be present in the fragment.
  const frag = decoded.split('#').pop();
  assert.ok(!frag.includes(' '), 'fragment must be url-encoded, no raw spaces');
  // Human-readable title header should be the raw label.
  assert.equal(s.headers['profile-title'], 'Русский текст with spaces');
});
