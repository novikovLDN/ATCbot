/**
 * Cashback percentage, and where it came from.
 *
 * The old card showed one number — the effective percent — which is the
 * least useful part on its own. Three independent rules feed it: the
 * loyalty tier the user earned, a grandfather floor that protects an
 * older, better rate, and an admin fix that overrides both. Seeing only
 * the result meant an operator could not tell whether 15% was earned,
 * protected, or set by hand, and so could not tell whether clearing the
 * fix would raise it or drop it.
 *
 * Now the card shows all three inputs and marks which one won.
 */
import { useId, useState } from "react";
import { Check, Loader2, Trash2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { Surface } from "@/components/ui/Surface";
import { IconButton } from "@/components/ui/controls";

export interface CashbackFixCardProps {
  /** `users.cashback_fixed_percent` — null when no admin fix is set. */
  fixedPercent: number | null;
  /** `users.cashback_floor_percent` — the grandfathered minimum. */
  floorPercent?: number | null;
  /** What the backend actually applies right now. */
  effectivePercent: number;
  onSet: (percent: number) => void;
  onClear: () => void;
  isPending?: boolean;
  className?: string;
}

export function CashbackFixCard({
  fixedPercent,
  floorPercent,
  effectivePercent,
  onSet,
  onClear,
  isPending = false,
  className,
}: CashbackFixCardProps) {
  const isFixed = typeof fixedPercent === "number";
  const [percent, setPercent] = useState<number | "">(isFixed ? (fixedPercent as number) : 10);
  const [confirming, setConfirming] = useState(false);

  const uid = useId();
  const percentId = `${uid}-cashback-percent`;

  const hasFloor = typeof floorPercent === "number" && floorPercent > 0;

  /**
   * The tier component is not sent as its own field, so it is derived:
   * when nothing overrides, the effective percent is the tier. When a fix
   * is in force we cannot know the tier and say so rather than guessing —
   * an invented number here would be read as fact.
   */
  const floorWins = !isFixed && hasFloor && (floorPercent as number) >= effectivePercent;
  const tierValue = isFixed ? null : effectivePercent;

  const sources: Array<{
    key: string;
    label: string;
    value: string;
    active: boolean;
    note: string;
  }> = [
    {
      key: "fix",
      label: "Фикс админа",
      value: isFixed ? `${fixedPercent}%` : "не задан",
      active: isFixed,
      note: "перекрывает тир и floor",
    },
    {
      key: "floor",
      label: "Grandfather-floor",
      value: hasFloor ? `${floorPercent}%` : "нет",
      active: floorWins,
      note: "защищает старую ставку",
    },
    {
      key: "tier",
      label: "Тир по тратам",
      value: tierValue === null ? "перекрыт фиксом" : `${tierValue}%`,
      active: !isFixed && !floorWins,
      note: "рассчитывается автоматически",
    },
  ];

  const percentValid = typeof percent === "number" && percent >= 0 && percent <= 100;

  return (
    <Surface
      label="Кешбэк"
      className={className}
      aside={
        isFixed && !confirming ? (
          <IconButton
            small
            label="Снять фикс кешбэка — вернутся тир и floor"
            onClick={() => setConfirming(true)}
            disabled={isPending}
            className="bg-tile-3 text-danger hover:bg-tile-4"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </IconButton>
        ) : undefined
      }
    >
      <div className="flex items-baseline gap-2">
        <span className="tabular text-[30px] font-semibold leading-9">{effectivePercent}%</span>
        <span className="t-mute text-[13px]">применяется сейчас</span>
      </div>

      {/* The composition. Each row names a rule, its value, and whether
          it is the one currently winning. */}
      <ul className="mt-3 flex flex-col gap-1">
        {sources.map((s) => (
          <li
            key={s.key}
            className={cn(
              "flex min-h-[40px] items-center justify-between gap-3 rounded-row px-3 py-2",
              s.active && "bg-tile-3",
            )}
          >
            <span className="flex min-w-0 items-center gap-1.5">
              {s.active && <Check className="h-3.5 w-3.5 shrink-0 text-accent" aria-hidden="true" />}
              <span className={cn("truncate text-[13px]", s.active ? "text-ink" : "t-mute")}>{s.label}</span>
            </span>
            <span className={cn("tabular shrink-0 text-[13px] font-medium", s.active ? "text-ink" : "text-ash")}>
              {s.value}
            </span>
          </li>
        ))}
      </ul>
      <p className="t-mute mt-2 px-3 text-[12px] leading-5">
        {isFixed
          ? "Зафиксировано вручную — тир и floor не суммируются и не применяются."
          : floorWins
          ? "Действует защищённая старая ставка: она выше текущего тира."
          : "Обычная логика: тир по тратам, не ниже floor."}
      </p>

      {confirming && (
        <div className="mt-3 rounded-row bg-tile-3 p-3">
          <p className="text-[13px]">Снять фикс? Вернётся расчёт по тиру и floor.</p>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <button
              type="button"
              className="btn-danger"
              disabled={isPending}
              onClick={() => {
                onClear();
                setConfirming(false);
              }}
            >
              {isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
              Снять фикс
            </button>
            <button type="button" className="btn-secondary bg-tile-4" onClick={() => setConfirming(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      <div className="mt-4">
        <label htmlFor={percentId} className="t-mute mb-1 block text-[12px]">
          Зафиксировать процент (0–100)
        </label>
        <input
          id={percentId}
          className="input tabular"
          type="number"
          min={0}
          max={100}
          value={percent}
          onChange={(e) => setPercent(e.target.value === "" ? "" : Number(e.target.value))}
        />
      </div>

      <button
        type="button"
        onClick={() => onSet(percent as number)}
        className="btn-secondary mt-2 w-full"
        disabled={isPending || !percentValid}
      >
        {isPending ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            <span className="sr-only">Сохраняю фикс кешбэка</span>
          </>
        ) : null}
        {isFixed ? "Обновить фикс" : "Зафиксировать"}
      </button>
    </Surface>
  );
}
