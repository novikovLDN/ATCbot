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
