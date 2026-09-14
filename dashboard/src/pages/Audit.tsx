import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  RefreshCcw,
  UserPlus,
  Crown,
  ShieldOff,
  Wallet,
  Percent,
  Megaphone,
  AlertCircle,
} from "lucide-react";
import { endpoints } from "@/lib/api";
import { fmtDate, fmtRelative, truncate } from "@/lib/format";
import { Spinner } from "@/components/Spinner";
import { PageHeader, Surface } from "@/components/ui/Surface";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { useEventStream } from "@/lib/ws";
import { useState } from "react";

interface AuditEntry extends Record<string, unknown> {
  id?: number;
  action?: string;
  admin_telegram_id?: number;
  user_telegram_id?: number;
  details?: string;
  created_at?: string;
}

const ICONS: Record<string, typeof Activity> = {
  subscription_renewed: ShieldOff,
  payment_approved: Wallet,
  admin_grant: UserPlus,
  admin_revoke: ShieldOff,
  admin_create_discount: Percent,
  vip_grant: Crown,
  vip_revoke: Crown,
  broadcast_sent: Megaphone,
  broadcast_created: Megaphone,
};

export function Audit() {
  const [limit, setLimit] = useState(100);
  const q = useQuery({
    queryKey: ["audit", limit],
    queryFn: () => endpoints.auditRecent(limit) as Promise<AuditEntry[]>,
    refetchInterval: 15_000,
  });

  useEventStream(() => {
    // Soft refresh on any event — feels live without hammering DB.
    q.refetch();
  });

  return (
    <div>
      <PageHeader
        title="Аудит"
        sub="Журнал действий"
        actions={
          <>
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="input w-auto"
              aria-label="Сколько записей показать"
            >
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={250}>250</option>
              <option value={500}>500</option>
            </select>
            <button
              type="button"
              onClick={() => q.refetch()}
              className="btn-secondary"
            >
              <RefreshCcw className="h-3.5 w-3.5" /> Обновить
            </button>
          </>
        }
      />

      <Surface label="Последние действия" aside={q.isFetching ? <Spinner /> : undefined}>
        {q.isLoading ? (
          <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-[68px] w-full rounded-row" />
            ))}
          </div>
        ) : q.isError && !q.data ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} className="bg-tile-3" />
        ) : !q.data || q.data.length === 0 ? (
          <EmptyState
            title="Журнал пуст"
            hint="Действия появятся здесь по мере поступления."
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {q.data.map((e) => {
              const Icon = ICONS[String(e.action ?? "")] ?? AlertCircle;
              return (
                <li
                  key={String(e.id ?? Math.random())}
                  className="flex items-start gap-3 rounded-row bg-tile-3 p-3 text-[14px]"
                >
                  <span className="t-body grid h-8 w-8 flex-none place-items-center rounded-full bg-tile-1">
                    <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 truncate">
                      <span className="font-medium">
                        {actionLabel(String(e.action ?? ""))}
                      </span>
                      {typeof e.user_telegram_id === "number" && (
                        <span className="badge-muted tabular bg-tile-1">
                          tg:{e.user_telegram_id}
                        </span>
                      )}
                    </div>
                    {typeof e.details === "string" && e.details && (
                      <div className="t-body mt-0.5 truncate text-[13px]">
                        {truncate(e.details, 200)}
                      </div>
                    )}
                    <div className="t-mute mt-1 flex flex-wrap items-center gap-x-2 text-[12px]">
                      {typeof e.admin_telegram_id === "number" && (
                        <span className="tabular">by admin:{e.admin_telegram_id}</span>
                      )}
                      {e.created_at && (
                        <span className="tabular">
                          · {fmtDate(String(e.created_at))} ·{" "}
                          {fmtRelative(String(e.created_at))}
                        </span>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Surface>
    </div>
  );
}

function actionLabel(action: string): string {
  const map: Record<string, string> = {
    admin_grant: "Выдан доступ",
    admin_revoke: "Отозван доступ",
    admin_create_discount: "Создана персональная скидка",
    payment_approved: "Платёж подтверждён",
    subscription_renewed: "Подписка продлена",
    vip_grant: "Выдан VIP",
    vip_revoke: "VIP снят",
    broadcast_sent: "Рассылка отправлена",
    broadcast_created: "Рассылка создана",
    ADMIN_SWITCH_TO_PLUS: "Тариф → Plus",
    ADMIN_SWITCH_TO_BASIC: "Тариф → Basic",
    promo_consumed: "Промокод использован",
    user_deleted: "Удаление пользователя",
  };
  return map[action] ?? action;
}
