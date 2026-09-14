/**
 * Cascading deletion of a user.
 *
 * The confirmation asks for the telegram id to be typed out. That is
 * deliberate friction and it is the right amount here: this removes the
 * account, its subscription, its payment history and its referral links,
 * and there is no undo. Typing the id also makes it impossible to delete
 * the wrong person by muscle memory after opening two cards in a row.
 */
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Surface } from "@/components/ui/Surface";
import { StatusDot } from "@/components/ui/controls";

interface Props {
  telegramId: number;
  onDelete: () => void;
  isPending?: boolean;
  className?: string;
}

export function UserDangerZone({ telegramId, onDelete, isPending = false, className }: Props) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");

  const matches = typed.trim() === String(telegramId);

  return (
    <Surface
      variant="raised"
      className={className}
      label={
        <>
          <StatusDot tone="err" />
          Опасная зона
        </>
      }
    >
      {!open ? (
        <>
          <p className="t-body text-[13px] leading-5">
            Удаление снесёт пользователя вместе с подпиской, историей платежей и реферальными
            связями. Отменить нельзя.
          </p>
          <button type="button" onClick={() => setOpen(true)} className="btn-danger mt-3">
            Удалить пользователя
          </button>
        </>
      ) : (
        <div>
          <label htmlFor="delete-confirm" className="t-body block text-[13px]">
            Введите <span className="tabular font-mono text-ink">{telegramId}</span> для
            подтверждения
          </label>
          <input
            id="delete-confirm"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            inputMode="numeric"
            className="input tabular mt-2 font-mono"
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setOpen(false);
                setTyped("");
              }}
            >
              Отмена
            </button>
            <button type="button" className="btn-danger" disabled={!matches || isPending} onClick={onDelete}>
              {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
              Удалить навсегда
            </button>
          </div>
        </div>
      )}
    </Surface>
  );
}
