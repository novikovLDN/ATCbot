import { useEffect, useRef, useState } from "react";
import { Zap } from "lucide-react";
import { useEventStream, type BusEvent } from "@/lib/ws";
import { fmtRub } from "@/lib/format";

interface Tick {
  id: number;
  telegramId: number | null;
  amount: number | null;
  tariff: string | null;
  isRenewal: boolean;
  at: number;
}

let tickCounter = 0;

/**
 * Живая горизонтальная лента последних одобренных платежей.
 * Слушает `payment:approved` из WS, копит буфер до 30 записей и
 * рендерит их дважды подряд с `animate-ticker` — эффект бесконечной
 * прокрутки. Пустое состояние — placeholder «ожидаем платежи…».
 *
 * Не блокирует layout: sticky/absolute не используется, вставляется
 * как обычный виджет-полоска в главную страницу.
 */
export function LivePaymentTicker() {
  const [ticks, setTicks] = useState<Tick[]>([]);
  const seededRef = useRef(false);

  useEventStream((e: BusEvent) => {
    if (e.type !== "payment:approved") return;
    const amt =
      typeof e.amount_rubles === "number"
        ? e.amount_rubles
        : typeof e.amount_kopecks === "number"
        ? e.amount_kopecks / 100
        : null;
    const t: Tick = {
      id: ++tickCounter,
      telegramId: typeof e.telegram_id === "number" ? e.telegram_id : null,
      amount: amt,
      tariff: typeof e.tariff === "string" ? e.tariff : null,
      isRenewal: !!e.is_renewal,
      at: Date.now(),
    };
    setTicks((prev) => [t, ...prev].slice(0, 30));
  });

  // Seed: placeholder «система ждёт» — чтобы полоска не была пустой,
  // пока не пришёл первый payment:approved.
  useEffect(() => {
    if (seededRef.current) return;
    seededRef.current = true;
  }, []);

  if (ticks.length === 0) {
    return (
      <div className="glass-panel flex items-center gap-3 px-4 py-2.5 text-xs text-fg-muted">
        <span className="pulse-live" />
        <span>
          Ожидаю платежи в реальном времени · подключение к событиям активно
        </span>
      </div>
    );
  }

  // Дублируем массив, чтобы marquee выглядел бесконечным без разрыва.
  const doubled = [...ticks, ...ticks];

  return (
    <div className="glass-panel relative overflow-hidden">
      <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-16 bg-gradient-to-r from-bg to-transparent" />
      <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-16 bg-gradient-to-l from-bg to-transparent" />
      <div className="flex items-center gap-2 px-4 py-2.5">
        <span className="pulse-live shrink-0" />
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-[0.16em] text-info">
          LIVE
        </span>
        <div className="relative flex-1 overflow-hidden">
          <div className="flex gap-6 animate-ticker whitespace-nowrap">
            {doubled.map((t, i) => (
              <span
                key={`${t.id}-${i}`}
                className="inline-flex items-center gap-1.5 text-xs text-fg-muted"
              >
                <Zap className="h-3 w-3 text-info" />
                {t.isRenewal ? "Продление" : "Новая"}
                {t.tariff && (
                  <span className="text-fg">· {t.tariff}</span>
                )}
                {t.amount !== null && (
                  <span className="font-semibold text-fg">
                    · {fmtRub(t.amount)}
                  </span>
                )}
                {t.telegramId !== null && (
                  <span className="text-fg-subtle">· tg:{t.telegramId}</span>
                )}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
