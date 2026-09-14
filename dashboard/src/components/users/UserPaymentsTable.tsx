/**
 * Purchase history.
 *
 * Four defects are fixed here, three of which produced wrong numbers
 * rather than ugly ones:
 *
 * 1. The totals filtered on `p.status === "paid"` while the badge beside
 *    them treated `approved` as paid too. Every user whose provider
 *    settles as `approved` had their spend understated, directly under a
 *    row saying the payment went through. Both now call `isPaidStatus`,
 *    which is the only definition in the codebase.
 *
 * 2. The header said "Все операции в боте" over a sum of at most 100
 *    rows. For a heavy user that is a wrong number presented as a
 *    complete one. When the response is exactly the limit we cannot know
 *    whether more exist, so the card says "последние N" instead.
 *
 * 3. `key={p.id ?? p.purchase_id ?? Math.random()}` generated a fresh key
 *    on every render for any row missing both ids, remounting the row —
 *    losing its expanded state and re-running its animation each time
 *    anything on the page changed.
 *
 * 4. Six columns do not fit a phone. Rather than scrolling horizontally
 *    through the most-used view on a support call, small screens get a
 *    Priority+ card: date, item, amount, with the rest behind a
 *    disclosure.
 */
import { useState } from "react";
import { ChevronDown, Loader2, RefreshCcw } from "lucide-react";
import type { PurchaseRow } from "@/types/user";
import {
  isPaidStatus,
  paymentTone,
  providerLabel,
  purchaseLabel,
  type PaymentTone,
} from "@/lib/userDomain";
import { fmtDate, fmtRub } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Surface } from "@/components/ui/Surface";
import { IconButton } from "@/components/ui/controls";
import { EmptyState } from "@/components/ui/states";
import { CopyButton } from "./shared/CopyButton";
import { CardError, CardLoading } from "./shared/CardStates";

export interface UserPaymentsTableProps {
  rows: PurchaseRow[];
  isLoading?: boolean;
  isFetching?: boolean;
  isError?: boolean;
  /** The `limit` the rows were fetched with — used to tell a complete
      history from a truncated one. */
  limit?: number;
  onRefresh?: () => void;
  className?: string;
}

const TONE_CLASS: Record<PaymentTone, string> = {
  paid: "badge-success",
  pending: "badge-warning",
  expired: "badge-muted",
  failed: "badge-danger",
  unknown: "badge-muted",
};

const TONE_LABEL: Record<PaymentTone, string> = {
  paid: "Оплачен",
  pending: "Ожидает",
  expired: "Истёк",
  failed: "Отменён",
  unknown: "—",
};

function StatusBadge({ status }: { status: string | null | undefined }) {
  const tone = paymentTone(status);
  return (
    <span className={TONE_CLASS[tone]}>
      {TONE_LABEL[tone] === "—" ? status || "—" : TONE_LABEL[tone]}
    </span>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <ChevronDown
      className={cn("h-4 w-4 transition-transform duration-[var(--dur-instant)]", open && "rotate-180")}
      aria-hidden="true"
    />
  );
}

/**
 * A key that survives re-renders even when the backend sent neither id.
 * Index alone would be wrong if rows could reorder, but this list is a
 * server-ordered snapshot replaced wholesale on refetch, so index is
 * stable within a render pass and combining it with the identifiers
 * keeps distinct rows distinct.
 */
function rowKey(p: PurchaseRow, index: number): string {
  return String(p.purchase_id ?? p.id ?? `idx-${index}`);
}

