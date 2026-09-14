/**
 * Subscription history as a vertical timeline.
 *
 * `GET /users/{tg}/history` has worked and been bound in api.ts the
 * whole time; nothing ever rendered it. It is the record of every
 * renewal, reissue and manual intervention on the account, which is what
 * turns "he says he paid twice in March" from a database question into a
 * glance.
 *
 * A timeline rather than a table because the axis that matters is time
 * and the entries have only three fields — a table would spend most of
 * its width on chrome, and the ordering is the information.
 */
import type { SubscriptionHistoryRow } from "@/types/user";
import { fmtDate, fmtRelative } from "@/lib/format";
import { Surface } from "@/components/ui/Surface";
import { StatusDot, type Tone } from "@/components/ui/controls";
import { EmptyState } from "@/components/ui/states";
import { CardError, CardLoading } from "./shared/CardStates";

export interface UserTimelineProps {
  rows: SubscriptionHistoryRow[] | null | undefined;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  className?: string;
}

const ACTION_RU: Record<string, string> = {
  renewal: "Продление",
  reissue: "Перевыпуск ключа",
  manual_reissue: "Перевыпуск вручную",
  new: "Первая подписка",
  purchase: "Покупка",
  admin_grant: "Выдано админом",
  admin_revoke: "Отозвано админом",
  trial: "Триал",
  switch_tariff: "Смена тарифа",
  upgrade: "Апгрейд",
  expired: "Истекла",
};

/**
 * Colour marks the kind of event, not its severity: a revoke is not an
 * error, it is an administrative action, and painting it red would have
 * operators chasing incidents that never happened.
 */
function dotTone(action: string): Tone {
  if (action.includes("revoke") || action === "expired") return "err";
  if (action.includes("reissue")) return "warn";
  if (action.includes("admin")) return "info";
  if (action === "trial") return "idle";
  return "ok";
}

function actionLabel(action: string | null | undefined): string {
  if (!action) return "Событие";
  return ACTION_RU[action] ?? action;
}

export function UserTimeline({
  rows,
  isLoading = false,
  isError = false,
  onRetry,
  className,
}: UserTimelineProps) {
  return (
    <Surface label="История подписки" className={className}>
      {isLoading ? (
        <CardLoading label="Загружаю историю…" />
      ) : isError ? (
        <CardError
          title="История не загрузилась"
          note="Ошибка запроса, а не пустая история."
          onRetry={onRetry}
        />
      ) : !rows || rows.length === 0 ? (
        <EmptyState
          title="Событий нет"
          hint="Подписка ни разу не продлевалась и ключ не перевыпускался."
        />
      ) : (
        <ol>
          {rows.map((r, i) => {
            const action = String(r.action_type ?? "");
            const last = i === rows.length - 1;
            return (
              <li
                key={String(r.id ?? `${action}-${r.created_at ?? i}`)}
                className="relative flex gap-3 pb-4 last:pb-0"
              >
                {/* The rail is drawn per-item and skipped on the last
                    one, so the line stops at the final dot instead of
                    trailing into empty space. */}
                {!last && (
                  <span aria-hidden="true" className="absolute bottom-0 left-[3.5px] top-3 w-px bg-tile-4" />
                )}
                <span className="relative mt-1.5 flex">
                  <StatusDot tone={dotTone(action)} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                    <span className="text-[14px] font-medium">{actionLabel(r.action_type)}</span>
                    <span className="t-mute tabular text-[12px]" title={fmtDate(r.created_at)}>
                      {r.created_at ? fmtRelative(r.created_at) : "—"}
                    </span>
                  </div>
                  <p className="t-mute tabular mt-0.5 text-[12px] leading-4">{fmtDate(r.created_at)}</p>
                  {(r.start_date || r.end_date) && (
                    <p className="t-body tabular mt-0.5 text-[12px] leading-4">
                      период: {fmtDate(r.start_date as string | null)} →{" "}
                      {fmtDate(r.end_date as string | null)}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Surface>
  );
}
