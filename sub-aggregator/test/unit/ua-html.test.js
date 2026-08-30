// Unit — UA classification + HTML rendering (browser branch).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { classifyUA, wantsHtml } from '../../src/ua.js';
import { renderSubPage } from '../../src/html.js';

test('classifyUA — VPN clients', () => {
  for (const ua of [
    'Happ/1.7.0 CFNetwork/1568.100.1',
    'v2rayNG/1.10.19',
    'V2RayTun/2.5 (iOS 17.0)',
    'Streisand/1.0',
    'Shadowrocket/2.2.55',
    'Hiddify/2.0',
    'Nekoray/2.3',
    'clash-verge-rev/2.0',
    'sing-box/1.9.0',
  ]) {
    assert.equal(classifyUA(ua), 'client', `expected client for ${JSON.stringify(ua)}`);
    assert.equal(wantsHtml(ua), false);
  }
});

test('classifyUA — browsers', () => {
  for (const ua of [
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1) AppleWebKit/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15) Firefox/128.0',
    'Mozilla/5.0 (Linux; Android 14) Chrome/126.0.0.0',
  ]) {
    assert.equal(classifyUA(ua), 'browser');
    assert.equal(wantsHtml(ua), true);
  }
});

test('classifyUA — unknown / curl / empty → not html', () => {
  for (const ua of ['curl/8.5.0', '', undefined, null, 'ELB-HealthChecker/2.0']) {
    assert.equal(wantsHtml(ua), false);
  }
});

test('renderSubPage — active page contains url + install buttons + escapes profile-title', () => {
  const html = renderSubPage({
    publicUrl: 'https://sub.example.com/tok123',
    subUserinfo: 'upload=100; download=200; total=1073741824; expire=1735689600',
    profileTitle: '<script>alert(1)</script>',
    isRevoked: false,
  });
  assert.ok(html.includes('https://sub.example.com/tok123'));
  assert.ok(html.includes('happ://add/https://sub.example.com/tok123'));
  assert.ok(html.includes('v2raytun://'));
  // XSS protection: raw <script> из profile-title не должен появиться.
  assert.ok(!html.includes('<script>alert(1)</script>'));
  assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
  // Traffic stats визуализируются.
  assert.ok(html.includes('1.0 ГБ') || html.includes('1 ГБ'));
});

test('renderSubPage — revoked path shows dedicated notice, no install buttons', () => {
  const html = renderSubPage({
    publicUrl: 'https://sub.example.com/tok999',
    subUserinfo: '',
    isRevoked: true,
  });
  assert.ok(html.includes('Подписка отозвана'));
  assert.ok(!html.includes('happ://add/'));
});

test('renderSubPage — QR SVG embedded inline (no external assets)', () => {
  const html = renderSubPage({
    publicUrl: 'https://sub.example.com/qr-test',
    subUserinfo: '',
    isRevoked: false,
  });
  // qrcode-svg produces `<svg ...>...</svg>` — check that a big-enough
  // SVG block is present.
  const svgMatches = html.match(/<svg[\s\S]+?<\/svg>/g) || [];
  // logo SVG + QR SVG — at least 2.
  assert.ok(svgMatches.length >= 2, `expected ≥2 inline SVGs, got ${svgMatches.length}`);
});
