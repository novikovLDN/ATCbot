const RUB = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 0,
});

const NUM = new Intl.NumberFormat("ru-RU");

export function fmtRub(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return RUB.format(n);
}

export function fmtNum(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return NUM.format(n);
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = typeof iso === "string" ? new Date(iso) : iso;
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = Date.now();
  const diff = d.getTime() - now;
  const abs = Math.abs(diff);
  const day = 86400000;
  const hour = 3600000;
  const min = 60000;
  let value: number;
  let unit: Intl.RelativeTimeFormatUnit;
  if (abs >= day) {
    value = Math.round(diff / day);
    unit = "day";
  } else if (abs >= hour) {
    value = Math.round(diff / hour);
    unit = "hour";
  } else if (abs >= min) {
    value = Math.round(diff / min);
    unit = "minute";
  } else {
    value = Math.round(diff / 1000);
    unit = "second";
  }
  return new Intl.RelativeTimeFormat("ru", { numeric: "auto" }).format(value, unit);
}

export function truncate(s: string, max = 32): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

/** Today's 00:00 Europe/Moscow as a UTC instant.
 * Moscow is fixed at UTC+3 (no DST since 2014), so we just take
 * the current UTC time, add 3 hours, floor to the day, subtract
 * 3 hours back. Returned as an ISO string the backend parses with
 * datetime.fromisoformat. */
export function mskTodayStartIso(now: Date = new Date()): string {
  const mskMs = now.getTime() + 3 * 3600 * 1000;
  const mskDay = Math.floor(mskMs / 86400000);
  const startUtcMs = mskDay * 86400000 - 3 * 3600 * 1000;
  return new Date(startUtcMs).toISOString();
}

/** Cache key that flips at MSK midnight — "2026-06-06" etc. Use this
 * as a React Query key segment so the cache invalidates at 00:00 МСК
 * even if the user keeps the dashboard open overnight. */
export function mskDayKey(now: Date = new Date()): string {
  const mskMs = now.getTime() + 3 * 3600 * 1000;
  const d = new Date(mskMs);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
