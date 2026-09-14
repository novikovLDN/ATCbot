// Node sidecar — wraps @incy/link-encoder for the Python bot.
//
// Usage:  node scripts/incy_encode.mjs <subscription_url>
// stdout: incy://crypt1/<base64url>
// exit 0 on success, 1 on usage error, 2 on encode failure.
//
// Why a sidecar:
//   The AES-256-GCM key Incy uses is shipped inside the npm package
//   (`derived from constants and binary assets shipped inside this
//    package`). We can't re-derive it from Python without re-implementing
//   the package internals, so we just call the upstream encoder.
//
// Resolved relative to __file__ in the Python wrapper, so cwd does not
// matter and the bot can run from anywhere.

import { encryptLink } from "@incy/link-encoder";

const url = process.argv[2];
if (!url) {
  process.stderr.write("usage: incy_encode.mjs <url>\n");
  process.exit(1);
}

try {
  // `name` shows up in the Incy app once the subscription is imported;
  // it's a label, not a unique key.
  const link = encryptLink(url, { name: "Atlas Secure" });
  process.stdout.write(link);
} catch (err) {
  process.stderr.write(`encode failed: ${err && err.message ? err.message : err}\n`);
  process.exit(2);
}
