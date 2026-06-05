import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  RefreshCcw,
  ScrollText,
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
import { EmptyState } from "@/components/EmptyState";
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
    refetchInterval: 30_000,
  });

  useEventStream(() => {
    // Soft refresh on any event — feels live without hammering DB.
    q.refetch();
  });

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Журнал
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
            Аудит
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="input w-auto"
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
        </div>
      </header>

      <div className="card p-5">
        <div className="mb-4 flex items-center justify-between">
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Последние действия
          </div>
          {q.isFetching && <Spinner />}
        </div>

        {q.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner /> Загружаю...
          </div>
        ) : !q.data || q.data.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title="Журнал пуст"
            description="Действия появятся здесь по мере поступления."
          />
        ) : (
          <ul className="divide-y divide-border/60">
            {q.data.map((e) => {
              const Icon = ICONS[String(e.action ?? "")] ?? AlertCircle;
              return (
                <li
                  key={String(e.id ?? Math.random())}
                  className="flex items-start gap-3 py-3 text-sm"
                >
                  <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
                    <Icon className="h-3.5 w-3.5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 truncate">
                      <span className="font-medium text-fg">
                        {actionLabel(String(e.action ?? ""))}
                      </span>
                      {typeof e.user_telegram_id === "number" && (
                        <span className="badge-muted">
                          tg:{e.user_telegram_id}
                        </span>
                      )}
                    </div>
                    {typeof e.details === "string" && e.details && (
                      <div className="mt-0.5 truncate text-xs text-fg-muted">
                        {truncate(e.details, 200)}
                      </div>
                    )}
                    <div className="mt-1 flex items-center gap-2 text-[11px] text-fg-subtle">
                      {typeof e.admin_telegram_id === "number" && (
                        <span>by admin:{e.admin_telegram_id}</span>
                      )}
                      {e.created_at && (
                        <span>
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
      </div>
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
