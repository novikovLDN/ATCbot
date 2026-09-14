/**
 * Trial state. Read-only — there is no endpoint to grant or reset one,
 * and a control that looks actionable but is not is worse than none.
 *
 * "Не использовался" and "истёк" are deliberately different states: the
 * first means the user still has a free trial available and is a
 * candidate for an offer, the second means they took it and did not
 * convert. The old card collapsed both into a blank.
 */
import type { UserTrial } from "@/types/user";
import { fmtDate, fmtRelative } from "@/lib/format";
import { Surface } from "@/components/ui/Surface";
import { KeyValueRow } from "./shared/KeyValueRow";

export interface UserTrialCardProps {
  trial: UserTrial | null | undefined;
  className?: string;
}

export function UserTrialCard({ trial, className }: UserTrialCardProps) {
  const used = trial?.trial_used_at ?? null;
  const expires = trial?.trial_expires_at ?? null;

  const expiresMs = expires ? new Date(expires).getTime() : null;
  const isActive =
    expiresMs !== null && Number.isFinite(expiresMs) && expiresMs > Date.now();

  return (
    <Surface
      label="Триал"
      className={className}
      aside={
        used ? (
          isActive ? (
            <span className="badge-success">Активен</span>
          ) : (
            <span className="badge-muted">Истёк</span>
          )
        ) : undefined
      }
    >
      {!used && !expires ? (
        <p className="t-mute text-[13px]">Не использовался — триал ещё доступен.</p>
      ) : (
        <div className="flex flex-col gap-1 rounded-row bg-tile-3 px-4 py-2">
          <KeyValueRow label="Активирован" value={fmtDate(used)} />
          <KeyValueRow label="Истекает" value={fmtDate(expires)} tone={isActive ? "default" : "muted"} />
          {expires && (
            <KeyValueRow
              label={isActive ? "Осталось" : "Истёк"}
              value={fmtRelative(expires)}
              tone={isActive ? "ok" : "muted"}
            />
          )}
        </div>
      )}
    </Surface>
  );
}
