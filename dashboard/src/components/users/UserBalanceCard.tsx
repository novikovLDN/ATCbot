/**
 * Balance adjustment — the one control on this screen that moves real
 * money.
 *
 * The bug this replaces, in full, because it is worth not reintroducing:
 *
 *   const v = -Math.abs(delta);
 *   setDelta(v);
 *   window.setTimeout(() => change.mutate(), 0);
 *
 * The mutation took no arguments and read `delta` from the closure of
 * the render it was created in. Setting state and firing a macrotask
 * happened to work under React 18's legacy batching — the state commits
 * before the timeout runs, so the *next* render's closure had the
 * negative value. Under StrictMode's double-invocation, Suspense, or
 * concurrent rendering, the timeout can run against the render that
 * still holds the positive number, and a withdrawal becomes a deposit of
 * the same size. Nothing in the UI would report it; the operator sees a
 * success toast either way.
 *
 * The fix is not a better-ordered `setState`. It is that the amount
 * travels as an argument — `onChange(-Math.abs(delta), reason)` — so no
 * render ordering can change what is sent. There is no code path here
 * that reads component state at dispatch time.
 */
import { useId, useState } from "react";
import { Loader2, Minus, Plus } from "lucide-react";
import { fmtRub } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Surface } from "@/components/ui/Surface";

export interface UserBalanceCardProps {
  balanceRubles: number;
  /**
   * Signed. Positive credits, negative debits. Wire straight to
   * `balanceChange.mutate({ delta_rubles, reason })` — or pass
   * `mutateAsync` and the form clears itself only once the write lands.
   */
  onChange: (deltaRubles: number, reason?: string) => void | Promise<unknown>;
  isPending?: boolean;
  className?: string;
}

export function UserBalanceCard({
  balanceRubles,
  onChange,
  isPending = false,
  className,
}: UserBalanceCardProps) {
  const [amount, setAmount] = useState<number | "">("");
  const [reason, setReason] = useState("");

  const uid = useId();
  const amountId = `${uid}-amount`;
  const reasonId = `${uid}-reason`;

  // The input is an unsigned magnitude and the button chooses the sign.
  // A signed field plus two buttons is what made the original ambiguous:
  // it was possible to type "-500" and press "Пополнить".
  const magnitude = typeof amount === "number" ? Math.abs(amount) : 0;
  const canSubmit = magnitude > 0 && !isPending;

  function submit(sign: 1 | -1) {
    if (!canSubmit) return;
    const result = onChange(sign * magnitude, reason.trim() || undefined);
    // Only clear once we know the write succeeded. Clearing eagerly on a
    // failed request loses the reason text the operator just typed and
    // invites them to re-enter the amount from memory.
    if (result && typeof (result as Promise<unknown>).then === "function") {
      (result as Promise<unknown>).then(
        () => {
          setAmount("");
          setReason("");
        },
        // The parent's mutation already surfaces the error as a toast;
        // swallowing here prevents an unhandled rejection on top of it.
        () => undefined,
      );
    } else {
      setAmount("");
      setReason("");
    }
  }

  const negative = balanceRubles < 0;

  return (
    <Surface label="Баланс" className={className}>
      <div className={cn("tabular text-[30px] font-semibold leading-9", negative && "text-danger")}>
        {fmtRub(balanceRubles)}
      </div>

      <div className="mt-4 flex flex-col gap-3">
        <div>
          <label htmlFor={amountId} className="t-mute mb-1 block text-[12px]">
            Сумма, ₽
          </label>
          <input
            id={amountId}
            className="input tabular"
            type="number"
            min={0}
            step="0.01"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value === "" ? "" : Number(e.target.value))}
          />
        </div>

        <div>
          <label htmlFor={reasonId} className="t-mute mb-1 block text-[12px]">
            Причина <span className="text-ash">(необязательно)</span>
          </label>
          <input
            id={reasonId}
            className="input"
            type="text"
            maxLength={200}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>

        {/* Naming the resulting balance before the click is the cheapest
            guard against a mistyped zero: the operator confirms an
            outcome rather than an input. */}
        {magnitude > 0 && (
          <p className="t-mute text-[12px] leading-5">
            Станет <span className="tabular text-success">{fmtRub(balanceRubles + magnitude)}</span>{" "}
            при пополнении ·{" "}
            <span className="tabular text-warning">{fmtRub(balanceRubles - magnitude)}</span> при
            списании
          </p>
        )}

        <div className="grid grid-cols-2 gap-2">
          <button type="button" onClick={() => submit(1)} disabled={!canSubmit} className="btn-secondary">
            {isPending ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                <span className="sr-only">Изменяю баланс</span>
              </>
            ) : (
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            Пополнить
          </button>
          <button type="button" onClick={() => submit(-1)} disabled={!canSubmit} className="btn-secondary">
            <Minus className="h-3.5 w-3.5" aria-hidden="true" />
            Списать
          </button>
        </div>
      </div>
    </Surface>
  );
}
