/**
 * What admins did to this user, scoped to this user.
 *
 * `/audit/recent` accepts a `telegram_id`, which is what turns a global
 * tail into an account history — but the Users screen never called it,
 * so answering "who gave this person 90% off in March" meant scrolling
 * the global audit page and hoping.
 *
 * The row shape is deliberately `Record<string, unknown>`: the audit
 * table has grown fields over time and older rows genuinely lack the
 * newer ones. Every field is therefore read through a narrowing helper
 * and a missing one renders as nothing rather than the string
 * "undefined" — which is what the global audit page prints today.
 */
import { fmtDate, fmtRelative, truncate } from "@/lib/format";
import { Surface } from "@/components/ui/Surface";
import { EmptyState } from "@/components/ui/states";
import { CardError, CardLoading } from "./shared/CardStates";

export type AuditRecord = Record<string, unknown>;

export interface UserAuditLogProps {
  rows: AuditRecord[] | null | undefined;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  className?: string;
}

const ACTION_RU: Record<string, string> = {
  admin_grant: "Выдан доступ",
  admin_revoke: "Отозван доступ",
  admin_create_discount: "Создана скидка",
  admin_delete_discount: "Снята скидка",
  admin_balance_change: "Изменён баланс",
  admin_cashback_fix: "Зафиксирован кешбэк",
  payment_approved: "Платёж подтверждён",
  subscription_renewed: "Подписка продлена",
  vip_grant: "Выдан VIP",
  vip_revoke: "VIP снят",
  promo_consumed: "Промокод использован",
  user_deleted: "Удаление пользователя",
  ADMIN_SWITCH_TO_PLUS: "Тариф → Plus",
  ADMIN_SWITCH_TO_BASIC: "Тариф → Basic",
};

function str(v: unknown): string | null {
  if (typeof v === "string" && v.trim()) return v;
  if (typeof v === "number") return String(v);
  return null;
}

/** Which admin. The column has been spelled three ways across
    migrations, so all of them are accepted rather than showing "—" for
    rows written by an older version of the bot. */
function adminOf(r: AuditRecord): string | null {
  return (
    str(r.admin_sub) ??
    str(r.admin_telegram_id) ??
    str(r.admin_id) ??
    str(r.actor) ??
    null
  );
}

function whenOf(r: AuditRecord): string | null {
  return str(r.created_at) ?? str(r.timestamp) ?? str(r.ts) ?? null;
}

export function UserAuditLog({
  rows,
  isLoading = false,
  isError = false,
  onRetry,
  className,
}: UserAuditLogProps) {
  return (
    <Surface label="Действия админов" className={className}>
      {isLoading ? (
        <CardLoading label="Загружаю аудит…" />
      ) : isError ? (
        <CardError
          title="Аудит не загрузился"
          note="Ошибка запроса, а не отсутствие действий."
          onRetry={onRetry}
        />
      ) : !rows || rows.length === 0 ? (
        <EmptyState
          title="Действий не было"
          hint="Ни один админ не менял этого пользователя вручную."
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((r, i) => {
            const action = str(r.action) ?? str(r.action_type) ?? "";
            const admin = adminOf(r);
            const when = whenOf(r);
            const details = str(r.details) ?? str(r.detail) ?? null;

            return (
              <li
                key={String(str(r.id) ?? `${action}-${when ?? i}`)}
                className="rounded-row bg-tile-3 px-4 py-3"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <span className="text-[14px] font-medium">
                    {ACTION_RU[action] ?? action ?? "Действие"}
                  </span>
                  {when && (
                    <span className="t-mute tabular shrink-0 text-[12px]" title={fmtDate(when)}>
                      {fmtRelative(when)}
                    </span>
                  )}
                </div>

                <div className="t-mute mt-1 flex flex-wrap items-baseline gap-x-2 text-[12px]">
                  <span className="tabular">{admin ? `админ ${admin}` : "админ не указан"}</span>
                  {when && <span className="tabular">· {fmtDate(when)}</span>}
                </div>

                {details && (
                  <p className="t-body mt-1 break-words text-[13px] leading-5">
                    {truncate(details, 200)}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Surface>
  );
}
