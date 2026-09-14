/**
 * User-domain vocabulary: how a tariff, a provider, a purchase or a
 * subscription state is named and classified.
 *
 * Extracted from the page component because these are product facts, not
 * presentation. Keeping them next to JSX is how the screen ended up with
 * two disagreeing definitions of "paid" — see `isPaidStatus`.
 */
import type { PurchaseRow, UserSubscription } from "@/types/user";

export const TARIFF_RU: Record<string, string> = {
  basic: "Basic",
  plus: "Plus",
};

export function tariffLabel(t: string | null | undefined): string {
  if (!t) return "—";
  return TARIFF_RU[t] ?? t;
}

export const PROVIDER_RU: Record<string, string> = {
  platega: "Platega",
  cryptobot: "CryptoBot",
  telegram_stars: "Stars",
  wata: "WATA",
  // Historical rows only: Lava was removed, old payments still carry it.
  lava: "Lava",
  balance: "С баланса",
  unknown: "—",
};

export function providerLabel(p: string | null | undefined): string {
  if (!p) return "—";
  return PROVIDER_RU[p] ?? p;
}

export function purchaseLabel(p: PurchaseRow): string {
  const t = p.purchase_type ?? "subscription";
  if (t === "subscription") {
    const tariff = p.tariff ? TARIFF_RU[p.tariff] ?? p.tariff : "Подписка";
    const period = p.period_days ? ` · ${p.period_days} дн` : "";
    const combo = p.is_combo ? " (комбо)" : "";
    return `${tariff}${period}${combo}`;
  }
  if (t === "traffic_pack") {
    return p.country ? `Traffic-пак · ${p.country.toUpperCase()}` : "Traffic-пак";
  }
  if (t === "balance_topup") return "Пополнение баланса";
  if (t === "telegram_premium") return "Telegram Premium";
  if (t === "steam") return "Steam пополнение";
  if (t === "proxy") return "Прокси";
  if (t === "farm_plot") {
    const id = (p as { farm_plot_id?: number | null }).farm_plot_id;
    return id ? `Фарм-участок #${id}` : "Фарм-участок";
  }
  return t;
}

/**
 * The single definition of a purchase having brought money in.
 *
 * The screen used to carry two. The status badge treated both `approved`
 * and `paid` as paid, while the totals directly above it summed only
 * `paid` — so every user whose provider reports `approved` had their
 * spend silently understated, next to a badge saying the payment went
 * through. Anything reading "how much did they pay" must call this.
 */
const PAID_STATUSES = new Set(["paid", "approved"]);

export function isPaidStatus(status: string | null | undefined): boolean {
  return !!status && PAID_STATUSES.has(status.toLowerCase());
}

export type PaymentTone = "paid" | "pending" | "expired" | "failed" | "unknown";

export function paymentTone(status: string | null | undefined): PaymentTone {
  const s = (status ?? "").toLowerCase();
  if (isPaidStatus(s)) return "paid";
  if (s === "pending" || s === "processing") return "pending";
  if (s === "expired") return "expired";
  if (s === "failed" || s === "rejected" || s === "cancelled") return "failed";
  return "unknown";
}

/**
 * Whether a subscription is genuinely usable right now.
 *
 * The backend used to answer this by returning null for anything expired,
 * which meant a lapsed user's card showed nothing at all — no history, no
 * reason, no last tariff. Now the row always comes back and the judgement
 * is made here.
 */
export function isSubscriptionActive(
  sub: UserSubscription | null | undefined,
): boolean {
  if (!sub) return false;
  if (sub.status && sub.status !== "active") return false;
  if (!sub.expires_at) return false;
  return new Date(sub.expires_at).getTime() > Date.now();
}

export type ActivationTone = "ok" | "pending" | "failed" | "unknown";

/**
 * Classifies the "they paid and got nothing" state.
 *
 * `activation_status`, `activation_attempts` and `last_activation_error`
 * all arrive with every user detail response and none of them were shown.
 * The failure they describe was only visible by opening a different screen
 * (/activations/pending) and knowing to look.
 */
export function activationTone(sub: UserSubscription | null): ActivationTone {
  if (!sub) return "unknown";
  const s = (sub.activation_status ?? "").toLowerCase();
  if (s === "active" || s === "ok" || s === "success") return "ok";
  if (s === "pending" || s === "retrying" || s === "processing") return "pending";
  if (s === "failed" || s === "error") return "failed";
  // No explicit status, but an error string and attempts means it tried
  // and did not finish.
  if (sub.last_activation_error) return "failed";
  return sub.activated_at ? "ok" : "unknown";
}

export const SOURCE_RU: Record<string, string> = {
  payment: "Оплата",
  admin: "Выдано вручную",
  trial: "Триал",
};

export function sourceLabel(s: string | null | undefined): string {
  if (!s) return "—";
  return SOURCE_RU[s] ?? s;
}

/**
 * Has the user ever actually connected?
 *
 * A subscription that was paid for, activated, and never passed a byte is
 * the earliest churn signal available — earlier than a missed renewal,
 * because by then the money is already gone.
 */
export function hasEverConnected(sub: UserSubscription | null): boolean {
  if (!sub) return false;
  return !!sub.first_traffic_at || (sub.last_bytes ?? 0) > 0;
}

/** Bytes → human units. Traffic values arrive raw from Remnawave. */
export function fmtBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return "—";
  if (bytes <= 0) return "0";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}
