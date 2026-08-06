import { useQuery } from "@tanstack/react-query";

import { endpoints } from "@/lib/api";

/**
 * Каталог сегментов и их размеры — один запрос на весь раздел.
 *
 * ПОЧЕМУ ОДИН ХУК НА ПЯТЬ ЭКРАНОВ. `GET /broadcasts/segments` считает
 * размер каждого из 54 сегментов отдельным запросом в базу (read.py:57)
 * — это самый дорогой запрос раздела. Раньше его звали из четырёх мест
 * с разными staleTime, и открытие мастера с уже открытым списком
 * отложенных считало все 54 сегмента дважды. Здесь один ключ и один
 * staleTime, React Query склеивает вызовы.
 *
 * ЧТО ЗНАЧИТ count === -1. Это не «ноль человек» и не «мало». Сервер
 * так помечает сегмент, чей подсчёт упал (SEGMENT_COUNT_FAIL): размер
 * неизвестен. Показать «-1 человек» нельзя, отправить «на минус одного»
 * тем более. Разбирать этот случай руками на каждом экране забудут,
 * поэтому наружу отдаётся `segmentCount() -> number | null`, и null
 * обязан быть обработан вызывающим.
 */

export interface Segment {
  key: string;
  label: string;
  description?: string;
  group?: string;
  count: number;
}

/** Сентинел сервера: размер сегмента посчитать не удалось. */
const COUNT_UNKNOWN = -1;

export function useSegments() {
  return useQuery({
    queryKey: ["broadcasts", "segments"],
    queryFn: () => endpoints.broadcastSegments(),
    // Минута: аудитория меняется медленнее, чем админ листает вкладки,
    // а каждый повторный счёт — 54 прохода по таблице пользователей.
    staleTime: 60_000,
  });
}

/** Человекочитаемое имя сегмента. Ключ как запасной вариант — сегмент
 *  мог исчезнуть из каталога, но остаться в старой записи рассылки. */
export function segmentLabel(segments: Segment[] | undefined, key: string): string {
  if (!key) return "—";
  return segments?.find((s) => s.key === key)?.label ?? key;
}

/** Размер сегмента. null — каталог ещё не приехал ИЛИ счёт не удался.
 *  Оба случая означают «числа нет», и оба запрещают отправку вслепую. */
export function segmentCount(
  segments: Segment[] | undefined,
  key: string,
): number | null {
  const found = segments?.find((s) => s.key === key);
  if (!found) return null;
  return found.count === COUNT_UNKNOWN ? null : found.count;
}

/** Сегменты по группам, в порядке каталога с сервера. Порядок значимый:
 *  на бэкенде каталог отсортирован по смыслу («Базовые», «Триал»,
 *  «Платная»…), а не по алфавиту, и пересортировка его сломает. */
export function groupSegments(
  segments: Segment[] | undefined,
): Array<{ group: string; items: Segment[] }> {
  const out: Array<{ group: string; items: Segment[] }> = [];
  for (const s of segments ?? []) {
    const group = s.group || "Прочее";
    // Ищем уже заведённую группу, а не только последнюю: если каталог на
    // сервере перестанет быть слитным по группам, заголовок не задвоится.
    const bucket = out.find((g) => g.group === group);
    if (bucket) bucket.items.push(s);
    else out.push({ group, items: [s] });
  }
  return out;
}