export function UserPaymentsTable({
  rows,
  isLoading = false,
  isFetching = false,
  isError = false,
  limit,
  onRefresh,
  className,
}: UserPaymentsTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const paidRows = rows.filter((p) => isPaidStatus(p.status));
  const paidTotal = paidRows.reduce((s, p) => s + (p.price_rubles ?? 0), 0);
  const pendingCount = rows.filter((p) => paymentTone(p.status) === "pending").length;

  // Exactly `limit` rows means the window was filled, which is
  // indistinguishable from there being more. Claiming completeness in
  // that case is the thing that made the old total misleading.
  const maybeTruncated = typeof limit === "number" && rows.length >= limit;

  function toggle(key: string) {
    setExpanded((cur) => (cur === key ? null : key));
  }

  return (
    <Surface
      label="История покупок"
      className={className}
      aside={
        <span className="flex items-center gap-2">
          {isFetching && (
            <>
              <Loader2 className="t-mute h-4 w-4 animate-spin" aria-hidden="true" />
              <span className="sr-only" role="status">
                Обновляю историю покупок
              </span>
            </>
          )}
          {onRefresh && (
            <IconButton small label="Обновить историю покупок" onClick={onRefresh} className="bg-tile-3 hover:bg-tile-4">
              <RefreshCcw className="h-3.5 w-3.5" aria-hidden="true" />
            </IconButton>
          )}
        </span>
      }
    >
      <div className="mb-4">
        <p className="text-[15px] font-semibold">
          {maybeTruncated ? `Последние ${rows.length} операций` : "Все операции в боте"}
        </p>
        <p className="t-mute mt-1 text-[13px]">
          <span className="tabular">{paidRows.length}</span> оплачено ·{" "}
          <span className="tabular">{fmtRub(paidTotal)}</span>
          {maybeTruncated ? " за показанные" : " всего"}
          {pendingCount > 0 && (
            <>
              {" "}
              · <span className="tabular">{pendingCount}</span> в ожидании
            </>
          )}
        </p>
      </div>

      {isLoading ? (
        <CardLoading label="Загружаю историю покупок…" />
      ) : isError ? (
        <CardError
          title="Не удалось загрузить историю покупок"
          note="Это ошибка запроса, а не отсутствие покупок."
        />
      ) : rows.length === 0 ? (
        <EmptyState
          title="Покупок нет"
          hint="Пользователь ещё ничего не покупал — ни подписку, ни traffic-паки, ни пополнение баланса."
        />
      ) : (
        <>
          {/* ── Phone: Priority+ cards ───────────────────────────── */}
          <ul className="flex flex-col gap-2 md:hidden">
            {rows.map((p, i) => {
              const key = rowKey(p, i);
              const open = expanded === key;
              return (
                <li key={key} className="rounded-row bg-tile-3">
                  <button
                    type="button"
                    onClick={() => toggle(key)}
                    aria-expanded={open}
                    className="flex min-h-[52px] w-full items-center justify-between gap-3 rounded-row px-4 py-2.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-[14px] font-medium">{purchaseLabel(p)}</span>
                      <span className="t-mute mt-0.5 block text-[12px]">{fmtDate(p.created_at)}</span>
                    </span>
                    <span className="t-mute flex shrink-0 items-center gap-2">
                      <span className="tabular text-[14px] font-semibold text-ink">
                        {typeof p.price_rubles === "number" ? fmtRub(p.price_rubles) : "—"}
                      </span>
                      <Chevron open={open} />
                    </span>
                  </button>
                  {open && (
                    <div className="flex flex-col gap-1 px-4 pb-3">
                      <PurchaseDetails p={p} />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>

          {/* ── Desktop: table ───────────────────────────────────── */}
          <div className="-mx-2 hidden overflow-x-auto px-2 md:block">
            <table className="dtable min-w-[640px]">
              <caption className="sr-only">
                История покупок пользователя: дата, что куплено, сумма, провайдер, промокод и
                статус. Строку можно раскрыть для технических данных платежа.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Дата</th>
                  <th scope="col">Что куплено</th>
                  <th scope="col" className="num">
                    Сумма
                  </th>
                  <th scope="col">Провайдер</th>
                  <th scope="col">Промо</th>
                  <th scope="col">Статус</th>
                  <th scope="col" className="w-10">
                    <span className="sr-only">Подробности</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p, i) => {
                  const key = rowKey(p, i);
                  const open = expanded === key;
                  return [
                    <tr key={key}>
                      <td className="t-mute tabular whitespace-nowrap">{fmtDate(p.created_at)}</td>
                      <td>
                        <span className="font-medium">{purchaseLabel(p)}</span>
                      </td>
                      <td className="num whitespace-nowrap font-medium">
                        {typeof p.price_rubles === "number" ? fmtRub(p.price_rubles) : "—"}
                      </td>
                      <td className="t-mute whitespace-nowrap">{providerLabel(p.payment_provider)}</td>
                      <td>
                        {p.promo_code ? (
                          <span className="badge-accent font-mono text-[11px]">{p.promo_code}</span>
                        ) : (
                          <span className="text-ash">—</span>
                        )}
                      </td>
                      <td>
                        <StatusBadge status={p.status} />
                      </td>
                      <td>
                        <IconButton
                          small
                          label={open ? "Скрыть детали платежа" : "Показать детали платежа"}
                          aria-expanded={open}
                          onClick={() => toggle(key)}
                          className="bg-transparent hover:bg-tile-3"
                        >
                          <Chevron open={open} />
                        </IconButton>
                      </td>
                    </tr>,
                    open ? (
                      <tr key={`${key}-details`}>
                        <td colSpan={7}>
                          <div className="grid gap-x-6 gap-y-1 rounded-row bg-tile-3 px-4 py-3 sm:grid-cols-2">
                            <PurchaseDetails p={p} />
                          </div>
                        </td>
                      </tr>
                    ) : null,
                  ];
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Surface>
  );
}

/**
 * The fields a disputed payment is actually reconciled with.
 *
 * `provider_invoice_id` is the join key to the provider's dashboard and
 * was never rendered, so verifying a "I paid and you took my money"
 * claim meant a database query. `expires_at` separates an abandoned
 * basket from a failed charge, which look identical without it.
 */
function PurchaseDetails({ p }: { p: PurchaseRow }) {
  const items: Array<{ label: string; value: string | null; copy?: boolean }> = [
    { label: "ID покупки", value: p.purchase_id ?? null, copy: true },
    { label: "Инвойс провайдера", value: p.provider_invoice_id ?? null, copy: true },
    { label: "Провайдер", value: providerLabel(p.payment_provider) },
    { label: "Статус", value: p.status ?? null },
    { label: "Истекает", value: p.expires_at ? fmtDate(p.expires_at) : null },
    { label: "Промокод", value: p.promo_code ?? null },
    { label: "Страна", value: p.country ? p.country.toUpperCase() : null },
    { label: "Период", value: p.period_days ? `${p.period_days} дн` : null },
  ];

  return (
    <>
      {items
        .filter((it) => it.value)
        .map((it) => (
          <div key={it.label} className="flex min-h-[28px] items-center justify-between gap-2 text-[13px]">
            <span className="t-mute shrink-0">{it.label}</span>
            <span className="flex min-w-0 items-center gap-1">
              <span
                className={cn("t-body min-w-0 truncate text-right", it.copy && "font-mono text-[12px]")}
                title={it.value as string}
              >
                {it.value}
              </span>
              {it.copy && <CopyButton value={it.value} label={it.label} />}
            </span>
          </div>
        ))}
    </>
  );
}
