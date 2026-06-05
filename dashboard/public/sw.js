// Minimal service worker — required by iOS for proper PWA install,
// even when we don't actually cache anything. We pass through to
// network for everything to keep the dashboard always fresh (admin
// data should never be stale from a SW cache).
//
// If/when we want offline-first behaviour, add cacheStorage logic
// here; the registration in main.tsx will pick it up on next visit.

const SW_VERSION = "atlas-admin-v1";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", () => {
  // No-op: let the browser handle everything. The SW just needs to
  // exist for iOS to treat the site as installable.
});
