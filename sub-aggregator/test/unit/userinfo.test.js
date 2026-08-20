// Unit — subscription-userinfo parse/merge (FR-4).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseUserinfo, buildUserinfo, mergeUserinfo } from '../../src/userinfo.js';

test('parseUserinfo — full string', () => {
  const p = parseUserinfo('upload=1; download=2; total=3; expire=4');
  assert.deepEqual(p, { upload: 1, download: 2, total: 3, expire: 4 });
});

test('parseUserinfo — missing fields default 0', () => {
  const p = parseUserinfo('upload=10; total=100');
  assert.deepEqual(p, { upload: 10, download: 0, total: 100, expire: 0 });
});

test('parseUserinfo — empty / null / undefined', () => {
  for (const v of [undefined, null, '', '   ']) {
    assert.deepEqual(parseUserinfo(v), { upload: 0, download: 0, total: 0, expire: 0 });
  }
});

test('parseUserinfo — garbage tolerated', () => {
  const p = parseUserinfo('upload=abc; foo=bar; download=99');
  assert.deepEqual(p, { upload: 0, download: 99, total: 0, expire: 0 });
});

test('buildUserinfo — deterministic order', () => {
  assert.equal(
    buildUserinfo({ total: 3, expire: 4, download: 2, upload: 1 }),
    'upload=1; download=2; total=3; expire=4',
  );
});

test('mergeUserinfo — traffic from gb, expire from main', () => {
  const merged = mergeUserinfo(
    'upload=1; download=2; total=3; expire=1735689600',
    'upload=100; download=200; total=300; expire=0',
  );
  assert.equal(merged, 'upload=100; download=200; total=300; expire=1735689600');
});

test('mergeUserinfo — expire falls back to gb when main has none', () => {
  const merged = mergeUserinfo(
    'upload=1; download=2; total=3',                // no expire
    'upload=10; download=20; total=30; expire=999', // has expire
  );
  assert.equal(merged, 'upload=10; download=20; total=30; expire=999');
});

test('mergeUserinfo — both headers missing → all zeros', () => {
  assert.equal(mergeUserinfo(undefined, undefined),
    'upload=0; download=0; total=0; expire=0');
});
