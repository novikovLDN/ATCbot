import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Users,
  Wallet,
  Coins,
  ChevronRight,
  Search,
  ArrowDownUp,
} from "lucide-react";
import { endpoints } from "@/lib/api";
import { fmtNum, fmtRub, fmtDate } from "@/lib/format";
import { StatCard } from "@/components/StatCard";
import { Spinner } from "@/components/Spinner";
import { EmptyState } from "@/components/EmptyState";

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
    <div className="space-y-6">
      <header>
        <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Партнёрка
        </div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
          Рефералы
        </h1>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard
          label="Всего реферреров"
          value={fmtNum(asNum(overall.data?.total_referrers))}
          icon={Users}
          loading={overall.isLoading}
        />
        <StatCard
          label="Приглашённых"
          value={fmtNum(asNum(overall.data?.total_referrals))}
          tone="accent"
          loading={overall.isLoading}
        />
        <StatCard
          label="Доход с партнёрки"
          value={fmtRub(asNum(overall.data?.total_revenue))}
          tone="success"
          icon={Wallet}
          loading={overall.isLoading}
        />
        <StatCard
          label="Cashback выплачено"
          value={fmtRub(asNum(overall.data?.total_cashback_paid))}
          icon={Coins}
          loading={overall.isLoading}
        />
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_400px]">
        <div className="card p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
                Топ реферреров
              </div>
              <h2 className="text-lg font-semibold text-fg">Лидерборд</h2>
            </div>
            <div className="flex items-center gap-2">
              <select
                className="input w-auto"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as SortBy)}
              >
                {(Object.keys(SORT_LABELS) as SortBy[]).map((k) => (
                  <option key={k} value={k}>
                    {SORT_LABELS[k]}
                  </option>
                ))}
              </select>
              <ArrowDownUp className="hidden h-3.5 w-3.5 text-fg-subtle md:block" />
            </div>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              setSearchSubmitted(q.trim());
            }}
            className="mb-4 flex items-center gap-2 rounded-xl border border-border bg-bg-subtle/60 px-3 py-1"
          >
            <Search className="h-3.5 w-3.5 text-fg-subtle" />
            <input
              className="flex-1 bg-transparent py-1.5 text-sm outline-none"
              placeholder="Поиск по ID или @username..."
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {q && (
              <button
                type="button"
                onClick={() => {
                  setQ("");
                  setSearchSubmitted("");
                }}
                className="text-xs text-fg-subtle hover:text-fg"
              >
                Очистить
              </button>
            )}
          </form>

          {top.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-fg-muted">
              <Spinner /> Загружаю...
            </div>
          ) : !top.data || top.data.length === 0 ? (
            <EmptyState
              icon={Users}
              title="Пусто"
              description="Под текущие фильтры реферреров нет."
            />
          ) : (
            <ul className="divide-y divide-border/60">
              {top.data.map((r, i) => {
                const id = Number(r.referrer_id ?? 0);
                if (!id) return null;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => setSelected(id)}
                      className={
                        selected === id
                          ? "flex w-full items-center gap-3 rounded-lg bg-bg-elevated/60 px-2 py-3 text-left transition"
                          : "flex w-full items-center gap-3 rounded-lg px-2 py-3 text-left transition hover:bg-bg-elevated/40"
                      }
                    >
                      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-bg-elevated font-mono text-xs text-fg-muted ring-1 ring-border">
                        {i + 1}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate font-medium text-fg">
                            {r.username ? `@${String(r.username)}` : `tg:${id}`}
                          </span>
                          {r.username && (
                            <span className="font-mono text-[11px] text-fg-subtle">
                              tg:{id}
                            </span>
                          )}
                          <span className="badge-muted">
                            {fmtNum(asNum(r.invited_count))} пригл.
                          </span>
                          <span className="badge-success">
                            {fmtRub(asNum(r.total_invited_revenue))} доход
                          </span>
                        </div>
                        <div className="mt-1 text-xs text-fg-muted">
                          cashback {fmtRub(asNum(r.total_cashback_paid))}
                          {r.first_referral_date
                            ? ` · с ${fmtDate(String(r.first_referral_date))}`
                            : ""}
                        </div>
                      </div>
                      <ChevronRight className="h-4 w-4 shrink-0 text-fg-subtle" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {selected ? (
          <ReferrerDetail referrerId={selected} />
        ) : (
          <div className="card hidden p-6 lg:block">
            <EmptyState
              icon={Users}
              title="Выбери реферрера"
              description="Кликни по строке — увидишь детали и историю выплат."
            />
          </div>
        )}
      </div>
    </div>
  );
}

function ReferrerDetail({ referrerId }: { referrerId: number }) {
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
      <div className="card flex items-center gap-3 p-6 text-sm text-fg-muted">
        <Spinner /> Загружаю...
      </div>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <EmptyState
        icon={Users}
        title="Не удалось загрузить"
        description="Попробуй обновить страницу."
      />
    );
  }

  const d = detail.data;
  const invited = (d.invited_users as Array<Record<string, unknown>> | undefined) ?? [];

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="card p-5">
        <div className="text-xs uppercase tracking-wider text-fg-subtle">
          Реферрер
        </div>
        <h3 className="mt-1 text-lg font-semibold text-fg">
          {d.username ? `@${String(d.username)}` : `tg:${referrerId}`}
        </h3>
        <div className="mt-4 grid grid-cols-2 gap-2">
          <Tile label="Пригласил" value={fmtNum(asNum(d.invited_count))} />
          <Tile label="Купили" value={fmtNum(asNum(d.paid_count))} />
          <Tile
            label="Доход"
            value={fmtRub(asNum(d.total_invited_revenue))}
            tone="success"
          />
          <Tile
            label="Cashback"
            value={fmtRub(asNum(d.total_cashback_paid))}
          />
        </div>
        <div className="mt-3 text-xs text-fg-muted">
          Текущий процент:{" "}
          <b className="text-fg">
            {fmtNum(asNum(d.current_cashback_percent))}%
          </b>
        </div>
      </div>

      <div className="card p-5">
        <div className="mb-3 text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Приглашённые ({invited.length})
        </div>
        {invited.length === 0 ? (
          <div className="text-sm text-fg-muted">Никого нет.</div>
        ) : (
          <ul className="max-h-[300px] divide-y divide-border/60 overflow-y-auto">
            {invited.slice(0, 30).map((u, i) => (
              <li key={i} className="flex items-center justify-between py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-fg">
                    {u.username
                      ? `@${String(u.username)}`
                      : `tg:${String(u.telegram_id ?? "—")}`}
                  </div>
                  {typeof u.registered_at === "string" && (
                    <div className="text-xs text-fg-muted">
                      {fmtDate(u.registered_at)}
                    </div>
                  )}
                </div>
                {u.paid_amount ? (
                  <span className="badge-success">
                    {fmtRub(asNum(u.paid_amount))}
                  </span>
                ) : (
                  <span className="badge-muted">не платил</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="card p-5">
        <div className="mb-3 text-xs font-medium uppercase tracking-wider text-fg-subtle">
          История cashback ({history.data?.total ?? 0})
        </div>
        {history.isLoading ? (
          <Spinner />
        ) : !history.data || history.data.rows.length === 0 ? (
          <div className="text-sm text-fg-muted">Нет начислений.</div>
        ) : (
          <ul className="max-h-[400px] divide-y divide-border/60 overflow-y-auto">
            {history.data.rows.map((r, i) => (
              <li key={i} className="flex items-center justify-between py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-fg">
                    {r.referred_username
                      ? `@${String(r.referred_username)}`
                      : `tg:${String(r.referred_user_id ?? "—")}`}
                  </div>
                  <div className="text-xs text-fg-muted">
                    {fmtDate(String(r.created_at ?? ""))}
                  </div>
                </div>
                <span className="badge-success">
                  {fmtRub(asNum(r.reward_amount))}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function Tile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "success";
}) {
  const text = tone === "success" ? "text-success" : "text-fg";
  return (
    <div className="rounded-xl border border-border bg-bg-subtle/60 p-3">
      <div className="text-[11px] uppercase tracking-wider text-fg-subtle">
        {label}
      </div>
      <div className={`mt-1 truncate text-lg font-semibold ${text}`}>{value}</div>
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
