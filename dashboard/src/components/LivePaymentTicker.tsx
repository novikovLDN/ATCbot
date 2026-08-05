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
 * Слушает `payment:approved` из WS и копит буфер до 30 записей.
 *
 * Была бесконечная marquee-прокрутка на 32 секунды (список рендерился
 * дважды, чтобы шов не был виден). Убрана: движущийся текст нельзя
 * прочитать не подгадывая момент, а бесконечная анимация держит композитор
 * занятым всё время, пока панель открыта (research §3.6, §8.8).
 * Теперь это обычная прокручиваемая полоса — новое слева, старое справа.
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
      <div className="card flex items-center gap-3 px-4 py-2.5 text-xs text-fg-muted">
        <span className="pulse-live" />
        <span>
          Ожидаю платежи в реальном времени · подключение к событиям активно
        </span>
      </div>
    );
  }

  return (
    <div className="card relative overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5">
        <span className="pulse-live shrink-0" />
        <span className="shrink-0 text-2xs font-medium uppercase tracking-[0.16em] text-info">
          LIVE
        </span>
        {/* Прокрутка мышью/пальцем вместо автопрокрутки: справа виден
            обрезанный элемент — по NN/g это лучшая подсказка о том, что
            содержимое продолжается (ux-patterns §4.5). */}
        <div className="relative flex-1 overflow-x-auto">
          <div className="flex gap-6 whitespace-nowrap">
            {ticks.map((t, i) => (
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
