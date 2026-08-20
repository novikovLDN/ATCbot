// Stub body for revoked / limited subscriptions. FR-7.
// Single vless line pointing to 127.0.0.1 — клиент "видит" ссылку, показывает
// remark (человекочитаемая причина), никаких коннектов не устанавливает.
import { config } from './config.js';

const REVOKED_UUID = '00000000-0000-0000-0000-000000000000';

/**
 * Return { body: base64 string, headers } for a revoked stub. Optional
 * `remark` overrides config default.
 */
export function buildStub(remark) {
  const label = remark || config.revokedRemark || 'revoked';
  const encoded = encodeURIComponent(label);
  const vless = `vless://${REVOKED_UUID}@127.0.0.1:443?type=tcp&security=none#${encoded}`;
  const b64 = Buffer.from(vless + '\n', 'utf8').toString('base64');
  return {
    body: b64,
    headers: {
      'content-type': 'text/plain; charset=utf-8',
      // 0/0/0/0 — клиент показывает бэйдж "нет трафика"; полезно на UI.
      'subscription-userinfo': 'upload=0; download=0; total=0; expire=0',
      'profile-title': label,
    },
  };
}
