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
  // Moscow time, like every day bucket and schedule in the panel — not the
  // viewer's device zone (an admin travelling would see shifted times).
  return d.toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow",
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

/** «1,2 ТБ» / «12,3 ГБ» / «450 МБ» / «120 КБ». Отрицательное значение обнуляем. */
export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const v = Math.max(0, Number(n));
  const one = (x: number) => x.toFixed(1).replace(".", ",");
  if (v >= 1024 ** 4) return `${one(v / 1024 ** 4)} ТБ`;
  if (v >= 1024 ** 3) return `${one(v / 1024 ** 3)} ГБ`;
  if (v >= 1024 ** 2) return `${(v / 1024 ** 2).toFixed(0)} МБ`;
  if (v >= 1024) return `${(v / 1024).toFixed(0)} КБ`;
  return `${v} Б`;
}

/** Дней до ISO-даты (округление вниз, минимум 0). */
export function daysUntil(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const diffMs = t - Date.now();
  if (diffMs <= 0) return 0;
  return Math.floor(diffMs / (1000 * 60 * 60 * 24));
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


/**
 * Kopecks → rubles, for display only.
 *
 * The API speaks kopecks end to end. Four different unit conventions
 * used to coexist — some endpoints returned rubles, some kopecks, and
 * Analytics guessed by magnitude (`raw >= 100_000 ? raw/100 : raw`),
 * which showed any tariff under 1000 ₽ a hundred times larger than the
 * same tariff on the main screen. Conversion belongs here and nowhere
 * else.
 */
export function fmtKop(kopecks: number | null | undefined): string {
  if (kopecks == null || Number.isNaN(kopecks)) return "—";
  return fmtRub(kopecks / 100);
}

/**
 * Compact form for tiles, where a full number would wrap: 12.9K, 4.2M.
 * Below 10 000 the exact figure fits, and rounding it away loses
 * precision the reader can actually use.
 */
export function fmtCompactKop(kopecks: number | null | undefined): string {
  if (kopecks == null || Number.isNaN(kopecks)) return "—";
  const rub = kopecks / 100;
  if (Math.abs(rub) < 10_000) return fmtRub(rub);
  if (Math.abs(rub) < 1_000_000) return `${(rub / 1000).toFixed(1).replace(".", ",")} тыс ₽`;
  return `${(rub / 1_000_000).toFixed(2).replace(".", ",")} млн ₽`;
}

const PCT = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

/** "12,5%" — percent values arrive from the API already ×100. */
export function fmtPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${PCT.format(n)}%`;
}

/** 12 900 → "12,9 тыс", 1 250 000 → "1,25 млн". */
export function fmtCompactNum(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs < 10_000) return NUM.format(n);
  if (abs < 1_000_000) return `${(n / 1000).toFixed(1).replace(".", ",")} тыс`;
  return `${(n / 1_000_000).toFixed(2).replace(".", ",")} млн`;
}

/** Moscow calendar date "2026-09-12" (as the API sends it) → "12 сен". */
export function fmtDay(iso: string): string {
  const [, m, d] = iso.split("-");
  if (!m || !d) return iso;
  return `${Number(d)} ${MONTHS[Number(m) - 1] ?? ""}`;
}

/** "2026-09-01" → "сен 2026". */
export function fmtMonth(iso: string): string {
  const [y, m] = iso.split("-");
  if (!y || !m) return iso;
  return `${MONTHS[Number(m) - 1] ?? ""} ${y}`;
}

/** Axis ticks: short, no currency — the tile title already says what it is. */
export function fmtAxisKop(kopecks: number): string {
  const rub = kopecks / 100;
  if (Math.abs(rub) >= 1_000_000) return `${(rub / 1_000_000).toFixed(1).replace(".", ",")} млн`;
  if (Math.abs(rub) >= 1_000) return `${Math.round(rub / 1000)} тыс`;
  return String(Math.round(rub));
}

export function fmtAxisBytes(bytes: number): string {
  if (bytes >= 1024 ** 4) return `${(bytes / 1024 ** 4).toFixed(1).replace(".", ",")} ТБ`;
  return `${Math.round(bytes / 1024 ** 3)} ГБ`;
}

/** Seconds → "45 с", "3 мин 20 с", "2 ч 15 мин", "3 дн 4 ч". */
export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return "—";
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s} с`;
  const m = Math.floor(s / 60);
  if (m < 60) {
    const r = s % 60;
    return r && m < 10 ? `${m} мин ${r} с` : `${m} мин`;
  }
  const h = Math.floor(m / 60);
  if (h < 48) {
    const r = m % 60;
    return r ? `${h} ч ${r} мин` : `${h} ч`;
  }
  const d = Math.floor(h / 24);
  const r = h % 24;
  return r ? `${d} дн ${r} ч` : `${d} дн`;
}
