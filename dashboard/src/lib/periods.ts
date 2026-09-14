/** Period pickers shared by the metric screens (Moscow days). */
export const WINDOWS = [
  { value: 7, label: "7 дней" },
  { value: 30, label: "30 дней" },
  { value: 90, label: "90 дней" },
];

/** The overview compares short windows: today, a week, a month. */
export const OVERVIEW_PERIODS = [
  { value: 1, label: "Сегодня" },
  { value: 7, label: "7 дней" },
  { value: 30, label: "30 дней" },
];

/** How the previous period reads next to a delta (like-for-like). */
export function comparedTo(days: number): string {
  return days === 1 ? "ко вчера до этого часа" : `к прошлым ${days} дн.`;
}
