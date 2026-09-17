/**
 * Автопродление пользователя (рекуррентная СБП-подписка Platega).
 *
 * Отменяет подписку только админ: у пользователя такой кнопки нет — он
 * пишет в поддержку, и отсюда её выключают. Показываем ровно то, что нужно
 * для этого разговора: сколько и как часто списывается, когда следующее
 * списание и сколько уже прошло.
 *
 * Отмена останавливает БУДУЩИЕ списания. Уже оплаченный период остаётся у
 * пользователя до конца — карточка говорит это прямым текстом, чтобы
 * «выключил автопродление» не путали с «забрал доступ».
 */
import { Loader2 } from "lucide-react";
import type { RecurringSubscription } from "@/lib/api";
import { fmtDate, fmtKop, fmtRelative } from "@/lib/format";
import { Surface } from "@/components/ui/Surface";
import { KeyValueRow } from "./shared/KeyValueRow";

export interface UserRecurringCardProps {
  subscriptions: RecurringSubscription[] | undefined;
  isLoading?: boolean;
  isPending?: boolean;
  onCancel: (subscriptionId: string) => void;
  className?: string;
}

/** Статусы подписки из доку Platega §2.3 — словами, а не кодом. */
const STATUS_LABEL: Record<string, string> = {
  Active: "Списывается",
  PendingAgreement: "Ждёт привязки счёта",
  PastDue: "Списание не прошло",
  Cancelled: "Отменена",
  Failed: "Привязка не удалась",
};

const PERIOD_LABEL: Record<number, string> = {
  30: "раз в месяц",
  90: "раз в 3 месяца",
  180: "раз в 6 месяцев",
  365: "раз в год",
  730: "раз в 2 года",
};

function statusBadge(status: string) {
  if (status === "Active") return <span className="badge-success">Активно</span>;
  if (status === "PendingAgreement") return <span className="badge-muted">Ждёт привязки</span>;
  return <span className="badge-warn">{STATUS_LABEL[status] ?? status}</span>;
}

export function UserRecurringCard({
  subscriptions,
  isLoading,
  isPending,
  onCancel,
  className,
}: UserRecurringCardProps) {
  const rows = subscriptions ?? [];

  return (
    <Surface
      label="Автопродление"
      className={className}
      aside={rows.length > 0 ? statusBadge(rows[0].status) : undefined}
    >
      {isLoading ? (
        <p className="t-mute text-[13px]">Загружаю…</p>
      ) : rows.length === 0 ? (
        <p className="t-mute text-[13px]">
          Автопродления нет — списаний не будет, пользователь платит вручную.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {rows.map((s) => (
            <div key={s.subscription_id} className="flex flex-col gap-1 rounded-row bg-tile-3 px-4 py-2">
              <KeyValueRow
                label="Списание"
                value={`${fmtKop(s.amount_kopecks)} · ${PERIOD_LABEL[s.interval_days] ?? `раз в ${s.interval_days} дн.`}`}
              />
              <KeyValueRow label="Статус" value={STATUS_LABEL[s.status] ?? s.status} />
              {s.next_charge_at && (
                <KeyValueRow
                  label="Следующее"
                  value={`${fmtDate(s.next_charge_at)} · ${fmtRelative(s.next_charge_at)}`}
                />
              )}
              {s.last_charge_at && (
                <KeyValueRow label="Последнее" value={fmtDate(s.last_charge_at)} tone="muted" />
              )}
              <KeyValueRow
                label="Списаний"
                value={
                  s.charges_failed
                    ? `${s.charges_success} успешных · ${s.charges_failed} неудачных`
                    : `${s.charges_success}`
                }
                tone={s.charges_failed ? "warn" : "default"}
              />
              <KeyValueRow label="ID подписки" value={s.subscription_id} mono tone="muted" />
              <button
                type="button"
                className="btn-secondary mt-2 self-start"
                disabled={isPending}
                onClick={() => onCancel(s.subscription_id)}
              >
                {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
                Выключить автопродление
              </button>
            </div>
          ))}
          <p className="t-mute text-[12px] leading-4">
            Отмена останавливает будущие списания. Уже оплаченный период остаётся
            у пользователя до конца — доступ не отзывается.
          </p>
        </div>
      )}
    </Surface>
  );
}
