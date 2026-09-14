import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Search } from "lucide-react";
import { endpoints } from "@/lib/api";
import { fmtNum, fmtRub, fmtDate } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { ListRow } from "@/components/ui/controls";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

type SortBy = "total_revenue" | "invited_count" | "cashback_paid";

const SORT_LABELS: Record<SortBy, string> = {
  total_revenue: "По доходу",
  invited_count: "По приглашённым",
  cashback_paid: "По cashback",
};

export function Referrals() {
  const overall = useQuery({
    queryKey: ["referrals", "overall"],
    queryFn: endpoints.referralsOverall,
  });

  const [sortBy, setSortBy] = useState<SortBy>("total_revenue");
  const [q, setQ] = useState("");
  const [searchSubmitted, setSearchSubmitted] = useState("");

  const top = useQuery({
    queryKey: ["referrals", "top", sortBy, searchSubmitted],
    queryFn: () =>
      endpoints.referralsTop({
        sort_by: sortBy,
        sort_order: "DESC",
        limit: 50,
        q: searchSubmitted || undefined,
      }),
  });

  const [selected, setSelected] = useState<number | null>(null);

  return (
    <div>
      <PageHeader title="Рефералы" sub="Партнёрка" />

      <Bento>
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          label="Всего реферреров"
          value={fmtNum(asNum(overall.data?.total_referrers))}
          loading={overall.isLoading}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          variant="raised"
          label="Приглашённых"
          value={fmtNum(asNum(overall.data?.total_referrals))}
          loading={overall.isLoading}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          variant="accent"
          label="Доход с партнёрки"
          value={fmtRub(asNum(overall.data?.total_revenue))}
          loading={overall.isLoading}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          variant="steel"
          label="Cashback выплачено"
          value={fmtRub(asNum(overall.data?.total_cashback_paid))}
          loading={overall.isLoading}
        />

        <Surface
          className="sm:col-span-6 xl:col-span-7"
          label="Топ реферреров"
          aside={
            <select
              className="input w-auto"
              aria-label="Сортировка"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortBy)}
            >
              {(Object.keys(SORT_LABELS) as SortBy[]).map((k) => (
                <option key={k} value={k}>
                  {SORT_LABELS[k]}
                </option>
              ))}
            </select>
          }
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setSearchSubmitted(q.trim());
            }}
            className="mb-4 flex items-center gap-2"
          >
            <div className="relative min-w-0 flex-1">
              <Search
                className="t-mute pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2"
                aria-hidden="true"
              />
              <input
                className="input pl-10"
                aria-label="Поиск по ID или @username"
                placeholder="Поиск по ID или @username..."
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
            {q && (
              <button
                type="button"
                onClick={() => {
                  setQ("");
                  setSearchSubmitted("");
                }}
                className="btn-ghost"
              >
                Очистить
              </button>
            )}
          </form>

          {top.isLoading ? (
            <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-[60px] w-full rounded-row" />
              ))}
            </div>
          ) : top.isError && !top.data ? (
            <ErrorState error={top.error} onRetry={() => top.refetch()} className="bg-tile-3" />
          ) : !top.data || top.data.length === 0 ? (
            <EmptyState title="Пусто" hint="Под текущие фильтры реферреров нет." />
          ) : (
            <ul className="flex flex-col gap-2">
              {top.data.map((r, i) => {
                const id = Number(r.referrer_id ?? 0);
                if (!id) return null;
                const isSel = selected === id;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => setSelected(id)}
                      aria-pressed={isSel}
                      className={cn("list-row w-full py-3 text-left", isSel && "bg-tile-4")}
                    >
                      <span
                        className={cn(
                          "tabular grid h-8 w-8 flex-none place-items-center rounded-full text-[12px] font-medium",
                          isSel ? "bg-accent text-onaccent" : "t-body bg-tile-1",
                        )}
                      >
                        {i + 1}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-[14px] font-medium">
                            {r.username ? `@${String(r.username)}` : `tg:${id}`}
                          </span>
                          {typeof r.username === "string" && r.username && (
                            <span className="t-mute font-mono text-[12px]">tg:{id}</span>
                          )}
                          <span className="badge-muted tabular">
                            {fmtNum(asNum(r.invited_count))} пригл.
                          </span>
                          <span className="badge-success tabular">
                            {fmtRub(asNum(r.total_invited_revenue))} доход
                          </span>
                        </span>
                        <span className="t-mute mt-1 block text-[12px]">
                          cashback {fmtRub(asNum(r.total_cashback_paid))}
                          {r.first_referral_date
                            ? ` · с ${fmtDate(String(r.first_referral_date))}`
                            : ""}
                        </span>
                      </span>
                      <ChevronRight className="t-mute h-4 w-4 flex-none" aria-hidden="true" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Surface>

        {selected ? (
          <ReferrerDetail referrerId={selected} className="sm:col-span-6 xl:col-span-5" />
        ) : (
          <Surface className="hidden sm:col-span-6 xl:col-span-5 xl:block" variant="raised">
            <EmptyState
              title="Выбери реферрера"
              hint="Кликни по строке — увидишь детали и историю выплат."
            />
          </Surface>
        )}
      </Bento>
    </div>
  );
}

function ReferrerDetail({ referrerId, className }: { referrerId: number; className?: string }) {
  const detail = useQuery({
    queryKey: ["referrals", "detail", referrerId],
    queryFn: () => endpoints.referrerDetail(referrerId),
  });
  const history = useQuery({
    queryKey: ["referrals", "history", referrerId],
    queryFn: () => endpoints.referrerHistory(referrerId, 100),
  });

  if (detail.isLoading) {
    return (
      <Surface className={className} variant="raised" label="Реферрер">
        <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-[52px] w-full rounded-row" />
          <Skeleton className="h-[52px] w-full rounded-row" />
        </div>
      </Surface>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <Surface className={className} variant="raised" label="Реферрер">
        <EmptyState
          title="Не удалось загрузить"
          hint="Попробуй обновить страницу."
          action={
            <button type="button" className="btn-secondary" onClick={() => detail.refetch()}>
              Повторить
            </button>
          }
        />
      </Surface>
    );
  }

  const d = detail.data;
  const invited = (d.invited_users as Array<Record<string, unknown>> | undefined) ?? [];

  return (
    <div className={cn("flex min-w-0 animate-fade-in flex-col gap-[var(--gap)]", className)}>
      <Surface variant="raised" label="Реферрер">
        <h3 className="text-[15px] font-semibold">
          {d.username ? `@${String(d.username)}` : `tg:${referrerId}`}
        </h3>
        <ul className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <li>
            <ListRow title="Пригласил" value={fmtNum(asNum(d.invited_count))} className="pr-4" />
          </li>
          <li>
            <ListRow title="Купили" value={fmtNum(asNum(d.paid_count))} className="pr-4" />
          </li>
          <li>
            <ListRow
              title="Доход"
              value={<span className="text-success">{fmtRub(asNum(d.total_invited_revenue))}</span>}
              className="pr-4"
            />
          </li>
          <li>
            <ListRow title="Cashback" value={fmtRub(asNum(d.total_cashback_paid))} className="pr-4" />
          </li>
        </ul>
        <p className="t-mute mt-3 text-[13px]">
          Текущий процент:{" "}
          <b className="tabular font-semibold text-ink">
            {fmtNum(asNum(d.current_cashback_percent))}%
          </b>
        </p>
      </Surface>

      <Surface label={`Приглашённые (${invited.length})`}>
        {invited.length === 0 ? (
          <p className="t-mute text-[14px]">Никого нет.</p>
        ) : (
          <ul className="-mr-2 flex max-h-[300px] flex-col gap-2 overflow-y-auto pr-2">
            {invited.slice(0, 30).map((u, i) => (
              <li key={i}>
                <ListRow
                  title={
                    u.username
                      ? `@${String(u.username)}`
                      : `tg:${String(u.telegram_id ?? "—")}`
                  }
                  meta={typeof u.registered_at === "string" ? fmtDate(u.registered_at) : undefined}
                  trailing={
                    u.paid_amount ? (
                      <span className="badge-success tabular">{fmtRub(asNum(u.paid_amount))}</span>
                    ) : (
                      <span className="badge-muted">не платил</span>
                    )
                  }
                  className="pr-3"
                />
              </li>
            ))}
          </ul>
        )}
      </Surface>

      <Surface variant="raised" label={`История cashback (${history.data?.total ?? 0})`}>
        {history.isLoading ? (
          <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
            <Skeleton className="h-[52px] w-full rounded-row" />
            <Skeleton className="h-[52px] w-full rounded-row" />
          </div>
        ) : !history.data || history.data.rows.length === 0 ? (
          <p className="t-mute text-[14px]">Нет начислений.</p>
        ) : (
          <ul className="-mr-2 flex max-h-[400px] flex-col gap-2 overflow-y-auto pr-2">
            {history.data.rows.map((r, i) => (
              <li key={i}>
                <ListRow
                  title={
                    r.referred_username
                      ? `@${String(r.referred_username)}`
                      : `tg:${String(r.referred_user_id ?? "—")}`
                  }
                  meta={fmtDate(String(r.created_at ?? ""))}
                  trailing={<span className="badge-success tabular">{fmtRub(asNum(r.reward_amount))}</span>}
                  className="pr-3"
                />
              </li>
            ))}
          </ul>
        )}
      </Surface>
    </div>
  );
}

function asNum(v: unknown): number | undefined {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}
