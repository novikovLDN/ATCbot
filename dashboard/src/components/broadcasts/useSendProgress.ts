import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useEventStream, type BusEvent } from "@/lib/ws";

/**
 * Живой ход отправки: сколько ушло, сколько не дошло, закончилось ли.
 *
 * Данные приезжают по WebSocket из шины (`app/events.py`), а не
 * опросом: рассылка на десять тысяч человек идёт минутами, и опрос раз
 * в пятнадцать секунд показывал бы её рывками.
 *
 * ПОЧЕМУ ЗАПИСИ САМИ ПРОПАДАЮТ ЧЕРЕЗ ВОСЕМЬ СЕКУНД ПОСЛЕ КОНЦА.
 * Иначе список копит зелёные плашки «готово» от всех рассылок за
 * сессию, и свежая теряется среди старых. Провал (`failed`) не
 * убирается — про него надо узнать, даже отойдя от экрана.
 */

export interface SendProgress {
  processed: number;
  total: number;
  sent: number;
  failed: number;
  status: "running" | "done" | "failed";
  error?: string;
}

const CLEAR_AFTER_MS = 8_000;

export function useSendProgress() {
  const qc = useQueryClient();
  const [map, setMap] = useState<Record<number, SendProgress>>({});
  // Таймеры уборки держим здесь, чтобы снять их при размонтировании:
  // setState после ухода со страницы — предупреждение в консоли и
  // утечка ссылки на компонент.
  const timers = useRef<number[]>([]);

  useEffect(
    () => () => {
      for (const t of timers.current) window.clearTimeout(t);
      timers.current = [];
    },
    [],
  );

  const scheduleClear = (id: number) => {
    const t = window.setTimeout(() => {
      setMap((prev) => {
        // Проверяем статус ещё раз: за восемь секунд ту же рассылку
        // могли запустить снова, и стирать её живой ход нельзя.
        if (prev[id]?.status !== "done") return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }, CLEAR_AFTER_MS);
    timers.current.push(t);
  };

  // Мемоизировать обработчик не нужно: useEventStream держит его в ref'е
  // и подписку не пересоздаёт (см. lib/ws.ts).
  useEventStream((e: BusEvent) => {
    const id = Number(e.broadcast_id ?? 0);
    if (!id) return;
    const n = (v: unknown) => Number(v ?? 0);

    if (e.type === "broadcast:created") {
      // Строка новой рассылки должна появиться сразу, а не через
      // пятнадцать секунд следующего опроса.
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
      setMap((p) => ({
        ...p,
        [id]: {
          processed: 0,
          total: n(e.audience),
          sent: 0,
          failed: 0,
          status: "running",
        },
      }));
    } else if (e.type === "broadcast:progress") {
      setMap((p) => ({
        ...p,
        [id]: {
          processed: n(e.processed),
          total: n(e.total),
          sent: n(e.sent),
          failed: n(e.failed),
          status: "running",
        },
      }));
    } else if (e.type === "broadcast:done") {
      setMap((p) => ({
        ...p,
        [id]: {
          processed: n(e.total),
          total: n(e.total),
          sent: n(e.sent),
          failed: n(e.failed),
          status: "done",
        },
      }));
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
      scheduleClear(id);
    } else if (e.type === "broadcast:failed") {
      setMap((p) => ({
        ...p,
        [id]: {
          processed: p[id]?.processed ?? 0,
          total: p[id]?.total ?? 0,
          sent: p[id]?.sent ?? 0,
          failed: p[id]?.failed ?? 0,
          status: "failed",
          error: String(e.error ?? ""),
        },
      }));
    }
  });

  return map;
}
