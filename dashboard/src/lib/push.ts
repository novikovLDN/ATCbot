import { api } from "./api";

/**
 * Web Push helpers. The browser side of /settings/push/*.
 *
 * Flow:
 *   1. isPushSupported() — feature detect SW + PushManager + Notification.
 *   2. getPushPermission() — read current state (granted/denied/default).
 *   3. enablePush() — ask permission, fetch VAPID key, subscribe via
 *      PushManager, POST subscription to server.
 *   4. disablePushOnThisDevice() — unsubscribe locally + tell server.
 */

export function isPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export function getPushPermission(): NotificationPermission {
  if (!isPushSupported()) return "denied";
  return Notification.permission;
}

export function isIOS(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  return (
    /iPhone|iPod/i.test(ua) ||
    /iPad/i.test(ua) ||
    (navigator.platform === "MacIntel" &&
      (navigator as { maxTouchPoints?: number }).maxTouchPoints !== undefined &&
      ((navigator as unknown as { maxTouchPoints: number }).maxTouchPoints ?? 0) > 1)
  );
}

export function isStandalonePWA(): boolean {
  if (typeof window === "undefined") return false;
  if ((window.navigator as unknown as { standalone?: boolean }).standalone) return true;
  if (window.matchMedia?.("(display-mode: standalone)").matches) return true;
  return false;
}

/**
 * iOS Safari only supports Web Push from PWAs installed to Home
 * Screen (iOS 16.4+). In a regular Safari tab, subscribe() will throw
 * even though the feature-detect passes.
 */
export function iosNeedsHomeScreen(): boolean {
  return isIOS() && !isStandalonePWA();
}

function urlBase64ToArrayBuffer(base64String: string): ArrayBuffer {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  // Explicitly construct ArrayBuffer (not SharedArrayBuffer) so the
  // PushManager.subscribe applicationServerKey typecheck accepts it.
  const ab = new ArrayBuffer(raw.length);
  const view = new Uint8Array(ab);
  for (let i = 0; i < raw.length; i++) view[i] = raw.charCodeAt(i);
  return ab;
}

export async function enablePush(label?: string): Promise<void> {
  if (!isPushSupported()) throw new Error("not_supported");

  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("permission_denied");

  const reg = await navigator.serviceWorker.ready;

  // Reuse an existing subscription if there is one — the user might
  // have already enabled push on this browser and the server might
  // have lost the record.
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    const { publicKey } = await api.get<{ publicKey: string }>(
      "/settings/push/vapid-key",
    );
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToArrayBuffer(publicKey),
    });
  }

  const json = sub.toJSON();
  const p256dh = json.keys?.p256dh ?? "";
  const auth = json.keys?.auth ?? "";
  if (!json.endpoint || !p256dh || !auth) {
    throw new Error("bad_subscription");
  }

  await api.post("/settings/push/subscribe", {
    endpoint: json.endpoint,
    p256dh,
    auth,
    user_agent: navigator.userAgent.slice(0, 280),
    label: label ?? deriveDeviceLabel(),
  });
}

export async function disablePushOnThisDevice(): Promise<void> {
  if (!isPushSupported()) return;
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (!sub) return;
  const endpoint = sub.endpoint;
  try {
    await sub.unsubscribe();
  } catch {
    //
  }
  try {
    await api.post("/settings/push/unsubscribe", { endpoint });
  } catch {
    //
  }
}

export async function isSubscribedHere(): Promise<boolean> {
  if (!isPushSupported()) return false;
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    return !!sub;
  } catch {
    return false;
  }
}

export interface PushSubscriptionRow {
  id: number;
  endpoint: string;
  user_agent?: string;
  label?: string;
  created_at?: string;
  last_used_at?: string;
}

export const listPushSubscriptions = () =>
  api.get<PushSubscriptionRow[]>("/settings/push/subscriptions");

export const sendPushTest = () =>
  api.post<{ sent: number; failed: number; removed: number; total: number }>(
    "/settings/push/test",
  );

function deriveDeviceLabel(): string {
  const ua = navigator.userAgent;
  if (/iPhone/i.test(ua)) return "iPhone";
  if (/iPad/i.test(ua)) return "iPad";
  if (/Android/i.test(ua)) return "Android";
  if (/Mac/i.test(ua)) return "Mac";
  if (/Windows/i.test(ua)) return "Windows";
  if (/Linux/i.test(ua)) return "Linux";
  return "Браузер";
}
