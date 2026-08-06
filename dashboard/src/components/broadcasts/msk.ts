/**
 * Московское время для планировщика.
 *
 * ПОЧЕМУ ВСЁ РУКАМИ, А НЕ ЧЕРЕЗ toLocaleString. Планировщик на сервере
 * принимает и отдаёт «стенные» часы Москвы (`Europe/Moscow`, UTC+3 без
 * перевода стрелок с 2014 года), а `<input type="datetime-local">`
 * работает в часовом поясе браузера. Админ может сидеть в любом поясе;
 * если просто отдать локальное время, рассылка уйдёт со сдвигом на
 * несколько часов — и это заметят только получатели.
 *
 * Смещение зашито числом (+3), а не берётся из Intl, сознательно: с
 * фиксированным смещением у пояса нет летнего времени, и сдвиг не
 * «поедет» из-за версии базы часовых поясов в браузере.
 */

const MSK_OFFSET_MIN = 3 * 60;
const pad = (n: number) => String(n).padStart(2, "0");

/**
 * Сейчас в Москве плюс N минут, в формате `YYYY-MM-DDTHH:MM` для
 * `<input type="datetime-local">`.
 *
 * ЗДЕСЬ БЫЛА ОШИБКА, И ОНА СТОИЛА ЦЕЛОЙ ФУНКЦИИ. Прежняя версия
 * подмешивала в расчёт `getTimezoneOffset()` браузера — и совпадала с
 * московским временем ровно в одном случае, когда браузер стоит на
 * UTC+0. Из Москвы (UTC+3) она давала время на три часа назад: при
 * значении по умолчанию «через час» в поле оказывалось время на два
 * часа раньше настоящего, сервер отвечал «scheduled_at is in the past»,
 * и отложить рассылку не получалось вовсе.
 *
 * Правильно так: московское стенное время — это UTC плюс три часа, и
 * пояс браузера в этом расчёте не участвует ни в каком виде. Сдвигаем
 * момент на три часа вперёд и читаем его UTC-представление — оно и есть
 * московские часы. Появится здесь `getTimezoneOffset()` — ошибка
 * вернётся.
 */
export function mskInputValue(minutesAhead = 0): string {
  const msk = new Date(Date.now() + (MSK_OFFSET_MIN + minutesAhead) * 60_000);
  return (
    `${msk.getUTCFullYear()}-${pad(msk.getUTCMonth() + 1)}-${pad(msk.getUTCDate())}` +
    `T${pad(msk.getUTCHours())}:${pad(msk.getUTCMinutes())}`
  );
}

/** `YYYY-MM-DDTHH:MM` из поля ввода → `YYYY-MM-DD HH:MM`, как ждёт API
 *  (`_parse_msk` принимает оба, но пробел — задокументированный вид). */
export function toApiMsk(inputValue: string): string {
  return inputValue.replace("T", " ");
}

/** UTC-метка от сервера → «дд.мм чч:мм» по Москве. */
export function fmtMsk(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const msk = new Date(d.getTime() + (d.getTimezoneOffset() + MSK_OFFSET_MIN) * 60_000);
  return `${pad(msk.getDate())}.${pad(msk.getMonth() + 1)} ${pad(msk.getHours())}:${pad(
    msk.getMinutes(),
  )}`;
}

/** JS-номер дня недели (0 — воскресенье) для значения поля ввода.
 *  Считаем по «стенной» дате, поэтому пояс браузера роли не играет. */
export function weekdayOf(inputValue: string): number {
  const [datePart] = inputValue.split("T");
  const [y, m, d] = datePart.split("-").map(Number);
  return new Date(y, m - 1, d).getDay();
}

/** Двигает дату на ближайший нужный день недели, сохраняя часы и минуты.
 *  Если сегодня уже нужный день — оставляет сегодня: проверку «в прошлом»
 *  делает сервер, и дублировать её здесь значит спорить с ним о секундах. */
export function snapToWeekday(inputValue: string, targetWeekday: number): string {
  const [datePart, timePart] = inputValue.split("T");
  const [y, m, d] = datePart.split("-").map(Number);
  const current = new Date(y, m - 1, d).getDay();
  const ahead = (targetWeekday - current + 7) % 7;
  const next = new Date(y, m - 1, d + ahead);
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}T${timePart}`;
}

export type Recurrence = "once" | "daily" | "weekdays" | "weekly";

export const RECURRENCE_LABELS: Record<Recurrence, string> = {
  once: "Один раз",
  daily: "Каждый день",
  weekdays: "По будням, пн–пт",
  weekly: "Раз в неделю",
};

/** Дни недели с понедельника — так их читают, в отличие от JS. */
export const WEEKDAYS: Array<{ js: number; short: string; long: string }> = [
  { js: 1, short: "Пн", long: "понедельник" },
  { js: 2, short: "Вт", long: "вторник" },
  { js: 3, short: "Ср", long: "среду" },
  { js: 4, short: "Чт", long: "четверг" },
  { js: 5, short: "Пт", long: "пятницу" },
  { js: 6, short: "Сб", long: "субботу" },
  { js: 0, short: "Вс", long: "воскресенье" },
];
