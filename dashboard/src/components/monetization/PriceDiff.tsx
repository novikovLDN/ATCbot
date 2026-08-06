import type { ReactNode } from "react";
import { ArrowRight } from "lucide-react";

import { fmtRub } from "@/lib/format";
import { cn } from "@/lib/cn";
import { periodLabel, tariffLabel } from "./labels";

/**
 * «Было → станет» — единственная защита от опечатки в поле цены.
 *
 * ПОЧЕМУ ОНА ОБЯЗАТЕЛЬНА. Цена действует на всех будущих покупателей
 * сразу. Опечатка (199 вместо 1990, лишний ноль, не тот период) — это
 * прямой убыток или остановка продаж, и по экрану её не видно: поле с
 * числом выглядит правильным при любом числе. Заметить можно только
 * сравнение — сколько платят сейчас и сколько будут платить.
 *
 * ЧТО ИМЕННО СРАВНИВАЕТСЯ. Не база, а ЭФФЕКТИВНАЯ цена: то, что
 * покупатель увидит в боте после применения глобальной скидки. Показывать
 * базу без скидки означало бы обещать одно, а продавать другое.
 *
 * СБРОС ПЕРЕОПРЕДЕЛЕНИЯ — ТОЖЕ ИЗМЕНЕНИЕ ЦЕНЫ, и показывается ровно так
 * же. «Вернуть как было» звучит безобидно ровно до момента, когда
 * выясняется, что «как было» — это на 800 ₽ дороже.
 */

/** Одна строка сравнения. Суммы — в рублях, как их видит покупатель. */
export interface PriceChange {
  tariff: string;
  periodDays: number;
  /** Эффективная цена сейчас. */
  from: number;
  /** Эффективная цена после сохранения. */
  to: number;
}

/**
 * Скидка на базу, копия `_apply_discount` из app/services/pricing.
 *
 * ОКРУГЛЕНИЕ «К БЛИЖАЙШЕМУ ЧЁТНОМУ», А НЕ Math.round. Python округляет
 * половину к чётному: round(98.5) = 98, а Math.round(98.5) = 99. Разница
 * в рубль, но она означала бы, что предпросмотр показывает не ту цену,
 * которую сохранит сервер, — а предпросмотр здесь для того и стоит,
 * чтобы ему верили.
 */
export function applyDiscount(base: number, percent: number): number {
  if (percent <= 0 || percent >= 100) return base;
  const x = (base * (100 - percent)) / 100;
  const floor = Math.floor(x);
  const frac = x - floor;
  if (frac > 0.5) return floor + 1;
  if (frac < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

export function PriceDiff({
  changes,
  /** Что это значит для дела: кого затронет, с какого момента. */
  note,
  className,
}: {
  changes: PriceChange[];
  note?: ReactNode;
  className?: string;
}) {
  const changed = changes.filter((c) => c.from !== c.to);

  if (changed.length === 0) {
    return (
      <div className={cn("rounded-md border border-border bg-bg-subtle p-3 text-base text-fg-muted", className)}>
        Цены не изменятся: новое значение совпадает с текущим.
      </div>
    );
  }

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="overflow-hidden rounded-md border border-border">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-border bg-bg-subtle text-xs text-fg-subtle">
              <th scope="col" className="px-2.5 py-1.5 font-medium">
                Тариф и период
              </th>
              <th scope="col" className="px-2.5 py-1.5 text-right font-medium">
                Платят сейчас
              </th>
              <th scope="col" className="px-2.5 py-1.5 text-right font-medium">
                Будут платить
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {changed.map((c) => {
              const delta = c.to - c.from;
              return (
                <tr key={`${c.tariff}-${c.periodDays}`}>
                  <td className="px-2.5 py-1.5 text-base text-fg">
                    {tariffLabel(c.tariff)}
                    <span className="text-fg-muted"> · {periodLabel(c.periodDays)}</span>
                  </td>
                  <td className="px-2.5 py-1.5 text-right text-base tabular-nums text-fg-muted">
                    {fmtRub(c.from)}
                  </td>
                  <td className="px-2.5 py-1.5 text-right text-base font-semibold tabular-nums text-fg">
                    <span className="inline-flex items-center gap-1">
                      <ArrowRight className="h-3 w-3 shrink-0 text-fg-subtle" aria-hidden />
                      {fmtRub(c.to)}
                    </span>
                    {/* Знак разницы написан словом «дешевле»/«дороже», а не
                        одним цветом: направление изменения — главное, что
                        человек должен прочитать, и оно не может зависеть
                        от восприятия оттенка. */}
                    <div className="text-2xs font-normal text-fg-muted">
                      {delta < 0 ? "дешевле" : "дороже"} на {fmtRub(Math.abs(delta))}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {note && <div className="text-xs text-fg-muted">{note}</div>}
    </div>
  );
}
