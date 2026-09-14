/**
 * Personal discount, in one parameterised card.
 *
 * There used to be two of these — `DiscountCard` and
 * `TrafficDiscountCard` — differing in four strings and two endpoint
 * names across ~90 duplicated lines. They had already drifted: one said
 * "Скидка создана", the other "Скидка на GB создана", and only one of
 * them ever got a `title` on its delete button. Parameterising is what
 * keeps the two from diverging again.
 *
 * `created_by` and `created_at` are rendered here for the first time.
 * The backend has always sent them; showing them turns the card into an
 * answer to "which admin gave this person 90% off, and when" without a
 * trip to the database.
 */
import { useId, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import type { UserDiscount } from "@/types/user";
import { fmtDate } from "@/lib/format";
import { Surface } from "@/components/ui/Surface";
import { IconButton } from "@/components/ui/controls";

export interface DiscountCardProps {
  title: string;
  /** One line explaining what the discount applies to. */
  hint?: string;
  existing: UserDiscount | null;
  /** `expiresInHours === null` means an open-ended discount. */
  onCreate: (percent: number, expiresInHours: number | null) => void;
  onDelete: () => void;
  isPending?: boolean;
  className?: string;
}

export function DiscountCard({
  title,
  hint,
  existing,
  onCreate,
  onDelete,
  isPending = false,
  className,
}: DiscountCardProps) {
  const [percent, setPercent] = useState<number | "">(30);
  const [hours, setHours] = useState<number | "">(24);
  // Deleting a discount is destructive and irreversible from this
  // screen, but a native confirm() blocks the whole tab and cannot be
  // styled or dismissed by keyboard consistently. Inline two-step
  // instead: the row itself becomes the confirmation.
  const [confirming, setConfirming] = useState(false);

  // Generated ids rather than hardcoded ones: this card is rendered
  // twice on the same page, and duplicate ids would point both labels at
  // whichever input mounted first.
  const uid = useId();
  const percentId = `${uid}-percent`;
  const hoursId = `${uid}-hours`;

  const percentValid = typeof percent === "number" && percent >= 1 && percent <= 100;

  return (
    <Surface
      label={title}
      className={className}
      aside={
        existing && !confirming ? (
          <IconButton
            small
            label={`Снять скидку: ${title}`}
            onClick={() => setConfirming(true)}
            disabled={isPending}
            className="bg-tile-3 text-danger hover:bg-tile-4"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </IconButton>
        ) : undefined
      }
    >
      {hint && <p className="t-mute -mt-1 mb-3 text-[13px] leading-5">{hint}</p>}

      {existing && (
        <div className="mb-3 rounded-row bg-tile-3 px-4 py-3">
          <div className="flex items-baseline justify-between gap-2">
            <span className="tabular text-[15px] font-semibold">−{existing.discount_percent}%</span>
            <span className="t-mute text-[13px]">
              {existing.expires_at ? `до ${fmtDate(existing.expires_at)}` : "бессрочно"}
            </span>
          </div>
          {/* The audit line. Absent on discounts issued before the
              column existed, so both halves are conditional. */}
          {(existing.created_by || existing.created_at) && (
            <p className="t-mute mt-1.5 text-[12px] leading-4">
              {existing.created_by ? `выдал ${existing.created_by}` : "выдана"}
              {existing.created_at ? ` · ${fmtDate(existing.created_at)}` : ""}
            </p>
          )}
        </div>
      )}

      {confirming && (
        <div className="mb-3 rounded-row bg-tile-3 p-3">
          <p className="text-[13px]">Снять скидку?</p>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <button
              type="button"
              className="btn-danger"
              disabled={isPending}
              onClick={() => {
                onDelete();
                setConfirming(false);
              }}
            >
              {isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
              Снять
            </button>
            <button type="button" className="btn-secondary bg-tile-4" onClick={() => setConfirming(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor={percentId} className="t-mute mb-1 block text-[12px]">
            Процент
          </label>
          <input
            id={percentId}
            className="input tabular"
            type="number"
            min={1}
            max={100}
            value={percent}
            onChange={(e) => setPercent(e.target.value === "" ? "" : Number(e.target.value))}
          />
        </div>
        <div>
          <label htmlFor={hoursId} className="t-mute mb-1 block text-[12px]">
            Часов <span className="text-ash">(пусто — навсегда)</span>
          </label>
          <input
            id={hoursId}
            className="input tabular"
            type="number"
            min={1}
            value={hours}
            onChange={(e) => setHours(e.target.value === "" ? "" : Number(e.target.value))}
          />
        </div>
      </div>

      <button
        type="button"
        onClick={() => onCreate(percent as number, typeof hours === "number" ? hours : null)}
        className="btn-secondary mt-3 w-full"
        disabled={isPending || !percentValid}
      >
        {isPending ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            <span className="sr-only">Применяю скидку</span>
          </>
        ) : (
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        {existing ? "Обновить" : "Применить"}
      </button>
    </Surface>
  );
}
