// Atlas Admin service worker.
//
// Two jobs:
//   1. Be registered so iOS Safari treats the dashboard as installable.
//   2. Handle web-push messages and forward them to the OS notification
//      center. Clicking a notification opens the dashboard (or focuses
//      an existing tab).

const SW_VERSION = "atlas-admin-v2";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Pass-through fetch: never serve a stale admin payload.
self.addEventListener("fetch", () => {});

// ── Web Push ────────────────────────────────────────────────────────

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_e) {
    try {
      data = { body: event.data && event.data.text() };
    } catch (_) {
      data = {};
    }
  }

  const title = data.title || "Atlas Admin";
  const options = {
    body: data.body || "",
    icon: data.icon || "/dashboard/icon-192.png",
    badge: data.badge || "/dashboard/icon-192.png",
    tag: data.tag || "atlas",
    data: { url: data.url || "/dashboard/" },
    // Re-show even if there's already one with the same tag.
    renotify: !!data.tag,
    requireInteraction: false,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/dashboard/";

  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      // Focus an existing tab if we already have one in the dashboard.
      for (const client of all) {
        if (client.url.includes("/dashboard")) {
          await client.focus();
          try {
            await client.navigate(url);
          } catch (_) {
            //
          }
          return;
        }
      }
      // Otherwise open a fresh window.
      await self.clients.openWindow(url);
    })(),
  );
});
